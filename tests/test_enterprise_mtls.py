from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import ssl
import threading
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from agent_enterprise import MtlsIdentityError, PolicyAuthorizationIssuer, client_context, policy_server, server_context, verify_peer_identity


def certificate_material(tmp_path, name: str, *, dns_name: str, uri: str | None = None, issuer=None):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.now(timezone.utc)
    issuer_name, signer = (subject, key) if issuer is None else (issuer[0].subject, issuer[1])
    identities = [x509.DNSName(dns_name)]
    if uri is not None:
        identities.append(x509.UniformResourceIdentifier(uri))
    is_ca = issuer is None
    # Include the extensions expected from a real CA chain. Python 3.13/OpenSSL
    # correctly rejects an otherwise plausible chain if its CA lacks a key
    # identifier or the key-cert-sign usage.
    cert = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(issuer_name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1)).add_extension(x509.SubjectAlternativeName(identities), critical=False)
        .add_extension(x509.BasicConstraints(ca=is_ca, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(signer.public_key()), critical=False)
        .add_extension(
            x509.KeyUsage(
                digital_signature=not is_ca,
                content_commitment=False,
                key_encipherment=not is_ca,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=is_ca,
                crl_sign=is_ca,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(signer, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / f"{name}.crt", tmp_path / f"{name}.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return cert, key, cert_path, key_path


def post(server, context):
    request = Request(
        f"https://localhost:{server.server_port}/",
        data=json.dumps({"order_id": "ORD-1", "product_id": "SKU-1", "quantity": 1}).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer dev-token"}, method="POST",
    )
    try:
        with urlopen(request, context=context, timeout=5) as response:
            return response.status
    except HTTPError as exc:
        return exc.code


def test_policy_mtls_requires_a_trusted_expected_workload_identity(tmp_path) -> None:
    ca_cert, ca_key, ca_path, _ = certificate_material(tmp_path, "ca", dns_name="development-ca")
    _, _, server_cert, server_key = certificate_material(tmp_path, "policy-service", dns_name="localhost", issuer=(ca_cert, ca_key))
    _, _, caller_cert, caller_key = certificate_material(tmp_path, "order-client", dns_name="order-client", issuer=(ca_cert, ca_key))
    _, _, wrong_cert, wrong_key = certificate_material(tmp_path, "other-client", dns_name="other-client", issuer=(ca_cert, ca_key))
    try:
        server = policy_server(
            PolicyAuthorizationIssuer.generate(key_id="policy", issuer="policy"), audience="executor",
            development_bearer_token="dev-token",
            ssl_context=server_context(certificate=server_cert, private_key=server_key, trust_bundle=ca_path),
            expected_client_dns_name="order-client",
        )
    except PermissionError:
        pytest.skip("environment forbids loopback HTTPS listener binding")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        valid = client_context(certificate=caller_cert, private_key=caller_key, trust_bundle=ca_path)
        assert post(server, valid) == 200

        wrong_identity = client_context(certificate=wrong_cert, private_key=wrong_key, trust_bundle=ca_path)
        assert post(server, wrong_identity) == 400

        no_client = ssl.create_default_context(cafile=str(ca_path))
        with pytest.raises((URLError, ssl.SSLError)):
            post(server, no_client)
    finally:
        server.shutdown()
        server.server_close()


def test_policy_mtls_accepts_only_the_configured_spiffe_uri_san(tmp_path) -> None:
    ca_cert, ca_key, ca_path, _ = certificate_material(tmp_path, "ca", dns_name="development-ca")
    _, _, server_cert, server_key = certificate_material(tmp_path, "policy-service", dns_name="localhost", issuer=(ca_cert, ca_key))
    workload = "spiffe://development.local/workload/order-client"
    _, _, caller_cert, caller_key = certificate_material(
        tmp_path, "order-client", dns_name="order-client", uri=workload, issuer=(ca_cert, ca_key),
    )
    _, _, wrong_cert, wrong_key = certificate_material(
        tmp_path, "other-client", dns_name="other-client", uri="spiffe://development.local/workload/other", issuer=(ca_cert, ca_key),
    )
    try:
        server = policy_server(
            PolicyAuthorizationIssuer.generate(key_id="policy", issuer="policy"), audience="executor",
            development_bearer_token="dev-token",
            ssl_context=server_context(certificate=server_cert, private_key=server_key, trust_bundle=ca_path),
            expected_client_uri=workload,
        )
    except PermissionError:
        pytest.skip("environment forbids loopback HTTPS listener binding")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        valid = client_context(certificate=caller_cert, private_key=caller_key, trust_bundle=ca_path)
        assert post(server, valid) == 200
        wrong = client_context(certificate=wrong_cert, private_key=wrong_key, trust_bundle=ca_path)
        assert post(server, wrong) == 400
    finally:
        server.shutdown()
        server.server_close()


def test_exact_spiffe_uri_identity_is_not_a_dns_or_common_name_fallback() -> None:
    class Connection:
        def getpeercert(self):
            return {"subjectAltName": (("URI", "spiffe://example.org/workload/order-client"),)}

    connection = Connection()
    verify_peer_identity(connection, expected_uri="spiffe://example.org/workload/order-client")
    with pytest.raises(MtlsIdentityError):
        verify_peer_identity(connection, expected_dns_name="order-client")
    with pytest.raises(ValueError):
        verify_peer_identity(connection, expected_dns_name="order-client", expected_uri="spiffe://example.org/workload/order-client")
