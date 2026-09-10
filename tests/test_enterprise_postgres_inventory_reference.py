from agent_core import Decision, decision_to_action
from agent_enterprise import (
    AuthorizedCommandExecutor,
    ExecutionCommand,
    ExecutorAuthorizationVerifier,
    InMemoryReplayStore,
    PolicyAuthorizationIssuer,
    PostgresInventoryReservationHandler,
)
from agent_postgres import PostgresInventoryReservationAuthority


def authorization(issuer, handler, decision_id: str):
    decision = Decision("SKU-1", "inventory", "RESERVE", "HIGH", "stock checked", decision_id=decision_id)
    action = decision_to_action(decision, "RESERVE", parameters={"quantity": 1})
    command = ExecutionCommand.from_action(action, decision, preconditions=handler.preconditions_for("SKU-1"))
    return command, issuer.authorize(command, audience="inventory-executor")


def test_postgres_inventory_commits_stock_and_reconciliation_evidence_together(tmp_path) -> None:
    authority = PostgresInventoryReservationAuthority(f"sqlite:///{tmp_path / 'inventory.db'}")
    authority.seed("SKU-1", available=1, version=5)
    handler = PostgresInventoryReservationHandler(authority)
    issuer = PolicyAuthorizationIssuer.generate(key_id="policy", issuer="policy")
    executor = AuthorizedCommandExecutor(
        ExecutorAuthorizationVerifier.from_pem(
            {"policy": issuer.public_key_pem()}, audience="inventory-executor", replay_store=InMemoryReplayStore()
        ),
        handler,
    )

    first, first_token = authorization(issuer, handler, "DEC-1")
    second, second_token = authorization(issuer, handler, "DEC-2")

    assert executor.execute(first, first_token) == "SUCCEEDED"
    assert executor.execute(second, second_token) == "CONFLICT"

    restarted = PostgresInventoryReservationAuthority(f"sqlite:///{tmp_path / 'inventory.db'}")
    assert restarted.snapshot("SKU-1").available == 0
    assert restarted.snapshot("SKU-1").version == 6
    assert restarted.reconcile(first.action_id) == "EXISTS"
    assert restarted.reconcile(second.action_id) == "CONFLICT"


def test_postgres_inventory_records_an_impossible_signed_quantity_as_conflict(tmp_path) -> None:
    authority = PostgresInventoryReservationAuthority(f"sqlite:///{tmp_path / 'inventory.db'}")
    authority.seed("SKU-1", available=1, version=5)

    assert authority.reserve(
        "OP-OVERSIZED", "SKU-1", 2, preconditions={"inventory_version": 5, "available": 1}
    ) == "CONFLICT"

    assert authority.snapshot("SKU-1").available == 1
    assert authority.reconcile("OP-OVERSIZED") == "CONFLICT"
