"""Runnable HTTP reference for separately deployed Policy and Executor services."""

from __future__ import annotations

import json
import logging
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Mapping

from agent_core import Decision, decision_to_action

from .authorization import (
    AuthorizationError, AuthorizedCommandExecutor, ExecutionCommand,
    ExecutorAuthorizationVerifier, PolicyAuthorizationIssuer, SignedAuthorization,
)
from .audit import DeltaAuthorizationAuditStore
from .oidc import AuthenticatedPrincipal, OidcJwtValidator, OidcValidationError
from .mtls import MtlsIdentityError, verify_peer_identity

logger = logging.getLogger(__name__)
DEFAULT_MAX_REQUEST_BYTES = 1_048_576


class RequestTooLarge(ValueError):
    """The request exceeds the deployment's configured safe body limit."""


def _handler(
    responder: Callable[[dict, dict[str, str]], tuple[int, dict]],
    *,
    expected_client_dns_name: str | None = None,
    expected_client_uri: str | None = None,
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            try:
                if expected_client_dns_name is not None or expected_client_uri is not None:
                    verify_peer_identity(
                        self.connection,
                        expected_dns_name=expected_client_dns_name,
                        expected_uri=expected_client_uri,
                    )
                length = _content_length(self.headers.get("Content-Length"), max_request_bytes)
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("JSON object required")
                status, response = responder(body, dict(self.headers))
            except RequestTooLarge as exc:
                status, response = 413, {"error": str(exc)}
            except (ValueError, AuthorizationError, MtlsIdentityError) as exc:
                status, response = 400, {"error": str(exc)}
            except Exception:
                # The reference must not sever the TCP connection on an
                # infrastructure failure. The detailed exception stays in the
                # service log; callers receive an explicit retryable failure.
                logger.exception("Unhandled enterprise HTTP handler failure")
                status, response = 500, {"error": "internal service error"}
            encoded = json.dumps(response, sort_keys=True).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_: object) -> None:
            return
    return Handler


def policy_server(issuer: PolicyAuthorizationIssuer, *, audience: str, audit_store: DeltaAuthorizationAuditStore | None = None, development_bearer_token: str | None = None, development_principal_id: str = "development-client", oidc_validator: OidcJwtValidator | None = None, preconditions_for: Callable[[str, str, int], Mapping[str, Any]] | None = None, host: str = "127.0.0.1", port: int = 0, ssl_context: ssl.SSLContext | None = None, expected_client_dns_name: str | None = None, expected_client_uri: str | None = None, max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES) -> ThreadingHTTPServer:
    """Policy endpoint signs only a deterministic, bounded CREATE_ORDER policy.

    ``preconditions_for`` is domain policy code: it supplies facts that the
    target domain will conditionally enforce at effect time. The executor
    verifies their signature but deliberately does not interpret them.
    """
    if development_bearer_token is not None and oidc_validator is not None:
        raise ValueError("choose either development_bearer_token or oidc_validator")

    def authorize(request: dict, headers: dict[str, str]) -> tuple[int, dict]:
        principal_id = development_principal_id
        if oidc_validator is not None:
            try:
                principal_id = oidc_validator.authenticate(headers.get("Authorization")).principal_id
            except OidcValidationError as exc:
                if audit_store: audit_store.record("POLICY_DENIED", detail="unauthenticated caller")
                return 401, {"error": str(exc)}
        elif development_bearer_token is not None and headers.get("Authorization") != f"Bearer {development_bearer_token}":
            if audit_store: audit_store.record("POLICY_DENIED", detail="unauthenticated caller")
            return 401, {"error": "authenticated caller required"}
        order_id, product_id, quantity = request.get("order_id"), request.get("product_id"), request.get("quantity")
        if not isinstance(order_id, str) or not isinstance(product_id, str) or not isinstance(quantity, int):
            raise ValueError("order_id, product_id, and integer quantity are required")
        if quantity < 1 or quantity > 10:
            if audit_store: audit_store.record("POLICY_DENIED", principal_id=principal_id, detail="quantity policy")
            return 403, {"error": "deterministic order policy denied quantity"}
        decision = Decision(order_id, "order", "APPROVE_ORDER", "MEDIUM", "quantity is within policy")
        action = decision_to_action(decision, "CREATE_ORDER", parameters={"product_id": product_id, "quantity": quantity}, idempotency_key=f"order:{order_id}")
        preconditions = dict(preconditions_for(order_id, product_id, quantity)) if preconditions_for else {}
        command = ExecutionCommand.from_action(action, decision, preconditions=preconditions)
        authorization = issuer.authorize(command, audience=audience)
        if audit_store: audit_store.record("POLICY_ISSUED", principal_id=principal_id, claims=authorization.claims)
        return 200, {"command": command.wire(), "authorization": authorization.wire()}
    _validate_request_limit(max_request_bytes)
    _validate_mtls_identity(ssl_context, expected_client_dns_name, expected_client_uri)
    server = ThreadingHTTPServer((host, port), _handler(
        authorize, expected_client_dns_name=expected_client_dns_name, expected_client_uri=expected_client_uri,
        max_request_bytes=max_request_bytes,
    ))
    if ssl_context is not None:
        server.socket = ssl_context.wrap_socket(server.socket, server_side=True)
    return server


