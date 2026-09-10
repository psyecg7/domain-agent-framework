# Release and compatibility policy

## Supported baseline

Every package in this repository supports Python 3.11, 3.12, and 3.13. The
release CI builds wheels, installs them into a fresh virtual environment, and
imports every package on each supported Python version. This catches metadata,
dependency-resolution, and non-editable-install regressions that a shared
developer checkout can hide.

| Package | Public role | Compatible release line |
| --- | --- | --- |
| `domain-agent-core` | Stable runtime semantics | `0.1.x` |
| `domain-agent-app` | Local-first ergonomic API | Core `>=0.1.0,<0.2.0` |
| `domain-agent-conformance` | Test-only assertions/templates | Independent runtime dependency set |
| `agent-delta` | Delta persistence adapter | Core `>=0.1.0,<0.2.0` |
| `agent-enterprise` | Cross-service authorization reference | Core, Delta, Postgres `>=0.1.0,<0.2.0` |
| `agent-lancedb`, `agent-ollama`, `agent-openai`, `agent-postgres`, `agent-redpanda`, `agent-scheduler` | Optional adapters | Core `>=0.1.0,<0.2.0` |

## Versioning rules

Each distributable package has its own version. `agent-app` is a public API,
not an unversioned convenience shim. Adapters and `agent-conformance` are also
versioned artifacts; their compatibility requirements are declared in their
own `pyproject.toml` files.

Before 1.0, a breaking public API change advances the minor line (for example,
`0.1` to `0.2`). Backward-compatible fixes advance the patch version. A
dependent package must constrain its framework dependency to the compatible
minor line, as `>=0.1.0,<0.2.0` does today. The conformance package deliberately
has no runtime dependency on the other packages.

## Validation tiers

1. **Release compatibility CI** builds and installs wheels in a clean
   environment, then imports every package.
2. **Unit and conformance CI** runs fast deterministic tests, including the
   reusable conformance assertions and their adopted references.
3. **Infrastructure CI** is opt-in: Redpanda, PostgreSQL, external model, and
   key-management tests require their actual services and credentials. A green
   release job is not evidence that unavailable infrastructure was exercised.

## Release checklist

- update the version of every package whose public contract changed;
- update dependency bounds when a compatible release line changes;
- build and install the wheels cleanly;
- run unit and conformance suites;
- run each available infrastructure suite and record intentionally skipped
  environments;
- update changelogs and compatibility documentation for removals or changed
  limits.
