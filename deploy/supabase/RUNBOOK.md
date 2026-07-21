# Supabase (Postgres host) Runbook - Goal A: portable Postgres hosting

This runbook covers deploying the app onto **Supabase's Postgres** and migrating
**to/from** it, while keeping the codebase provider-neutral. The app treats
Supabase as one interchangeable Postgres backend; the only Supabase-specific
value is `DATABASE_URL`.

Everything here is verified by `apps/backend/scripts/verify_postgres.sh`, which
reproduces Supabase's topology (Postgres + PgBouncer transaction pooling)
locally and runs the full migration chain + the runtime harness.

## Key facts (why the steps are shaped this way)

- **Two endpoints, two jobs.** Supabase gives a **direct** connection (port
  `5432`) and a **pooled** connection (Transaction pooler, port `6543`).
  - App runtime -> **pooled** (`6543`) via `DATABASE_URL`, `DB_USE_POOLER=true`.
    asyncpg's server-side prepared statements are disabled in this mode by the
    app's engine options (`statement_cache_size=0`), required behind PgBouncer.
  - Migrations / DDL -> **direct** (`5432`) via `MIGRATION_DATABASE_URL`.
    `CREATE INDEX CONCURRENTLY` and the migration's session-level advisory lock
    are unsafe through a transaction pooler, so migrations must not use `6543`.
- **Migrations run automatically at startup** (`DB_AUTO_MIGRATE=true`, default).
  On boot the app brings the schema to `head` under a Postgres advisory lock
  (safe across multiple instances/workers - one migrates, the rest wait then
  no-op), using `MIGRATION_DATABASE_URL` (the direct endpoint) if set, else
  `DATABASE_URL`. A migration/connection failure aborts startup (fail-fast).
  Set `DB_AUTO_MIGRATE=false` to run migrations in a dedicated release step.
- **TLS is automatic.** Hosted mode defaults to `sslmode=require`; libpq
  `?sslmode=...` params in the URL are honored and translated per-driver (asyncpg
  rejects a raw `sslmode` kwarg). Override with `DB_SSL=verify-full` to also
  validate the CA chain.
- **Readiness probe.** `GET /api/v1/health/ready` returns 200 only when the DB
  and KVStore are reachable (else 503) - use it for the platform readiness gate.
  `GET /api/v1/health` stays a dependency-free liveness check.
- **Schema is owned by Alembic** (`0001`->`0014`). No Postgres extensions are
  required; the FTS layer uses core `to_tsvector`/GIN.
- **A `pg_dump` custom-format dump is the portable migration artifact** - it
  restores into any Postgres (Supabase / Neon / RDS / self-hosted).

---

## A. Fresh deploy onto Supabase

1. **Create the Supabase project**; copy both connection strings (Settings ->
   Database): the **Direct** (`5432`) and **Transaction pooler** (`6543`).
2. **Configure env** from `deploy/supabase/.env.hosted.example`:
   - `SINGLE_USER_MODE=false`, `DATABASE_URL=<pooled 6543>`, `DB_USE_POOLER=true`
   - `MIGRATION_DATABASE_URL=<direct 5432>` (so startup migration avoids the pooler)
   - `DB_SSL=require`, `DB_AUTO_MIGRATE=true`
   - `SESSION_SECRET`, `IP_HASH_SECRET` (generate; required in hosted mode)
   - `OWNER_EMAIL` (+ optional `OWNER_PASSWORD`), `FRONTEND_BASE_URL`, CORS
   - `STORAGE_PROVIDER=cloudinary` (+ creds) so files don't grow the DB
3. **Boot the app.** With `DB_AUTO_MIGRATE=true` the schema is migrated to head
   automatically at startup (on the direct endpoint, under an advisory lock)
   before traffic is served. To migrate manually instead, set
   `DB_AUTO_MIGRATE=false` and run:
   ```bash
   cd apps/backend
   ALEMBIC_DATABASE_URL="postgresql+asyncpg://postgres:<pwd>@db.<ref>.supabase.co:5432/postgres" \
     .venv/bin/python -m alembic upgrade head
   ```
4. **Gate traffic on readiness:** point the platform's readiness probe at
   `GET /api/v1/health/ready` (200 = DB + KVStore reachable).
5. **Smoke test** the critical paths: signup/login, resume upload+parse, global
   search (exercises the Postgres GIN FTS path), application tracker.

## B. Migrate an existing Postgres -> Supabase (data move)

1. **Backup the current DB** (direct endpoint of the source):
   ```bash
   deploy/supabase/backup.sh "postgresql://<user>:<pwd>@<old-host>:5432/<db>" ./backups
   ```
2. **Prepare the target**: either restore the dump (moves schema + data) OR run
   `alembic upgrade head` then restore data-only. Simplest is a full restore:
   ```bash
   deploy/supabase/restore.sh \
     "postgresql://postgres:<pwd>@db.<ref>.supabase.co:5432/postgres" \
     ./backups/backup_<stamp>.dump
   ```
3. **Verify the head revision** on the target:
   ```bash
   ALEMBIC_DATABASE_URL="postgresql+asyncpg://postgres:<pwd>@db.<ref>.supabase.co:5432/postgres" \
     .venv/bin/python -m alembic current   # expect: 0014 (head)
   ```
4. **Cutover**: point the app's `DATABASE_URL` at the Supabase **pooled**
   string, restart, smoke test (§A.5). Keep the old DB read-only until verified.

## C. Migrate away from Supabase -> another Postgres (the escape hatch)

Identical to §B with source/target swapped: `backup.sh` against Supabase's
**direct** endpoint, `restore.sh` into the new host, verify `alembic current`,
repoint `DATABASE_URL`, restart. **No code changes.** This is the portability
guarantee - it's why we did not build a Supabase-specific integration.

## D. Rollback

- **Bad cutover:** repoint `DATABASE_URL` back to the previous host and restart.
  Reversible in seconds if the old DB was kept read-only (not yet decommissioned).
- **Bad migration:** the chain is reversible (verified by the harness):
  ```bash
  ALEMBIC_DATABASE_URL="<direct-url>" .venv/bin/python -m alembic downgrade -1
  ```
  Prefer restoring the pre-migration `backup.sh` dump for data-affecting steps.

## E. Local verification before touching Supabase

```bash
cd apps/backend
scripts/verify_postgres.sh      # Postgres + PgBouncer(transaction) round-trip
```
Proves, against a real pooler: full `0001->0014` upgrade, full downgrade->upgrade
(reversibility), and the runtime data layer incl. the GIN FTS path and
prepared-statement safety. A green run here is the pre-flight for §A.

## F. Operational notes (Phase 2 - capacity/longevity)

- **Keep files out of Postgres.** `STORAGE_PROVIDER=cloudinary` (or `s3`) so
  avatars/uploads don't inflate DB size or backups - the main lever against a
  size-limited tier "filling up".
- **Back up regularly.** Schedule `backup.sh`; the dump doubles as your
  migrate-away artifact.
- **Watch DB size** against the tier limit; the app adds no extensions or heavy
  server-side objects, so growth is dominated by row data.
