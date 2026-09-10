from __future__ import annotations

from typing import Protocol

from agent_core.primitives.recommendation import Recommendation
from agent_core.primitives.reasoning_context import ReasoningContext


class Reasoner(Protocol):
    def reason(self, context: ReasoningContext) -> list[Recommendation]:
        ...
