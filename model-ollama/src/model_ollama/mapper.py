from __future__ import annotations

import json
from typing import Any

from agent_core import Intent, Recommendation


class RecommendationMappingError(ValueError):
    pass


class IntentMappingError(ValueError):
    pass


class IntentMapper:
    @staticmethod
    def from_json(response: str) -> Intent:
        try:
            payload = json.loads(response)
        except json.JSONDecodeError as exc:
            raise IntentMappingError("Ollama output is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise IntentMappingError("Ollama intent output must be an object")

        intent_type = payload.get("intent_type")
        parameters = payload.get("parameters", {})
        metadata = payload.get("metadata", {})
        if not isinstance(intent_type, str) or not intent_type.strip():
            raise IntentMappingError("Ollama intent has an invalid type")
        if not isinstance(parameters, dict) or not isinstance(metadata, dict):
            raise IntentMappingError("Ollama intent has invalid mappings")
        try:
            return Intent(intent_type=intent_type, parameters=parameters, metadata=metadata)
        except ValueError as exc:
            raise IntentMappingError("Ollama intent is invalid") from exc


class RecommendationMapper:
    @staticmethod
    def from_json(response: str) -> list[Recommendation]:
        try:
            payload = json.loads(response)
        except json.JSONDecodeError as exc:
            raise RecommendationMappingError("Ollama output is not valid JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("recommendations"), list):
            raise RecommendationMappingError("Ollama output must contain a recommendations list")

        recommendations = []
        for index, item in enumerate(payload["recommendations"]):
            if not isinstance(item, dict):
                raise RecommendationMappingError(f"Recommendation {index} must be an object")
            recommendation_type = item.get("recommendation_type")
            if not isinstance(recommendation_type, str) or not recommendation_type.strip():
                raise RecommendationMappingError(f"Recommendation {index} has an invalid type")
            rationale = item.get("rationale")
            if rationale is not None and not isinstance(rationale, str):
                raise RecommendationMappingError(f"Recommendation {index} has an invalid rationale")
            parameters = item.get("parameters", {})
            metadata = item.get("metadata", {})
            if not isinstance(parameters, dict) or not isinstance(metadata, dict):
                raise RecommendationMappingError(f"Recommendation {index} has invalid mappings")
            confidence = item.get("confidence")
            if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float))):
                raise RecommendationMappingError(f"Recommendation {index} has an invalid confidence")
            try:
                recommendations.append(
                    Recommendation(
                        recommendation_type=recommendation_type,
                        rationale=rationale,
                        parameters=parameters,
                        confidence=float(confidence) if confidence is not None else None,
                        metadata=metadata,
                    )
                )
            except ValueError as exc:
                raise RecommendationMappingError(f"Recommendation {index} is invalid") from exc
        return recommendations


__all__ = [
    "IntentMapper",
    "IntentMappingError",
    "RecommendationMapper",
    "RecommendationMappingError",
]
