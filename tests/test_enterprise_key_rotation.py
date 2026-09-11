from __future__ import annotations

import pytest

from agent_core import Decision, decision_to_action
from agent_enterprise import AuthorizationError, ExecutionCommand, ExecutorAuthorizationVerifier, InMemoryReplayStore, PolicyAuthorizationIssuer


def command() -> ExecutionCommand:
    decision = Decision("ORD-ROTATE", "order", "CREATE", "HIGH", "approved")
    return ExecutionCommand.from_action(decision_to_action(decision, "CREATE_ORDER"), decision)


def test_executor_accepts_old_and_new_policy_keys_during_overlap_then_retires_old_key() -> None:
    old = PolicyAuthorizationIssuer.generate(key_id="policy-v1", issuer="policy")
    new = PolicyAuthorizationIssuer.generate(key_id="policy-v2", issuer="policy")
    execution = command()
    old_authorization = old.authorize(execution, audience="executor")
    new_authorization = new.authorize(execution, audience="executor")

    overlap = ExecutorAuthorizationVerifier.from_pem(
        {"policy-v1": old.public_key_pem(), "policy-v2": new.public_key_pem()},
        audience="executor", replay_store=InMemoryReplayStore(),
    )
    overlap.verify(execution, old_authorization)
    overlap.verify(execution, new_authorization)

    retired = ExecutorAuthorizationVerifier.from_pem(
        {"policy-v2": new.public_key_pem()}, audience="executor", replay_store=InMemoryReplayStore(),
    )
    old_after_retirement = command()
    with pytest.raises(AuthorizationError, match="key is unknown"):
        retired.verify(old_after_retirement, old.authorize(old_after_retirement, audience="executor"))
    new_after_retirement = command()
    retired.verify(new_after_retirement, new.authorize(new_after_retirement, audience="executor"))
