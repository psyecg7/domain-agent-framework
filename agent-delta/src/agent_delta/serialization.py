from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from agent_core.primitives.state import State


class StateSerializer:
    @staticmethod
    def to_record(state: State) -> dict[str, Any]:
        return {
            "entity_id": state.entity_id,
            "entity_type": state.entity_type,
            "values": json.dumps(state.values, default=_json_default, sort_keys=True),
            "version": state.version,
            "updated_at": state.updated_at.isoformat(),
        }

    @staticmethod
    def from_record(record: dict[str, Any]) -> State:
        if record is None:
            raise ValueError("Record is None")

        values_json = record.get("values")
        if values_json is None:
            raise ValueError("State values are missing")

        values = json.loads(values_json)
        updated_at = record.get("updated_at")
        if updated_at is None:
            updated_at_value = datetime.now(timezone.utc)
        else:
            updated_at_value = datetime.fromisoformat(updated_at)
            if updated_at_value.tzinfo is None:
                updated_at_value = updated_at_value.replace(tzinfo=timezone.utc)
            else:
                updated_at_value = updated_at_value.astimezone(timezone.utc)

        return State(
            entity_id=record["entity_id"],
            entity_type=record["entity_type"],
            values=values,
            version=int(record.get("version", 0)),
            updated_at=updated_at_value,
        )


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
