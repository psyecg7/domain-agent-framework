"""Local Policy process entry point; this process alone reads the private key."""
from __future__ import annotations
import argparse
from pathlib import Path
from .authorization import PolicyAuthorizationIssuer
from .audit import DeltaAuthorizationAuditStore
from .http_reference import policy_server
from .oidc import OidcJwtValidator
from .mtls import server_context
from .vault_transit import VaultTransitPolicyAuthorizationIssuer

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-key", type=Path)
    parser.add_argument("--public-key-out", required=True, type=Path)
    parser.add_argument("--key-id", default="local-policy-key")
    parser.add_argument("--issuer", default="local-order-policy")
    parser.add_argument("--audience", default="local-order-executor")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--max-request-bytes", type=int, default=1_048_576, help="maximum accepted HTTP request body size")
    parser.add_argument("--development-bearer-token")
    parser.add_argument("--development-principal-id", default="development-client")
    parser.add_argument("--oidc-issuer", help="OIDC issuer URL used to authenticate Policy callers")
    parser.add_argument("--oidc-audience", help="required OIDC access-token audience")
    parser.add_argument("--required-scope", action="append", help="required OIDC scope; repeat for each scope")
    parser.add_argument("--tls-cert", type=Path, help="Policy server certificate PEM")
    parser.add_argument("--tls-key", type=Path, help="Policy server private-key PEM")
    parser.add_argument("--tls-client-ca", type=Path, help="trusted client CA PEM")
    identity = parser.add_mutually_exclusive_group()
    identity.add_argument("--expected-client-dns", help="exact client certificate DNS SAN")
    identity.add_argument("--expected-client-uri", help="exact client certificate URI SAN, for example a SPIFFE ID")
    parser.add_argument("--audit-table", type=Path)
    parser.add_argument("--generate-development-key", action="store_true")
    parser.add_argument("--vault-url", help="Vault URL for Transit-backed Policy signing")
    parser.add_argument("--vault-token", help="Vault token permitted only to sign with the configured Transit key")
    parser.add_argument("--vault-transit-key", help="existing Vault Transit Ed25519 key name")
    parser.add_argument("--vault-transit-mount", default="transit")
    args = parser.parse_args()
    if args.development_bearer_token and args.oidc_issuer:
        raise SystemExit("choose either --development-bearer-token or --oidc-issuer")
    if args.oidc_issuer and (not args.oidc_audience or not args.required_scope):
        raise SystemExit("--oidc-issuer requires --oidc-audience and at least one --required-scope")
    tls_values = (args.tls_cert, args.tls_key, args.tls_client_ca)
    if any(tls_values) and (not all(tls_values) or not (args.expected_client_dns or args.expected_client_uri)):
        raise SystemExit("mTLS requires --tls-cert, --tls-key, --tls-client-ca, and one expected client DNS or URI SAN")
    if (args.expected_client_dns or args.expected_client_uri) and not all(tls_values):
        raise SystemExit("an expected client identity requires --tls-cert, --tls-key, and --tls-client-ca")
    vault_values = (args.vault_url, args.vault_token, args.vault_transit_key)
    if any(vault_values) and not all(vault_values):
        raise SystemExit("Vault Transit requires --vault-url, --vault-token, and --vault-transit-key together")
    if all(vault_values):
        if args.private_key or args.generate_development_key:
            raise SystemExit("Vault Transit signing cannot be combined with a local private key")
        issuer = VaultTransitPolicyAuthorizationIssuer(
            vault_url=args.vault_url, token=args.vault_token, transit_key=args.vault_transit_key,
            transit_mount=args.vault_transit_mount, key_id=args.key_id, issuer=args.issuer,
        )
    elif args.private_key and args.private_key.exists():
        issuer = PolicyAuthorizationIssuer.from_private_key_pem(args.private_key.read_bytes(), key_id=args.key_id, issuer=args.issuer)
    elif args.private_key and args.generate_development_key:
        issuer = PolicyAuthorizationIssuer.generate(key_id=args.key_id, issuer=args.issuer)
        args.private_key.write_bytes(issuer.private_key_pem())
    else:
        raise SystemExit("provide Vault Transit configuration or --private-key with --generate-development-key for local development")
    args.public_key_out.write_bytes(issuer.public_key_pem())
    audit = DeltaAuthorizationAuditStore(args.audit_table) if args.audit_table else None
    oidc = OidcJwtValidator.from_issuer(
        issuer=args.oidc_issuer, audience=args.oidc_audience, required_scopes=set(args.required_scope)
    ) if args.oidc_issuer else None
    tls = server_context(certificate=args.tls_cert, private_key=args.tls_key, trust_bundle=args.tls_client_ca) if args.tls_cert else None
    policy_server(issuer, audience=args.audience, audit_store=audit, development_bearer_token=args.development_bearer_token, development_principal_id=args.development_principal_id, oidc_validator=oidc, port=args.port, ssl_context=tls, expected_client_dns_name=args.expected_client_dns, expected_client_uri=args.expected_client_uri, max_request_bytes=args.max_request_bytes).serve_forever()

if __name__ == "__main__":
    main()
