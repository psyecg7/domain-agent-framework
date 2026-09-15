"""Verify the published enterprise v1 vector against the Python reference."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from enterprise import (
    AuthorizationError,
    ExecutionCommand,
    ExecutorAuthorizationVerifier,
    InMemoryReplayStore,
    SignedAuthorization,
)
from enterprise.authorization import _canonical_json


VECTOR = Path(__file__).resolve().parents[1] / "protocol" / "test-vectors" / "enterprise" / "authorization-v1.json"


def _vector() -> dict:
    return json.loads(VECTOR.read_text())


def _verifier(vector: dict) -> ExecutorAuthorizationVerifier:
    return ExecutorAuthorizationVerifier.from_pem(
        {"vector-key-1": vector["public_key_pem"].encode()},
        audience="example-executor",
        replay_store=InMemoryReplayStore(),
    )


def test_enterprise_v1_vector_verifies_portably() -> None:
    vector = _vector()
    command = ExecutionCommand.from_wire(vector["command"])
    authorization = SignedAuthorization.from_wire(vector["authorization"])

    assert _canonical_json(authorization.claims).decode() == vector["canonical_claims_utf8"]
    _verifier(vector).verify(command, authorization, now=vector["verification_now"])


def test_enterprise_v1_vector_rejects_tampering_and_expiry() -> None:
    vector = _vector()
    authorization = SignedAuthorization.from_wire(vector["authorization"])

    with pytest.raises(AuthorizationError, match="does not bind"):
        _verifier(vector).verify(
            ExecutionCommand.from_wire(vector["tampered_command"]),
            authorization,
            now=vector["verification_now"],
        )

    with pytest.raises(AuthorizationError, match="expired"):
        _verifier(vector).verify(
            ExecutionCommand.from_wire(vector["command"]),
            authorization,
            now=vector["expected"]["expired_at"],
        )
