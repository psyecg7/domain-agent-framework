# Build your first agent

Start here. Ignore the other packages for now.

The framework helps you write a safe automation. An input arrives, your code
looks at the current facts, your code decides what should happen, and a handler
does the work.

```text
Event → State → Policy → Decision → Action → Handler
```

There are four things to understand:

| Word | Plain meaning |
| --- | --- |
| Event | Something happened. For example: a temperature reading arrived. |
| State | The latest facts we know about one thing. For example: the sensor temperature is 42. |
| Policy | Your ordinary Python `if` statement. It decides whether work is allowed. |
| Action | Work that policy allowed. For example: notify an operator. |

The framework's job is small: it makes the policy step unavoidable before the
action handler runs.

Packages whose name begins with `agent-` are libraries for building an agent;
they are not agents themselves. `storage-postgres` is a database plug,
`transport-redpanda` is a message-transport plug, and `memory-lancedb` is an
optional document-search plug. [Ports and adapters](ports-and-adapters.md)
explains this vocabulary when you need it.

## A complete local example

```python
from agent_app import AgentApp
from agent_core import Event

app = AgentApp()

@app.policy("measurement.received")
def check_temperature(state):
    if state.values.get("temperature", 0) > 40:
        return app.decide("NOTIFY_OPERATOR", reason="temperature is too high")

@app.action("NOTIFY_OPERATOR")
def notify(action):
    print(f"Send an alert for {action.entity_id}")

app.process(
    Event(
        "measurement.received",
        entity_id="sensor-1",
        entity_type="sensor",
        payload={"temperature": 42},
    )
)
```

This prints an alert. If the temperature is 40 or lower, the policy returns no
decision and the handler does not run.

Use this API first: [`agent-app`](../agent-app/README.md). It is designed for a
single Python process and keeps state in memory.

## What to ignore for now

You do not need any of these to build the example above:

- `Agent`, ports, and the lower-level core primitives;
- PostgreSQL, Delta, Kafka, Redpanda, and outboxes;
- AI reasoners, Ollama, OpenAI, or LanceDB;
- capabilities, cross-domain requests, enterprise authorization, and
  conformance tests.

They exist for applications with extra requirements. Add one only when you
have that requirement.

## Add one thing when you need it

| When your application needs this | Read/use this next |
| --- | --- |
| Keep state after a restart | [PostgreSQL state](core/state-and-storage.md) |
| Send work to another service | [Events and capabilities](core/events-and-capabilities.md) and `transport-redpanda` |
| Let an AI suggest an idea | [Policy and reasoning](core/policy-and-reasoning.md) |
| Give that AI related notes or documents | [Memory and LanceDB](core/memory.md) |
| Call a payment provider or another risky external API | [External effect boundary](external-effect-boundary.md) |

Do not add another package merely because it is available. Start with
`AgentApp`, then add the smallest component that solves a real problem.
