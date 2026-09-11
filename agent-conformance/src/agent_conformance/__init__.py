"""Test-only assertions for domain and adapter conformance."""

from .operations import (
    RECONCILIATION_OUTCOMES,
    assert_duplicate_delivery_is_ignored,
    assert_duplicate_operation_has_one_outbox_message,
    assert_duplicate_operation_is_idempotent,
    assert_external_effect_retries_use_stable_idempotency_key,
    assert_external_reconciliation_resolves_unknown,
    assert_external_transport_failure_remains_unknown,
    assert_failed_atomic_operation_rolls_back,
    assert_failed_publish_remains_pending,
    assert_lineage_is_preserved,
    assert_missing_result_remains_unknown,
    assert_preconditions_are_forwarded,
    assert_reconciliation_outcome,
    assert_restart_reconciles_effect,
    assert_stale_preconditions_conflict,
)

__all__ = [
    "RECONCILIATION_OUTCOMES",
    "assert_duplicate_delivery_is_ignored",
    "assert_duplicate_operation_has_one_outbox_message",
    "assert_duplicate_operation_is_idempotent",
    "assert_external_effect_retries_use_stable_idempotency_key",
    "assert_external_reconciliation_resolves_unknown",
    "assert_external_transport_failure_remains_unknown",
    "assert_failed_atomic_operation_rolls_back",
    "assert_failed_publish_remains_pending",
    "assert_lineage_is_preserved",
    "assert_missing_result_remains_unknown",
    "assert_preconditions_are_forwarded",
    "assert_reconciliation_outcome",
    "assert_restart_reconciles_effect",
    "assert_stale_preconditions_conflict",
]
