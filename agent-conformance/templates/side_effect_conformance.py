"""Copy into a domain test module, rename to ``test_<domain>_conformance.py``, and adapt.

This is deliberately a test template, not an application base class. Replace
the four callbacks below with the domain's own effect, evidence, and recovery
operations. Delete checks that do not apply and add domain-specific recovery
assertions beside them.
"""

from agent_conformance import (
    assert_duplicate_delivery_is_ignored,
    assert_duplicate_operation_is_idempotent,
    assert_missing_result_remains_unknown,
    assert_restart_reconciles_effect,
    assert_stale_preconditions_conflict,
)


def test_reservation_conformance(reservation_domain) -> None:
    """Replace ``reservation_domain`` with a fixture owned by this domain."""
    assert_duplicate_operation_is_idempotent(
        "reserve-duplicate",
        apply=reservation_domain.reserve,
        effect_count=reservation_domain.effect_count,
    )
    assert_restart_reconciles_effect(
        "reserve-restart",
        apply=reservation_domain.reserve,
        reconcile_after_restart=reservation_domain.restarted().reconcile,
    )
    assert_duplicate_delivery_is_ignored(
        deliver=reservation_domain.deliver_same_result_event,
        observed_state=reservation_domain.process_snapshot,
    )


def test_reservation_stale_precondition_does_not_mutate(reservation_domain) -> None:
    """Build the stale command after a competing effect advances domain state."""
    stale_command = reservation_domain.stale_reservation_command()
    assert_stale_preconditions_conflict(
        execute=lambda: reservation_domain.execute(stale_command),
        outcome_of=lambda result: result.status,
        effect_count=reservation_domain.effect_count,
    )


def test_reservation_missing_evidence_is_explicit(reservation_domain) -> None:
    assert_missing_result_remains_unknown(
        "result-lost-before-evidence",
        reconcile=reservation_domain.reconcile,
    )
