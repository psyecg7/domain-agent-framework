from __future__ import annotations

import json
from typing import Any, Mapping

from agent_core import Intent

from .client import OllamaClient
from .mapper import IntentMapper, IntentMappingError


class IntentInterpretationError(RuntimeError):
    pass


class OllamaIntentInterpreter:
    def __init__(self, client: OllamaClient) -> None:
        self.client = client

    def interpret(self, request: str, context: Mapping[str, Any]) -> Intent:
        if not request.strip():
            raise IntentInterpretationError("Request must not be empty")
        try:
            response = self.client.generate(self._build_prompt(request, context))
            return IntentMapper.from_json(response)
        except IntentMappingError as exc:
            raise IntentInterpretationError("Ollama returned an invalid intent response") from exc
        except Exception as exc:
            if isinstance(exc, IntentInterpretationError):
                raise
            raise IntentInterpretationError("Ollama intent interpretation failed") from exc

    def _build_prompt(self, request: str, context: Mapping[str, Any]) -> str:
        return (
            "Translate the human request into intent data. Return only valid JSON with this exact shape: "
            '{"intent_type": string, "parameters": object, "metadata": object}. '
            "Do not execute anything, select capabilities, or authorize actions.\n"
            + json.dumps({"request": request, "context": dict(context)}, sort_keys=True, default=str)
        )


__all__ = ["OllamaIntentInterpreter", "IntentInterpretationError"]
