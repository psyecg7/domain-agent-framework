"""One Memory match returned by semantic or other retrieval.

The score is supplied by the MemoryStore implementation. The runtime passes the
result to a reasoner as context and does not treat its score as policy evidence.
"""

from __future__ import annotations

from dataclasses import dataclass

from .memory import Memory


@dataclass(frozen=True)
class MemoryResult:
    memory: Memory
    score: float


__all__ = ["MemoryResult"]
