# Enterprise security integration profile

`docker-compose.enterprise.yml` is an optional **development/staging** profile
for testing the controls that surround `agent-enterprise`:

| Service | Local endpoint | What it tests |
| --- | --- | --- |
| Keycloak | `http://127.0.0.1:8080` | OIDC client/service identity and JWT validation integration. |
| Vault | `http://127.0.0.1:8200` | Signing-key retrieval and rotation workflow integration. |
| step-ca | `https://127.0.0.1:9000` | Policy-to-Executor certificate issuance and mTLS trust wiring. |

For a complete, reproducible manual proof using all three services, follow
[the enterprise authorization runbook](enterprise-manual-e2e.md).

## One-command local verification

After activating the repository's Python environment, run:

```bash
bash scripts/verify_enterprise_stack.sh
```

The verifier starts this Compose profile when needed, waits for Keycloak,
Vault, and step-ca, initializes the disposable Transit key, proves step-ca can
issue the documented workload identities, and runs the real Keycloak, Vault,
mTLS, and replay tests. It removes only an enterprise profile that it started
itself. If the profile was already running, it leaves it running. Set
`KEEP_ENTERPRISE_STACK=1` to retain a newly started profile for inspection.

This is a local development/staging acceptance check. Its temporary
certificates and all Compose credentials are disposable; it is not a
production enrollment or deployment workflow.

Start it separately from the normal broker/database stack:

```bash
docker compose -f docker-compose.enterprise.yml up -d
docker compose -f docker-compose.enterprise.yml ps
curl -fsS http://127.0.0.1:8080/health/ready
```

The profile uses its own Compose project name, so it will not treat the normal
Redpanda/PostgreSQL stack as an orphan. Do **not** add `--remove-orphans` to a
command run before this updated file is in use: the older local project name
was shared with the normal stack.

The Keycloak development realm is imported from
[`infra/keycloak/domain-agent-development-realm.json`](../infra/keycloak/domain-agent-development-realm.json).
Its bootstrap credentials and every secret in this Compose file are public,
deliberately weak development values. They must never be reused outside a
local integration environment.

Keycloak imports the development realm only when its database is first created.
After updating the realm JSON in this repository, reset **only this disposable
enterprise profile** to re-import it:

```bash
docker compose -f docker-compose.enterprise.yml down -v
docker compose -f docker-compose.enterprise.yml up -d
```

This removes the enterprise profile's Keycloak and step-ca volumes. It does not
touch the separate Redpanda/PostgreSQL development stack.

## What this profile does not provide

It is not a production KMS/HSM, identity plane, or certificate authority:

- Vault is launched in `-dev` mode with an in-memory store and a known root
  token. It is useful only for exercising a key-provider integration.
- Keycloak uses `start-dev`, bootstrap credentials, and local HTTP. Production
  needs TLS, backups, restricted administration, and an organization-specific
  issuer/audience/role policy.
- step-ca stores a local development CA in a Docker volume. Production needs
  controlled CA administration, service enrollment, renewal/revocation, and
  protected root/intermediate keys.

For production, replace these containers with the organization’s OIDC
provider, workload-identity/mTLS platform, and KMS/HSM (or a properly operated
Vault cluster). Configure `agent-enterprise` with shared PostgreSQL replay and
revocation stores. Before any Policy or Executor replica starts, run the
versioned `python -m agent_enterprise.migrate --database-url ...` deployment
job, then prove these gates in staging:

1. An unauthenticated caller cannot obtain a Policy authorization.
2. Policy and Executor reject an untrusted or expired mTLS certificate.
3. Policy signs through a managed/non-exportable key and key rotation accepts
   the new key while retiring the old one on schedule.
4. A revocation becomes visible to every Executor within the documented SLO.
5. Two Executor replicas share the PostgreSQL replay claim and only one can
   execute an authorization.

The Enterprise Policy reference can validate Keycloak JWTs through local JWKS
verification. Start it with:

```bash
python -m agent_enterprise.policy_service \
  --private-key /tmp/policy.pem --public-key-out /tmp/policy.pub \
  --oidc-issuer http://127.0.0.1:8080/realms/domain-agent-development \
  --oidc-audience policy-service --required-scope order:submit
```

The local Compose profile does not automatically enroll application
certificates. The Enterprise
HTTP reference does support mTLS server contexts:
pass `--tls-cert`, `--tls-key`, `--tls-client-ca`, and exactly one of
`--expected-client-dns` or `--expected-client-uri` to Policy or Executor. Use
step-ca to issue one certificate per workload identity. The reference accepts
only one exact DNS SAN (for example `order-client`, `policy-service`, or
`executor-service`) or one exact URI SAN such as
`spiffe://example.org/workload/order-client`; it never falls back to common
name or wildcard matching. Mount the resulting files into workloads and
configure each listener to trust the CA and expect its one authorized peer
identity.

This profile remains the integration environment for those deployment-specific
adapters without putting OIDC, TLS, or KMS dependencies in `agent-core`.

After resetting the disposable profile following a realm-JSON change, prove
that Keycloak-issued client credentials satisfy the same validator used by
Policy:

```bash
KEYCLOAK_OIDC_ISSUER=http://127.0.0.1:8080/realms/domain-agent-development \
  pytest -q tests/test_enterprise_keycloak_integration.py
```

Initialize the disposable Vault Transit key, then run the real-Vault proof:

```bash
bash scripts/bootstrap_vault_development_transit.sh
VAULT_TRANSIT_URL=http://127.0.0.1:8200 VAULT_TRANSIT_TOKEN=dev-root-token \
  pytest -q tests/test_enterprise_vault_transit_integration.py
```

Run the complete OIDC → Vault Transit → mTLS → Executor replay proof:

```bash
KEYCLOAK_OIDC_ISSUER=http://127.0.0.1:8080/realms/domain-agent-development \
VAULT_TRANSIT_URL=http://127.0.0.1:8200 VAULT_TRANSIT_TOKEN=dev-root-token \
  pytest -q tests/test_enterprise_end_to_end_integration.py
```

The Policy CLI can then sign without a local `--private-key`:

```bash
python -m agent_enterprise.policy_service --public-key-out /tmp/policy.pub \
  --vault-url http://127.0.0.1:8200 --vault-token dev-root-token \
  --vault-transit-key policy-authorization
```
