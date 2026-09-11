"""Opt-in complete enterprise boundary proof using local Keycloak and Vault."""
from __future__ import annotations

import json
import os
import ssl
import threading
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from agent_enterprise import (
    AuthorizedCommandExecutor, ExecutorAuthorizationVerifier, InMemoryReplayStore,
    OidcJwtValidator, VaultTransitPolicyAuthorizationIssuer, client_context,
    executor_server, policy_server, server_context,
)

OIDC_ISSUER = os.getenv("KEYCLOAK_OIDC_ISSUER")
VAULT_URL, VAULT_TOKEN = os.getenv("VAULT_TRANSIT_URL"), os.getenv("VAULT_TRANSIT_TOKEN")
pytestmark = pytest.mark.skipif(
    not (OIDC_ISSUER and VAULT_URL and VAULT_TOKEN),
    reason="set KEYCLOAK_OIDC_ISSUER, VAULT_TRANSIT_URL, and VAULT_TRANSIT_TOKEN for enterprise E2E integration",
)


def material(tmp_path, name: str, dns: str, issuer=None):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.now(timezone.utc)
    issuer_name, signer = (subject, key) if issuer is None else (issuer[0].subject, issuer[1])
    cert = x509.CertificateBuilder().subject_name(subject).issuer_name(issuer_name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1)).add_extension(x509.SubjectAlternativeName([x509.DNSName(dns)]), critical=False).add_extension(x509.BasicConstraints(ca=issuer is None, path_length=None), critical=True).sign(signer, hashes.SHA256())
    cert_path, key_path = tmp_path / f"{name}.crt", tmp_path / f"{name}.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return cert, key, cert_path, key_path


def post(server, body, context, authorization=None):
    headers = {"Content-Type": "application/json"}
    if authorization: headers["Authorization"] = f"Bearer {authorization}"
    request = Request(f"https://localhost:{server.server_port}/", data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urlopen(request, context=context, timeout=5) as response:
            return response.status, json.loads(response.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_oidc_vault_mtls_and_replay_boundary(tmp_path) -> None:
    token_request = Request(f"{OIDC_ISSUER}/protocol/openid-connect/token", data=urlencode({"grant_type": "client_credentials", "client_id": "order-client", "client_secret": "order-client-dev-only"}).encode(), headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    with urlopen(token_request, timeout=5) as response:
        access_token = json.loads(response.read())["access_token"]

    ca, ca_key, ca_path, _ = material(tmp_path, "ca", "development-ca")
    _, _, policy_server_cert, policy_server_key = material(tmp_path, "policy-listener", "localhost", (ca, ca_key))
    _, _, executor_server_cert, executor_server_key = material(tmp_path, "executor-listener", "localhost", (ca, ca_key))
    _, _, caller_cert, caller_key = material(tmp_path, "order-client", "order-client", (ca, ca_key))
    _, _, policy_client_cert, policy_client_key = material(tmp_path, "policy-service", "policy-service", (ca, ca_key))
    issuer = VaultTransitPolicyAuthorizationIssuer(vault_url=VAULT_URL, token=VAULT_TOKEN, transit_key=os.getenv("VAULT_TRANSIT_KEY", "policy-authorization"), key_id="vault-policy", issuer="local-order-policy")
    policy = policy_server(issuer, audience="local-order-executor", oidc_validator=OidcJwtValidator.from_issuer(issuer=OIDC_ISSUER, audience="policy-service", required_scopes={"order:submit"}), ssl_context=server_context(certificate=policy_server_cert, private_key=policy_server_key, trust_bundle=ca_path), expected_client_dns_name="order-client")
    effects = []
    verifier = ExecutorAuthorizationVerifier.from_pem({"vault-policy": issuer.public_key_pem()}, audience="local-order-executor", replay_store=InMemoryReplayStore())
    executor = executor_server(AuthorizedCommandExecutor(verifier, effects.append, owned_action_types={"CREATE_ORDER"}), ssl_context=server_context(certificate=executor_server_cert, private_key=executor_server_key, trust_bundle=ca_path), expected_client_dns_name="policy-service")
    for server in (policy, executor): threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        caller_tls = client_context(certificate=caller_cert, private_key=caller_key, trust_bundle=ca_path)
        status, approved = post(policy, {"order_id": "ORD-E2E", "product_id": "SKU-1", "quantity": 1}, caller_tls, access_token)
        assert status == 200
        policy_tls = client_context(certificate=policy_client_cert, private_key=policy_client_key, trust_bundle=ca_path)
        status, _ = post(executor, approved, policy_tls)
        assert status == 200 and len(effects) == 1
        status, _ = post(executor, approved, policy_tls)
        assert status == 400 and len(effects) == 1
    finally:
        for server in (policy, executor):
            server.shutdown(); server.server_close()
