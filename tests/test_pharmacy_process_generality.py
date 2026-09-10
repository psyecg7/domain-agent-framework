from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import inspect

from agent_core import Event
from test_pharmacy_process_semantics import PurchaseProcess


@dataclass
class PimSnapshot:
    process_id: str
    product_id: str
    product_version: str
    correlation_id: str
    status: str
    validation: dict[str, str] = field(default_factory=dict)
    operations: dict[str, dict[str, Any]] = field(default_factory=dict)
    applied_event_ids: set[str] = field(default_factory=set)
    recovery_reason: str | None = None


class ProductPublicationProcess:
    """PIM-specific publication lifecycle, intentionally unrelated to Order process code."""

    REQUIRED_VALIDATIONS = ("identity", "content")
    TARGETS = ("catalog", "search")

    def __init__(
        self,
        process_id: str,
        product_id: str,
        product_version: str,
        correlation_id: str,
        *,
        snapshot: PimSnapshot | None = None,
    ) -> None:
        self.process_id = process_id
        self.product_id = product_id
        self.product_version = product_version
        self.correlation_id = correlation_id
        self.status = "PENDING_VALIDATION"
        self.validation: dict[str, str] = {}
        self.operations: dict[str, dict[str, Any]] = {}
        self.applied_event_ids: set[str] = set()
        self.recovery_reason: str | None = None
        if snapshot is not None:
            self._restore(snapshot)

    def observe(self, event: Event) -> None:
        if event.metadata.get("correlation_id") != self.correlation_id:
            return
        if event.event_id in self.applied_event_ids:
            return
        self.applied_event_ids.add(event.event_id)

        if event.event_type == "pim.validation.result":
            validation = event.payload.get("validation")
            if validation in self.REQUIRED_VALIDATIONS:
                self.validation.setdefault(
                    validation,
                    "VALID" if event.payload.get("valid") is True else "INVALID",
                )
            if any(value == "INVALID" for value in self.validation.values()):
                self.status = "CORRECTION_REQUIRED"
            elif len(self.validation) == len(self.REQUIRED_VALIDATIONS):
                self.status = "READY_TO_PUBLISH"
            return

        if event.event_type == "pim.publication.requested":
            operation_id = event.payload.get("operation_id")
            target = event.payload.get("target")
            if isinstance(operation_id, str) and target in self.TARGETS:
                self.operations.setdefault(
                    operation_id,
                    {"target": target, "status": "PENDING", "attempts": 1},
                )
                self.status = "PUBLISHING"
            return

        if event.event_type == "pim.publication.result":
            operation_id = event.payload.get("operation_id")
            if not isinstance(operation_id, str) or operation_id not in self.operations:
                self.status = "RECOVERY_REQUIRED"
                self.recovery_reason = "Publication result arrived without a request"
                return
            operation = self.operations[operation_id]
            if operation["status"] in {"PUBLISHED", "FAILED"}:
                return
            operation["status"] = "PUBLISHED" if event.payload.get("published") is True else "FAILED"
            operation["last_event_id"] = event.event_id
            operation["last_causation_id"] = event.metadata.get("causation_id")
            statuses = [value["status"] for value in self.operations.values()]
            if len(statuses) == len(self.TARGETS) and all(status == "PUBLISHED" for status in statuses):
                self.status = "PUBLISHED"
            elif any(status == "PUBLISHED" for status in statuses):
                self.status = "PARTIALLY_PUBLISHED"
            elif any(status == "FAILED" for status in statuses):
                self.status = "CORRECTION_REQUIRED"

    def begin_publication(self) -> None:
        if self.status != "READY_TO_PUBLISH":
            raise ValueError("Product is not ready to publish")
        self.status = "PUBLISHING"
        for target in self.TARGETS:
            operation_id = f"{self.process_id}:{self.product_version}:{target}"
            self.operations.setdefault(
                operation_id,
                {"target": target, "status": "PENDING", "attempts": 1},
            )

    def request_recovery(self, reason: str) -> None:
        if self.status not in {"PARTIALLY_PUBLISHED", "CORRECTION_REQUIRED", "RECOVERY_REQUIRED"}:
            raise ValueError("Publication does not require recovery")
        self.status = "RECOVERY_REQUIRED"
        self.recovery_reason = reason

    def snapshot(self) -> PimSnapshot:
        return PimSnapshot(
            process_id=self.process_id,
            product_id=self.product_id,
            product_version=self.product_version,
            correlation_id=self.correlation_id,
            status=self.status,
            validation=dict(self.validation),
            operations={key: dict(value) for key, value in self.operations.items()},
            applied_event_ids=set(self.applied_event_ids),
            recovery_reason=self.recovery_reason,
        )

    def _restore(self, snapshot: PimSnapshot) -> None:
        if snapshot.process_id != self.process_id or snapshot.correlation_id != self.correlation_id:
            raise ValueError("Publication snapshot identity does not match")
        self.status = snapshot.status
        self.validation = dict(snapshot.validation)
        self.operations = {key: dict(value) for key, value in snapshot.operations.items()}
        self.applied_event_ids = set(snapshot.applied_event_ids)
        self.recovery_reason = snapshot.recovery_reason


def pim_event(event_type: str, correlation_id: str, payload: dict[str, Any], causation_id: str = "source-event") -> Event:
    return Event(
        event_type=event_type,
        entity_id="P123",
        entity_type="product",
        payload=payload,
        source="pim",
        metadata={"correlation_id": correlation_id, "causation_id": causation_id},
    )


