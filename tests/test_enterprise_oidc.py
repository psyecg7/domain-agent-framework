from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from agent_enterprise import OidcJwtValidator, OidcValidationError


def b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def token(key: rsa.RSAPrivateKey, *, claims: dict, key_id: str = "test-key") -> str:
    header = b64(json.dumps({"alg": "RS256", "kid": key_id}, separators=(",", ":")).encode())
    payload = b64(json.dumps(claims, separators=(",", ":")).encode())
    signature = b64(key.sign(f"{header}.{payload}".encode(), padding.PKCS1v15(), hashes.SHA256()))
    return f"{header}.{payload}.{signature}"


def validator(key: rsa.RSAPrivateKey) -> OidcJwtValidator:
    numbers = key.public_key().public_numbers()
    jwks = {"keys": [{"kty": "RSA", "kid": "test-key", "n": b64(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")), "e": b64(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big"))}]}
    return OidcJwtValidator(issuer="https://identity.example/realms/domain", audience="policy-service", required_scopes={"order:submit"}, jwks_uri="https://identity.example/jwks", jwks_loader=lambda: jwks)


def claims(**overrides: object) -> dict:
    return {"iss": "https://identity.example/realms/domain", "aud": ["policy-service"], "sub": "service-account-order-client", "azp": "order-client", "preferred_username": "order-client", "scope": "order:submit", "iat": 1_900_000_000, "exp": 1_900_000_060, **overrides}


def test_oidc_validator_accepts_only_signed_configured_identity() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    principal = validator(key).authenticate(f"Bearer {token(key, claims=claims())}", now=1_900_000_001)
    assert principal.principal_id == "order-client"
    assert principal.scopes == {"order:submit"}


def test_oidc_validator_never_uses_mutable_username_as_audit_identity() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    principal = validator(key).authenticate(
        f"Bearer {token(key, claims=claims(azp=None, preferred_username='renamed-user'))}",
        now=1_900_000_001,
    )
    assert principal.principal_id == "service-account-order-client"


@pytest.mark.parametrize("changed", [{"aud": ["other-service"]}, {"scope": "read"}, {"exp": 1_900_000_000}, {"iss": "https://attacker.example"}])
def test_oidc_validator_fails_closed_for_wrong_claims(changed: dict) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(OidcValidationError):
        validator(key).authenticate(f"Bearer {token(key, claims=claims(**changed))}", now=1_900_000_001)


def test_oidc_validator_rejects_a_signature_from_another_key() -> None:
    trusted = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(OidcValidationError, match="signature"):
        validator(trusted).authenticate(f"Bearer {token(attacker, claims=claims())}", now=1_900_000_001)
