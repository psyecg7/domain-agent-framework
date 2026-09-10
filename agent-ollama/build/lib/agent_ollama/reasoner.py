from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from typing import Any

from agent_core import ReasoningContext, Recommendation

from .client import OllamaClient
from .mapper import RecommendationMapper, RecommendationMappingError


class ReasoningError(RuntimeError):
    pass


class OllamaReasoner:
    def __init__(self, client: OllamaClient) -> None:
        self.client = client

    def reason(self, context: ReasoningContext) -> list[Recommendation]:
        try:
            response = self.client.generate(self._build_prompt(context))
            return RecommendationMapper.from_json(response)
        except RecommendationMappingError as exc:
            raise ReasoningError("Ollama returned an invalid recommendation response") from exc
        except Exception as exc:
            if isinstance(exc, ReasoningError):
                raise
            raise ReasoningError("Ollama reasoning failed") from exc

    def _build_prompt(self, context: ReasoningContext) -> str:
        serialized = {
            "state": _jsonable(asdict(context.state)),
            "observations": [_jsonable(asdict(item)) for item in context.observations],
            "memories": [
                {"memory": _jsonable(asdict(item.memory)), "score": item.score}
                for item in context.memories
            ],
            "policy_context": _jsonable(dict(context.policy_context)),
            "metadata": _jsonable(dict(context.metadata)),
        }
        return (
            "Return only valid JSON with this exact top-level shape: "
            '{"recommendations":[{"recommendation_type":string,"rationale":string|null,'
            '"parameters":object,"confidence":number|null,"metadata":object}]}.'
            " Recommendations are advisory only. Do not claim to authorize actions.\n"
            + json.dumps(serialized, sort_keys=True, default=_jsonable)
        )


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


__all__ = ["OllamaReasoner", "ReasoningError"]
