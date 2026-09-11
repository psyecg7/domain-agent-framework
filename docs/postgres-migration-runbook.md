# PostgreSQL migration runbook

Use this procedure before deploying a version that changes an `agent-postgres`
or `agent-enterprise` PostgreSQL schema. Migrations are a deployment operation:
do not let application replicas race to create or alter tables at startup.

## Scope

The current migration commands manage:

- `agent-postgres`: state, atomic-operation, outbox, and event-receipt tables;
- `agent-enterprise`: authorization replay and decision-revocation tables.

They record applied versions in `agent_schema_migrations`. Version records are
scoped by the configured table prefix or authorization table pair, so multiple
independent deployments can share one database safely.

## Preflight

1. Identify the exact target database URL and table names/prefixes used by the
   deployment.
2. Confirm a recent, restorable database backup exists. For PostgreSQL, take a
   logical backup or use the organization’s approved snapshot mechanism.
3. Run the repository’s service-backed integration checks from the release
   candidate and verify the matching CI run is green.
4. Schedule the migration as one controlled job with permission to alter only
   the target application schema. Do not give ordinary Policy, Executor, or
   consumer replicas DDL privileges.

## Apply the adapter migration

Run this once per configured `agent-postgres` table prefix:

```bash
python -m agent_postgres.migrate \
  --database-url "$POSTGRES_DATABASE_URL" \
  --table-prefix agent_atomic \
  --state-table agent_states \
  --receipt-table agent_event_receipts
```

The command is idempotent. A fresh database creates the current tables; an
older outbox gains the HA lease columns. It prints `schema already current`
when no version remains to apply.

## Apply the Enterprise authorization migration

Before starting Policy or Executor replicas that use PostgreSQL replay or
revocation stores, run:

```bash
python -m agent_enterprise.migrate \
  --database-url "$POSTGRES_DATABASE_URL" \
  --replay-table agent_authorization_replay \
  --revocation-table agent_authorization_revocations
```

## Verify before rollout

Inspect the recorded versions with a read-only database account:

```sql
SELECT component, version, applied_at
FROM agent_schema_migrations
ORDER BY component, version;
```

For the current adapter release, an upgraded atomic-outbox component must have
versions `1` and `2`. Version `2` adds `lease_owner`, `lease_expires_at`, and
`publish_attempts` to its outbox table.

Then deploy one canary replica and verify it can read its state/receipt/outbox
tables without DDL permission. Only then roll out the remaining replicas.

## Failure and rollback

Stop the rollout if a migration fails or verification does not show the
expected version. Do not start a newer replica against a partially migrated
schema.

Current migrations are additive and intentionally have no automatic down
migration. If an application rollback is necessary after a successful schema
upgrade, deploy the previous compatible application version where safe; remove
columns only through a separately reviewed, backup-tested operational change.
Restore from backup when the migration itself or its data assumptions are
incorrect.

## Staging gate

Before production rollout, execute this same runbook against a staging copy of
the target topology and run:

```bash
POSTGRES_ATOMIC_DATABASE_URL="$POSTGRES_DATABASE_URL" \
REDPANDA_BOOTSTRAP_SERVERS="$REDPANDA_BOOTSTRAP_SERVERS" \
  pytest -q tests/test_postgres_atomicity_integration.py \
            tests/test_postgres_redpanda_receipt_integration.py
```

For Enterprise deployments, also run the documented Keycloak/Vault/mTLS
verification. Domain-specific external effects still require their own
idempotency and reconciliation staging checks.
