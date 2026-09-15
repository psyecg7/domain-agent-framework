from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agent_core import Event


class RedpandaEventMapper:
    def __init__(
        self,
        *,
        event_type_field: str = "event_type",
        entity_id_field: str = "entity_id",
        entity_type_field: str = "entity_type",
        event_id_field: str = "event_id",
        timestamp_field: str = "occurred_at",
        source_field: str = "source",
    ) -> None:
        self.event_type_field = event_type_field
        self.entity_id_field = entity_id_field
        self.entity_type_field = entity_type_field
        self.event_id_field = event_id_field
        self.timestamp_field = timestamp_field
        self.source_field = source_field

    def from_record(self, record: dict[str, Any], *, source: str | None = None) -> Event:
        if not isinstance(record, dict):
            raise TypeError("Redpanda record must be a dictionary")

        payload = dict(record)

        event_type = payload.get(self.event_type_field)
        entity_id = payload.get(self.entity_id_field)
        entity_type = payload.get(self.entity_type_field)
        event_id = payload.get(self.event_id_field)
        occurred_at_value = payload.get(self.timestamp_field)
        source_value = payload.get(self.source_field, source)
        metadata = payload.get("metadata", {})
        idempotency_key = payload.get("idempotency_key")

        if event_type is None:
            raise ValueError("Redpanda record missing event_type")
        if entity_id is None:
            raise ValueError("Redpanda record missing entity_id")
        if entity_type is None:
            raise ValueError("Redpanda record missing entity_type")
        if event_id is None:
            raise ValueError("Redpanda record missing event_id")
        if occurred_at_value is None:
            raise ValueError("Redpanda record missing occurred_at")

        timestamp = self._parse_timestamp(occurred_at_value)
        payload.pop(self.event_type_field, None)
        payload.pop(self.entity_id_field, None)
        payload.pop(self.entity_type_field, None)
        payload.pop(self.event_id_field, None)
        payload.pop(self.timestamp_field, None)
        payload.pop(self.source_field, None)
        payload.pop("metadata", None)
        payload.pop("idempotency_key", None)
        payload.pop("_transport", None)

        return Event(
            event_id=str(event_id),
            event_type=str(event_type),
            entity_id=str(entity_id),
            entity_type=str(entity_type),
            payload=payload,
            occurred_at=timestamp,
            source=str(source_value) if source_value is not None else None,
            metadata=dict(metadata) if isinstance(metadata, dict) else {},
            idempotency_key=str(idempotency_key) if idempotency_key is not None else None,
        )

    def _parse_timestamp(self, value: Any) -> datetime:
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, str):
            dt = datetime.fromisoformat(value)
        elif isinstance(value, (int, float)):
            dt = datetime.fromtimestamp(value, tz=timezone.utc)
        else:
            raise TypeError(f"Unsupported timestamp type: {type(value)!r}")

        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def to_record(self, event: Event) -> dict[str, Any]:
        payload = dict(event.payload)
        payload[self.event_type_field] = event.event_type
        payload[self.entity_id_field] = event.entity_id
        payload[self.entity_type_field] = event.entity_type
        payload[self.event_id_field] = event.event_id
        payload[self.timestamp_field] = event.occurred_at.isoformat()
        if event.source is not None:
            payload[self.source_field] = event.source
        if event.metadata:
            payload["metadata"] = dict(event.metadata)
        if event.idempotency_key is not None:
            payload["idempotency_key"] = event.idempotency_key
        return payload
