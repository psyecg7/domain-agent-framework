import pytest

from agent_conformance import (
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


class Ledger:
    def __init__(self) -> None:
        self.effects: set[str] = set()
        self.outbox = {"release-1": "PENDING"}

    def apply(self, operation_id: str) -> None:
        self.effects.add(operation_id)

    def count(self) -> int:
        return len(self.effects)

    def reconcile(self, operation_id: str) -> str:
        return "EXISTS" if operation_id in self.effects else "ABSENT"

    def fail_publish(self, operation_id: str) -> None:
        raise OSError("broker unavailable")

    def status(self, operation_id: str) -> str:
        return self.outbox[operation_id]


def test_operation_helpers_accept_a_conforming_domain() -> None:
    ledger = Ledger()
    assert_duplicate_operation_is_idempotent("reserve-1", apply=ledger.apply, effect_count=ledger.count)
    assert_restart_reconciles_effect("reserve-2", apply=ledger.apply, reconcile_after_restart=ledger.reconcile)
    assert_reconciliation_outcome("missing", reconcile=ledger.reconcile, expected="ABSENT")
    assert_failed_publish_remains_pending("release-1", publish=ledger.fail_publish, status=ledger.status)


def test_reconciliation_helper_rejects_an_unrecognized_outcome() -> None:
    with pytest.raises(AssertionError, match="unsupported outcome"):
        assert_reconciliation_outcome("operation", reconcile=lambda _: "MAYBE", expected="EXISTS")


def test_delivery_lineage_preconditions_and_unknown_helpers_accept_conforming_application() -> None:
    observed: list[str] = []

    def deliver_once() -> None:
        if "event-1" not in observed:
            observed.append("event-1")

    assert_duplicate_delivery_is_ignored(deliver=deliver_once, observed_state=lambda: tuple(observed))
    assert_lineage_is_preserved(
        produce=lambda: {"metadata": {"correlation_id": "C-1", "causation_id": "E-1"}},
        metadata_of=lambda message: message["metadata"],
        correlation_id="C-1",
        causation_id="E-1",
    )
    assert_preconditions_are_forwarded(
        {"version": 5, "available": 2},
        execute=lambda: {"preconditions": {"version": 5, "available": 2}},
        preconditions_of=lambda command: command["preconditions"],
    )
    assert_missing_result_remains_unknown("missing", reconcile=lambda _: "STILL_UNKNOWN")


def test_stale_precondition_helper_requires_conflict_without_an_effect() -> None:
    effects: list[str] = []

    assert_stale_preconditions_conflict(
        execute=lambda: "CONFLICT",
        outcome_of=lambda outcome: outcome,
        effect_count=lambda: len(effects),
    )

    with pytest.raises(AssertionError, match="mutated the business effect"):
        assert_stale_preconditions_conflict(
            execute=lambda: (effects.append("incorrect-effect") or "CONFLICT"),
            outcome_of=lambda outcome: outcome,
            effect_count=lambda: len(effects),
        )


def test_duplicate_delivery_helper_rejects_a_second_state_transition() -> None:
    observed: list[str] = []

    with pytest.raises(AssertionError, match="Duplicate delivery changed"):
        assert_duplicate_delivery_is_ignored(
            deliver=lambda: observed.append("event-1"),
            observed_state=lambda: tuple(observed),
        )


def test_atomicity_helpers_accept_one_outbox_message_and_full_rollback() -> None:
    pending: list[str] = []
    applied: list[str] = []

    def execute(operation_id: str) -> None:
        if operation_id not in applied:
            applied.append(operation_id)
            pending.append(f"event:{operation_id}")

    assert_duplicate_operation_has_one_outbox_message(
        "reserve-1",
        execute=execute,
        pending_messages=lambda: tuple(pending),
        message_identity=lambda message: message,
    )

    def fail_after_mutation() -> None:
        applied.append("rollback")
        pending.append("event:rollback")
        applied.remove("rollback")
        pending.remove("event:rollback")
        raise RuntimeError("transaction rolled back")

    assert_failed_atomic_operation_rolls_back(
        execute=fail_after_mutation,
        observed_state=lambda: tuple(applied),
        pending_messages=lambda: tuple(pending),
    )


def test_external_effect_helpers_require_stable_identity_unknown_and_reconciliation() -> None:
    provider_keys: list[str] = []
    status = {"payment-1": "PENDING", "payment-timeout": "PENDING"}

    def accepted_attempt(operation_id: str) -> None:
        provider_keys.append(operation_id)

    assert_external_effect_retries_use_stable_idempotency_key(
        "payment-1",
        attempt=accepted_attempt,
        observed_idempotency_keys=lambda: tuple(provider_keys),
    )

    def timeout(operation_id: str) -> None:
        provider_keys.append(operation_id)
        status[operation_id] = "UNKNOWN"
        raise TimeoutError("provider result was lost")

    assert_external_transport_failure_remains_unknown(
        "payment-timeout",
        attempt=timeout,
        status=lambda operation_id: status[operation_id],
    )
    assert_external_reconciliation_resolves_unknown(
        "payment-timeout",
        status=lambda operation_id: status[operation_id],
        reconcile=lambda operation_id: status.__setitem__(operation_id, "SUCCEEDED"),
        expected_status="SUCCEEDED",
    )


def test_external_effect_helpers_reject_unsafe_retry_and_guessed_result() -> None:
    provider_keys: list[str] = []

    def unsafe_attempt(operation_id: str) -> None:
        provider_keys.append(f"{operation_id}-{len(provider_keys)}")

    with pytest.raises(AssertionError, match="same operation ID"):
        assert_external_effect_retries_use_stable_idempotency_key(
            "payment-unsafe",
            attempt=unsafe_attempt,
            observed_idempotency_keys=lambda: tuple(provider_keys),
        )

    with pytest.raises(AssertionError, match="must remain UNKNOWN"):
        assert_external_transport_failure_remains_unknown(
            "payment-guessed",
            attempt=lambda operation_id: None,
            status=lambda operation_id: "SUCCEEDED",
        )

    with pytest.raises(AssertionError, match="expected 'SUCCEEDED'"):
        assert_external_reconciliation_resolves_unknown(
            "payment-unresolved",
            status=lambda operation_id: "UNKNOWN",
            reconcile=lambda operation_id: None,
            expected_status="SUCCEEDED",
        )
