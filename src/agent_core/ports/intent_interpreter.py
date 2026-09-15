"""Boundary for translating a human request into a declarative Intent.

An implementation may be deterministic or model-backed, but it produces a
request description only. Capability lookup and the target domain's policy
remain separate authorization steps.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from agent_core.primitives.intent import Intent


class IntentInterpreter(Protocol):
    def interpret(self, request: str, context: Mapping[str, Any]) -> Intent:
        ...


__all__ = ["IntentInterpreter"]
