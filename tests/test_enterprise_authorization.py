from __future__ import annotations

from datetime import timedelta

import pytest

cryptography = pytest.importorskip("cryptography")

from agent_core import Decision, decision_to_action
from agent_enterprise import (
    AuthorizationError,
    AuthorizedCommandExecutor,
    ExecutionCommand,
    ExecutorAuthorizationVerifier,
    DeltaReplayStore,
    DeltaAuthorizationAuditStore,
    InMemoryReplayStore,
    PolicyAuthorizationIssuer,
    SignedAuthorization,
)


def command() -> ExecutionCommand:
    decision = Decision("ORD-1", "order", "CREATE", "HIGH", "all facts approved", decision_id="DEC-1")
    action = decision_to_action(
        decision,
        "CREATE_ORDER",
        parameters={"product_id": "SKU-1", "quantity": 2},
        idempotency_key="order-create-1",
    )
    return ExecutionCommand.from_action(action, decision)


def boundary():
    issuer = PolicyAuthorizationIssuer.generate(key_id="policy-2026-01", issuer="order-policy")
    verifier = ExecutorAuthorizationVerifier.from_pem(
        {"policy-2026-01": issuer.public_key_pem()},
        audience="order-executor",
        replay_store=InMemoryReplayStore(),
    )
    return issuer, verifier


def test_executor_accepts_a_policy_signed_bound_command_once() -> None:
    issuer, verifier = boundary()
    execution = command()
    authorization = issuer.authorize(execution, audience="order-executor")

    verifier.verify(execution, authorization)

    with pytest.raises(AuthorizationError, match="already been used"):
        verifier.verify(execution, authorization)


def test_authorized_executor_never_calls_the_effect_before_verification() -> None:
    issuer, verifier = boundary()
    execution = command()
    executed: list[ExecutionCommand] = []
    executor = AuthorizedCommandExecutor(verifier, executed.append)

    with pytest.raises(AuthorizationError):
        executor.execute(execution, SignedAuthorization({}, "invalid"))
    assert executed == []

    executor.execute(execution, issuer.authorize(execution, audience="order-executor"))
    assert executed == [execution]


def test_delta_replay_store_survives_executor_restart(tmp_path) -> None:
    table_path = tmp_path / "executor-replay"
    first = DeltaReplayStore(table_path)
    assert first.claim("AUTH-1", 2_000_000_000) is True

    restarted = DeltaReplayStore(table_path)
    assert restarted.claim("AUTH-1", 2_000_000_000) is False


def test_delta_audit_appends_records_without_rewriting_prior_evidence(tmp_path) -> None:
    audit = DeltaAuthorizationAuditStore(tmp_path / "audit")
    audit.record("POLICY_ISSUED", principal_id="policy")
    audit.record("EXECUTOR_EXECUTED", principal_id="executor")

    import deltalake

    outcomes = deltalake.DeltaTable(audit.path).to_pandas()["outcome"].tolist()
    assert outcomes.count("POLICY_ISSUED") == 1
    assert outcomes.count("EXECUTOR_EXECUTED") == 1


def test_policy_private_key_round_trips_without_giving_executor_a_private_key_api() -> None:
    issuer, _ = boundary()
    restored = PolicyAuthorizationIssuer.from_private_key_pem(
        issuer.private_key_pem(), key_id="policy-2026-01", issuer="order-policy"
    )
    execution = command()
    assert restored.authorize(execution, audience="order-executor").claims["key_id"] == "policy-2026-01"

    import agent_enterprise.executor_service as executor_service
    assert "private-key" not in executor_service.main.__code__.co_consts


@pytest.mark.parametrize(
    "replacement",
    [
        lambda cmd: ExecutionCommand(**{**cmd.__dict__, "action_type": "DELETE_ORDER"}),
        lambda cmd: ExecutionCommand(**{**cmd.__dict__, "entity_id": "ORD-OTHER"}),
        lambda cmd: ExecutionCommand(**{**cmd.__dict__, "parameters": {"product_id": "SKU-1", "quantity": 999}}),
    ],
)
def test_executor_rejects_authorization_lifted_to_a_different_command(replacement) -> None:
    issuer, verifier = boundary()
    execution = command()
    authorization = issuer.authorize(execution, audience="order-executor")

    with pytest.raises(AuthorizationError, match="does not bind"):
        verifier.verify(replacement(execution), authorization)


def test_executor_rejects_tampered_signature_wrong_audience_and_expiry() -> None:
    issuer, verifier = boundary()
    execution = command()
    authorization = issuer.authorize(execution, audience="order-executor")

    with pytest.raises(AuthorizationError, match="signature"):
        verifier.verify(execution, SignedAuthorization(authorization.claims, authorization.signature[:-2] + "aa"))

    wrong_audience = issuer.authorize(execution, audience="other-executor")
    with pytest.raises(AuthorizationError, match="not intended"):
        verifier.verify(execution, wrong_audience)

    expired = issuer.authorize(execution, audience="order-executor", ttl=timedelta(seconds=1))
    with pytest.raises(AuthorizationError, match="expired"):
        verifier.verify(execution, expired, now=expired.claims["expires_at"])
