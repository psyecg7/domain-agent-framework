# Decisions and actions

A `Decision` records a deterministic domain judgment for one entity. It has a
target, type, severity, reason, time, and stable decision ID.

An `Action` is an instruction to perform an effect. Public code cannot create
an `Action` directly. It must call:

```python
action = decision_to_action(decision, "NOTIFY_OPERATOR")
```

The factory requires a real `Decision` object and copies its entity target into
the Action. `Agent` uses this same path by default.

This prevents a `Recommendation` from being used as an Action accidentally. It
does not prove that every `Decision` in every Python process came from a real
policy: trusted application code can still construct a Decision. For a distrust
boundary between separately deployed Policy and Executor services, use
[`enterprise`](../../enterprise/README.md), which signs and verifies
short-lived, replay-protected authorizations.

`ActionExecutor` is a small port:

```python
executor.execute(action)
```

The executor owns the final side effect. The core does not provide retries,
transactions with external providers, or recovery. A payment API, device, or
third-party HTTP call needs stable idempotency identity and a domain-owned
reconciliation path. See the [external effect boundary](../external-effect-boundary.md).
