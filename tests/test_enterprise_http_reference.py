from __future__ import annotations

import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from agent_enterprise import (
    AuthorizedCommandExecutor, DeltaReplayStore, ExecutorAuthorizationVerifier,
    DeltaAuthorizationAuditStore, PolicyAuthorizationIssuer, executor_server, policy_server,
)


def post(server, payload: dict, headers: dict[str, str] | None = None) -> tuple[int, dict]:
    url = f"http://127.0.0.1:{server.server_port}/"
    request = Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", **(headers or {})}, method="POST")
    try:
        with urlopen(request) as response:
            return response.status, json.loads(response.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


def running(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def test_policy_and_executor_http_boundary_with_delta_replay(tmp_path) -> None:
    issuer = PolicyAuthorizationIssuer.generate(key_id="policy-key", issuer="order-policy")
    audit = DeltaAuthorizationAuditStore(tmp_path / "audit")
    try:
        policy = policy_server(
            issuer,
            audience="order-executor",
            audit_store=audit,
            development_bearer_token="dev-token",
            development_principal_id="dev-order-client",
            preconditions_for=lambda order_id, product_id, quantity: {"inventory_version": 5, "available": 2},
        )
    except PermissionError:
        pytest.skip("environment forbids loopback HTTP listener binding")
    effects = []
    verifier = ExecutorAuthorizationVerifier.from_pem(
        {"policy-key": issuer.public_key_pem()}, audience="order-executor", replay_store=DeltaReplayStore(tmp_path / "replay")
    )
    executor = executor_server(AuthorizedCommandExecutor(verifier, effects.append), audit_store=audit)
    running(policy)
    running(executor)
    try:
        status, _ = post(policy, {"order_id": "ORD-1", "product_id": "SKU-1", "quantity": 2})
        assert status == 401
        status, approved = post(policy, {"order_id": "ORD-1", "product_id": "SKU-1", "quantity": 2}, headers={"Authorization": "Bearer dev-token"})
        assert status == 200
        assert approved["command"]["preconditions"] == {"inventory_version": 5, "available": 2}
        status, result = post(executor, approved)
        assert status == 200 and result["status"] == "executed"
        assert len(effects) == 1

        stale = {**approved, "command": {**approved["command"], "preconditions": {"inventory_version": 4, "available": 2}}}
        status, _ = post(executor, stale)
        assert status == 400
        assert len(effects) == 1

        tampered = {**approved, "command": {**approved["command"], "parameters": {"product_id": "SKU-1", "quantity": 999}}}
        status, _ = post(executor, tampered)
        assert status == 400
        assert len(effects) == 1

        status, _ = post(executor, approved)
        assert status == 400
        assert len(effects) == 1
        records = audit.path and __import__("deltalake").DeltaTable(audit.path).to_pandas()
        outcomes = set(records["outcome"])
        assert {"POLICY_ISSUED", "EXECUTOR_EXECUTED", "EXECUTOR_REJECTED"} <= outcomes
        issued = records[records["outcome"] == "POLICY_ISSUED"].iloc[0]
        assert issued["principal_id"] == "dev-order-client"
        assert "dev-token" not in " ".join(records["claims"].tolist())
    finally:
        policy.shutdown(); policy.server_close()
        executor.shutdown(); executor.server_close()
