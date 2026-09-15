from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from agent_core import Memory


class MemorySerializer:
    @staticmethod
    def to_record(memory: Memory, vector: list[float]) -> dict[str, Any]:
        return {
            "memory_id": memory.memory_id,
            "entity_id": memory.entity_id,
            "entity_type": memory.entity_type,
            "content": memory.content,
            "metadata": json.dumps(memory.metadata, sort_keys=True, default=_json_default),
            "created_at": memory.created_at.isoformat(),
            "source": memory.source,
            "vector": vector,
        }

    @staticmethod
    def from_record(record: dict[str, Any]) -> Memory:
        metadata_value = record.get("metadata", "{}")
        metadata = json.loads(metadata_value) if isinstance(metadata_value, str) else dict(metadata_value)
        created_at = record["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        return Memory(
            memory_id=str(record["memory_id"]),
            entity_id=str(record["entity_id"]),
            entity_type=str(record["entity_type"]),
            content=str(record["content"]),
            metadata=metadata,
            created_at=created_at,
            source=record.get("source"),
        )


def _json_default(value: Any) -> Any:
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


__all__ = ["MemorySerializer"]
