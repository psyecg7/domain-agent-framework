from __future__ import annotations

from dataclasses import dataclass

from .memory import Memory


@dataclass(frozen=True)
class MemoryResult:
    memory: Memory
    score: float


__all__ = ["MemoryResult"]
