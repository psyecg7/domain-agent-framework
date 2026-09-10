from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .memory_result import MemoryResult
from .observation import Observation
from .state import State


@dataclass(frozen=True)
class ReasoningContext:
    """Read-only structured context supplied to a reasoner."""

    state: State
    observations: Sequence[Observation] = field(default_factory=tuple)
    memories: Sequence[MemoryResult] = field(default_factory=tuple)
    policy_context: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "observations", tuple(self.observations))
        object.__setattr__(self, "memories", tuple(self.memories))


__all__ = ["ReasoningContext"]
