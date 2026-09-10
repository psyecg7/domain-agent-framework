# Changelog

## Unreleased

### Added

- `domain-agent-app`: the versioned, local-first public application API.
- `domain-agent-conformance`: test-only callable assertions, an adoption
  template, and a CI-checkable conformance discipline for side-effecting
  domains.
- Delta-backed reference persistence for effect evidence and process state,
  including explicit reconciliation outcomes.
- Optional Enterprise reference components for signed, replay-protected
  cross-service command authorization and domain-owned preconditions.
- Release compatibility CI: clean wheel installation and smoke imports on
  Python 3.11–3.13, plus a separate unit/conformance validation job.

### Changed

- `local_agent`, `InMemoryStateStore`, `FunctionPolicyEngine`, and
  `FunctionActionExecutor` are no longer exposed from the public
  `agent_core` API. Their private implementation moved to `agent_app._local`.
  `AgentApp` is the supported local-first developer API.
- Dependent packages now constrain their framework dependency to the supported
  `>=0.1.0,<0.2.0` release line. See `docs/release-compatibility.md`.

### Known limits

- The local runtime requires a `Decision` to construct an `Action`, but it
  cannot prove that arbitrary Python code obtained that Decision from a real
  Policy evaluation. Use the Enterprise boundary across deployment trust
  domains.
- Durable evidence, replay claims, and process stores require a production
  backend with cross-process atomicity when executors scale beyond one process.
- Reconciliation, recovery, and atomic business preconditions remain
  domain-owned contracts, validated through conformance tests rather than
  supplied by `agent_core`.
