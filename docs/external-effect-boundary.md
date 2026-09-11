# External side-effect boundary

This framework cannot make a database transaction include a payment provider,
device, email service, or third-party HTTP API. It therefore does not promise
exactly-once external execution. Instead, it defines the safe boundary that a
domain must implement and verify.

## Required contract

For every external business effect, the effect-owning domain must:

1. Create one stable `operation_id` before the first provider call. Attempts
   may vary, but the operation ID does not.
2. Persist the request/effect intent and its outbound work before calling the
   provider. For PostgreSQL-backed domains,
   `PostgresAtomicOperationStore` can atomically record the domain mutation,
   operation claim, and outbox message.
3. Send that exact operation ID through the provider's idempotency mechanism:
   an idempotency header, merchant reference, device command ID, or equivalent.
4. On a timeout, connection reset, or lost response after possible acceptance,
   record `UNKNOWN`. Do not infer `SUCCEEDED` or `FAILED`, and do not silently
   submit a new operation ID.
5. Reconcile through an authoritative provider query or callback keyed by the
   stable operation ID. The effect-owning domain maps that evidence to its own
   result vocabulary and chooses recovery, compensation, or human escalation.

```text
database transaction -> durable operation + outbox
                     -> provider call(operation_id as idempotency key)
                     -> result observed? ---- yes -> durable domain evidence
                                           |
                                           no
                                           v
                                        UNKNOWN
                                           |
                                           v
                         provider reconciliation(operation_id)
                                           |
                         proven result / STILL_UNKNOWN / escalation
```

## Provider admission rule

An external provider is eligible for unattended side effects only when it
supports at least one of these routes to truth:

- idempotent execution keyed by a caller-controlled stable identifier; or
- an authoritative reconciliation query keyed by a caller-controlled stable
  identifier.

For money movement, inventory movement, access changes, or physical commands,
require both whenever possible. A provider with neither property is not safe
for unattended retry: route failures to explicit human review rather than
guessing or replaying the command.

## What the framework provides

[`agent-conformance`](../agent-conformance/README.md) provides test helpers
that require stable external idempotency keys, `UNKNOWN` after an ambiguous
transport failure, and reconciliation-driven resolution. They are callable
checks to run in each domain's CI; they do not choose domain outcome words,
retry schedules, compensation, or escalation authority.

[`agent-postgres`](../agent-postgres/README.md) provides an optional atomic
operation/outbox transaction for database-backed domains. It makes the durable
handoff to a provider safe, but cannot make the provider call atomic. Consumers
and providers must still tolerate duplicate delivery of the stable operation
identity.
