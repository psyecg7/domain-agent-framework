from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError

from agent_core import Agent, Event, State
from agent_postgres import ConcurrentStateUpdate, PostgresStateStore, StateStoreUnavailable


def test_postgres_state_store_distinguishes_duplicate_creation_from_outage(tmp_path, monkeypatch) -> None:
    store = PostgresStateStore(f"sqlite:///{tmp_path / 'state.db'}")
    store.save(State("ORD-1", "order"))

    with pytest.raises(ConcurrentStateUpdate, match="already exists"):
        store.save(State("ORD-1", "order"))

    def unavailable():
        raise OperationalError("SELECT", {}, RuntimeError("database down"))

    monkeypatch.setattr(store.engine, "connect", unavailable)
    with pytest.raises(StateStoreUnavailable, match="unavailable"):
        store.get("ORD-1", "order")


def test_postgres_state_store_persists_an_agents_first_observation(tmp_path) -> None:
    class NoDecisions:
        def evaluate(self, state):
            return []

    store = PostgresStateStore(f"sqlite:///{tmp_path / 'state.db'}")
    agent = Agent(store, NoDecisions())
    agent.process(Event("order.received", "ORD-1", "order", {"quantity": 2}))

    stored = store.get("ORD-1", "order")
    assert stored is not None
    assert stored.values == {"quantity": 2}
    assert stored.version == 1
