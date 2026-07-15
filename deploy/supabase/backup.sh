#!/usr/bin/env bash
# Portable logical backup of the application database (Goal A: keep the ability
# to migrate away from any Postgres host, Supabase included).
#
# The dump IS the migration artifact: a `custom`-format pg_dump can be restored
# into ANY Postgres (Supabase, Neon, RDS, self-hosted) with restore.sh. Runs
# pg_dump inside the postgres:16 image, so no host psql/pg_dump is required.
#
# Usage:
#   deploy/supabase/backup.sh "postgresql://user:pass@host:5432/db" [out_dir]
#
# IMPORTANT: use the DIRECT (non-pooled) connection string for dumps — the
# transaction pooler (Supabase 6543) is not suited to long dump sessions. Use
# the 5432 direct endpoint. Do NOT pass the SQLAlchemy "+asyncpg"/"+psycopg"
# suffix here — this is libpq, so a plain postgresql:// URL.
set -euo pipefail

DB_URL="${1:-}"
OUT_DIR="${2:-./backups}"
if [[ -z "$DB_URL" ]]; then
  echo "usage: $0 <postgres_direct_url> [out_dir]" >&2
  exit 2
fi
if [[ "$DB_URL" == *"+asyncpg"* || "$DB_URL" == *"+psycopg"* ]]; then
  echo "error: strip the SQLAlchemy driver suffix; use a plain postgresql:// URL for pg_dump" >&2
  exit 2
fi

mkdir -p "$OUT_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_FILE="$OUT_DIR/backup_${STAMP}.dump"

# Optional: reach a Postgres running in a local Docker container/network. For a
# remote host (Supabase, etc.) leave DOCKER_NET unset. `DOCKER_NET=host` lets a
# URL of 127.0.0.1:<published-port> reach a locally published container.
NET_ARGS=()
[[ -n "${DOCKER_NET:-}" ]] && NET_ARGS=(--network "$DOCKER_NET")

echo "==> Dumping to $OUT_FILE (custom format, compressed)"
docker run --rm -i \
  "${NET_ARGS[@]}" \
  -e PGCONNECT_TIMEOUT=15 \
  -v "$(cd "$OUT_DIR" && pwd)":/backups \
  postgres:16 \
  pg_dump --format=custom --no-owner --no-privileges --verbose \
          --file="/backups/$(basename "$OUT_FILE")" "$DB_URL"

echo "==> Backup complete: $OUT_FILE"
ls -lh "$OUT_FILE"