def executor_server(executor: AuthorizedCommandExecutor, *, audit_store: DeltaAuthorizationAuditStore | None = None, host: str = "127.0.0.1", port: int = 0, ssl_context: ssl.SSLContext | None = None, expected_client_dns_name: str | None = None, expected_client_uri: str | None = None, max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES) -> ThreadingHTTPServer:
    """Executor endpoint verifies an envelope immediately before its effect."""
    def execute(request: dict, _: dict[str, str]) -> tuple[int, dict]:
        command = ExecutionCommand.from_wire(request.get("command", {}))
        authorization = SignedAuthorization.from_wire(request.get("authorization", {}))
        try:
            effect_result = executor.execute(command, authorization)
        except AuthorizationError as exc:
            if audit_store: audit_store.record("EXECUTOR_REJECTED", claims=authorization.claims, detail=str(exc))
            raise
        if audit_store: audit_store.record("EXECUTOR_EXECUTED", claims=authorization.claims)
        response = {"status": "executed", "action_id": command.action_id}
        if effect_result is not None:
            response["effect_result"] = effect_result
        return 200, response
    _validate_request_limit(max_request_bytes)
    _validate_mtls_identity(ssl_context, expected_client_dns_name, expected_client_uri)
    server = ThreadingHTTPServer((host, port), _handler(
        execute, expected_client_dns_name=expected_client_dns_name, expected_client_uri=expected_client_uri,
        max_request_bytes=max_request_bytes,
    ))
    if ssl_context is not None:
        server.socket = ssl_context.wrap_socket(server.socket, server_side=True)
    return server


def _validate_mtls_identity(
    ssl_context: ssl.SSLContext | None,
    expected_client_dns_name: str | None,
    expected_client_uri: str | None,
) -> None:
    selectors = (expected_client_dns_name is not None) + (expected_client_uri is not None)
    if ssl_context is None and selectors:
        raise ValueError("an expected client identity requires ssl_context")
    if ssl_context is not None and selectors != 1:
        raise ValueError("ssl_context requires exactly one expected client DNS SAN or URI SAN")


def _validate_request_limit(max_request_bytes: int) -> None:
    if not isinstance(max_request_bytes, int) or isinstance(max_request_bytes, bool) or max_request_bytes < 1:
        raise ValueError("max_request_bytes must be a positive integer")


def _content_length(value: str | None, max_request_bytes: int) -> int:
    if value is None:
        raise ValueError("Content-Length is required")
    try:
        length = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Content-Length must be an integer") from exc
    if length < 0:
        raise ValueError("Content-Length must not be negative")
    if length > max_request_bytes:
        raise RequestTooLarge(f"request body exceeds {max_request_bytes} byte limit")
    return length
