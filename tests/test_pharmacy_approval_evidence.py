"""Milestone 2W: Compliance-owned approval as an Order policy fact."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import inspect

import pytest

from agent_application import CapabilityInvoker, UnknownCapabilityError
from agent_core import Capability, Event, InMemoryCapabilityRegistry, Intent
from agent_delta import (
    ApprovalEvidenceUnavailable,
    DeltaComplianceApprovalEvidenceStore,
    DurableComplianceApprovalAuthority,
)


class Transport:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self.handlers: dict[str, list] = {}

    def subscribe(self, event_type: str, handler) -> None:
        self.handlers.setdefault(event_type, []).append(handler)

    def publish(self, event: Event) -> None:
        self.events.append(event)
        for handler in self.handlers.get(event.event_type, []):
            handler(event)


@dataclass(frozen=True)
class ComplianceDecision:
    operation_id: str
    process_id: str
    approved: bool
    approver_id: str
    decided_at: datetime


@dataclass
class ComplianceApprovalAuthority:
    """Append-only Compliance evidence; Order cannot set this fact itself."""

    decisions: dict[str, list[ComplianceDecision]] = field(default_factory=dict)

    def decide(self, operation_id: str, process_id: str, approved: bool, approver_id: str) -> ComplianceDecision:
        decision = ComplianceDecision(
            operation_id, process_id, approved, approver_id, datetime.now(timezone.utc)
        )
        self.decisions.setdefault(operation_id, []).append(decision)
        return decision

    def outcome(self, operation_id: str) -> tuple[str, ComplianceDecision | None]:
        decisions = self.decisions.get(operation_id, [])
        if not decisions:
            return "PENDING", None
        if len({decision.approved for decision in decisions}) > 1:
            return "CONFLICT", decisions[-1]
        return ("APPROVED" if decisions[-1].approved else "DENIED"), decisions[-1]


class OrderPurchasePolicy:
    """Order-owned policy: approval is one fact, never a pre-policy gate."""

    required_facts = ("pim", "inventory", "pricing", "approval")

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def evaluate(self, facts: dict[str, object]) -> str:
        self.calls.append(dict(facts))
        if facts.get("approval") == "CONFLICT":
            return "CONFLICT"
        if any(fact not in facts for fact in self.required_facts):
            return "WAITING_FACTS"
        return "CAN_PURCHASE" if all(facts[fact] is True for fact in self.required_facts) else "CANNOT_PURCHASE"


class OrderApprovalAssessment:
    """Order-specific composition, not a generic approval or workflow runtime."""

    requirements = {
        "pim": "product.information.retrieve",
        "inventory": "inventory.availability.check",
        "pricing": "pricing.price.retrieve",
        "approval": "compliance.approval.decide",
    }

    def __init__(self, invoker: CapabilityInvoker, transport: Transport, policy: OrderPurchasePolicy) -> None:
        self.invoker = invoker
        self.transport = transport
        self.policy = policy
        self.facts: dict[str, dict[str, object]] = {}
        self.status: dict[str, str] = {}
        for event_type in (
            "pim.product.result",
            "inventory.availability.result",
            "pricing.price.result",
            "compliance.approval.result",
        ):
            transport.subscribe(event_type, self.observe)

    def request(self, process_id: str, correlation_id: str, *, causation_id: str = "order-assessment") -> None:
        operation_id = f"{process_id}:compliance.approval"
        self.status.setdefault(correlation_id, "WAITING_FACTS")
        for requirement, intent_type in self.requirements.items():
            self.invoker.invoke(
                Intent(intent_type, {"process_id": process_id, "operation_id": operation_id}),
                entity_id=process_id,
                entity_type="order_process",
                context={"correlation_id": correlation_id, "causation_id": causation_id},
            )

    def observe(self, event: Event) -> None:
        correlation_id = event.metadata.get("correlation_id")
        if not isinstance(correlation_id, str):
            return
        facts = self.facts.setdefault(correlation_id, {})
        if event.event_type == "compliance.approval.result":
            status = event.payload.get("status")
            facts["approval"] = True if status == "APPROVED" else "CONFLICT" if status == "CONFLICT" else False
        else:
            facts[str(event.payload["domain"])] = event.payload.get("valid") is True
        self.status[correlation_id] = self.policy.evaluate(facts)


def capability(capability_id: str, owner: str, intent_types: tuple[str, ...] = ()) -> Capability:
    return Capability(
        capability_id, capability_id, f"{owner} fact", metadata={"owner": owner, "intent_types": intent_types}
    )


def build(
    *,
    pim: bool = True,
    inventory: bool = True,
    pricing: bool = True,
    include_approval: bool = True,
    approval_authority=None,
):
    registry = InMemoryCapabilityRegistry()
    registry.register(capability("product.information.retrieve", "pim"))
    registry.register(capability("inventory.availability.check", "inventory"))
    registry.register(capability("pricing.price.retrieve", "pricing"))
    if include_approval:
        registry.register(capability("compliance.approval.decide", "compliance"))
    transport = Transport()
    authority = approval_authority or ComplianceApprovalAuthority()
    policy = OrderPurchasePolicy()
    assessment = OrderApprovalAssessment(CapabilityInvoker(registry, transport), transport, policy)

    def route(event: Event) -> None:
        capability_id = event.payload["capability_id"]
        intent = event.payload["intent"]
        correlation_id = event.metadata["correlation_id"]
        if capability_id == "compliance.approval.decide":
            operation_id = intent["parameters"]["operation_id"]
            status, decision = authority.outcome(operation_id)
            if status == "PENDING":
                return
            transport.publish(Event(
                "compliance.approval.result", event.entity_id, "order_process",
                {
                    "operation_id": operation_id,
                    "process_id": intent["parameters"]["process_id"],
                    "approved": status == "APPROVED",
                    "status": status,
                    "approver_id": decision.approver_id,
                    "decided_at": decision.decided_at.isoformat(),
                },
                source="compliance",
                metadata={"correlation_id": correlation_id, "causation_id": event.metadata["causation_id"]},
            ))
            return
        valid = {"product.information.retrieve": pim, "inventory.availability.check": inventory,
                 "pricing.price.retrieve": pricing}[capability_id]
        domain = {"product.information.retrieve": "pim", "inventory.availability.check": "inventory",
                  "pricing.price.retrieve": "pricing"}[capability_id]
        event_type = {"pim": "pim.product.result", "inventory": "inventory.availability.result",
                      "pricing": "pricing.price.result"}[domain]
        transport.publish(Event(
            event_type, event.entity_id, "order_process", {"domain": domain, "valid": valid},
            source=domain, metadata={"correlation_id": correlation_id, "causation_id": event.event_id},
        ))

    transport.subscribe("capability.invocation", route)
    return assessment, authority, transport, policy


def approve(authority: ComplianceApprovalAuthority, process_id: str = "process-1", approved: bool = True) -> None:
    authority.decide(f"{process_id}:compliance.approval", process_id, approved, "human-approver")


def test_approval_does_not_override_inventory_denial() -> None:
    assessment, authority, _, policy = build(inventory=False)
    approve(authority)

    assessment.request("process-1", "C1")

    assert assessment.status["C1"] == "CANNOT_PURCHASE"
    assert policy.calls


def test_policy_denies_when_all_domain_facts_pass_but_approval_is_denied() -> None:
    assessment, authority, _, policy = build()
    approve(authority, approved=False)

    assessment.request("process-1", "C1")

    assert assessment.status["C1"] == "CANNOT_PURCHASE"
    assert policy.calls[-1]["approval"] is False


def test_pending_approval_keeps_all_other_valid_facts_waiting() -> None:
    assessment, _, _, policy = build()

    assessment.request("process-1", "C1")

    assert assessment.status["C1"] == "WAITING_FACTS"
    assert policy.calls
    assert "approval" not in policy.calls[-1]


def test_conflicting_approval_evidence_is_preserved_and_does_not_proceed() -> None:
    assessment, authority, _, _ = build()
    approve(authority, approved=True)
    approve(authority, approved=False)

    assessment.request("process-1", "C1")

    assert assessment.status["C1"] == "CONFLICT"
    assert len(authority.decisions["process-1:compliance.approval"]) == 2


def test_missing_approval_capability_is_unresolved_not_a_substitute_fact() -> None:
    assessment, authority, transport, _ = build(include_approval=False)
    approve(authority)

    with pytest.raises(UnknownCapabilityError):
        assessment.request("process-1", "C1")

    assert assessment.status["C1"] == "WAITING_FACTS"
    assert not any(event.metadata.get("owner") == "compliance" for event in transport.events)


def test_human_decision_uses_the_same_capability_event_boundary() -> None:
    assessment, authority, transport, _ = build()
    approve(authority)

    assessment.request("process-1", "C1", causation_id="human-intervention")

    result = next(event for event in transport.events if event.event_type == "compliance.approval.result")
    assert result.metadata["causation_id"] == "human-intervention"
    assert result.payload["approver_id"] == "human-approver"


def test_no_generic_approval_primitive_was_added_to_agent_core() -> None:
    import agent_core

    source = inspect.getsource(agent_core)
    assert "ApprovalManager" not in source
    assert "ApprovalGate" not in source
    assert not hasattr(agent_core, "Approval")


def test_durable_approved_evidence_survives_restart_through_the_policy_event_path(tmp_path) -> None:
    operation_id = "process-1:compliance.approval"
    table_path = tmp_path / "compliance-approval"
    original = DurableComplianceApprovalAuthority(DeltaComplianceApprovalEvidenceStore(table_path))
    original.decide(operation_id, "process-1", True, "human-approver", decision_id="APPROVAL-1")

    restarted = DurableComplianceApprovalAuthority(DeltaComplianceApprovalEvidenceStore(table_path))
    assessment, _, transport, policy = build(approval_authority=restarted)
    assessment.request("process-1", "C1")

    result = next(event for event in transport.events if event.event_type == "compliance.approval.result")
    assert result.payload["status"] == "APPROVED"
    assert result.payload["approver_id"] == "human-approver"
    assert assessment.status["C1"] == "CAN_PURCHASE"
    assert policy.calls[-1]["approval"] is True


def test_durable_denial_survives_restart_and_policy_still_denies(tmp_path) -> None:
    operation_id = "process-1:compliance.approval"
    table_path = tmp_path / "compliance-denial"
    DurableComplianceApprovalAuthority(DeltaComplianceApprovalEvidenceStore(table_path)).decide(
        operation_id, "process-1", False, "human-approver", decision_id="DENIAL-1"
    )

    restarted = DurableComplianceApprovalAuthority(DeltaComplianceApprovalEvidenceStore(table_path))
    assessment, _, _, policy = build(approval_authority=restarted)
    assessment.request("process-1", "C1")

    assert assessment.status["C1"] == "CANNOT_PURCHASE"
    assert policy.calls[-1]["approval"] is False


def test_durable_conflicting_evidence_survives_restart_and_remains_conflict(tmp_path) -> None:
    operation_id = "process-1:compliance.approval"
    table_path = tmp_path / "compliance-conflict"
    original = DurableComplianceApprovalAuthority(DeltaComplianceApprovalEvidenceStore(table_path))
    original.decide(operation_id, "process-1", True, "human-approver", decision_id="APPROVAL-1")
    original.decide(operation_id, "process-1", False, "compliance-officer", decision_id="DENIAL-1")

    restarted = DurableComplianceApprovalAuthority(DeltaComplianceApprovalEvidenceStore(table_path))
    assessment, _, _, _ = build(approval_authority=restarted)
    assessment.request("process-1", "C1")

    assert assessment.status["C1"] == "CONFLICT"
    assert len(restarted.evidence_store.decisions_for(operation_id)) == 2


def test_durable_approval_store_exposes_only_operational_read_failures_as_unavailable(tmp_path, monkeypatch) -> None:
    store = DeltaComplianceApprovalEvidenceStore(tmp_path / "compliance-unavailable")

    def unavailable():
        raise OSError("disk unavailable")

    monkeypatch.setattr(store, "_records", unavailable)
    with pytest.raises(ApprovalEvidenceUnavailable):
        store.outcome("process-1:compliance.approval")
    with pytest.raises(ValueError):
        store.outcome("")
