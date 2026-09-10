# agent-ollama

`agent-ollama` is an optional reasoning adapter for `agent-core`.

It also provides `OllamaIntentInterpreter`, a separate conversational boundary that translates natural language into a generic `Intent`. It does not discover capabilities, authorize requests, create decisions, or execute actions.

## Contract

```text
State + observations + optional memory context
                 |
                 v
          OllamaReasoner
                 |
                 v
        Recommendation[]
                 |
                 v
  deterministic PolicyEngine validation
                 |
                 v
          Decision -> Action
```

The Ollama model is advisory only. It can recommend, explain, and infer, but it cannot authorize an action, mutate state, write memory, execute tools, or bypass deterministic policy evaluation. The adapter never produces `Decision` or `Action` objects.

Conversational interpretation is a separate flow:

```text
Human request -> IntentInterpreter -> Intent -> CapabilityRegistry -> Event -> Domain Agent
```

The registry, not the model, is authoritative for available capabilities. Unknown capabilities stop without an event, and multiple matches are surfaced as ambiguity rather than silently selected.

`ReasoningContext` contains structured `State`, observations, optional `MemoryResult` values, policy context, and metadata. State remains authoritative structured state; semantic memory remains supplemental context.

## Usage

```python
from agent_ollama import HttpOllamaClient, OllamaReasoner

reasoner = OllamaReasoner(
    HttpOllamaClient(model="llama3.2", base_url="http://localhost:11434")
)
```

The Ollama endpoint must return JSON with a `recommendations` array. Each item requires a non-empty `recommendation_type`; `rationale`, `parameters`, `confidence`, and `metadata` are validated. Invalid JSON or schema raises `ReasoningError` and cannot produce an action.

## Dependency boundary

```text
agent-redpanda  -> agent-core <- agent-delta
agent-lancedb   -> agent-core <- agent-ollama
```

The core has no Ollama dependency. No LangChain, tool calling, automatic event embedding, or autonomous execution is included in this milestone.
