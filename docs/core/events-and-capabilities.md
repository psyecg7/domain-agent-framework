# Events and capabilities

An `Event` is the message the core processes. It includes:

- an event type;
- the target `entity_id` and `entity_type`;
- a payload;
- a stable event ID; and
- optional idempotency and lineage metadata.

The core can process an event locally. A transport adapter such as
[`transport-redpanda`](../../transport-redpanda/README.md) is responsible for moving it
between processes.

## Capabilities are discovery, not execution

`Capability` declares what a domain supports. `Intent` declares what a caller
wants. A registry can find capabilities that support an intent:

```text
Intent → CapabilityRegistry → one matching Capability → invocation Event
```

Finding a capability does not authorize or execute it. The application-layer
`CapabilityInvoker` publishes one invocation event. The owning domain receives
that event, evaluates its own state and policy, and later publishes a normal
result event.

This avoids direct agent-to-agent method calls and keeps ownership clear:

- the requesting domain owns its process and recovery decision;
- the target domain owns its state, validation, effect, and result;
- transport owns delivery mechanics;
- the core owns only the shared message and lifecycle vocabulary.

For partial failure, do not infer an outcome after a lost result. Use a stable
operation ID and ask the domain that owns the effect to reconcile it. The
[reconciliation reference](../milestone-2S.md) documents the demonstrated
`EXISTS`, `ABSENT`, `CONFLICT`, and `STILL_UNKNOWN` outcome contract.
