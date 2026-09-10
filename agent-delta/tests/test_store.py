from __future__ import annotations

from pathlib import Path

from agent_core import Agent, Event, State
from agent_delta import DeltaStateStore


class InventoryPolicyEngine:
    def evaluate(self, state: State) -> list:
        stock = state.values.get("available_stock")
        if isinstance(stock, (int, float)) and stock < 5:
            return [
                type(
                    "Decision",
                    (),
                    {
                        "entity_id": state.entity_id,
                        "entity_type": state.entity_type,
                        "decision_type": "INVESTIGATE",
                        "severity": "MEDIUM",
                        "reason": "Inventory below reorder threshold",
                        "confidence": None,
                    },
                )()
            ]
        return []


class NoopReasoner:
    def reason(self, state: State, decisions: list) -> list:
        return decisions


class GenericActionFactory:
    def __call__(self, decision):
        return type(
            "Action",
            (),
            {
                "action_type": "CREATE_REPLENISHMENT",
                "entity_id": decision.entity_id,
                "entity_type": decision.entity_type,
                "parameters": {"reason": decision.reason},
            },
        )()


def test_delta_store_save_and_reload() -> None:
    path = Path("/tmp/agent-delta-test-save")
    if path.exists():
        import shutil
        shutil.rmtree(path)

    store = DeltaStateStore(path)
    state = State(entity_id="sku-1", entity_type="inventory_item", values={"available_stock": 3}, version=1)
    store.save(state)

    reloaded = store.get("sku-1", "inventory_item")
    assert reloaded is not None
    assert reloaded.entity_id == "sku-1"
    assert reloaded.entity_type == "inventory_item"
    assert reloaded.values["available_stock"] == 3
    assert reloaded.version == 1


def test_delta_store_updates_existing_state() -> None:
    path = Path("/tmp/agent-delta-test-update")
    if path.exists():
        import shutil
        shutil.rmtree(path)

    store = DeltaStateStore(path)
    state = State(entity_id="sku-2", entity_type="inventory_item", values={"available_stock": 10}, version=1)
    store.save(state)

    state.values["available_stock"] = 4
    state.version = 2
    state.updated_at = state.updated_at
    store.save(state)

    loaded = store.get("sku-2", "inventory_item")
    assert loaded is not None
    assert loaded.values["available_stock"] == 4
    assert loaded.version == 2


def test_delta_store_isolates_entity_keys() -> None:
    path = Path("/tmp/agent-delta-test-keys")
    if path.exists():
        import shutil
        shutil.rmtree(path)

    store = DeltaStateStore(path)
    store.save(State(entity_id="sku-3", entity_type="inventory_item", values={"available_stock": 1}, version=1))
    store.save(State(entity_id="sku-4", entity_type="inventory_item", values={"available_stock": 2}, version=1))
    store.save(State(entity_id="order-1", entity_type="order", values={"risk_score": 0.9}, version=1))

    assert store.get("sku-3", "inventory_item").values["available_stock"] == 1
    assert store.get("sku-4", "inventory_item").values["available_stock"] == 2
    assert store.get("order-1", "order").values["risk_score"] == 0.9


def test_delta_store_missing_state_returns_none() -> None:
    path = Path("/tmp/agent-delta-test-missing")
    if path.exists():
        import shutil
        shutil.rmtree(path)

    store = DeltaStateStore(path)
    assert store.get("missing", "inventory_item") is None


def test_delta_store_persists_across_restart() -> None:
    path = Path("/tmp/agent-delta-test-restart")
    if path.exists():
        import shutil
        shutil.rmtree(path)

    first = DeltaStateStore(path)
    first.save(State(entity_id="sku-5", entity_type="inventory_item", values={"available_stock": 7}, version=1))
    second = DeltaStateStore(path)
    reloaded = second.get("sku-5", "inventory_item")
    assert reloaded is not None
    assert reloaded.values["available_stock"] == 7
    assert reloaded.version == 1


def test_delta_store_works_with_agent_runtime() -> None:
    path = Path("/tmp/agent-delta-test-agent")
    if path.exists():
        import shutil
        shutil.rmtree(path)

    store = DeltaStateStore(path)
    agent = Agent(
        store,
        InventoryPolicyEngine(),
        reasoner=NoopReasoner(),
        action_executor=None,
        action_factory=lambda decision: type(
            "Action",
            (),
            {
                "action_type": "CREATE_REPLENISHMENT",
                "entity_id": decision.entity_id,
                "entity_type": decision.entity_type,
                "parameters": {"reason": decision.reason},
            },
        )(),
    )

    event = Event(
        event_type="inventory.stock_changed",
        entity_id="sku-10",
        entity_type="inventory_item",
        payload={"available_stock": 2},
        source="inventory_system",
    )

    result = agent.process(event)
    assert result.state.values["available_stock"] == 2
    assert result.decisions[0].decision_type == "INVESTIGATE"

    restarted_store = DeltaStateStore(path)
    persisted = restarted_store.get("sku-10", "inventory_item")
    assert persisted is not None
    assert persisted.values["available_stock"] == 2
    assert persisted.version == 1
