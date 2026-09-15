# Core ports

Ports are small `Protocol` interfaces in `agent_core.ports`. They let an
application choose infrastructure without making the core depend on it.

| Port | Responsibility | Typical implementation |
| --- | --- | --- |
| `StateStore` | Load and save current structured entity state. | In-memory store, PostgreSQL, Delta |
| `PolicyEngine` | Turn state and approved advice into Decisions. | Domain Python code |
| `Reasoner` | Produce non-authoritative Recommendations. | OpenAI, Ollama, local logic |
| `ActionExecutor` | Perform an already-created Action. | Local handler, publisher, domain adapter |
| `MemoryStore` | Retrieve optional semantic context. | LanceDB |
| `CapabilityRegistry` | List and match declared Capabilities. | In-memory/application registry |
| `IntentInterpreter` | Translate a human request into an Intent. | Deterministic parser or LLM adapter |

A port describes a boundary, not a production guarantee. For example, an
`ActionExecutor` protocol does not make a payment call idempotent, and a
`StateStore` protocol does not make two workers atomic. Select an adapter and
add the domain contract required for the effect you are implementing.
