# Manual enterprise authorization proof

This runbook reproduces the local, end-to-end security boundary using the
development Compose profile. It proves this path:

```text
Keycloak client token
→ Policy OIDC validation + caller mTLS
→ Vault Transit authorization signing
→ Executor Policy mTLS + signature verification
→ PostgreSQL replay claim
→ one effect only
```

This is a **local development/staging proof**, not a production deployment.
The Keycloak client secrets, Vault development token, and step-ca provisioner
password used below are intentionally disposable.

## Prerequisites

From the repository root, create and start the local services:

```bash
docker compose -f docker-compose.redpanda.yml up -d
docker compose -f docker-compose.enterprise.yml up -d

source .venv-3.12/bin/activate
bash scripts/bootstrap_vault_development_transit.sh
```

Wait until Keycloak, step-ca, and Vault are available:

```bash
curl -fsS http://127.0.0.1:8080/health/ready
curl -kfsS https://127.0.0.1:9000/health
curl -fsS http://127.0.0.1:8200/v1/sys/health
```

If the Keycloak realm JSON has changed since the enterprise profile was first
started, reset only its disposable volumes before proceeding:

```bash
docker compose -f docker-compose.enterprise.yml down -v
docker compose -f docker-compose.enterprise.yml up -d
```

## Create development workload certificates

The generated files live under `.local/mtls/`, which is ignored by Git.

```bash
mkdir -p .local/mtls
docker compose -f docker-compose.enterprise.yml cp \
  step-ca:/home/step/certs/root_ca.crt .local/mtls/ca.crt
```

Issue certificates for the three workload identities:

```bash
for name in order-client policy-service executor-service; do
  docker compose -f docker-compose.enterprise.yml exec step-ca sh -lc "
    token=\$(step ca token ${name} --san ${name} \
      --password-file /home/step/secrets/password)
    step ca certificate ${name} /home/step/certs/${name}.crt \
      /home/step/secrets/${name}.key --token \"\$token\"
  "
  docker compose -f docker-compose.enterprise.yml cp \
    step-ca:/home/step/certs/${name}.crt .local/mtls/${name}.crt
  docker compose -f docker-compose.enterprise.yml cp \
    step-ca:/home/step/secrets/${name}.key .local/mtls/${name}.key
  chmod 600 .local/mtls/${name}.key
done
```

Confirm the material exists. Do not commit it:

```bash
ls -l .local/mtls
git check-ignore -v .local/mtls/order-client.key
```

## Start Policy

In terminal 1, start Policy. It intentionally stays in the foreground without
a startup banner.

```bash
cd /Users/stefanorocco/apps/domain-agent-framework
source .venv-3.12/bin/activate

python -m agent_enterprise.policy_service \
  --public-key-out /tmp/policy.pub \
  --vault-url http://127.0.0.1:8200 \
  --vault-token dev-root-token \
  --vault-transit-key policy-authorization \
  --oidc-issuer http://127.0.0.1:8080/realms/domain-agent-development \
  --oidc-audience policy-service \
  --required-scope order:submit \
  --tls-cert .local/mtls/policy-service.crt \
  --tls-key .local/mtls/policy-service.key \
  --tls-client-ca .local/mtls/ca.crt \
  --expected-client-dns order-client \
  --port 8081
```

## Start Executor

In terminal 2, start Executor. It trusts only the `policy-service` mTLS
identity, owns `CREATE_ORDER`, and stores replay/revocation evidence in the
local PostgreSQL container.

```bash
cd /Users/stefanorocco/apps/domain-agent-framework
source .venv-3.12/bin/activate

python -m agent_enterprise.executor_service \
  --public-key /tmp/policy.pub \
  --postgres-replay-url postgresql+psycopg://agent:agent@localhost:5432/agent_atomic \
  --postgres-revocation-url postgresql+psycopg://agent:agent@localhost:5432/agent_atomic \
  --owned-action-type CREATE_ORDER \
  --effect-log /tmp/executor-effects-mtls.jsonl \
  --tls-cert .local/mtls/executor-service.crt \
  --tls-key .local/mtls/executor-service.key \
  --tls-client-ca .local/mtls/ca.crt \
  --expected-client-dns policy-service \
  --port 8082
```

