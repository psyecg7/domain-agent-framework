"""Enterprise authorization audit evidence; retention is external."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from deltalake import DeltaTable, write_deltalake


class DeltaAuthorizationAuditStore:
    """Durable Delta audit evidence. Never stores bearer tokens or private keys.

    Each record is written using Delta's append mode; it never rebuilds and
    overwrites the table from a stale read. This permits Policy and Executor
    to share a table without the old read/overwrite lost-row race. A failed
    concurrent Delta transaction is surfaced to the caller rather than being
    silently converted into a missing audit record.

    This is not an immutable compliance ledger: table retention, access
    control, and protection against privileged rewrites remain external
    governance responsibilities.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            write_deltalake(self.path, data=pd.DataFrame([self._schema()]), mode="append")

    def record(self, outcome: str, *, principal_id: str | None = None, claims: dict[str, Any] | None = None, detail: str | None = None) -> None:
        safe = dict(claims or {})
        safe.pop("signature", None)
        safe.pop("token", None)
        row = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "outcome": outcome,
            "principal_id": principal_id or "",
            "claims": json.dumps(safe, sort_keys=True),
            "detail": detail or "",
        }
        write_deltalake(self.path, data=pd.DataFrame([row]), mode="append")

    @staticmethod
    def _schema() -> dict[str, str]:
        return {"recorded_at": "1970-01-01T00:00:00+00:00", "outcome": "__schema__", "principal_id": "", "claims": "{}", "detail": ""}
