"""Opt-in proof that the Compose Keycloak profile issues acceptable Policy JWTs."""

from __future__ import annotations

import json
import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from agent_enterprise import OidcJwtValidator


ISSUER = os.getenv("KEYCLOAK_OIDC_ISSUER")
pytestmark = pytest.mark.skipif(
    not ISSUER,
    reason="set KEYCLOAK_OIDC_ISSUER to run real Keycloak OIDC integration",
)


def test_keycloak_client_credentials_token_is_accepted_by_policy_validator() -> None:
    request = Request(
        f"{ISSUER}/protocol/openid-connect/token",
        data=urlencode({
            "grant_type": "client_credentials",
            "client_id": "order-client",
            "client_secret": "order-client-dev-only",
        }).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        access_token = json.loads(response.read())["access_token"]

    principal = OidcJwtValidator.from_issuer(
        issuer=ISSUER,
        audience="policy-service",
        required_scopes={"order:submit"},
    ).authenticate(f"Bearer {access_token}")
    assert "order-client" in principal.principal_id
