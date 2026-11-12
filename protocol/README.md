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
| [`kad/inventory-command-v1.proto`](kad/inventory-command-v1.proto) | Streaming gRPC command-ingress example. |
| [`test-vectors/`](test-vectors) | Portable event and Enterprise authorization test data. |

Domain payloads remain domain-owned. A protocol envelope says how a message is
identified and delivered; it does not define what `inventory.reserve` or
`payment.capture` means.

## Streaming commands

The KAD Inventory Protobuf contract accepts many commands over one gRPC
bidirectional stream. `ACCEPTED` means the Inventory command inbox persisted a
command; final business outcomes remain asynchronous events. It is an
application example, not a generic workflow or command primitive.

The Python KAD reference and [`sdk-java`](../sdk-java/README.md) both generate
bindings from this exact file. Their adapters expose the same distinction:
acceptance is durable ingress, not a synchronous reservation result.

## Migration status

`transport-redpanda` now emits `protocol_version: 1` and accepts legacy records
without the field. Producers in another language should emit v1 immediately.
Consumers must reject an explicitly unsupported version rather than attempting
to interpret it.

The Enterprise HTTP reference already exchanges the v1 command and signed
authorization JSON shapes. The fixed vector is checked by the Python suite and
is intended to be checked unchanged by every other implementation.

The first implementation is [`sdk-java`](../sdk-java/README.md). It consumes
these same fixtures directly rather than maintaining copied Java-only vectors.

## Compatibility rule

Never change a v1 schema in place. Add a new version, document the producer and
consumer migration path, and keep an old consumer until all producers are
upgraded. The [compatibility policy](compatibility.md) gives the short rules.
