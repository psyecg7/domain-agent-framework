"""Vault Transit-backed issuer for Enterprise authorization envelopes."""
from __future__ import annotations

import base64
import json
import time
import uuid
from datetime import timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .authorization import AuthorizationError, ExecutionCommand, SignedAuthorization, _canonical_json


class VaultTransitUnavailable(AuthorizationError):
    """Vault Transit cannot safely provide the configured signing operation."""


class VaultTransitPolicyAuthorizationIssuer:
    """Policy issuer whose Ed25519 private key never leaves Vault Transit.

    A small bounded retry budget absorbs transient Vault/network jitter. It
    never retries authorization failures or malformed responses, and still
    fails closed when that budget is exhausted.
    """

    def __init__(self, *, vault_url: str, token: str, transit_key: str, key_id: str, issuer: str, transit_mount: str = "transit", request_timeout_seconds: float = 5.0, retry_attempts: int = 3, retry_backoff_seconds: float = 0.1) -> None:
        if not all(isinstance(value, str) and value for value in (vault_url, token, transit_key, key_id, issuer, transit_mount)):
            raise ValueError("Vault URL, token, mount, key, key_id, and issuer are required")
        self.vault_url = vault_url.rstrip("/")
        self.token = token
        self.transit_key = transit_key
        self.transit_mount = transit_mount.strip("/")
        self.key_id = key_id
        self.issuer = issuer
        if not isinstance(request_timeout_seconds, (int, float)) or request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        if not isinstance(retry_attempts, int) or not 1 <= retry_attempts <= 5:
            raise ValueError("retry_attempts must be an integer from 1 to 5")
        if not isinstance(retry_backoff_seconds, (int, float)) or retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must be non-negative")
        self.request_timeout_seconds = float(request_timeout_seconds)
        self.retry_attempts = retry_attempts
        self.retry_backoff_seconds = float(retry_backoff_seconds)
        self._public_key = self._read_public_key()

    def public_key_pem(self) -> bytes:
        return self._public_key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)

    def authorize(self, command: ExecutionCommand, *, audience: str, ttl: timedelta = timedelta(seconds=30)) -> SignedAuthorization:
        if not audience or ttl.total_seconds() <= 0:
            raise ValueError("audience and a positive ttl are required")
        now = int(time.time())
        claims: dict[str, Any] = {
            "version": 1, "authorization_id": str(uuid.uuid4()), "issuer": self.issuer,
            "key_id": self.key_id, "audience": audience, "issued_at": now,
            "expires_at": now + int(ttl.total_seconds()), **command.binding(),
        }
        signature = self._request(f"sign/{self.transit_key}", {"input": base64.b64encode(_canonical_json(claims)).decode()}).get("signature")
        if not isinstance(signature, str) or not signature.startswith("vault:v") or ":" not in signature:
            raise VaultTransitUnavailable("Vault Transit returned an invalid signature")
        encoded = signature.rsplit(":", 1)[1]
        try:
            raw = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise VaultTransitUnavailable("Vault Transit returned an invalid signature") from exc
        return SignedAuthorization(claims, base64.urlsafe_b64encode(raw).decode())

    def _read_public_key(self) -> Ed25519PublicKey:
        data = self._request(f"keys/{self.transit_key}", None, method="GET")
        keys = data.get("keys")
        latest = data.get("latest_version")
        value = keys.get(str(latest), {}).get("public_key") if isinstance(keys, dict) else None
        if not isinstance(value, str):
            raise VaultTransitUnavailable("Vault Transit did not return an Ed25519 public key")
        try:
            return Ed25519PublicKey.from_public_bytes(base64.b64decode(value, validate=True))
        except Exception as exc:
            raise VaultTransitUnavailable("Vault Transit public key is invalid") from exc

    def _request(self, path: str, body: dict[str, Any] | None, *, method: str = "POST") -> dict[str, Any]:
        url = f"{self.vault_url}/v1/{self.transit_mount}/{path}"
        request = Request(url, data=None if body is None else json.dumps(body).encode(), method=method, headers={"X-Vault-Token": self.token, "Content-Type": "application/json"})
        for attempt in range(self.retry_attempts):
            try:
                with urlopen(request, timeout=self.request_timeout_seconds) as response:
                    response_body = json.loads(response.read())
                break
            except (HTTPError, URLError, OSError) as exc:
                if not self._is_transient(exc) or attempt == self.retry_attempts - 1:
                    raise VaultTransitUnavailable("Vault Transit is unavailable or rejected the signing operation") from exc
                time.sleep(self.retry_backoff_seconds * (2 ** attempt))
            except (ValueError, json.JSONDecodeError) as exc:
                raise VaultTransitUnavailable("Vault Transit is unavailable or rejected the signing operation") from exc
        data = response_body.get("data") if isinstance(response_body, dict) else None
        if not isinstance(data, dict):
            raise VaultTransitUnavailable("Vault Transit returned a malformed response")
        return data

    @staticmethod
    def _is_transient(error: Exception) -> bool:
        if isinstance(error, HTTPError):
            return error.code in {429, 500, 502, 503, 504}
        return isinstance(error, (URLError, OSError))
