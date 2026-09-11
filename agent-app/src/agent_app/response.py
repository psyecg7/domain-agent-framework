"""Private response-rendering composition for :mod:`agent_app`."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Protocol

from agent_core import Event


class ResponseInterpreter(Protocol):
    def interpret(self, event: Event, context: Mapping[str, Any]) -> str:
        ...


ResponseFormatter = Callable[[Event, Mapping[str, Any]], str]


class ResponseInterpretationError(RuntimeError):
    """Raised when a result event cannot be rendered as a response."""


class DeterministicResponseInterpreter:
    """Render result events through explicitly registered application formatters."""

    def __init__(self, formatters: Mapping[str, ResponseFormatter]) -> None:
        self._formatters = dict(formatters)

    def interpret(self, event: Event, context: Mapping[str, Any]) -> str:
        if not event.event_type.strip() or not isinstance(event.payload, dict):
            raise ResponseInterpretationError("Result event is malformed")
        formatter = self._formatters.get(event.event_type)
        if formatter is None:
            raise ResponseInterpretationError(
                f"No response formatter registered for {event.event_type}"
            )
        response = formatter(event, context)
        if not isinstance(response, str) or not response.strip():
            raise ResponseInterpretationError("Response formatter returned empty text")
        return response


__all__ = [
    "DeterministicResponseInterpreter",
    "ResponseFormatter",
    "ResponseInterpretationError",
    "ResponseInterpreter",
]
