#!/usr/bin/env bash
# Verify the framework in layers: deterministic tests first, then the optional
# local Redpanda/PostgreSQL proof when Docker and its Python dependencies exist.
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

compose=(docker compose -f docker-compose.redpanda.yml)
started_stack=false

cleanup() {
  local status=$?
  if [[ "${started_stack}" == true && "${KEEP_INFRA_STACK:-0}" != "1" ]]; then
    "${compose[@]}" down >/dev/null || true
  fi
  exit "${status}"
}
trap cleanup EXIT

echo "==> Deterministic unit and conformance checks"
# Do not let an exported local broker/database URL silently turn this tier into
# a partial infrastructure run. The selected integration proofs run below.
"${python_bin}" -m pytest -q -m 'not integration'

if [[ "${VERIFY_INFRA:-1}" == "0" ]]; then
  echo "SKIPPED: infrastructure checks disabled (VERIFY_INFRA=0)."
  exit 0
fi

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  echo "SKIPPED: infrastructure checks require Docker Compose."
  exit 0
fi

if ! "${python_bin}" -c 'import confluent_kafka, psycopg' >/dev/null 2>&1; then
  echo "SKIPPED: infrastructure checks require confluent-kafka and psycopg in ${python_bin}."
  exit 0
fi

if [[ -z $("${compose[@]}" ps -aq) ]]; then
  started_stack=true
fi

echo "==> Starting Redpanda and PostgreSQL"
"${compose[@]}" up -d

echo "==> Waiting for Redpanda and PostgreSQL"
for _ in $(seq 1 30); do
  if "${compose[@]}" exec -T postgres pg_isready -U agent -d agent_atomic >/dev/null 2>&1 \
    && "${compose[@]}" exec -T redpanda rpk cluster health >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

if ! "${compose[@]}" exec -T postgres pg_isready -U agent -d agent_atomic >/dev/null 2>&1 \
  || ! "${compose[@]}" exec -T redpanda rpk cluster health >/dev/null 2>&1; then
  echo "FAILED: Redpanda/PostgreSQL did not become ready." >&2
  "${compose[@]}" ps >&2 || true
  "${compose[@]}" logs >&2 || true
  exit 1
fi

echo "==> Broker and database integration checks"
POSTGRES_ATOMIC_DATABASE_URL='postgresql+psycopg://agent:agent@localhost:5432/agent_atomic' \
REDPANDA_BOOTSTRAP_SERVERS='localhost:19092' \
  "${python_bin}" -m pytest -q \
    tests/test_postgres_atomicity_integration.py \
    tests/test_postgres_redpanda_receipt_integration.py \
    agent-redpanda/tests/test_reconciliation_integration.py

echo "PASS: deterministic and Redpanda/PostgreSQL infrastructure checks completed."
