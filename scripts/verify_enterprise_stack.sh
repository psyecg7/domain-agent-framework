#!/usr/bin/env bash
# Run the disposable enterprise security proof against real local services.
#
# This is a development/staging verifier, never a deployment script. It starts
# only docker-compose.enterprise.yml, initializes its Vault Transit key,
# exercises Keycloak/Vault through pytest, and proves step-ca can issue the
# workload identities used by the manual runbook.
set -euo pipefail

repository_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "${repository_root}"

if [[ -n "${PYTHON_BIN:-}" ]]; then
  python_bin=${PYTHON_BIN}
elif [[ -x .venv-3.12/bin/python ]]; then
  python_bin=.venv-3.12/bin/python
else
  python_bin=python
fi
compose=(docker compose -f docker-compose.enterprise.yml)
run_directory=$(mktemp -d "${TMPDIR:-/tmp}/agent-enterprise-verify.XXXXXX")
profile_was_absent=false
issued_container_files=()

cleanup() {
  local status=$?
  local issued_file
  for issued_file in "${issued_container_files[@]}"; do
    "${compose[@]}" exec -T step-ca sh -lc \
      "rm -f /home/step/certs/${issued_file}.crt /home/step/secrets/${issued_file}.key" \
      >/dev/null 2>&1 || true
  done
  rm -rf "${run_directory}"
  if [[ "${profile_was_absent}" == true && "${KEEP_ENTERPRISE_STACK:-0}" != "1" ]]; then
    "${compose[@]}" down >/dev/null || true
  fi
  exit "${status}"
}
trap cleanup EXIT

command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }
command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }
command -v openssl >/dev/null || { echo "openssl is required" >&2; exit 1; }
"${python_bin}" -c 'import agent_enterprise' >/dev/null || {
  echo "${python_bin} cannot import agent_enterprise; activate the project virtual environment first" >&2
  exit 1
}

if [[ -z $("${compose[@]}" ps -aq) ]]; then
  profile_was_absent=true
fi
"${compose[@]}" up -d

wait_for() {
  local url=$1
  local name=$2
  for _ in $(seq 1 60); do
    if curl -kfsS "${url}" >/dev/null; then
      return 0
    fi
    sleep 1
  done
  echo "${name} did not become ready within 60 seconds" >&2
  return 1
}

wait_for http://127.0.0.1:8080/health/ready Keycloak
wait_for https://127.0.0.1:9000/health step-ca
wait_for http://127.0.0.1:8200/v1/sys/health Vault

bash scripts/bootstrap_vault_development_transit.sh

# The HTTP E2E pytest uses temporary certs so it never writes keys into the
# repository. These checks additionally prove the Compose CA can issue the
# identities used by the documented service deployment.
"${compose[@]}" cp step-ca:/home/step/certs/root_ca.crt "${run_directory}/ca.crt"
"${compose[@]}" cp step-ca:/home/step/certs/intermediate_ca.crt "${run_directory}/intermediate-ca.crt"
for identity in order-client policy-service executor-service; do
  container_name="${identity}-verify-$$"
  issued_container_files+=("${container_name}")
  "${compose[@]}" exec -T step-ca sh -lc "
    token=\$(step ca token '${identity}' --san '${identity}' \\
      --password-file /home/step/secrets/password)
    step ca certificate '${identity}' \\
      /home/step/certs/${container_name}.crt \\
      /home/step/secrets/${container_name}.key \\
      --token \"\$token\"
  "
  "${compose[@]}" cp "step-ca:/home/step/certs/${container_name}.crt" "${run_directory}/${identity}.crt"
  openssl verify \
    -CAfile "${run_directory}/ca.crt" \
    -untrusted "${run_directory}/intermediate-ca.crt" \
    "${run_directory}/${identity}.crt" >/dev/null
done

KEYCLOAK_OIDC_ISSUER=http://127.0.0.1:8080/realms/domain-agent-development \
VAULT_TRANSIT_URL=http://127.0.0.1:8200 \
VAULT_TRANSIT_TOKEN=dev-root-token \
  "${python_bin}" -m pytest -q \
  tests/test_enterprise_keycloak_integration.py \
  tests/test_enterprise_vault_transit_integration.py \
  tests/test_enterprise_end_to_end_integration.py \
  tests/test_enterprise_mtls.py

echo "PASS: enterprise OIDC, Vault Transit, mTLS transport, replay, and step-ca enrollment verified."
