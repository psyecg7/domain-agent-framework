from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from typing import Any, Mapping, Protocol

from agent_core import Intent, Recommendation, ReasoningContext


class ChatClient(Protocol):
    def complete(self, *, system: str, user: str) -> str:
        ...


class OpenAIResponseError(RuntimeError):
    pass


class OpenAIChatClient:
    def __init__(self, *, model: str, api_key: str | None = None) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - dependency boundary
            raise RuntimeError("openai is required for OpenAIChatClient") from exc
        self.model = model
        self.client = OpenAI(api_key=api_key)

    def complete(self, *, system: str, user: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        content = response.choices[0].message.content
        if not content:
            raise OpenAIResponseError("OpenAI returned an empty response")
        return content


class OpenAIIntentInterpreter:
    def __init__(self, client: ChatClient) -> None:
        self.client = client

    def interpret(self, request: str, context: Mapping[str, Any]) -> Intent:
        if not request.strip():
            raise OpenAIResponseError("Request must not be empty")
        payload = self._json(self.client.complete(
            system=("Translate a request to JSON only: {intent_type:string,parameters:object,metadata:object}. "
                    "Do not authorize, select a capability, or execute anything."),
            user=json.dumps({"request": request, "context": dict(context)}, default=str),
        ))
        try:
            return Intent(
                intent_type=self._required_string(payload, "intent_type"),
                parameters=self._mapping(payload, "parameters"),
                metadata=self._mapping(payload, "metadata"),
            )
        except (TypeError, ValueError) as exc:
            raise OpenAIResponseError("OpenAI returned an invalid intent") from exc

    @staticmethod
    def _json(content: str) -> dict[str, Any]:
        try:
            value = json.loads(content)
        except json.JSONDecodeError as exc:
            raise OpenAIResponseError("OpenAI output was not JSON") from exc
        if not isinstance(value, dict):
            raise OpenAIResponseError("OpenAI output must be a JSON object")
        return value

    @staticmethod
    def _required_string(value: Mapping[str, Any], key: str) -> str:
        result = value.get(key)
        if not isinstance(result, str) or not result.strip():
            raise ValueError(f"{key} must be a non-empty string")
        return result

    @staticmethod
    def _mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
        result = value.get(key, {})
        if not isinstance(result, dict):
            raise ValueError(f"{key} must be an object")
        return result


class OpenAIReasoner:
    def __init__(self, client: ChatClient) -> None:
        self.client = client

    def reason(self, context: ReasoningContext) -> list[Recommendation]:
        source = {
            "state": _jsonable(asdict(context.state)),
            "observations": _jsonable([asdict(item) for item in context.observations]),
            "memories": _jsonable([asdict(item) for item in context.memories]),
            "policy_context": _jsonable(dict(context.policy_context)),
            "metadata": _jsonable(dict(context.metadata)),
        }
        payload = OpenAIIntentInterpreter._json(self.client.complete(
            system=("Return JSON only: {recommendations:[{recommendation_type:string,rationale:string|null,"
                    "parameters:object,confidence:number|null,metadata:object}]}. Recommendations are advisory only."),
            user=json.dumps(source, default=str),
        ))
        items = payload.get("recommendations")
        if not isinstance(items, list):
            raise OpenAIResponseError("OpenAI output must include recommendations")
        recommendations = []
        for item in items:
            if not isinstance(item, dict):
                raise OpenAIResponseError("Recommendation must be an object")
            try:
                confidence = item.get("confidence")
                if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float))):
                    raise ValueError("confidence must be numeric")
                recommendations.append(Recommendation(
                    recommendation_type=OpenAIIntentInterpreter._required_string(item, "recommendation_type"),
                    rationale=item.get("rationale"),
                    parameters=OpenAIIntentInterpreter._mapping(item, "parameters"),
                    confidence=float(confidence) if confidence is not None else None,
                    metadata=OpenAIIntentInterpreter._mapping(item, "metadata"),
                ))
            except (TypeError, ValueError) as exc:
                raise OpenAIResponseError("OpenAI returned an invalid recommendation") from exc
        return recommendations


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value
