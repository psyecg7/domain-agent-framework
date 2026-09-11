from __future__ import annotations

import argparse

from .migrations import migrate_enterprise_authorization


def main() -> None:
    parser = argparse.ArgumentParser(description="Upgrade Enterprise authorization schemas")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--replay-table", default="agent_authorization_replay")
    parser.add_argument("--revocation-table", default="agent_authorization_revocations")
    args = parser.parse_args()
    applied = migrate_enterprise_authorization(
        args.database_url, replay_table=args.replay_table, revocation_table=args.revocation_table,
    )
    print("applied migrations:" if applied else "schema already current", *applied)


if __name__ == "__main__":
    main()
