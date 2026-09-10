from agent_core import Decision, decision_to_action
from agent_conformance import assert_stale_preconditions_conflict
from agent_enterprise import (
    AuthorizedCommandExecutor,
    DeltaInventoryReservationHandler,
    ExecutionCommand,
    ExecutorAuthorizationVerifier,
    InMemoryReplayStore,
    PolicyAuthorizationIssuer,
)


def approved_reservation(issuer, handler, decision_id: str):
    decision = Decision("SKU-1", "inventory", "RESERVE", "HIGH", "stock available", decision_id=decision_id)
    action = decision_to_action(decision, "RESERVE", parameters={"quantity": 1})
    command = ExecutionCommand.from_action(action, decision, preconditions=handler.preconditions_for("SKU-1"))
    return command, issuer.authorize(command, audience="inventory-executor")


def test_delta_inventory_conditionally_applies_only_one_of_two_stale_authorizations(tmp_path) -> None:
    handler = DeltaInventoryReservationHandler(tmp_path / "inventory")
    handler.seed("SKU-1", available=1, version=5)
    issuer = PolicyAuthorizationIssuer.generate(key_id="policy", issuer="policy")
    verifier = ExecutorAuthorizationVerifier.from_pem(
        {"policy": issuer.public_key_pem()}, audience="inventory-executor", replay_store=InMemoryReplayStore()
    )
    executor = AuthorizedCommandExecutor(verifier, handler)

    first, first_authorization = approved_reservation(issuer, handler, "DEC-1")
    second, second_authorization = approved_reservation(issuer, handler, "DEC-2")

    assert executor.execute(first, first_authorization) == "SUCCEEDED"
    assert_stale_preconditions_conflict(
        execute=lambda: executor.execute(second, second_authorization),
        outcome_of=lambda outcome: outcome,
        effect_count=lambda: handler.snapshot("SKU-1").version,
    )

    restarted = DeltaInventoryReservationHandler(tmp_path / "inventory")
    assert restarted.snapshot("SKU-1").available == 0
    assert restarted.snapshot("SKU-1").version == 6
