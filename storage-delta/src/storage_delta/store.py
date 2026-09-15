from __future__ import annotations

from pathlib import Path

import pandas as pd
from deltalake import DeltaTable, write_deltalake

from agent_core.primitives.state import State
from agent_core.ports.state_store import StateStore

from .serialization import StateSerializer


class DeltaStateStore(StateStore):
    def __init__(self, table_path: str | Path) -> None:
        self.table_path = str(table_path)
        self._path = Path(self.table_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_table()

    def _ensure_table(self) -> None:
        if self._path.exists():
            return

        schema_row = pd.DataFrame(
            [
                {
                    "entity_id": "__schema__",
                    "entity_type": "__meta__",
                    "values": "{}",
                    "version": 0,
                    "updated_at": "1970-01-01T00:00:00+00:00",
                }
            ]
        )
        write_deltalake(self._path, data=schema_row, mode="append")

    def get(self, entity_id: str, entity_type: str) -> State | None:
        table = DeltaTable(self._path)
        records = table.to_pandas()
        for _, row in records.iterrows():
            if row.get("entity_id") == entity_id and row.get("entity_type") == entity_type:
                return StateSerializer.from_record(dict(row))
        return None

    def save(self, state: State) -> None:
        record = StateSerializer.to_record(state)
        table = DeltaTable(self._path)
        df = table.to_pandas()
        filtered = df[
            (df["entity_id"].astype(str) != state.entity_id) | (df["entity_type"].astype(str) != state.entity_type)
        ]

        payload = pd.DataFrame([record])
        combined = pd.concat([filtered, payload], ignore_index=True)
        write_deltalake(self._path, data=combined, mode="overwrite")
