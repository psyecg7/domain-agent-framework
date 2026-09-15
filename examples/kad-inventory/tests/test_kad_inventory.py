from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from kad_inventory import InMemoryInventoryState, create_app


class CapturedEvents:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def publish(self, event: dict, *, topic: str | None = None, key: str | None = None) -> None:
        self.events.append({"topic": topic, "key": key, "event": event})


@pytest.mark.asyncio
async def test_stale_ai_intent_cannot_mutate_inventory_and_emits_a_conflict_event() -> None:
    state = InMemoryInventoryState()
    state.seed("SKU-992", "WH-B", available_quantity=40, version_token=1)
    events = CapturedEvents()
    app = create_app(state, events)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        evaluation = await client.post("/evaluate-alternative", json={
            "sku": "SKU-992", "target_warehouse": "WH-B", "quantity": 40,
        })
        assert evaluation.status_code == 200
        fact = evaluation.json()
        assert fact == {
            "feasible": True,
            "target_warehouse": "WH-B",
            "evaluated_against_state_version": 1,
            "epistemic_status": "AUTHORITATIVE_EVALUATION",
            "valid_until_epoch": fact["valid_until_epoch"],
        }

        # A live customer transaction wins the race before the AI proposal runs.
        state.apply_operational_event({
            "event_type": "CustomerInventoryConsumed",
            "sku": "SKU-992", "warehouse": "WH-B",
            "available_quantity": 0, "version_token": 2,
        })
        attempted = await client.post("/execute-intent", json={
            "order_id": "ORD-992", "sku": "SKU-992", "target_warehouse": "WH-B",
            "quantity": 40, "expected_state_version": fact["evaluated_against_state_version"],
        })

    assert attempted.status_code == 409
    assert attempted.json()["detail"]["reason"] == "INVARIANT_VIOLATION_VERSION_MISMATCH"
    assert events.events[0]["event"] == {
        "event_type": "ReallocationFailed",
        "reason": "INVARIANT_VIOLATION_VERSION_MISMATCH",
        "order_id": "ORD-992", "sku": "SKU-992", "target_warehouse": "WH-B",
        "expected_state_version": 1, "actual_state_version": 2,
    }
    assert state.snapshot("SKU-992", "WH-B").available_quantity == 0
    assert state.snapshot("SKU-992", "WH-B").version_token == 2


@pytest.mark.asyncio
async def test_evaluation_is_read_only_and_fresh_intent_is_applied_once() -> None:
    state = InMemoryInventoryState()
    state.seed("SKU-1", "WH-A", available_quantity=5, version_token=4)
    events = CapturedEvents()
    app = create_app(state, events)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        evaluation = await client.post("/evaluate-alternative", json={
            "sku": "SKU-1", "target_warehouse": "WH-A", "quantity": 2,
        })
        assert state.snapshot("SKU-1", "WH-A").available_quantity == 5
        result = await client.post("/execute-intent", json={
            "order_id": "ORD-1", "sku": "SKU-1", "target_warehouse": "WH-A",
            "quantity": 2, "expected_state_version": evaluation.json()["evaluated_against_state_version"],
        })

    assert result.status_code == 200
    assert result.json()["remaining_quantity"] == 3
    assert events.events[0]["event"]["event_type"] == "InventoryReallocated"
