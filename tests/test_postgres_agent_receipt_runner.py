from __future__ import annotations

import pytest

from agent_core import Agent, Event
from agent_postgres import PostgresAgentReceiptRunner, PostgresEventReceiptStore, PostgresStateStore


class NoDecisions:
    def evaluate(self, state):
        return []


def test_runner_persists_one_agent_observation_and_skips_restart_redelivery(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'agent-receipt.db'}"
    state_store = PostgresStateStore(database_url)
    receipts = PostgresEventReceiptStore(database_url)
    runner = PostgresAgentReceiptRunner(
        Agent(state_store, NoDecisions()), state_store=state_store, receipt_store=receipts,
    )
    event = Event("order.received", "ORD-1", "order", {"quantity": 2})

    first = runner.process_once(event)
    assert first.receipt.applied is True
    assert first.result is not None
    assert first.result.state.version == 1

    restarted_state_store = PostgresStateStore(database_url)
    restarted_receipts = PostgresEventReceiptStore(database_url)
    restarted = PostgresAgentReceiptRunner(
        Agent(restarted_state_store, NoDecisions()),
        state_store=restarted_state_store,
        receipt_store=restarted_receipts,
    )
    duplicate = restarted.process_once(event)

    assert duplicate.receipt.applied is False
    assert duplicate.result is None
    stored = restarted_state_store.get("ORD-1", "order")
    assert stored is not None
    assert stored.values == {"quantity": 2}
    assert stored.version == 1


def test_runner_rolls_back_state_and_receipt_when_policy_fails(tmp_path) -> None:
    class FailingPolicy:
        def evaluate(self, state):
            raise RuntimeError("simulated policy failure")

    database_url = f"sqlite:///{tmp_path / 'agent-receipt.db'}"
    state_store = PostgresStateStore(database_url)
    receipts = PostgresEventReceiptStore(database_url)
    runner = PostgresAgentReceiptRunner(
        Agent(state_store, FailingPolicy()), state_store=state_store, receipt_store=receipts,
    )
    event = Event("order.received", "ORD-1", "order", {"quantity": 2})

    with pytest.raises(RuntimeError, match="simulated policy failure"):
        runner.process_once(event)

    assert state_store.get("ORD-1", "order") is None
    assert receipts.has_receipt(event.event_id) is False


def test_runner_rejects_an_agent_that_can_execute_actions(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'agent-receipt.db'}"
    state_store = PostgresStateStore(database_url)
    receipts = PostgresEventReceiptStore(database_url)
    agent = Agent(state_store, NoDecisions(), action_executor=object())

    with pytest.raises(ValueError, match="without an action executor"):
        PostgresAgentReceiptRunner(agent, state_store=state_store, receipt_store=receipts)
