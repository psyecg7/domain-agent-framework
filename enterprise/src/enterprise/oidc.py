"""Fail-closed OIDC access-token validation for the Enterprise Policy ingress.

This module deliberately uses only the standard library and ``cryptography``.
It verifies a JWT against the issuer's JWKS locally; it never trusts a caller's
unverified claims or performs per-request token introspection.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class OidcValidationError(RuntimeError):
    """A bearer token is missing, invalid, or not authorized for Policy."""


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """The minimal, verified caller identity made available to Policy."""

    principal_id: str
    subject: str
    scopes: frozenset[str]
    claims: Mapping[str, Any]


def _b64url(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, UnicodeEncodeError) as exc:
        raise OidcValidationError("Bearer token is malformed") from exc


def _json_part(value: str) -> dict[str, Any]:
    try:
        decoded = json.loads(_b64url(value))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OidcValidationError("Bearer token is malformed") from exc
    if not isinstance(decoded, dict):
        raise OidcValidationError("Bearer token is malformed")
    return decoded


class OidcJwtValidator:
    """Validate signed OIDC access tokens against a cached JWKS set.

    ``issuer``, ``audience``, and at least one required scope are deployment
    policy—not framework defaults.  A JWKS lookup failure rejects the request;
    it must never degrade to accepting an unverified token.
    """

    _ALGORITHMS: dict[str, hashes.HashAlgorithm] = {
        "RS256": hashes.SHA256(), "RS384": hashes.SHA384(), "RS512": hashes.SHA512(),
    }

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        required_scopes: set[str] | frozenset[str],
        jwks_uri: str,
        cache_seconds: int = 300,
        jwks_loader: Callable[[], Mapping[str, Any]] | None = None,
    ) -> None:
        if not all(isinstance(value, str) and value for value in (issuer, audience, jwks_uri)):
            raise ValueError("issuer, audience, and jwks_uri must be non-empty strings")
        if not isinstance(cache_seconds, int) or cache_seconds < 1:
            raise ValueError("cache_seconds must be a positive integer")
        if not required_scopes or any(not isinstance(scope, str) or not scope for scope in required_scopes):
            raise ValueError("at least one non-empty required scope is required")
        self.issuer = issuer.rstrip("/")
        self.audience = audience
        self.required_scopes = frozenset(required_scopes)
        self.jwks_uri = jwks_uri
        self.cache_seconds = cache_seconds
        self._jwks_loader = jwks_loader or self._load_jwks
        self._keys: dict[str, rsa.RSAPublicKey] = {}
        self._cache_until = 0

    @classmethod
    def from_issuer(
        cls,
        *,
        issuer: str,
        audience: str,
        required_scopes: set[str] | frozenset[str],
        cache_seconds: int = 300,
    ) -> "OidcJwtValidator":
        """Discover the issuer JWKS URI before accepting any request."""
        base = issuer.rstrip("/")
        try:
            with urlopen(f"{base}/.well-known/openid-configuration", timeout=5) as response:
                discovery = json.loads(response.read())
        except (URLError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise OidcValidationError("OIDC discovery is unavailable or malformed") from exc
        jwks_uri = discovery.get("jwks_uri") if isinstance(discovery, dict) else None
        if discovery.get("issuer") != base or not isinstance(jwks_uri, str):
            raise OidcValidationError("OIDC discovery issuer or JWKS URI is invalid")
        return cls(issuer=base, audience=audience, required_scopes=required_scopes, jwks_uri=jwks_uri, cache_seconds=cache_seconds)

    def authenticate(self, authorization_header: str | None, *, now: int | None = None) -> AuthenticatedPrincipal:
        if not isinstance(authorization_header, str) or not authorization_header.startswith("Bearer "):
            raise OidcValidationError("Bearer access token is required")
        token = authorization_header.removeprefix("Bearer ").strip()
        parts = token.split(".")
        if len(parts) != 3 or any(not part for part in parts):
            raise OidcValidationError("Bearer token is malformed")
        header, claims = _json_part(parts[0]), _json_part(parts[1])
        algorithm, key_id = header.get("alg"), header.get("kid")
        if algorithm not in self._ALGORITHMS or not isinstance(key_id, str) or not key_id:
            raise OidcValidationError("Bearer token algorithm or key identity is invalid")
        key = self._key(key_id, now=int(time.time()) if now is None else now)
        try:
            key.verify(_b64url(parts[2]), f"{parts[0]}.{parts[1]}".encode(), padding.PKCS1v15(), self._ALGORITHMS[algorithm])
        except InvalidSignature as exc:
            raise OidcValidationError("Bearer token signature is invalid") from exc
        current = int(time.time()) if now is None else now
        self._validate_claims(claims, current)
        scopes = frozenset(str(claims.get("scope", "")).split())
        if not self.required_scopes.issubset(scopes):
            raise OidcValidationError("Bearer token lacks required scope")
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise OidcValidationError("Bearer token subject is invalid")
        # Client-credentials tokens commonly have an opaque service-account
        # ``sub``. ``azp`` is the verified OAuth client that was authorized to
        # obtain this token, so it is the useful Policy audit principal. User
        # flows use immutable ``sub``; never use mutable display names for an
        # audit principal.
        principal = claims.get("azp", subject)
        if not isinstance(principal, str) or not principal:
            principal = subject
        return AuthenticatedPrincipal(principal, subject, scopes, dict(claims))

    def _validate_claims(self, claims: Mapping[str, Any], now: int) -> None:
        audience = claims.get("aud")
        audiences = {audience} if isinstance(audience, str) else set(audience) if isinstance(audience, list) and all(isinstance(item, str) for item in audience) else set()
        if claims.get("iss") != self.issuer or self.audience not in audiences:
            raise OidcValidationError("Bearer token issuer or audience is invalid")
        exp, nbf, iat = claims.get("exp"), claims.get("nbf"), claims.get("iat")
        if not isinstance(exp, int) or exp <= now or (nbf is not None and (not isinstance(nbf, int) or nbf > now)) or (iat is not None and (not isinstance(iat, int) or iat > now)):
            raise OidcValidationError("Bearer token is expired or has an invalid time window")

    def _key(self, key_id: str, *, now: int) -> rsa.RSAPublicKey:
        if now >= self._cache_until or key_id not in self._keys:
            self._keys = self._parse_keys(self._jwks_loader())
            self._cache_until = now + self.cache_seconds
        key = self._keys.get(key_id)
        if key is None:
            raise OidcValidationError("Bearer token signing key is unknown")
        return key

    def _load_jwks(self) -> Mapping[str, Any]:
        try:
            with urlopen(self.jwks_uri, timeout=5) as response:
                value = json.loads(response.read())
        except (URLError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise OidcValidationError("OIDC JWKS is unavailable or malformed") from exc
        if not isinstance(value, dict):
            raise OidcValidationError("OIDC JWKS is malformed")
        return value

    @staticmethod
    def _parse_keys(value: Mapping[str, Any]) -> dict[str, rsa.RSAPublicKey]:
        keys = value.get("keys")
        if not isinstance(keys, list):
            raise OidcValidationError("OIDC JWKS is malformed")
        parsed: dict[str, rsa.RSAPublicKey] = {}
        for item in keys:
            if not isinstance(item, dict) or item.get("kty") != "RSA" or not isinstance(item.get("kid"), str):
                continue
            try:
                numbers = rsa.RSAPublicNumbers(
                    int.from_bytes(_b64url(item["e"]), "big"), int.from_bytes(_b64url(item["n"]), "big")
                )
                parsed[item["kid"]] = numbers.public_key()
            except (KeyError, ValueError, TypeError, OidcValidationError):
                continue
        if not parsed:
            raise OidcValidationError("OIDC JWKS contains no usable RSA keys")
        return parsed
