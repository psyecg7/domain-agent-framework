from __future__ import annotations

from typing import Any, Mapping, Protocol

from agent_core.primitives.intent import Intent


class IntentInterpreter(Protocol):
    def interpret(self, request: str, context: Mapping[str, Any]) -> Intent:
        ...


__all__ = ["IntentInterpreter"]
