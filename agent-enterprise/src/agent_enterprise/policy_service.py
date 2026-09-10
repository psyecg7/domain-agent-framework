"""Local Policy process entry point; this process alone reads the private key."""
from __future__ import annotations
import argparse
from pathlib import Path
from .authorization import PolicyAuthorizationIssuer
from .audit import DeltaAuthorizationAuditStore
from .http_reference import policy_server

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-key", required=True, type=Path)
    parser.add_argument("--public-key-out", required=True, type=Path)
    parser.add_argument("--key-id", default="local-policy-key")
    parser.add_argument("--issuer", default="local-order-policy")
    parser.add_argument("--audience", default="local-order-executor")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--development-bearer-token")
    parser.add_argument("--development-principal-id", default="development-client")
    parser.add_argument("--audit-table", type=Path)
    parser.add_argument("--generate-development-key", action="store_true")
    args = parser.parse_args()
    if args.private_key.exists():
        issuer = PolicyAuthorizationIssuer.from_private_key_pem(args.private_key.read_bytes(), key_id=args.key_id, issuer=args.issuer)
    elif args.generate_development_key:
        issuer = PolicyAuthorizationIssuer.generate(key_id=args.key_id, issuer=args.issuer)
        args.private_key.write_bytes(issuer.private_key_pem())
    else:
        raise SystemExit("private key is missing; use --generate-development-key only for local development")
    args.public_key_out.write_bytes(issuer.public_key_pem())
    audit = DeltaAuthorizationAuditStore(args.audit_table) if args.audit_table else None
    policy_server(issuer, audience=args.audience, audit_store=audit, development_bearer_token=args.development_bearer_token, development_principal_id=args.development_principal_id, port=args.port).serve_forever()

if __name__ == "__main__":
    main()
