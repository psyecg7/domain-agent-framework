"""Small callable-based checks; these are not runtime interfaces or ports."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


RECONCILIATION_OUTCOMES = frozenset({"EXISTS", "ABSENT", "CONFLICT", "STILL_UNKNOWN"})


def assert_duplicate_operation_is_idempotent(
    operation_id: str,
    *,
    apply: Callable[[str], Any],
    effect_count: Callable[[], int],
) -> None:
    """Assert one logical operation causes exactly one business effect.

    ``apply`` deliberately has no prescribed return type: domain adapters
    retain their own result vocabulary. The assertion only checks the durable
    business fact that duplicate delivery did not create a second effect.
    """
    before = effect_count()
    apply(operation_id)
    after_first = effect_count()
    apply(operation_id)
    after_second = effect_count()
    if after_first != before + 1:
        raise AssertionError(
            f"Operation {operation_id!r} did not produce exactly one initial effect: "
            f"before={before}, after_first={after_first}"
        )
    if after_second != after_first:
        raise AssertionError(
            f"Duplicate operation {operation_id!r} produced another effect: "
            f"after_first={after_first}, after_second={after_second}"
        )


def assert_restart_reconciles_effect(
    operation_id: str,
    *,
    apply: Callable[[str], Any],
    reconcile_after_restart: Callable[[str], str],
) -> None:
    """Assert an effect survives a lost result and is proven after restart."""
    apply(operation_id)
    assert_reconciliation_outcome(
        operation_id,
        reconcile=reconcile_after_restart,
        expected="EXISTS",
    )


def assert_reconciliation_outcome(
    operation_id: str,
    *,
    reconcile: Callable[[str], str],
    expected: str,
) -> None:
    """Assert one authoritative reconciliation answer from the closed vocabulary."""
    if expected not in RECONCILIATION_OUTCOMES:
        raise ValueError(f"Expected outcome must be one of {sorted(RECONCILIATION_OUTCOMES)!r}")
    actual = reconcile(operation_id)
    if actual not in RECONCILIATION_OUTCOMES:
        raise AssertionError(
            f"Reconciliation returned unsupported outcome {actual!r}; "
            f"expected one of {sorted(RECONCILIATION_OUTCOMES)!r}"
        )
    if actual != expected:
        raise AssertionError(
            f"Reconciliation for {operation_id!r} returned {actual!r}, expected {expected!r}"
        )


def assert_failed_publish_remains_pending(
    operation_id: str,
    *,
    publish: Callable[[str], Any],
    status: Callable[[str], str],
) -> None:
    """Assert a pre-acceptance publish failure leaves a durable request pending.

    The application owns the outbox implementation and retry policy. This
    helper only verifies the invariant that a failed publish is not marked as
    delivered or confirmed.
    """
    try:
        publish(operation_id)
    except Exception:
        pass
    else:
        raise AssertionError("publish must fail to exercise the pending-outbox contract")
    actual = status(operation_id)
    if actual != "PENDING":
        raise AssertionError(
            f"Failed publish for {operation_id!r} must remain PENDING, got {actual!r}"
        )


def assert_duplicate_delivery_is_ignored(
    *,
    deliver: Callable[[], Any],
    observed_state: Callable[[], Any],
) -> None:
    """Assert redelivery of one event does not change observed application state.

    ``deliver`` must submit the *same* event identity on both calls. The
    application chooses its event representation and snapshot type; the helper
    only checks that the second delivery is a no-op.
    """
    deliver()
    after_first = observed_state()
    deliver()
    after_duplicate = observed_state()
    if after_duplicate != after_first:
        raise AssertionError(
            "Duplicate delivery changed observed state: "
            f"after_first={after_first!r}, after_duplicate={after_duplicate!r}"
        )


def assert_lineage_is_preserved(
    *,
    produce: Callable[[], Any],
    metadata_of: Callable[[Any], Mapping[str, Any]],
    correlation_id: str,
    causation_id: str,
) -> None:
    """Assert a produced message preserves the specified correlation lineage."""
    produced = produce()
    metadata = metadata_of(produced)
    actual_correlation = metadata.get("correlation_id")
    actual_causation = metadata.get("causation_id")
    if actual_correlation != correlation_id or actual_causation != causation_id:
        raise AssertionError(
            "Produced message did not preserve lineage: "
            f"correlation_id={actual_correlation!r}, causation_id={actual_causation!r}"
        )


def assert_preconditions_are_forwarded(
    expected: Mapping[str, Any],
    *,
    execute: Callable[[], Any],
    preconditions_of: Callable[[Any], Mapping[str, Any]],
) -> None:
    """Assert an executor receives exactly the domain preconditions it was given.

    This proves transport/binding preservation only. The owning domain must
    separately prove atomic enforcement and its ``CONFLICT`` behavior.
    """
    received = preconditions_of(execute())
    if dict(received) != dict(expected):
        raise AssertionError(
            f"Preconditions were altered in transit: received={dict(received)!r}, "
            f"expected={dict(expected)!r}"
        )


def assert_stale_preconditions_conflict(
    *,
    execute: Callable[[], Any],
    outcome_of: Callable[[Any], str],
    effect_count: Callable[[], int],
) -> None:
    """Assert a stale conditional effect returns ``CONFLICT`` without mutation.

    ``execute`` must submit a command whose state assumptions have already
    been invalidated. The domain owns its conditional write; this helper
    verifies only the externally observable contract.
    """
    before = effect_count()
    outcome = outcome_of(execute())
    after = effect_count()
    if outcome != "CONFLICT":
        raise AssertionError(f"Stale preconditions must return CONFLICT, got {outcome!r}")
    if after != before:
        raise AssertionError(
            "Stale preconditions mutated the business effect: "
            f"before={before}, after={after}"
        )


def assert_missing_result_remains_unknown(
    operation_id: str,
    *,
    reconcile: Callable[[str], str],
) -> None:
    """Assert an unresolved result becomes explicit ``STILL_UNKNOWN``.

    An authoritative query that proves no effect exists must instead return
    ``ABSENT``. This helper is for the distinct case where reconciliation
    cannot establish a result from its available evidence.
    """
    assert_reconciliation_outcome(
        operation_id,
        reconcile=reconcile,
        expected="STILL_UNKNOWN",
    )
