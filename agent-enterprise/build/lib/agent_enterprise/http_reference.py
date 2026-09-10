"""Runnable HTTP reference for separately deployed Policy and Executor services."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Mapping

from agent_core import Decision, decision_to_action

from .authorization import (
    AuthorizationError, AuthorizedCommandExecutor, ExecutionCommand,
    ExecutorAuthorizationVerifier, PolicyAuthorizationIssuer, SignedAuthorization,
)
from .audit import DeltaAuthorizationAuditStore


def _handler(responder: Callable[[dict, dict[str, str]], tuple[int, dict]]):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("JSON object required")
                status, response = responder(body, dict(self.headers))
            except (ValueError, AuthorizationError) as exc:
                status, response = 400, {"error": str(exc)}
            encoded = json.dumps(response, sort_keys=True).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_: object) -> None:
            return
    return Handler


def policy_server(issuer: PolicyAuthorizationIssuer, *, audience: str, audit_store: DeltaAuthorizationAuditStore | None = None, development_bearer_token: str | None = None, development_principal_id: str = "development-client", preconditions_for: Callable[[str, str, int], Mapping[str, Any]] | None = None, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    """Policy endpoint signs only a deterministic, bounded CREATE_ORDER policy.

    ``preconditions_for`` is domain policy code: it supplies facts that the
    target domain will conditionally enforce at effect time. The executor
    verifies their signature but deliberately does not interpret them.
    """
    def authorize(request: dict, headers: dict[str, str]) -> tuple[int, dict]:
        if development_bearer_token is not None and headers.get("Authorization") != f"Bearer {development_bearer_token}":
            if audit_store: audit_store.record("POLICY_DENIED", detail="unauthenticated caller")
            return 401, {"error": "authenticated caller required"}
        order_id, product_id, quantity = request.get("order_id"), request.get("product_id"), request.get("quantity")
        if not isinstance(order_id, str) or not isinstance(product_id, str) or not isinstance(quantity, int):
            raise ValueError("order_id, product_id, and integer quantity are required")
        if quantity < 1 or quantity > 10:
            if audit_store: audit_store.record("POLICY_DENIED", principal_id=development_principal_id, detail="quantity policy")
            return 403, {"error": "deterministic order policy denied quantity"}
        decision = Decision(order_id, "order", "APPROVE_ORDER", "MEDIUM", "quantity is within policy")
        action = decision_to_action(decision, "CREATE_ORDER", parameters={"product_id": product_id, "quantity": quantity}, idempotency_key=f"order:{order_id}")
        preconditions = dict(preconditions_for(order_id, product_id, quantity)) if preconditions_for else {}
        command = ExecutionCommand.from_action(action, decision, preconditions=preconditions)
        authorization = issuer.authorize(command, audience=audience)
        if audit_store: audit_store.record("POLICY_ISSUED", principal_id=development_principal_id, claims=authorization.claims)
        return 200, {"command": command.wire(), "authorization": authorization.wire()}
    return ThreadingHTTPServer((host, port), _handler(authorize))


def executor_server(executor: AuthorizedCommandExecutor, *, audit_store: DeltaAuthorizationAuditStore | None = None, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
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
    return ThreadingHTTPServer((host, port), _handler(execute))
