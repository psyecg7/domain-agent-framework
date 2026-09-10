from __future__ import annotations

import json

import pytest

from agent_ollama import IntentInterpretationError, OllamaIntentInterpreter


class FakeClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response


def test_natural_language_becomes_generic_intent() -> None:
    client = FakeClient(
        json.dumps(
            {
                "intent_type": "inventory.reserve",
                "parameters": {"product_id": "P123", "quantity": 2},
                "metadata": {"channel": "conversation"},
            }
        )
    )

    intent = OllamaIntentInterpreter(client).interpret("Reserve two units of product P123.", {})

    assert intent.intent_type == "inventory.reserve"
    assert intent.parameters["quantity"] == 2
    assert intent.metadata["channel"] == "conversation"
    assert "Reserve two units" in client.prompts[0]


def test_malformed_or_empty_request_fails_safely() -> None:
    with pytest.raises(IntentInterpretationError):
        OllamaIntentInterpreter(FakeClient("not valid json")).interpret("Reserve two units", {})

    with pytest.raises(IntentInterpretationError):
        OllamaIntentInterpreter(FakeClient("{}")).interpret("", {})
