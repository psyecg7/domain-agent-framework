from __future__ import annotations

from typing import Protocol

from agent_core.primitives.memory import Memory
from agent_core.primitives.memory_result import MemoryResult


class MemoryStore(Protocol):
    def store(self, memory: Memory) -> None:
        ...

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        entity_id: str | None = None,
        entity_type: str | None = None,
    ) -> list[MemoryResult]:
        ...


__all__ = ["MemoryStore"]
