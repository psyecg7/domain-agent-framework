# Release and compatibility policy

## Supported baseline

Every package in this repository supports Python 3.11, 3.12, and 3.13. The
release CI builds wheels, installs them into a fresh virtual environment, and
imports every package on each supported Python version. This catches metadata,
dependency-resolution, and non-editable-install regressions that a shared
developer checkout can hide.

| Package | Public role | Compatible release line |
| --- | --- | --- |
| `domain-agent-core` | Stable runtime semantics | `0.2.x` |
| `domain-agent-app` | Local-first ergonomic, invocation, and conversational-composition API | Core `>=0.2.0,<0.3.0` |
| `domain-agent-conformance` | Test-only assertions/templates | Independent runtime dependency set |
| `agent-delta` | Delta persistence adapter | Core `>=0.2.0,<0.3.0` |
| `agent-enterprise` | Cross-service authorization reference | Core, Delta, Postgres `>=0.2.0,<0.3.0` |
| `agent-postgres` | Optional adapter: state, conditional writes, and atomic operation/outbox support | Core `>=0.2.0,<0.3.0` |
| `agent-lancedb`, `agent-ollama`, `agent-openai`, `agent-redpanda`, `agent-scheduler` | Optional adapters | Core `>=0.2.0,<0.3.0` |

## Versioning rules

Each distributable package has its own version. `agent-app` is a public API,
not an unversioned convenience shim. Adapters and `agent-conformance` are also
versioned artifacts; their compatibility requirements are declared in their
own `pyproject.toml` files.

Before 1.0, a breaking public API change advances the minor line (for example,
`0.1` to `0.2`). Backward-compatible fixes advance the patch version. A
dependent package must constrain its framework dependency to the compatible
minor line, as `>=0.2.0,<0.3.0` does today. The conformance package deliberately
has no runtime dependency on the other packages.

## Validation tiers

1. **Release compatibility CI** builds and installs wheels in a clean
   environment, then imports every package.
2. **Unit and conformance CI** runs fast deterministic tests, including the
   reusable conformance assertions and their adopted references.
3. **Infrastructure CI** starts the repository's Redpanda and PostgreSQL
   Compose services on every push and pull request. It exercises broker
   redelivery, transactional event receipts, and atomicity with real services.
   Keycloak, Vault, external-model, and production key-management tests remain
   opt-in because they require separate credentials and deployment lifecycle
   controls. A green infrastructure job is not evidence that those unavailable
   systems were exercised.

For local reproduction, `bash scripts/verify_framework.sh` runs tiers two and,
when Docker plus its Python drivers are available, three. It reports optional
infrastructure checks as `SKIPPED` instead of presenting a partial validation
as a complete one.

## Release checklist

- update the version of every package whose public contract changed;
- update dependency bounds when a compatible release line changes;
- remove generated `build/` directories before producing release wheels, so a
  deleted module cannot survive from an earlier local build;
- build and install the wheels cleanly;
- run unit and conformance suites;
- run each available infrastructure suite and record intentionally skipped
  environments;
- run the versioned PostgreSQL migration job against the staging schema before
  deploying replicas that use adapter or Enterprise PostgreSQL stores;
- follow the [PostgreSQL migration runbook](postgres-migration-runbook.md),
  including backup, recorded-version verification, and canary rollout;
- update changelogs and compatibility documentation for removals or changed
  limits.
