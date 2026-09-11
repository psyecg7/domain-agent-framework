from __future__ import annotations

import base64
import json
from urllib.error import HTTPError

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent_core import Decision, decision_to_action
from agent_enterprise import ExecutionCommand, ExecutorAuthorizationVerifier, InMemoryReplayStore, VaultTransitPolicyAuthorizationIssuer


class Response:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def read(self): return json.dumps(self.body).encode()


def test_vault_transit_issuer_signs_without_a_local_policy_private_key(monkeypatch) -> None:
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes_raw()

    def vault(request, timeout):
        if request.get_method() == "GET":
            return Response({"data": {"latest_version": 1, "keys": {"1": {"public_key": base64.b64encode(public).decode()}}}})
        payload = json.loads(request.data)
        signature = key.sign(base64.b64decode(payload["input"]))
        return Response({"data": {"signature": f"vault:v1:{base64.b64encode(signature).decode()}"}})

    monkeypatch.setattr("agent_enterprise.vault_transit.urlopen", vault)
    issuer = VaultTransitPolicyAuthorizationIssuer(vault_url="https://vault.example", token="restricted", transit_key="policy", key_id="vault-policy-v1", issuer="policy")
    decision = Decision("ORD-1", "order", "CREATE", "HIGH", "approved")
    action = decision_to_action(decision, "CREATE_ORDER", parameters={"quantity": 1})
    command = ExecutionCommand.from_action(action, decision)
    authorization = issuer.authorize(command, audience="executor")
    verifier = ExecutorAuthorizationVerifier.from_pem({"vault-policy-v1": issuer.public_key_pem()}, audience="executor", replay_store=InMemoryReplayStore())
    verifier.verify(command, authorization)


def test_vault_transit_retries_only_transient_failures(monkeypatch) -> None:
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes_raw()
    calls = 0

    def vault(request, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HTTPError("https://vault.example", 503, "unavailable", {}, None)
        return Response({"data": {"latest_version": 1, "keys": {"1": {"public_key": base64.b64encode(public).decode()}}}})

    monkeypatch.setattr("agent_enterprise.vault_transit.urlopen", vault)
    monkeypatch.setattr("agent_enterprise.vault_transit.time.sleep", lambda _: None)
    issuer = VaultTransitPolicyAuthorizationIssuer(
        vault_url="https://vault.example", token="restricted", transit_key="policy",
        key_id="vault-policy-v1", issuer="policy", retry_backoff_seconds=0,
    )
    assert calls == 2
    assert issuer.public_key_pem()
