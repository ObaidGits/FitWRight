#!/usr/bin/env bash
# End-to-end Supabase-readiness verification (Goal A: portable Postgres hosting).
#
# Stands up real Postgres + a transaction-mode PgBouncer (Supabase's topology),
# then proves the project runs on it unmodified:
#
#   1. alembic upgrade head   on the DIRECT connection   (schema applies on PG)
#   2. alembic downgrade base then upgrade head           (migrations reversible)
#   3. scripts/verify_postgres.py via the POOLED endpoint (runtime + GIN FTS +
#                                                          prepared-stmt safety)
#
# Usage:
#   scripts/verify_postgres.sh          # spin up, verify, tear down
#   KEEP=1 scripts/verify_postgres.sh   # leave the stack running afterwards
#
# Requires: docker (compose v2) and the backend virtualenv (.venv). Run from the
# backend dir (apps/backend). Exit code 0 = everything passed.
set -euo pipefail

cd "$(dirname "$0")/.."   # -> apps/backend

COMPOSE="docker compose -f scripts/verify-postgres.compose.yml"
PY="${PYTHON:-.venv/bin/python}"
PG_DIRECT_PORT="${PG_DIRECT_PORT:-55432}"
PG_POOLED_PORT="${PG_POOLED_PORT:-56432}"

DIRECT_URL="postgresql+asyncpg://verify:verify@127.0.0.1:${PG_DIRECT_PORT}/verifydb"
POOLED_URL="postgresql+asyncpg://verify:verify@127.0.0.1:${PG_POOLED_PORT}/verifydb"

cleanup() {
  if [[ "${KEEP:-0}" != "1" ]]; then
    echo "==> Tearing down harness"
    $COMPOSE down -v >/dev/null 2>&1 || true
  else
    echo "==> KEEP=1: leaving stack up (direct :${PG_DIRECT_PORT}, pooled :${PG_POOLED_PORT})"
  fi
}
trap cleanup EXIT

echo "==> Starting Postgres + PgBouncer (transaction mode)"
$COMPOSE up -d --wait

echo "==> [1/3] alembic upgrade head (DIRECT connection)"
ALEMBIC_DATABASE_URL="$DIRECT_URL" "$PY" -m alembic upgrade head

echo "==> [2/3] alembic downgrade base -> upgrade head (reversibility)"
ALEMBIC_DATABASE_URL="$DIRECT_URL" "$PY" -m alembic downgrade base
ALEMBIC_DATABASE_URL="$DIRECT_URL" "$PY" -m alembic upgrade head

echo "==> [3/3] runtime harness via POOLED endpoint (transaction pooling)"
DATABASE_URL="$POOLED_URL" DB_USE_POOLER=true SINGLE_USER_MODE=true \
  "$PY" scripts/verify_postgres.py

echo "==> VERIFICATION PASSED: project runs on Postgres behind a transaction pooler."
