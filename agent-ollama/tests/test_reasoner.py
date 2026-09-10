from __future__ import annotations

import json

import pytest

from agent_core import Memory, MemoryResult, ReasoningContext, State
from agent_ollama import OllamaReasoner, ReasoningError


class FakeOllamaClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response


def make_context() -> ReasoningContext:
    return ReasoningContext(
        state=State(entity_id="entity-1", entity_type="device", values={"temperature": 80}),
        memories=[
            MemoryResult(
                memory=Memory(entity_id="entity-1", entity_type="device", content="A prior overheating event."),
                score=0.8,
            )
        ],
        metadata={"request_id": "req-1"},
    )


def test_reasoner_returns_generic_recommendation() -> None:
    client = FakeOllamaClient(
        json.dumps(
            {
                "recommendations": [
                    {
                        "recommendation_type": "INSPECT",
                        "rationale": "Temperature is elevated.",
                        "parameters": {"priority": "high"},
                        "confidence": 0.91,
                    }
                ]
            }
        )
    )

    recommendations = OllamaReasoner(client).reason(make_context())

    assert recommendations[0].recommendation_type == "INSPECT"
    assert recommendations[0].parameters == {"priority": "high"}
    assert recommendations[0].confidence == 0.91
    assert "A prior overheating event." in client.prompts[0]


def test_malformed_output_fails_safely() -> None:
    reasoner = OllamaReasoner(FakeOllamaClient("not-json"))

    with pytest.raises(ReasoningError):
        reasoner.reason(make_context())