## Prove one authorized execution

In terminal 3, obtain a Keycloak token, request Policy authorization over
mTLS, and immediately execute it over the Policy-to-Executor mTLS channel.
Authorizations are intentionally short-lived (30 seconds), so keep these
commands together.

```bash
TOKEN=$(curl -fsS -X POST \
  http://127.0.0.1:8080/realms/domain-agent-development/protocol/openid-connect/token \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d 'grant_type=client_credentials' \
  -d 'client_id=order-client' \
  -d 'client_secret=order-client-dev-only' |
  python -c 'import json, sys; print(json.load(sys.stdin)["access_token"])')

curl --noproxy '*' -fsS \
  --cacert .local/mtls/ca.crt \
  --cert .local/mtls/order-client.crt \
  --key .local/mtls/order-client.key \
  --resolve policy-service:8081:127.0.0.1 \
  -X POST https://policy-service:8081/ \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"order_id":"ORD-MTLS-REPRO","product_id":"SKU-1","quantity":2}' \
  > /tmp/approved-order-mtls.json

curl --noproxy '*' -fsS \
  --cacert .local/mtls/ca.crt \
  --cert .local/mtls/policy-service.crt \
  --key .local/mtls/policy-service.key \
  --resolve executor-service:8082:127.0.0.1 \
  -X POST https://executor-service:8082/ \
  -H 'Content-Type: application/json' \
  --data-binary @/tmp/approved-order-mtls.json

echo
```

Expected response:

```json
{"action_id":"…","status":"executed"}
```

## Prove the negative boundaries

### Replay is rejected

Resend the same envelope. Expected result: `Authorization has already been
used`.

```bash
curl --noproxy '*' -sS \
  --cacert .local/mtls/ca.crt \
  --cert .local/mtls/policy-service.crt \
  --key .local/mtls/policy-service.key \
  --resolve executor-service:8082:127.0.0.1 \
  -X POST https://executor-service:8082/ \
  -H 'Content-Type: application/json' \
  --data-binary @/tmp/approved-order-mtls.json

echo
```

### Wrong workload identity is rejected

Use `order-client` to contact Executor. The certificate is trusted by the CA,
but it is not the configured Policy workload. Expected result: `mTLS client
identity is not authorized`.

```bash
curl --noproxy '*' -sS \
  --cacert .local/mtls/ca.crt \
  --cert .local/mtls/order-client.crt \
  --key .local/mtls/order-client.key \
  --resolve executor-service:8082:127.0.0.1 \
  -X POST https://executor-service:8082/ \
  -H 'Content-Type: application/json' \
  --data-binary @/tmp/approved-order-mtls.json

echo
```

### Expired authorization is rejected

Wait longer than 30 seconds after saving a newly approved envelope, then send
it with the correct Policy certificate. Expected result: `Authorization is
expired or has an invalid time window`.

## Automated equivalent

The manual proof is mirrored by automated checks:

```bash
KEYCLOAK_OIDC_ISSUER=http://127.0.0.1:8080/realms/domain-agent-development \
VAULT_TRANSIT_URL=http://127.0.0.1:8200 VAULT_TRANSIT_TOKEN=dev-root-token \
  pytest -q tests/test_enterprise_end_to_end_integration.py
```

For a no-downtime Vault signing-key transition, follow the
[key rotation runbook](enterprise-key-rotation.md).

## Cleanup

Stop Policy and Executor with `Ctrl+C` in their terminals. Stop the security
profile when it is no longer needed:

```bash
docker compose -f docker-compose.enterprise.yml down
```

To remove its disposable Keycloak and step-ca data as well, use `down -v`.
That does not touch the separate Redpanda/PostgreSQL development stack.
