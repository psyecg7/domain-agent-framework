from __future__ import annotations

import argparse

from .migrations import migrate_agent_postgres


def main() -> None:
    parser = argparse.ArgumentParser(description="Upgrade agent-postgres schemas")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--table-prefix", default="agent_atomic")
    parser.add_argument("--state-table", default="agent_states")
    parser.add_argument("--receipt-table", default="agent_event_receipts")
    args = parser.parse_args()
    applied = migrate_agent_postgres(
        args.database_url, table_prefix=args.table_prefix, state_table=args.state_table, receipt_table=args.receipt_table,
    )
    print("applied migrations:" if applied else "schema already current", *applied)


if __name__ == "__main__":
    main()
