#!/usr/bin/env bash
# Initializes only the disposable Vault dev container's Transit key.
# Never use its token or this bootstrap pattern in production.
set -euo pipefail

vault_addr="${VAULT_ADDR:-http://127.0.0.1:8200}"
vault_token="${VAULT_TOKEN:-dev-root-token}"
key_name="${VAULT_TRANSIT_KEY:-policy-authorization}"

request() {
  curl -fsS -H "X-Vault-Token: ${vault_token}" -H 'Content-Type: application/json' "$@"
}

if ! request "${vault_addr}/v1/sys/mounts/transit" >/dev/null 2>&1; then
  request --request POST --data '{"type":"transit"}' "${vault_addr}/v1/sys/mounts/transit" >/dev/null
fi

if ! request "${vault_addr}/v1/transit/keys/${key_name}" >/dev/null 2>&1; then
  request --request POST --data '{"type":"ed25519"}' "${vault_addr}/v1/transit/keys/${key_name}" >/dev/null
fi

echo "Vault Transit development key is ready: ${key_name}"
