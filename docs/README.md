# Documentation map

Choose one path. You do not need to read this repository from top to bottom.

## I am new to the framework

1. [Build your first agent](first-agent.md): one local Python application.
2. [Ports and adapters](ports-and-adapters.md): what PostgreSQL, Redpanda, and
   LanceDB packages are, why model packages have a different role, and why none
   of them is an agent.
3. [Core reference](core/README.md): the details of the event-to-action
   lifecycle when you need them.

## I need to add a capability

- [Component map](components.md): choose one package for one requirement.
- [Developer guide](developer-guide.md): move from a local application to
  durable storage, transport, AI advice, and recovery.
- [External effect boundary](external-effect-boundary.md): payments, devices,
  and third-party HTTP APIs.

## I am operating a production system

- [Production safety case](production-safety-case.md): what is verified and
  what remains a domain or deployment responsibility.
- [PostgreSQL migration runbook](postgres-migration-runbook.md): schema
  migration and rollout.
- [Enterprise security profile](enterprise-compose.md): local integration for
  OIDC, mTLS, and Vault; it is not a production deployment guide.
- [Release compatibility](release-compatibility.md) and
  [publishing](publishing.md): releases and package distribution.

## I want architectural evidence

The [milestone documents](milestone-2R.md) record adversarial experiments and
their limits. Read them after you understand the local application path; they
are evidence for design decisions, not onboarding instructions.
