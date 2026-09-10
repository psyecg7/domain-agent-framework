from __future__ import annotations

from datetime import datetime, timezone

from agent_redpanda.mapper import RedpandaEventMapper


def test_redpanda_record_maps_to_generic_event() -> None:
    mapper = RedpandaEventMapper()
    record = {
        "event_id": "evt-123",
        "event_type": "inventory.stock_changed",
        "entity_id": "sku-42",
        "entity_type": "inventory_item",
        "source": "inventory-system",
        "occurred_at": "2024-02-01T10:15:00+00:00",
        "available_stock": 3,
    }

    event = mapper.from_record(record)

    assert event.event_id == "evt-123"
    assert event.event_type == "inventory.stock_changed"
    assert event.entity_id == "sku-42"
    assert event.entity_type == "inventory_item"
    assert event.payload == {"available_stock": 3}
    assert event.source == "inventory-system"
    assert event.occurred_at.tzinfo is not None
    assert event.occurred_at.utcoffset() == timezone.utc.utcoffset(datetime.now(timezone.utc))


def test_mapper_rejects_missing_required_field() -> None:
    mapper = RedpandaEventMapper()

    try:
        mapper.from_record({
            "event_type": "order.created",
            "entity_id": "order-1",
            "entity_type": "order",
            "payload": {"risk_score": 0.9},
        })
        raise AssertionError("Expected ValueError for missing event_id")
    except ValueError as exc:
        assert "event_id" in str(exc)


def test_mapper_round_trips_lineage_metadata() -> None:
    mapper = RedpandaEventMapper()
    event = mapper.from_record(
        {
            "event_id": "evt-result",
            "event_type": "inventory.availability.result",
            "entity_id": "sku-42",
            "entity_type": "inventory_item",
            "occurred_at": "2024-02-01T10:15:00+00:00",
            "metadata": {"correlation_id": "C1", "causation_id": "A1"},
            "available": True,
        }
    )

    record = mapper.to_record(event)

    assert record["metadata"] == {"correlation_id": "C1", "causation_id": "A1"}


def test_mapper_round_trips_idempotency_key() -> None:
    mapper = RedpandaEventMapper()
    event = mapper.from_record({
        "event_id": "evt-1",
        "event_type": "inventory.stock_changed",
        "entity_id": "sku-42",
        "entity_type": "inventory_item",
        "occurred_at": "2024-02-01T10:15:00+00:00",
        "idempotency_key": "stock-change-1",
    })

    assert event.idempotency_key == "stock-change-1"
    assert mapper.to_record(event)["idempotency_key"] == "stock-change-1"
