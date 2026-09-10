# agent-app

`agent-app` is the local-first developer entry point for
`domain-agent-core`. It composes the existing safe runtime; it does not add a
second policy, action, or authority model.

It is the framework's ergonomic, versioned application API: `agent-core`
owns semantics, `agent-app` owns local-first composition, and adapters own
infrastructure. Public `AgentApp`, `policy`, `action`, `decide`, and `process`
behave as a supported surface and follow this package's semantic versioning.

```bash
pip install -e ./agent-app
```

```python
from agent_app import AgentApp
from agent_core import Event

app = AgentApp()

@app.policy("measurement.received")
def investigate(state):
    if state.values.get("temperature", 0) > 40:
        return app.decide("INVESTIGATE", severity="MEDIUM", reason="threshold exceeded")
    return None

@app.action("INVESTIGATE")
def notify(action):
    print(f"Investigate {action.entity_id}")

app.process(Event(
    "measurement.received", "sensor-1", "sensor",
    {"temperature": 42}, source="telemetry",
))
```

The whole local application model is:

```text
Event -> registered deterministic policy -> Decision -> registered action
```

`app.decide(...)` returns a target-independent `DecisionSpec`, not a list or
an already-targeted `Decision`. A policy may return one `DecisionSpec`, one raw
`Decision`, an iterable of either, or `None`. `AgentApp` materializes a
`DecisionSpec` only after the policy returns, using the `State` passed into the
policy function. It does not read an ambient in-flight Event or processing
context; the helper merely avoids repeating the entity target already present
in `state`.

`AgentApp` is intentionally in-memory and single-process. Graduate to the
lower-level `Agent` constructor and adapters only when you need durable state,
message transport, AI recommendations, cross-domain capabilities, or
cross-service authorization.
