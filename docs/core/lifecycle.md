# Core lifecycle

The core runtime has one entry point:

```python
result = agent.process(event)
```

It performs this fixed sequence:

```text
Event → Observation → State → optional reasoning → policy → Decision → Action
```

1. An [`Event`](../../src/agent_core/primitives/event.py) identifies what
   happened, the target entity, and an optional payload.
2. The runtime turns each payload field into an `Observation`.
3. It loads the entity's current `State`, applies those observations, and saves
   the updated state through a `StateStore`.
4. An optional `Reasoner` may produce advisory `Recommendation` values.
5. A deterministic `PolicyEngine` evaluates the state, and optionally those
   recommendations, to produce `Decision` values.
6. Each Decision becomes an `Action`. If configured, an `ActionExecutor`
   receives the action.

`AgentResult` returns the event, updated state, recommendations, decisions, and
actions. It is useful for tests, local tools, and observability.

The core does not decide what an event means in your domain. A payment,
reservation, or temperature threshold is application policy. The lifecycle only
makes the transition through those steps explicit.

## Start locally

Most Python applications should use
[`AgentApp`](../../agent-app/README.md), which composes this lifecycle with an
in-memory store and local handlers. Use raw `Agent` when you need to choose
your own state store, reasoner, executor, or memory store.
