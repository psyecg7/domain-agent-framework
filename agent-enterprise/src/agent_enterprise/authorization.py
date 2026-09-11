"""Asymmetric, cross-service authorization for a policy-to-executor boundary.

This package is deliberately outside agent-core. It becomes meaningful only
when the issuer's Ed25519 private key is held by a separately deployed Policy
service and the Executor receives only the corresponding public key.
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from agent_core import Action, Decision


class AuthorizationError(RuntimeError):
    """Authorization is missing, altered, expired, misdirected, or replayed."""


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _payload_hash(parameters: Mapping[str, Any]) -> str:
    try:
        encoded = _canonical_json(dict(parameters))
    except (TypeError, ValueError) as exc:
        # A malformed/non-JSON-serializable payload must fail closed as an
        # AuthorizationError, not leak a raw TypeError out of authorize().
        raise AuthorizationError("Command parameters are not JSON-serializable") from exc
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ExecutionCommand:
    """Network-safe action description received by the Executor service."""

    action_id: str
    decision_id: str
    action_type: str
    entity_id: str
    entity_type: str
    parameters: dict[str, Any]
    preconditions: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None

    @classmethod
    def from_action(cls, action: Action, decision: Decision, *, preconditions: Mapping[str, Any] | None = None) -> "ExecutionCommand":
        if action.entity_id != decision.entity_id or action.entity_type != decision.entity_type:
            raise AuthorizationError("Action target does not match its Decision")
        return cls(
            action_id=action.action_id,
            decision_id=decision.decision_id,
            action_type=action.action_type,
            entity_id=action.entity_id,
            entity_type=action.entity_type,
            parameters=dict(action.parameters),
            preconditions=dict(preconditions or {}),
            idempotency_key=action.idempotency_key,
        )

    def binding(self) -> dict[str, str | None]:
        return {
            "action_id": self.action_id,
            "decision_id": self.decision_id,
            "action_type": self.action_type,
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "parameters_sha256": _payload_hash(self.parameters),
            "preconditions_sha256": _payload_hash(self.preconditions),
            "idempotency_key": self.idempotency_key,
        }

    def wire(self) -> dict[str, Any]:
        return {**self.binding(), "parameters": dict(self.parameters), "preconditions": dict(self.preconditions)}

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> "ExecutionCommand":
        required = ("action_id", "decision_id", "action_type", "entity_id", "entity_type")
        if any(not isinstance(value.get(name), str) or not value[name] for name in required):
            raise AuthorizationError("Execution command is malformed")
        parameters = value.get("parameters")
        if not isinstance(parameters, dict):
            raise AuthorizationError("Execution command parameters are malformed")
        preconditions = value.get("preconditions", {})
        if not isinstance(preconditions, dict):
            raise AuthorizationError("Execution command preconditions are malformed")
        idempotency_key = value.get("idempotency_key")
        if idempotency_key is not None and not isinstance(idempotency_key, str):
            raise AuthorizationError("Execution command idempotency key is malformed")
        return cls(
            action_id=value["action_id"], decision_id=value["decision_id"], action_type=value["action_type"],
            entity_id=value["entity_id"], entity_type=value["entity_type"], parameters=dict(parameters), preconditions=dict(preconditions),
            idempotency_key=idempotency_key,
        )


@dataclass(frozen=True)
class SignedAuthorization:
    """Portable Ed25519 authorization envelope sent to the Executor."""

    claims: dict[str, Any]
    signature: str

    def wire(self) -> dict[str, Any]:
        return {"claims": dict(self.claims), "signature": self.signature}

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> "SignedAuthorization":
        claims = value.get("claims")
        signature = value.get("signature")
        if not isinstance(claims, dict) or not isinstance(signature, str):
            raise AuthorizationError("Authorization envelope is malformed")
        return cls(claims=dict(claims), signature=signature)


class ReplayStore(Protocol):
    def claim(self, authorization_id: str, expires_at: int) -> bool:
        ...


class RevocationStore(Protocol):
    """Decision-level authorization withdrawal checked immediately before use."""

    def is_revoked(self, decision_id: str, *, now: int) -> bool:
        ...


class CommandEffectExecutor(Protocol):
    def __call__(self, command: ExecutionCommand) -> Any:
        ...


class InMemoryReplayStore:
    """Reference replay store.

    claim() is protected by an in-process lock, so concurrent threads (e.g.
    a threaded HTTP Executor handling two submissions of the same
    authorization at once) cannot both observe "not yet claimed" before
    either writes. This closes the same-process race; it still provides no
    guarantee across separate processes/replicas — a production Executor
    that scales beyond one process needs durable shared state with real
    cross-process atomicity (see DeltaReplayStore's own limits).
    """

    def __init__(self) -> None:
        self._claims: dict[str, int] = {}
        self._lock = threading.Lock()

    def claim(self, authorization_id: str, expires_at: int) -> bool:
        with self._lock:
            now = int(time.time())
            self._claims = {key: expiry for key, expiry in self._claims.items() if expiry >= now}
            if authorization_id in self._claims:
                return False
            self._claims[authorization_id] = expires_at
            return True


class InMemoryRevocationStore:
    """Thread-safe reference revocation set for one Executor process.

    Production replicas require one shared, durable implementation. A revoked
    decision remains revoked until its original authorization expiry, after
    which expiry itself rejects the command.
    """

    def __init__(self) -> None:
        self._revocations: dict[str, int] = {}
        self._lock = threading.Lock()

    def revoke(self, decision_id: str, *, expires_at: int) -> None:
        if not isinstance(decision_id, str) or not decision_id.strip():
            raise ValueError("decision_id must be a non-empty string")
        if not isinstance(expires_at, int):
            raise TypeError("expires_at must be an integer epoch timestamp")
        with self._lock:
            self._revocations[decision_id] = expires_at

    def is_revoked(self, decision_id: str, *, now: int) -> bool:
        with self._lock:
            self._revocations = {
                key: expiry for key, expiry in self._revocations.items() if expiry >= now
            }
            return decision_id in self._revocations


class PolicyAuthorizationIssuer:
    """Held only by Policy after its deterministic evaluation authorizes action.

    authorize() trusts the ExecutionCommand it is given: it does not itself
    verify that the command was produced via ExecutionCommand.from_action().
    A Policy implementation is responsible for building commands through
    ExecutionCommand.from_action(action, decision) so that "bound to the
    Decision" is actually backed by the decision_to_action() construction
    guarantee, rather than a decision_id string supplied by hand. A Policy
    implementation that hand-constructs an ExecutionCommand bypasses that
    guarantee; this class has no way to detect that from the command alone.
    """

    def __init__(self, private_key: Ed25519PrivateKey, *, key_id: str, issuer: str) -> None:
        if not key_id or not issuer:
            raise ValueError("key_id and issuer must be non-empty")
        self._private_key = private_key
        self.key_id = key_id
        self.issuer = issuer

    @classmethod
    def generate(cls, *, key_id: str, issuer: str) -> "PolicyAuthorizationIssuer":
        return cls(Ed25519PrivateKey.generate(), key_id=key_id, issuer=issuer)

    def public_key_pem(self) -> bytes:
        return self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def private_key_pem(self) -> bytes:
        return self._private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

    @classmethod
    def from_private_key_pem(cls, pem: bytes, *, key_id: str, issuer: str) -> "PolicyAuthorizationIssuer":
        key = serialization.load_pem_private_key(pem, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("Policy private key is not Ed25519")
        return cls(key, key_id=key_id, issuer=issuer)

    def authorize(self, command: ExecutionCommand, *, audience: str, ttl: timedelta = timedelta(seconds=30)) -> SignedAuthorization:
        if not audience or ttl.total_seconds() <= 0:
            raise ValueError("audience and a positive ttl are required")
        now = datetime.now(timezone.utc)
        claims: dict[str, Any] = {
            "version": 1,
            "authorization_id": str(uuid.uuid4()),
            "issuer": self.issuer,
            "key_id": self.key_id,
            "audience": audience,
            "issued_at": int(now.timestamp()),
            "expires_at": int((now + ttl).timestamp()),
            **command.binding(),
        }
        signature = base64.urlsafe_b64encode(self._private_key.sign(_canonical_json(claims))).decode("ascii")
        return SignedAuthorization(claims=claims, signature=signature)


class ExecutorAuthorizationVerifier:
    """Executor-side fail-closed verifier with no access to Policy private keys."""

    def __init__(self, public_keys: Mapping[str, Ed25519PublicKey], *, audience: str, replay_store: ReplayStore, revocation_store: RevocationStore | None = None, clock_skew_seconds: int = 5) -> None:
        if not audience:
            raise ValueError("audience must be non-empty")
        self._public_keys = dict(public_keys)
        self.audience = audience
        self.replay_store = replay_store
        self.revocation_store = revocation_store
        if not isinstance(clock_skew_seconds, int) or not 0 <= clock_skew_seconds <= 60:
            raise ValueError("clock_skew_seconds must be an integer from 0 to 60")
        self.clock_skew_seconds = clock_skew_seconds

    @classmethod
    def from_pem(cls, public_keys: Mapping[str, bytes], *, audience: str, replay_store: ReplayStore, revocation_store: RevocationStore | None = None, clock_skew_seconds: int = 5) -> "ExecutorAuthorizationVerifier":
        parsed: dict[str, Ed25519PublicKey] = {}
        for key_id, pem in public_keys.items():
            key = serialization.load_pem_public_key(pem)
            if not isinstance(key, Ed25519PublicKey):
                raise ValueError(f"Key {key_id!r} is not Ed25519")
            parsed[key_id] = key
        return cls(parsed, audience=audience, replay_store=replay_store, revocation_store=revocation_store, clock_skew_seconds=clock_skew_seconds)

    def verify(
        self,
        command: ExecutionCommand,
        authorization: SignedAuthorization,
        *,
        now: int | None = None,
        owned_action_types: frozenset[str] | None = None,
    ) -> None:
        claims = authorization.claims
        key_id = claims.get("key_id")
        if not isinstance(key_id, str) or key_id not in self._public_keys:
            raise AuthorizationError("Authorization key is unknown")
        try:
            signature = base64.urlsafe_b64decode(authorization.signature.encode("ascii"))
            self._public_keys[key_id].verify(signature, _canonical_json(claims))
        except (InvalidSignature, ValueError) as exc:
            raise AuthorizationError("Authorization signature is invalid") from exc
        current_time = int(time.time()) if now is None else now
        if claims.get("version") != 1 or claims.get("audience") != self.audience:
            raise AuthorizationError("Authorization is not intended for this Executor")
        expires_at = claims.get("expires_at")
        issued_at = claims.get("issued_at")
        if not isinstance(expires_at, int) or not isinstance(issued_at, int) or issued_at > current_time + self.clock_skew_seconds or expires_at <= current_time - self.clock_skew_seconds:
            raise AuthorizationError("Authorization is expired or has an invalid time window")
        if any(claims.get(key) != value for key, value in command.binding().items()):
            raise AuthorizationError("Authorization does not bind this execution command")
        decision_id = claims.get("decision_id")
        if not isinstance(decision_id, str) or not decision_id:
            raise AuthorizationError("Authorization has no decision identity")
        if self.revocation_store is not None and self.revocation_store.is_revoked(decision_id, now=current_time):
            raise AuthorizationError("Authorization decision has been revoked")
        if owned_action_types is not None and command.action_type not in owned_action_types:
            # This check must precede replay claiming.  A command delivered to
            # the wrong executor must not consume a valid authorization and
            # thereby deny the executor that actually owns the capability.
            raise AuthorizationError(f"Executor does not own action type {command.action_type!r}")
        authorization_id = claims.get("authorization_id")
        if not isinstance(authorization_id, str) or not authorization_id:
            raise AuthorizationError("Authorization has no replay identity")
        if not self.replay_store.claim(authorization_id, expires_at):
            raise AuthorizationError("Authorization has already been used")


class AuthorizedCommandExecutor:
    """The Executor-side gate: verify first, then permit one side effect."""

    def __init__(self, verifier: ExecutorAuthorizationVerifier, execute_effect: CommandEffectExecutor, *, owned_action_types: set[str] | None = None) -> None:
        self.verifier = verifier
        self.execute_effect = execute_effect
        self.owned_action_types = frozenset(owned_action_types) if owned_action_types is not None else None

    def execute(self, command: ExecutionCommand, authorization: SignedAuthorization) -> Any:
        self.verifier.verify(command, authorization, owned_action_types=self.owned_action_types)
        return self.execute_effect(command)
