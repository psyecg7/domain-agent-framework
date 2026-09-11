# Enterprise signing-key rotation

`ExecutionCommand` authorizations carry a signed `key_id`. Executor can trust a
key ring during a rotation overlap using repeated
`--trusted-public-key KEY_ID=PATH` arguments. This runbook rotates Vault
Transit signing keys without a period in which valid in-flight authorizations
are rejected.

Do not remove an old public key until its maximum authorization TTL, clock
skew allowance, transport delay, and any retry window have elapsed.

## 1. Create the next development Transit key

The current development key is `policy-authorization` with key ID
`local-policy-key`. Create a separate successor key:

```bash
VAULT_TRANSIT_KEY=policy-authorization-v2 \
  bash scripts/bootstrap_vault_development_transit.sh
```

Export only its public key for Executor configuration:

```bash
VAULT_TRANSIT_URL=http://127.0.0.1:8200 VAULT_TRANSIT_TOKEN=dev-root-token \
python -c '
from pathlib import Path
from agent_enterprise import VaultTransitPolicyAuthorizationIssuer
issuer = VaultTransitPolicyAuthorizationIssuer(
    vault_url="http://127.0.0.1:8200", token="dev-root-token",
    transit_key="policy-authorization-v2", key_id="local-policy-key-v2",
    issuer="local-order-policy",
)
Path("/tmp/policy-v2.pub").write_bytes(issuer.public_key_pem())
'
```

## 2. Deploy Executor with both keys

Restart Executor with both the old and new public keys. During the overlap it
will accept valid authorizations from either key ID:

```bash
python -m agent_enterprise.executor_service \
  --trusted-public-key local-policy-key=/tmp/policy.pub \
  --trusted-public-key local-policy-key-v2=/tmp/policy-v2.pub \
  --postgres-replay-url postgresql+psycopg://agent:agent@localhost:5432/agent_atomic \
  --postgres-revocation-url postgresql+psycopg://agent:agent@localhost:5432/agent_atomic \
  --owned-action-type CREATE_ORDER \
  --effect-log /tmp/executor-effects-mtls.jsonl \
  --tls-cert .local/mtls/executor-service.crt \
  --tls-key .local/mtls/executor-service.key \
  --tls-client-ca .local/mtls/ca.crt \
  --expected-client-dns policy-service --port 8082
```

## 3. Switch Policy to the new key

Restart Policy with the successor Transit key and a new `key_id`:

```bash
python -m agent_enterprise.policy_service \
  --public-key-out /tmp/policy-v2.pub \
  --key-id local-policy-key-v2 \
  --vault-url http://127.0.0.1:8200 --vault-token dev-root-token \
  --vault-transit-key policy-authorization-v2 \
  --oidc-issuer http://127.0.0.1:8080/realms/domain-agent-development \
  --oidc-audience policy-service --required-scope order:submit \
  --tls-cert .local/mtls/policy-service.crt \
  --tls-key .local/mtls/policy-service.key \
  --tls-client-ca .local/mtls/ca.crt \
  --expected-client-dns order-client --port 8081
```

Verify the overlap with a newly issued v2 authorization. This obtains a fresh
OIDC token, asks Policy for an authorization, confirms the key ID, and sends
the result immediately to Executor (the development authorization TTL is
short):

```bash
TOKEN=$(
  curl -fsS -X POST \
    http://127.0.0.1:8080/realms/domain-agent-development/protocol/openid-connect/token \
    -H 'Content-Type: application/x-www-form-urlencoded' \
    -d 'grant_type=client_credentials' \
    -d 'client_id=order-client' \
    -d 'client_secret=order-client-dev-only' |
  python -c 'import json, sys; print(json.load(sys.stdin)["access_token"])'
)

curl --noproxy '*' -fsS \
  --cacert .local/mtls/ca.crt \
  --cert .local/mtls/order-client.crt \
  --key .local/mtls/order-client.key \
  --resolve policy-service:8081:127.0.0.1 \
  -X POST https://policy-service:8081/ \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"order_id":"ORD-ROTATION-1","product_id":"SKU-1","quantity":2}' \
  > /tmp/approved-order-v2.json

python -m json.tool /tmp/approved-order-v2.json | grep key_id

curl --noproxy '*' -fsS \
  --cacert .local/mtls/ca.crt \
  --cert .local/mtls/policy-service.crt \
  --key .local/mtls/policy-service.key \
  --resolve executor-service:8082:127.0.0.1 \
  -X POST https://executor-service:8082/ \
  -H 'Content-Type: application/json' \
  --data-binary @/tmp/approved-order-v2.json

echo
```

The output must identify `local-policy-key-v2` and report `"status":
"executed"`. Keep the overlap active for at least the maximum authorization
TTL, clock-skew allowance, transport delay, and retry window.

## 4. Retire the old key

After the planned overlap has elapsed, restart Executor with only the v2 key:

```bash
python -m agent_enterprise.executor_service \
  --trusted-public-key local-policy-key-v2=/tmp/policy-v2.pub \
  --postgres-replay-url postgresql+psycopg://agent:agent@localhost:5432/agent_atomic \
  --postgres-revocation-url postgresql+psycopg://agent:agent@localhost:5432/agent_atomic \
  --owned-action-type CREATE_ORDER \
  --effect-log /tmp/executor-effects-mtls.jsonl \
  --tls-cert .local/mtls/executor-service.crt \
  --tls-key .local/mtls/executor-service.key \
  --tls-client-ca .local/mtls/ca.crt \
  --expected-client-dns policy-service --port 8082
```

Verify v2 after retirement using the same commands from step 3, changing the
order ID to `ORD-ROTATION-2` and the output file to
`/tmp/approved-order-v2-retired.json`, so a new authorization is issued. It
must again report `local-policy-key-v2` and `"status": "executed"`.

After the overlap window has elapsed, an otherwise-valid authorization carrying
`key_id: local-policy-key` must fail with `Authorization key is unknown`.
Preserve the old Vault key and audit evidence according to the organization's
retention policy; do not destroy it merely because Executor no longer trusts it
for new execution.

## Automated contract

The overlap and retirement behavior is covered by:

```bash
pytest -q tests/test_enterprise_key_rotation.py
```
