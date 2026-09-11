"""Small mTLS transport helpers for the Enterprise HTTP reference.

TLS is transport/deployment infrastructure, so this module lives in
``agent-enterprise`` rather than core.  The server context requires a client
certificate and the request handler additionally checks one authenticated peer
identity to prevent any certificate from the CA being treated as Policy. Exact
DNS SAN and SPIFFE URI SAN identities are both supported; wildcard and common-
name fallback are deliberately never accepted.
"""

from __future__ import annotations

import ssl
from pathlib import Path


class MtlsIdentityError(RuntimeError):
    """The TLS peer is absent or is not the configured workload identity."""


def server_context(*, certificate: Path, private_key: Path, trust_bundle: Path) -> ssl.SSLContext:
    """Create a TLS-1.3-preferred server context requiring a trusted client."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(certificate), str(private_key))
    context.load_verify_locations(cafile=str(trust_bundle))
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def client_context(*, certificate: Path, private_key: Path, trust_bundle: Path) -> ssl.SSLContext:
    """Create a client context that verifies the receiving workload."""
    context = ssl.create_default_context(cafile=str(trust_bundle))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(certificate), str(private_key))
    return context


def verify_peer_identity(
    connection: object,
    *,
    expected_dns_name: str | None = None,
    expected_uri: str | None = None,
) -> None:
    """Require one exact DNS or URI subject-alternative-name identity.

    A caller must select exactly one identity form.  This avoids broadening a
    workload's authority by treating either of two identities as sufficient.
    URI values are intended for exact SPIFFE IDs such as
    ``spiffe://example.org/workload/policy-service``.
    """
    selectors = (expected_dns_name is not None) + (expected_uri is not None)
    if selectors != 1:
        raise ValueError("configure exactly one expected_dns_name or expected_uri")
    expected_kind, expected_value = (
        ("DNS", expected_dns_name) if expected_dns_name is not None else ("URI", expected_uri)
    )
    if not isinstance(expected_value, str) or not expected_value:
        raise ValueError(f"{expected_kind.lower()} identity must be a non-empty string")
    peer = getattr(connection, "getpeercert", lambda: None)()
    if not peer:
        raise MtlsIdentityError("mTLS client certificate is required")
    # ``ssl.match_hostname`` was removed in Python 3.12. More importantly, a
    # workload identity is not a browser hostname: accept only one exact DNS
    # Subject Alternative Name, never a wildcard or CN fallback.
    names = peer.get("subjectAltName", ()) if isinstance(peer, dict) else ()
    if (expected_kind, expected_value) not in names:
        raise MtlsIdentityError("mTLS client identity is not authorized")


def verify_peer_dns_name(connection: object, expected_dns_name: str) -> None:
    """Compatibility wrapper for exact DNS SAN workload identities."""
    verify_peer_identity(connection, expected_dns_name=expected_dns_name)
