"""Local Executor process entry point; it accepts only Policy public keys."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from .authorization import AuthorizedCommandExecutor, ExecutorAuthorizationVerifier
from .delta_replay import DeltaReplayStore
from .postgres_authorization import PostgresReplayStore, PostgresRevocationStore
from .mtls import server_context
from .audit import DeltaAuthorizationAuditStore
from .http_reference import executor_server

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-key", type=Path, help="legacy single trusted Policy public-key PEM")
    parser.add_argument(
        "--trusted-public-key", action="append", metavar="KEY_ID=PATH",
        help="trusted Policy key for rotation overlap; repeat for each key",
    )
    replay = parser.add_mutually_exclusive_group(required=True)
    replay.add_argument("--replay-table", type=Path, help="Delta reference replay table (single serialized writer)")
    replay.add_argument("--postgres-replay-url", help="shared PostgreSQL replay store for multi-replica Executors")
    parser.add_argument("--postgres-revocation-url", help="shared PostgreSQL decision-revocation store")
    parser.add_argument("--effect-log", required=True, type=Path)
    parser.add_argument("--key-id", default="local-policy-key")
    parser.add_argument("--audience", default="local-order-executor")
    parser.add_argument(
        "--owned-action-type", action="append", required=True,
        help="signed action type this Executor is permitted to perform; repeat for each owned type",
    )
    parser.add_argument("--port", type=int, default=8082)
    parser.add_argument("--max-request-bytes", type=int, default=1_048_576, help="maximum accepted HTTP request body size")
    parser.add_argument("--audit-table", type=Path)
    parser.add_argument("--tls-cert", type=Path, help="Executor server certificate PEM")
    parser.add_argument("--tls-key", type=Path, help="Executor server private-key PEM")
    parser.add_argument("--tls-client-ca", type=Path, help="trusted Policy client CA PEM")
    identity = parser.add_mutually_exclusive_group()
    identity.add_argument("--expected-client-dns", help="exact Policy certificate DNS SAN")
    identity.add_argument("--expected-client-uri", help="exact Policy certificate URI SAN, for example a SPIFFE ID")
    args = parser.parse_args()
    if bool(args.public_key) == bool(args.trusted_public_key):
        raise SystemExit("provide exactly one --public-key or one-or-more --trusted-public-key KEY_ID=PATH values")
    tls_values = (args.tls_cert, args.tls_key, args.tls_client_ca)
    if any(tls_values) and (not all(tls_values) or not (args.expected_client_dns or args.expected_client_uri)):
        raise SystemExit("mTLS requires --tls-cert, --tls-key, --tls-client-ca, and one expected client DNS or URI SAN")
    if (args.expected_client_dns or args.expected_client_uri) and not all(tls_values):
        raise SystemExit("an expected client identity requires --tls-cert, --tls-key, and --tls-client-ca")
    replay_store = PostgresReplayStore(args.postgres_replay_url) if args.postgres_replay_url else DeltaReplayStore(args.replay_table)
    revocation_store = PostgresRevocationStore(args.postgres_revocation_url) if args.postgres_revocation_url else None
    trusted_keys: dict[str, bytes]
    if args.public_key:
        trusted_keys = {args.key_id: args.public_key.read_bytes()}
    else:
        trusted_keys = {}
        for item in args.trusted_public_key:
            key_id, separator, path = item.partition("=")
            if not separator or not key_id or not path or key_id in trusted_keys:
                raise SystemExit("each --trusted-public-key must be a unique KEY_ID=PATH value")
            try:
                trusted_keys[key_id] = Path(path).read_bytes()
            except OSError as exc:
                raise SystemExit(f"cannot read trusted public key {path!r}: {exc}") from exc
    verifier = ExecutorAuthorizationVerifier.from_pem(
        trusted_keys, audience=args.audience,
        replay_store=replay_store, revocation_store=revocation_store,
    )
    def effect(command) -> None:
        with args.effect_log.open("a", encoding="utf-8") as log:
            log.write(json.dumps(command.wire(), sort_keys=True) + "\n")
    audit = DeltaAuthorizationAuditStore(args.audit_table) if args.audit_table else None
    tls = server_context(certificate=args.tls_cert, private_key=args.tls_key, trust_bundle=args.tls_client_ca) if args.tls_cert else None
    executor_server(
        AuthorizedCommandExecutor(verifier, effect, owned_action_types=set(args.owned_action_type)),
        audit_store=audit,
        port=args.port,
        ssl_context=tls,
        expected_client_dns_name=args.expected_client_dns,
        expected_client_uri=args.expected_client_uri,
        max_request_bytes=args.max_request_bytes,
    ).serve_forever()

if __name__ == "__main__":
    main()
