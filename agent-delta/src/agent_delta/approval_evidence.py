"""Durable, Compliance-owned approval evidence for Order policy facts.

This adapter keeps approval vocabulary and evidence ownership in the Compliance
domain. It deliberately does not add an approval primitive to ``agent-core``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd
from deltalake import DeltaTable, write_deltalake
from deltalake.exceptions import DeltaError


_OUTCOMES = {"PENDING", "APPROVED", "DENIED", "CONFLICT"}


class ApprovalEvidenceUnavailable(RuntimeError):
    """A Delta operational failure prevented Compliance from reading evidence."""


@dataclass(frozen=True)
class ComplianceApprovalDecision:
    """Append-only Compliance evidence; Order never creates this value."""

    decision_id: str
    operation_id: str
    process_id: str
    approved: bool
    approver_id: str
    decided_at: datetime


class DeltaComplianceApprovalEvidenceStore:
    """Append-only approval decisions, keyed by operation ID.

    A repeated ``decision_id`` with identical evidence is idempotent. A reused
    ID with different evidence is rejected as an integrity error. This adapter
    has no cross-process compare-and-swap/write-serialization guarantee;
    deployments with concurrent Compliance writers need an external storage or
    topology-level serialization contract.
    """

    def __init__(self, table_path: str | Path) -> None:
        self.table_path = Path(table_path)
        self.table_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._ensure_table()
        except (DeltaError, OSError) as exc:
            raise ApprovalEvidenceUnavailable("Approval evidence is unavailable") from exc

    def record(self, decision: ComplianceApprovalDecision) -> bool:
        self._validate(decision)
        try:
            records = self._records()
            existing = records[records["decision_id"] == decision.decision_id]
            if not existing.empty:
                if self._from_record(dict(existing.iloc[0])) == decision:
                    return False
                raise ValueError("decision_id is already associated with different approval evidence")
            self._write(pd.concat([records, pd.DataFrame([self._to_record(decision)])], ignore_index=True))
            return True
        except (DeltaError, OSError) as exc:
            raise ApprovalEvidenceUnavailable("Approval evidence is unavailable") from exc

    def decisions_for(self, operation_id: str) -> list[ComplianceApprovalDecision]:
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("operation_id must be a non-empty string")
        try:
            matches = self._records().query("operation_id == @operation_id")
        except (DeltaError, OSError) as exc:
            raise ApprovalEvidenceUnavailable("Approval evidence is unavailable") from exc
        return [self._from_record(dict(record)) for _, record in matches.iterrows()]

    def outcome(self, operation_id: str) -> tuple[str, ComplianceApprovalDecision | None]:
        decisions = self.decisions_for(operation_id)
        if not decisions:
            return "PENDING", None
        if len({decision.approved for decision in decisions}) > 1:
            return "CONFLICT", decisions[-1]
        return ("APPROVED" if decisions[-1].approved else "DENIED"), decisions[-1]

    def _ensure_table(self) -> None:
        if self.table_path.exists():
            return
        write_deltalake(self.table_path, data=pd.DataFrame([self._schema_record()]), mode="append")

    def _records(self) -> pd.DataFrame:
        records = DeltaTable(self.table_path).to_pandas()
        return records[records["decision_id"] != "__schema__"].copy()

    def _write(self, records: pd.DataFrame) -> None:
        write_deltalake(
            self.table_path,
            data=pd.concat([pd.DataFrame([self._schema_record()]), records], ignore_index=True),
            mode="overwrite",
        )

    @staticmethod
    def _validate(decision: ComplianceApprovalDecision) -> None:
        for name in ("decision_id", "operation_id", "process_id", "approver_id"):
            if not isinstance(value := getattr(decision, name), str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if type(decision.approved) is not bool:
            raise TypeError("approved must be a bool")
        if not isinstance(decision.decided_at, datetime):
            raise TypeError("decided_at must be a datetime")

    @staticmethod
    def _to_record(decision: ComplianceApprovalDecision) -> dict[str, str]:
        return {
            "decision_id": decision.decision_id,
            "operation_id": decision.operation_id,
            "process_id": decision.process_id,
            "approved": str(decision.approved).lower(),
            "approver_id": decision.approver_id,
            "decided_at": decision.decided_at.astimezone(timezone.utc).isoformat(),
        }

    @staticmethod
    def _from_record(record: dict[str, Any]) -> ComplianceApprovalDecision:
        return ComplianceApprovalDecision(
            decision_id=str(record["decision_id"]),
            operation_id=str(record["operation_id"]),
            process_id=str(record["process_id"]),
            approved=str(record["approved"]).lower() == "true",
            approver_id=str(record["approver_id"]),
            decided_at=datetime.fromisoformat(str(record["decided_at"])),
        )

    @staticmethod
    def _schema_record() -> dict[str, str]:
        return {
            "decision_id": "__schema__",
            "operation_id": "",
            "process_id": "",
            "approved": "false",
            "approver_id": "",
            "decided_at": "1970-01-01T00:00:00+00:00",
        }


class DurableComplianceApprovalAuthority:
    """Compliance capability authority backed by durable approval evidence."""

    def __init__(self, evidence_store: DeltaComplianceApprovalEvidenceStore) -> None:
        self.evidence_store = evidence_store

    def decide(
        self,
        operation_id: str,
        process_id: str,
        approved: bool,
        approver_id: str,
        *,
        decision_id: str | None = None,
        decided_at: datetime | None = None,
    ) -> ComplianceApprovalDecision:
        decision = ComplianceApprovalDecision(
            decision_id=decision_id or str(uuid4()),
            operation_id=operation_id,
            process_id=process_id,
            approved=approved,
            approver_id=approver_id,
            decided_at=decided_at or datetime.now(timezone.utc),
        )
        self.evidence_store.record(decision)
        return decision

    def outcome(self, operation_id: str) -> tuple[str, ComplianceApprovalDecision | None]:
        return self.evidence_store.outcome(operation_id)


__all__ = [
    "ApprovalEvidenceUnavailable",
    "ComplianceApprovalDecision",
    "DeltaComplianceApprovalEvidenceStore",
    "DurableComplianceApprovalAuthority",
]
