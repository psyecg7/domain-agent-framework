# agent-enterprise

`agent-enterprise` is an optional reference boundary for a separately deployed
Policy service and Executor service. It is not part of `agent-core` and does
not make an in-process Python application adversary-proof.

Policy holds an Ed25519 private key. After its deterministic policy has
authorized an action, it signs an `ExecutionCommand` binding the Decision ID,
action type, target, parameter hash, idempotency key, audience, and a short
expiry. Executor receives only the public key and fails closed on a missing,
altered, expired, wrong-audience, or replayed authorization.

Commands may also carry domain-defined `preconditions`, protected by a separate
`preconditions_sha256` claim. Executor verifies but does not interpret them;
the target domain performs its own native atomic conditional write. A stale
precondition becomes a business `CONFLICT`, not a framework retry or inferred
success. This avoids imposing a universal `state_version` model on `agent-core`.

`DeltaInventoryReservationHandler` is the runnable Inventory reference: it
uses a Delta conditional update over the domain's product version, observed
availability, and requested quantity. Two valid authorizations based on the
same stock snapshot therefore yield one `SUCCEEDED` effect and one business
`CONFLICT`. It is an example of a target-domain implementation, not a generic
Inventory abstraction. Its stock transition and the older reservation-evidence
ledger are not co-committed; a production Inventory system must establish that
effect-evidence consistency in its own system of record.

For a transactional operational system of record, use
`PostgresInventoryReservationHandler` with
`PostgresInventoryReservationAuthority`. Its conditional stock update and
operation evidence insert share one SQL transaction, closing the crash window
between a successful stock effect and durable reconciliation evidence.

```text
Policy service (private key) -> signed authorization -> Executor (public key)
```

Install it only in services that need this cross-service boundary:

```bash
pip install -e ./agent-enterprise
```

`DeltaReplayStore` provides durable replay evidence for a serialized Executor
writer and survives process restart. Like the other Delta reference stores, it
does not provide cross-process compare-and-swap; multi-Executor deployments
must serialize authorization IDs through their transport or choose a store with
atomic conditional writes. Production also needs key provisioning from its
enterprise key-management system. This package intentionally does not provide
an HTTP endpoint that signs caller-supplied decisions: signing must be called
inside the Policy service after its own deterministic evaluation.

The included HTTP reference exposes that shape with a narrow deterministic
order policy endpoint and a separate Executor endpoint. A domain Policy can
provide `preconditions_for` when constructing the Policy server; those facts
are signed and delivered unchanged to the domain effect handler. Its
end-to-end local test covers a precondition-bearing command, tampering, and a
replay.

## Open question: precondition negotiation

`preconditions_sha256` is intentionally conservative: it proves that Policy
authorized one exact state assumption. It does not define how a domain may
legitimately execute against newer state—for example after a price or stock
re-read—when only some assumptions still matter. A binary hash comparison must
therefore yield `CONFLICT` unless the domain supplies its own authoritative
comparison and atomic write rule. ETags, version ranges, field-level
invariants, and refreshed authorization are enterprise/domain contract choices;
they are not provided by `agent-core` or this reference package.

For a local two-process run (development keys only):

```bash
python -m agent_enterprise.policy_service --private-key /tmp/policy.pem --public-key-out /tmp/policy.pub --generate-development-key
python -m agent_enterprise.executor_service --public-key /tmp/policy.pub --replay-table /tmp/executor-replay --effect-log /tmp/executor-effects.jsonl
```

Run these in separate terminals. The Executor command deliberately has no
private-key option.

For local authenticated ingress, add `--development-bearer-token dev-token` to
the Policy command and send `Authorization: Bearer dev-token`. This adapter is
for local development only; enterprise deployments use their OIDC provider and
service mTLS.

## Production deployment TODOs

- Replace the development bearer token with OIDC/JWT validation against the
  enterprise identity provider, including issuer, audience, expiry, signature,
  tenant, and role/scope checks before Policy signs an authorization.
- Use mTLS and workload/service identity between Policy and Executor.
- Store the Policy signing key in KMS/HSM with rotation; do not use local PEM
  files outside development.
- Use a durable shared replay store with atomic conditional claims, or enforce
  one serialized Executor writer per authorization ID.
- Treat Delta audit records as durable operational evidence, not as an
  immutable compliance ledger. Retention, access control, and protection from
  privileged rewrites belong to the enterprise governance platform.
- Deploy Policy and Executor as separate workloads in staging and run the same
  authorization, replay, audit, Redpanda recovery, and consumer-handoff checks
  against that environment before production rollout.
