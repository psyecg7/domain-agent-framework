# agent-conformance

`agent-conformance` is an executable test helper package for adapters and
domain applications built on `domain-agent-framework`. It is **not** a runtime
package, a generic reconciliation interface, an outbox implementation, or a
workflow engine. Domains retain ownership of their operation IDs, effect
semantics, reconciliation capability, and recovery policy.

It currently supplies callable assertion templates for these invariants:

- one stable operation ID produces one business effect;
- a successful effect is reconcilable after restart;
- reconciliation answers use `EXISTS`, `ABSENT`, `CONFLICT`, or
  `STILL_UNKNOWN`;
- a pre-acceptance publish failure leaves an application-owned outbox pending;
- duplicate delivery does not change observed application state;
- correlation and causation identifiers survive a produced-message boundary;
- domain preconditions arrive at the effect handler unchanged; and
- stale preconditions return `CONFLICT` without producing another effect; and
- an unresolved/lost result remains explicitly `STILL_UNKNOWN`.

The precondition helper checks forwarding, not atomic enforcement. Each domain
must separately prove that its native conditional write rejects stale
preconditions as `CONFLICT`; `assert_stale_preconditions_conflict` supports
that proof when given a stale command and a domain effect counter. Empty-policy
rejection and authorization issuance remain runtime and enterprise-boundary
tests rather than adapter contracts.

The package has no runtime dependencies. It is a callable assertion/template
library, not an auto-discovered pytest suite: invoke its functions inside your
own tests whether the domain is built with raw `Agent`, `AgentApp`, or only an
adapter.

Install it in a test environment:

```bash
pip install -e ./agent-conformance
```

## Adopt it in a domain

1. Copy [`templates/side_effect_conformance.py`](templates/side_effect_conformance.py)
   into the domain's test package and rename it to `test_<domain>_conformance.py`.
2. Replace its callbacks with fixtures and operations owned by that domain.
   The template is intentionally not runnable until those names are supplied.
3. Keep only applicable checks, then add the domain's own recovery and business
   invariant assertions beside them.
4. Run the new test file in the domain's CI job. The framework's own
   [conformance workflow](../.github/workflows/conformance.yml) demonstrates a
   separate job that validates this package and its Delta evidence reference.

For every side-effecting domain, record this short checklist in its README or
test module:

- [ ] stable operation ID and duplicate-effect check;
- [ ] duplicate event-delivery check;
- [ ] durable evidence and restart reconciliation check;
- [ ] explicit `STILL_UNKNOWN` behavior when reconciliation evidence is
      unavailable or inconclusive;
- [ ] precondition forwarding and stale-`CONFLICT` check, when the effect has
      mutable business state;
- [ ] outbox/publish-failure check, when the domain publishes follow-up work;
- [ ] domain-owned recovery and escalation assertions.

The CI workflow cannot infer whether an arbitrary package has side effects.
Adopting the checklist and adding the domain's conformance test to its CI job
is therefore an explicit engineering requirement, not a framework claim that
all domains are automatically covered.

## Minimal adapter contract

Use callable-based checks in your own domain tests. No adapter needs to inherit
from a framework base class or implement a new runtime port.

```python
from agent_conformance import (
    assert_duplicate_operation_is_idempotent,
    assert_restart_reconciles_effect,
)


def test_inventory_conformance():
    assert_duplicate_operation_is_idempotent(
        "reserve-123",
        apply=inventory.reserve,
        effect_count=inventory.effect_count,
    )
    assert_restart_reconciles_effect(
        "reserve-456",
        apply=inventory.reserve,
        reconcile_after_restart=restarted_inventory.reconcile,
    )
```

The supplied checks cover only facts that remain generic across domains:

- one stable operation ID produces one business effect;
- a successful effect can be proven after restart through reconciliation;
- reconciliation uses the closed vocabulary `EXISTS`, `ABSENT`, `CONFLICT`,
  `STILL_UNKNOWN`;
- a pre-acceptance publish failure remains `PENDING` in the application-owned
  outbox;
- duplicate delivery leaves application state unchanged;
- produced messages preserve correlation and causation lineage;
- preconditions are forwarded unchanged to the domain handler; and
- stale preconditions return `CONFLICT` without a second effect; and
- an unresolved/lost result is represented by `STILL_UNKNOWN`, not implied
  success.

Add the relevant checks to CI for every side-effecting domain. Recovery actions,
attempt semantics, retries, compensation, and storage implementation remain
domain-specific tests.
