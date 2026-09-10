"""Local Executor process entry point; it accepts only Policy public keys."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from .authorization import AuthorizedCommandExecutor, ExecutorAuthorizationVerifier
from .delta_replay import DeltaReplayStore
from .audit import DeltaAuthorizationAuditStore
from .http_reference import executor_server

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-key", required=True, type=Path)
    parser.add_argument("--replay-table", required=True, type=Path)
    parser.add_argument("--effect-log", required=True, type=Path)
    parser.add_argument("--key-id", default="local-policy-key")
    parser.add_argument("--audience", default="local-order-executor")
    parser.add_argument("--port", type=int, default=8082)
    parser.add_argument("--audit-table", type=Path)
    args = parser.parse_args()
    verifier = ExecutorAuthorizationVerifier.from_pem(
        {args.key_id: args.public_key.read_bytes()}, audience=args.audience, replay_store=DeltaReplayStore(args.replay_table)
    )
    def effect(command) -> None:
        with args.effect_log.open("a", encoding="utf-8") as log:
            log.write(json.dumps(command.wire(), sort_keys=True) + "\n")
    audit = DeltaAuthorizationAuditStore(args.audit_table) if args.audit_table else None
    executor_server(AuthorizedCommandExecutor(verifier, effect), audit_store=audit, port=args.port).serve_forever()

if __name__ == "__main__":
    main()
