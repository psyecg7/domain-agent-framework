# Milestone 2W — Domain-Owned Approval as Policy Evidence

## Scenario

Order evaluates a purchase from four authoritative facts: PIM validity,
Inventory availability, Pricing validity, and a Compliance approval decision.
Order requests `compliance.approval.decide` through the same
`Intent -> CapabilityRegistry -> capability.invocation Event` boundary used
for every other fact. Compliance returns an ordinary
`compliance.approval.result` event containing `approved`, `approver_id`,
`decided_at`, `process_id`, and `operation_id`.

Approval is append-only Compliance evidence keyed by operation ID. Its
vocabulary is `PENDING`, `APPROVED`, `DENIED`, and `CONFLICT`; conflicting
decisions are retained rather than overwritten.

## Business semantics

Order's policy evaluates approval alongside PIM, Inventory, and Pricing facts
on every observation. Approval never bypasses policy:

- an approved decision cannot override an Inventory denial;
- a denied decision prevents purchase even when all other facts are valid;
- missing approval leaves Order waiting for facts;
- conflicting approval evidence produces `CONFLICT`, not an automatic answer.

The human decision uses the same capability/event boundary and records human
causation. `DeltaComplianceApprovalEvidenceStore` now promotes Compliance
evidence to durable, append-only Delta records keyed by operation ID. Approval,
denial, and conflict retain the existing `PENDING`/`APPROVED`/`DENIED`/
`CONFLICT` vocabulary after restart.

## Process semantics

Order owns the meaning of the combined facts (`WAITING_FACTS`,
`CAN_PURCHASE`, `CANNOT_PURCHASE`, or `CONFLICT`). Compliance owns who made an
approval decision and what evidence exists for it. Missing or unregistered
Compliance capability does not become approval or denial; Order remains
unresolved.

`OrderApprovalAssessment` is the forward reference for extending an
approval-required purchase fact set. The earlier three-fact
`OrderPurchaseAssessment` remains a frozen historical experiment and is not
silently retrofitted by this milestone.

## Verified

- Compliance is a distinct capability owner from Order.
- Approval is requested through ordinary capability invocation events.
- Approval is policy input, not a pre-policy gate: policy runs while approval
  is pending and after it arrives.
- Approval cannot override another domain denial, and policy can deny a
  fully-valid three-domain purchase when Compliance denies it.
- Conflicting Compliance evidence is preserved and surfaces `CONFLICT`.
- Human-caused approval evidence keeps its causation through the same event
  boundary.
- Durable approval, denial, and conflicting evidence survive a restart and
  reach the existing Order policy through the ordinary capability/event path.
- A Delta operational read failure becomes the narrow
  `ApprovalEvidenceUnavailable` error; it is not silently converted into a
  new approval outcome or an empty evidence set.
- No generic `Approval`, `ApprovalManager`, or `ApprovalGate` was added to
  `agent-core`.

## Inference

Domain-owned evidence patterns apply to business authorization facts as well
as technical/effect facts. The shared shape does not justify a generic core
approval primitive: the authority, required approvers, expiry, and business
meaning remain Compliance and Order concerns.

## Open questions

- Whether approver requirements, conditions, expiry, and delegation belong in
  Order policy, capability metadata, or a richer Compliance-domain contract.
- `DeltaComplianceApprovalEvidenceStore` has the same single-writer limit as
  the reservation and process stores: it has no cross-process compare-and-swap
  or write-serialization guarantee for the same operation ID.
- How an approval conflict is resolved by the responsible business process.

## What 2W disproved

2W disproved that human approval must be a pre-policy gate, and that
domain-owned evidence patterns apply only to technical/effect facts. It did
not prove a generic approval system or a universal approval policy.

## Core impact

None. The experiment uses existing `Intent`, `Capability`, `Event`, and
application-owned policy/evidence records. Adding a generic approval primitive
would choose ownership and semantics not justified by this one domain slice.

## Validation

The broker-enabled Python 3.12 suite passed with **224 passed, 0 skipped**.
