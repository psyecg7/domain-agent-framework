"""Opt-in proof against a real Vault Transit server."""
from __future__ import annotations

import os
import pytest

from agent_core import Decision, decision_to_action
from agent_enterprise import ExecutionCommand, ExecutorAuthorizationVerifier, InMemoryReplayStore, VaultTransitPolicyAuthorizationIssuer

URL, TOKEN = os.getenv("VAULT_TRANSIT_URL"), os.getenv("VAULT_TRANSIT_TOKEN")
pytestmark = pytest.mark.skipif(not URL or not TOKEN, reason="set VAULT_TRANSIT_URL and VAULT_TRANSIT_TOKEN to run real Vault Transit integration")


def test_vault_transit_signer_authorizes_a_command_without_local_private_key() -> None:
    issuer = VaultTransitPolicyAuthorizationIssuer(vault_url=URL, token=TOKEN, transit_key=os.getenv("VAULT_TRANSIT_KEY", "policy-authorization"), key_id="vault-policy", issuer="policy")
    decision = Decision("ORD-1", "order", "CREATE", "HIGH", "approved")
    command = ExecutionCommand.from_action(decision_to_action(decision, "CREATE_ORDER"), decision)
    authorization = issuer.authorize(command, audience="executor")
    ExecutorAuthorizationVerifier.from_pem({"vault-policy": issuer.public_key_pem()}, audience="executor", replay_store=InMemoryReplayStore()).verify(command, authorization)
