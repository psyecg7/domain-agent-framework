# protocol

`protocol` is the language-neutral contract for services that participate in
this framework. It is not Python code, a runtime dependency, or a fourth
runtime layer. It contains versioned JSON schemas, wire rules, and fixed test
vectors that Go, Java, TypeScript, Rust, or Python implementations can use.

## What is stable here

| Artifact | Purpose |
| --- | --- |
| [`events/event-envelope-v1.schema.json`](events/event-envelope-v1.schema.json) | Broker event envelope: identity, target, lineage, idempotency, and domain data. |
| [`enterprise/execution-command-v1.schema.json`](enterprise/execution-command-v1.schema.json) | The exact action request an Executor receives. |
| [`enterprise/signed-authorization-v1.schema.json`](enterprise/signed-authorization-v1.schema.json) | The signed Policy authorization envelope. |
| [`enterprise/signing-v1.md`](enterprise/signing-v1.md) | Canonical byte and Ed25519 verification rules. |
| [`test-vectors/enterprise/authorization-v1.json`](test-vectors/enterprise/authorization-v1.json) | Portable valid and rejection test data. |

Domain payloads remain domain-owned. A protocol envelope says how a message is
identified and delivered; it does not define what `inventory.reserve` or
`payment.capture` means.

## Migration status

`transport-redpanda` now emits `protocol_version: 1` and accepts legacy records
without the field. Producers in another language should emit v1 immediately.
Consumers must reject an explicitly unsupported version rather than attempting
to interpret it.

The Enterprise HTTP reference already exchanges the v1 command and signed
authorization JSON shapes. The fixed vector is checked by the Python suite and
is intended to be checked unchanged by every other implementation.

## Compatibility rule

Never change a v1 schema in place. Add a new version, document the producer and
consumer migration path, and keep an old consumer until all producers are
upgraded. The [compatibility policy](compatibility.md) gives the short rules.
