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
does not provide cross-process compare-and-swap. For multiple Executor
replicas, `PostgresReplayStore` uses a shared primary-key claim and
`PostgresRevocationStore` distributes decision withdrawals. An Executor checks
revocation after verifying the signature and binding, but before it claims the
authorization or calls the effect handler. Revocation is fail-closed once the
shared write is visible; its propagation deadline is an operational SLO.
Production also needs key provisioning from its enterprise key-management
system. This package intentionally does not provide
an HTTP endpoint that signs caller-supplied decisions: signing must be called
inside the Policy service after its own deterministic evaluation.

If an authorized command calls a payment provider, device, or third-party API,
the signature does not make that provider call transactional. The target domain
must also follow the [external side-effect boundary](../docs/external-effect-boundary.md):
use the signed stable operation/idempotency identity, retain `UNKNOWN` after an
ambiguous provider failure, and reconcile before recovery.

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
python -m agent_enterprise.executor_service --public-key /tmp/policy.pub --replay-table /tmp/executor-replay --owned-action-type CREATE_ORDER --effect-log /tmp/executor-effects.jsonl
```

Run these in separate terminals. The Executor command deliberately has no
private-key option.

For a multi-replica Executor, replace `--replay-table` with a shared database
URL and provide the same shared revocation table:

```bash
python -m agent_enterprise.executor_service --public-key /tmp/policy.pub \
  --postgres-replay-url postgresql+psycopg://agent:agent@localhost:5432/agent_atomic \
  --postgres-revocation-url postgresql+psycopg://agent:agent@localhost:5432/agent_atomic \
  --owned-action-type CREATE_ORDER \
  --effect-log /tmp/executor-effects.jsonl
```

For local authenticated ingress, add `--development-bearer-token dev-token` to
the Policy command and send `Authorization: Bearer dev-token`. This adapter is
for local development only; enterprise deployments use their OIDC provider and
service mTLS.

The Policy service can instead validate an OIDC access token locally against
the issuer's JWKS. It requires exact issuer, audience, expiry, signature, and
scope checks; a discovery/JWKS failure rejects the request rather than falling
back to development authentication:

```bash
python -m agent_enterprise.policy_service \
  --private-key /tmp/policy.pem --public-key-out /tmp/policy.pub \
  --oidc-issuer http://127.0.0.1:8080/realms/domain-agent-development \
  --oidc-audience policy-service --required-scope order:submit
```

`--development-bearer-token` and `--oidc-issuer` are mutually exclusive.

Both Policy and Executor can require mTLS at their HTTP listener by supplying
`--tls-cert`, `--tls-key`, `--tls-client-ca`, and exactly one identity
selector: `--expected-client-dns` or `--expected-client-uri`. TLS rejects an
absent or untrusted certificate before HTTP handling; the reference then
accepts only the exact configured DNS SAN or URI SAN—never a wildcard, a
common-name fallback, or either of two identities. URI SANs support explicit
SPIFFE IDs. For example, Policy can require the Order client identity:

```bash
python -m agent_enterprise.policy_service ... \
  --tls-cert /run/tls/policy.crt --tls-key /run/tls/policy.key \
  --tls-client-ca /run/tls/ca.crt --expected-client-dns order-client
```

Executor uses the same options with `--expected-client-dns policy-service`.
For SPIFFE-based deployment identity, replace the DNS selector with one exact
URI, for example:

```bash
--expected-client-uri spiffe://example.org/workload/order-client
```

The caller of either HTTPS endpoint must use a client TLS context containing
its own certificate/key and the trusted CA bundle.

The HTTP reference also rejects request bodies larger than 1 MiB before reading
them, returning `413`. The `policy_server` and `executor_server` constructors
accept `max_request_bytes` when a deployment needs a smaller bounded command
envelope. A production HTTP gateway should enforce a matching request limit.
The runnable service entry points expose the same setting as
`--max-request-bytes`.

## Vault Transit signing

`VaultTransitPolicyAuthorizationIssuer` reads only the named Transit key's
public key and asks Transit to sign the canonical authorization claims. The
Policy private key is never exported into the process. Configure the Policy CLI
with `--vault-url`, `--vault-token`, and `--vault-transit-key` instead of a
local `--private-key`. The Vault token must be restricted to read the named key
metadata and sign with that one key; it must not have key-export or broad root
capabilities.

The issuer uses a small bounded retry budget only for transient network and
HTTP `429`/`5xx` responses. Authentication/authorization failures and malformed
Vault responses fail immediately. A permanent Vault outage still fails closed;
production should use workload-authenticated short-lived tokens (for example a
Vault Agent sidecar), renewal monitoring, and an availability SLO rather than a
long-lived static token.

For a no-downtime key transition, Executor accepts a repeated
`--trusted-public-key KEY_ID=PATH` key ring. Deploy the new public key to
Executor before changing Policy's `--key-id`, then retire the old key only
after the overlap window. See the
[signing-key rotation runbook](../docs/enterprise-key-rotation.md).

For a disposable local Keycloak, Vault, and step-ca environment, see the
[enterprise security Compose profile](../docs/enterprise-compose.md). Its
development credentials and in-memory Vault must not be used in production.
Run `bash scripts/verify_enterprise_stack.sh` for the automated local proof.
The complete manual verification is recorded in the
[manual enterprise authorization runbook](../docs/enterprise-manual-e2e.md).

## Production deployment TODOs

Before deploying Enterprise Policy/Executor replicas with PostgreSQL replay or
revocation stores, provision their versioned schema explicitly:

```bash
python -m agent_enterprise.migrate \
  --database-url 'postgresql+psycopg://agent:agent@db:5432/agent'
```

This is idempotent and records the Enterprise authorization schema version in
the shared `agent_schema_migrations` table. Run it as a controlled deployment
job rather than relying on application startup.
Follow the [PostgreSQL migration runbook](../docs/postgres-migration-runbook.md)
for staging and rollout controls.

- Configure production issuer/audience/scope policy and enterprise identity
  authorization around the OIDC validator; the local Keycloak profile is not a
  production identity provider.
- Operate certificate enrollment, renewal, revocation, and workload identity
  mapping for the mTLS boundary.
- Use a restricted Vault/KMS/HSM signing identity with rotation; never use the
  development token or local PEM keys outside development.
- Use a durable shared replay store with atomic conditional claims, or enforce
  one serialized Executor writer per authorization ID.
- Define and test the revocation propagation SLO between Policy and every
  Executor replica. A revoked decision is rejected only after the shared
  revocation write is visible to the receiving Executor.
- Treat Delta audit records as durable operational evidence, not as an
  immutable compliance ledger. Retention, access control, and protection from
  privileged rewrites belong to the enterprise governance platform.
- Deploy Policy and Executor as separate workloads in staging and run the same
  authorization, replay, audit, Redpanda recovery, and consumer-handoff checks
  against that environment before production rollout.