def make_process(correlation_id: str = "publication-1", version: str = "v7") -> ProductPublicationProcess:
    return ProductPublicationProcess("publication-1", "P123", version, correlation_id)


def ready_process() -> ProductPublicationProcess:
    process = make_process()
    process.observe(pim_event("pim.validation.result", process.correlation_id, {"validation": "content", "valid": True}))
    process.observe(pim_event("pim.validation.result", process.correlation_id, {"validation": "identity", "valid": True}))
    return process


def publication_result(process: ProductPublicationProcess, target: str, published: bool, causation_id: str = "publish-action") -> Event:
    return pim_event(
        "pim.publication.result",
        process.correlation_id,
        {
            "operation_id": f"{process.process_id}:{process.product_version}:{target}",
            "target": target,
            "published": published,
        },
        causation_id,
    )


def test_pim_process_has_independent_identity() -> None:
    process = make_process()
    event = pim_event("pim.validation.result", process.correlation_id, {"validation": "identity", "valid": True})

    assert process.process_id != process.product_id
    assert process.process_id != event.event_id
    assert process.correlation_id != event.event_id
    assert process.correlation_id == event.metadata["correlation_id"]


def test_pim_operation_identity_is_stable() -> None:
    process = ready_process()
    process.begin_publication()

    assert set(process.operations) == {"publication-1:v7:catalog", "publication-1:v7:search"}
    assert process.operations["publication-1:v7:catalog"]["target"] == "catalog"


def test_duplicate_operation_does_not_repeat_business_effect() -> None:
    process = ready_process()
    process.begin_publication()
    requested = pim_event(
        "pim.publication.requested",
        process.correlation_id,
        {"operation_id": "publication-1:v7:catalog", "target": "catalog"},
    )
    process.observe(requested)
    process.observe(requested)

    assert len(process.operations) == 2
    assert process.operations["publication-1:v7:catalog"]["attempts"] == 1


def test_pim_partial_completion_is_explicit() -> None:
    process = ready_process()
    process.begin_publication()
    process.observe(publication_result(process, "catalog", True))
    process.observe(publication_result(process, "search", False))

    assert process.status == "PARTIALLY_PUBLISHED"
    assert process.operations["publication-1:v7:catalog"]["status"] == "PUBLISHED"
    assert process.operations["publication-1:v7:search"]["status"] == "FAILED"


def test_pim_recovery_is_business_semantics() -> None:
    process = ready_process()
    process.begin_publication()
    process.observe(publication_result(process, "catalog", True))
    process.observe(publication_result(process, "search", False))
    process.request_recovery("Correct publication before retry")

    assert process.status == "RECOVERY_REQUIRED"
    assert process.recovery_reason == "Correct publication before retry"


def test_pim_process_restart_preserves_progress() -> None:
    process = ready_process()
    process.begin_publication()
    catalog_result = publication_result(process, "catalog", True)
    process.observe(catalog_result)
    restarted = ProductPublicationProcess(
        process.process_id,
        process.product_id,
        process.product_version,
        process.correlation_id,
        snapshot=process.snapshot(),
    )
    restarted.observe(catalog_result)
    restarted.observe(publication_result(restarted, "search", True))

    assert restarted.process_id == process.process_id
    assert restarted.operations["publication-1:v7:catalog"]["status"] == "PUBLISHED"
    assert restarted.status == "PUBLISHED"


def test_pim_out_of_order_result_is_handled() -> None:
    process = ready_process()
    process.begin_publication()
    process.observe(publication_result(process, "search", True))
    process.observe(publication_result(process, "catalog", True))

    assert process.status == "PUBLISHED"


def test_pim_missing_result_does_not_create_false_success() -> None:
    process = ready_process()
    process.begin_publication()
    process.observe(publication_result(process, "catalog", True))

    assert process.status == "PARTIALLY_PUBLISHED"
    assert process.operations["publication-1:v7:search"]["status"] == "PENDING"
    assert process.status != "PUBLISHED"


def test_pim_process_state_does_not_duplicate_product_state() -> None:
    process = ready_process()
    snapshot = process.snapshot()

    assert snapshot.validation == {"content": "VALID", "identity": "VALID"}
    assert not hasattr(snapshot, "product_name")
    assert not hasattr(snapshot, "product_attributes")
    assert process.product_id == "P123"


def test_pim_process_remains_event_driven() -> None:
    process = make_process()
    source = inspect.getsource(ProductPublicationProcess)

    process.observe(pim_event("pim.validation.result", process.correlation_id, {"validation": "identity", "valid": True}))
    assert process.validation["identity"] == "VALID"
    assert ".process(" not in source
    assert "Agent" not in source
    assert "PurchaseProcess" not in source


def test_order_and_pim_semantics_can_be_compared_without_shared_process_abstraction() -> None:
    order = PurchaseProcess("order-process", "ORD-1", "P123", "order-correlation")
    pim = make_process("pim-correlation")

    assert type(order) is not type(pim)
    assert hasattr(order, "operations") and hasattr(pim, "operations")
    assert hasattr(order, "applied_event_ids") and hasattr(pim, "applied_event_ids")
    assert order.OPERATION_KINDS != pim.TARGETS
    assert "reservation" not in pim.TARGETS
    assert "publication" not in order.OPERATION_KINDS
