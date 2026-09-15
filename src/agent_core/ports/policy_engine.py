"""Boundary for deterministic domain policy evaluation.

Policy reads current State and returns Decisions. When it accepts
Recommendations, it remains the authority that filters model or heuristic
advice before an Action can be formed.
"""

from __future__ import annotations

from typing import Protocol

from agent_core.primitives.decision import Decision
from agent_core.primitives.recommendation import Recommendation
from agent_core.primitives.state import State


class PolicyEngine(Protocol):
    def evaluate(self, state: State) -> list[Decision]:
        ...

    def evaluate_recommendations(
        self,
        state: State,
        recommendations: list[Recommendation],
    ) -> list[Decision]:
        ...
