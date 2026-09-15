"""Boundary for producing non-authoritative Recommendations.

Reasoners receive read-only context and may use an LLM, a heuristic, or another
implementation to advise. They never return Decisions or execute Actions.
"""

from __future__ import annotations

from typing import Protocol

from agent_core.primitives.recommendation import Recommendation
from agent_core.primitives.reasoning_context import ReasoningContext


class Reasoner(Protocol):
    def reason(self, context: ReasoningContext) -> list[Recommendation]:
        ...
