#!/usr/bin/env bash
# Restore a backup.sh dump into ANY Postgres (Supabase / Neon / RDS / self-host).
# This is the "migrate away / migrate in" half of the portability guarantee.
#
# Usage:
#   deploy/supabase/restore.sh "postgresql://user:pass@host:5432/db" backups/backup_XXXX.dump
#
# Notes:
#   - Use the DIRECT (non-pooled) connection string (5432), not the pooler.
#   - Plain postgresql:// URL (libpq), no "+asyncpg"/"+psycopg" suffix.
#   - --clean --if-exists makes the restore idempotent onto an existing schema;
#     for a brand-new target DB it is simply a no-op on the drop step.
#   - Restores into an EMPTY or matching-schema database. For a fresh target,
#     you may instead run `alembic upgrade head` and skip restore if you only
#     need schema; use restore to move DATA between hosts.
set -euo pipefail

DB_URL="${1:-}"
DUMP_FILE="${2:-}"
if [[ -z "$DB_URL" || -z "$DUMP_FILE" ]]; then
  echo "usage: $0 <postgres_direct_url> <dump_file>" >&2
  exit 2
fi
if [[ ! -f "$DUMP_FILE" ]]; then
  echo "error: dump file not found: $DUMP_FILE" >&2
  exit 2
fi
if [[ "$DB_URL" == *"+asyncpg"* || "$DB_URL" == *"+psycopg"* ]]; then
  echo "error: strip the SQLAlchemy driver suffix; use a plain postgresql:// URL for pg_restore" >&2
  exit 2
fi

DUMP_DIR="$(cd "$(dirname "$DUMP_FILE")" && pwd)"
DUMP_BASE="$(basename "$DUMP_FILE")"

# Optional local-container networking (see backup.sh). Unset for remote hosts.
NET_ARGS=()
[[ -n "${DOCKER_NET:-}" ]] && NET_ARGS=(--network "$DOCKER_NET")

echo "==> Restoring $DUMP_FILE into target database"
docker run --rm -i \
  "${NET_ARGS[@]}" \
  -e PGCONNECT_TIMEOUT=15 \
  -v "$DUMP_DIR":/backups \
  postgres:16 \
  pg_restore --clean --if-exists --no-owner --no-privileges --verbose \
             --dbname="$DB_URL" "/backups/$DUMP_BASE"

echo "==> Restore complete."
echo "    Reminder: run 'alembic current' against the target to confirm the head revision."
