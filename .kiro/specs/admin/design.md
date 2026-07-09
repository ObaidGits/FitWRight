# Design — P2 Admin

## Overview

Inherits ADRs/standards from `../phase-2-roadmap.md`; depends on P1 (RBAC,
`audit_log`, sessions + session-cache, user-scoping, KVStore, Alembic). This
document makes every decision needed to implement a secure, observable, scalable
admin subsystem: capability-based access, dashboards + a daily rollup, cursor-
paginated user search + audited detail, a recoverable grace-period deletion +
purge pipeline, role management, an append-only audit view, and the frontend
rewire — all correct under concurrency and at scale.

## Architecture

Admin is a set of `/api/v1/admin/*` endpoints guarded by capability dependencies,
plus the existing Atelier admin UI (ui-revamp Task 15) rewired from mock
`adminApi` to real calls. Cross-user reads — the **only** ones allowed in the
product — go through an **isolated `AdminRepo`** so they are centralized,
reviewable, and exempt (by allowlist) from the P1 "no unscoped query" CI guard,
while every ordinary repo stays user-scoped.

```
admin UI (app/admin/*) ─ adminApi ─► /api/v1/admin/* ─ require_capability ─► AdminService
                                                              │
                    ┌─────────────────────────────────────────┼───────────────────────────┐
                    ▼                         ▼                ▼                             ▼
               AdminRepo (cross-user   MetricsService     LifecycleService            AuditService
               reads, allowlisted)     (rollup + live)    (disable/role/delete/restore) (append-only)
                    │                         │                │                             │
                    └── DB (indexed) ─────────┴── metrics_daily┴── sessions/KVStore ─────────┘
Scheduled jobs: RollupJob (nightly, UTC) · PurgeJob (grace-elapsed) — single-flighted (KVStore lock); run under SCHEDULER_MODE (ADR-15): free=external_cron → authenticated internal endpoint; premium=internal (APScheduler/worker)
```

Layering rule: routers never touch the ORM; they call services; only `AdminRepo`
issues cross-user queries.

## Data Models

New/changed tables (Alembic migrations, forward + reversible):

- `users` (P1) gains:
  - `deleted_at: str?` (iso; soft-delete marker) — index `(deleted_at)`.
  - `resume_count: int default 0`, `application_count: int default 0`
    (denormalized usage counters, maintained incrementally by the owning
    services or reconciled by the rollup) — avoids per-row N+1 (R11.3).
  - `last_active_at: str?` (updated from session activity; index for
    active-user calc).
- `metrics_daily`: `day_utc: str, metric: str, value: int, computed_at: str`,
  PK `(day_utc, metric)`. UPSERT target (R10.3). Only CLOSED UTC days written.
- Indexes (created `CONCURRENTLY` on Postgres; SQLite recreates offline):
  `users(status)`, `users(role, status)` (active-admin count),
  `users(created_at, id)` (list sort/cursor), `users(deleted_at)`,
  `users(email)` (unique, prefix search), `users(last_active_at)`,
  `sessions(user_id, revoked_at, last_seen_at)` (active-user + revoke),
  `audit_log(ts, id)`, `audit_log(event, ts)`, `audit_log(actor_user_id, ts)`,
  `audit_log(target_user_id, ts)`.
- **Capabilities**: no table in P2 — `admin.read`/`admin.manage` are derived from
  role=`admin`. The derivation lives in one `capabilities_for(user)` function so a
  future `roles`/`capabilities` table is a drop-in (extension point, R1.3/R14.5).

Metric definitions (authoritative, UTC days) — the **metric registry**:
| metric | daily value | live "today" |
|---|---|---|
| `signups` | users created that day | count today |
| `active_users` | distinct users with session activity that day | distinct today |
| `resumes_tailored` | improvements created that day | count today |
Overview "active users (last N days)" is a separate live windowed distinct over
`sessions.last_seen_at` — distinct from the daily series (R2.1 vs R3.1).

## Components and Interfaces

### API design (`/api/v1/admin`, capability-guarded, standard envelope)
| Method | Path | Capability | Purpose |
|---|---|---|---|
| GET | `/stats` | admin.read | overview stats (+ `computed_at`, `stale`) |
| GET | `/usage-series?metric=&window=` | admin.read | daily series (rollup+live) |
| GET | `/users?cursor=&q=&status=&role=&verified=&deleted=` | admin.read | user list |
| GET | `/users/{id}` | admin.read | detail (audited; content-free) |
| PATCH | `/users/{id}` | admin.manage | set `status` and/or `role` (distinct audit events) |
| POST | `/users/{id}/disable` · `/enable` | admin.manage | explicit status ops (idempotent) |
| POST | `/users/bulk-disable` | admin.manage | bounded batch disable (per-target audit) |
| POST | `/users/{id}/delete` | admin.manage | soft-delete (typed email confirm) |
| POST | `/users/{id}/restore` | admin.manage | restore within grace period |
| GET | `/audit?cursor=&event=&actor=&target=&from=&to=` | admin.read | audit view |

- Lists return `{ items, next_cursor }`; cursor = base64url of `(sort_key, id)`;
  default sort `created_at desc, id desc`; stable tie-break by `id`.
- Mutations require the P1 CSRF token. **Delete uses `POST …/delete`** (not
  `DELETE` with a body) so intermediaries can't strip the confirmation, and the
  body `{ "email": "<target email>" }` must match (R14.1).
- `PATCH /users/{id}` and the explicit `POST` ops both funnel through
  `LifecycleService`; a no-op (already in target state) returns `{ changed:false }`
  with no audit (R6.5).
- Response models are explicit Pydantic `AdminUserRow` / `AdminUserDetail` /
  `AdminStats` / `UsageSeries` / `AuditEntry` with an **allowlist**; a
  serialization test asserts hashes/tokens/keys never appear (R14.3).

### AdminRepo (isolated cross-user reads)
- The single module permitted to query without `user_id` scoping. Exposes typed,
  read-only aggregate methods; annotated so the P1 unscoped-query CI guard
  allowlists exactly this file. No write methods (writes go through services with
  their own scoping/txn rules).

### Runtime settings store (free/premium toggles — ADR-14)
- The admin subsystem owns admin-tunable **runtime** settings, persisted in a
  `settings` table (or the existing config store) and exposed via capability-guarded
  `GET/PUT /api/v1/admin/settings` (`admin.read`/`admin.manage`). Every change is
  audited on the append-only `audit_log` (`admin.setting_changed`) and defaults are
  conservative (free-tier safe) so an unconfigured deploy stays within free limits.
- Canonical runtime toggles (ADR-14): `keepalive_enabled` /
  `keepalive_interval_minutes` / `keepalive_target_url`; `notification_transport` /
  `polling_interval_seconds`; `ai_rate_limit_per_user` / `ai_daily_token_cap`; and
  upload caps / cache TTLs / page sizes.
- Env-var (deploy-time) toggles are **not** in this store — `DATABASE_URL`,
  `KVSTORE_URL`, `STORAGE_PROVIDER`, and `SCHEDULER_MODE` are environment config,
  chosen once per environment, and read at startup rather than through the settings
  API.

### Frontend interface
- Keep the typed `lib/api/admin.ts` shape; swap mock → real fetch. Extend for:
  audit list + filters, restore action, bulk-disable, `computed_at`/`stale`,
  cursor pagination, capability-aware rendering.

## Metrics & rollup

- `RollupJob` (nightly, UTC, single-flighted): for each just-closed UTC day and
  each registry metric, compute the value via an indexed query and UPSERT into
  `metrics_daily`. A `backfill(from,to)` command populates history and is
  idempotent.
- Series endpoint: read closed days from `metrics_daily`; compute the current
  partial day live; concatenate (never double-count — rollup only writes closed
  days). Unknown metric → 400 `unknown_metric`.
- Stats endpoint: rollup-derived where possible + bounded live-today; cached in
  KVStore (60s TTL, keyed by params) with `computed_at`; on live/rollup failure,
  return last cache + `stale:true` (R2.4).
- Usage counters (`resume_count`/`application_count`, `last_active_at`) are
  maintained incrementally by the owning P1/P3 services; a reconciliation pass in
  RollupJob corrects drift.

## Deletion, grace & purge

1. `POST /users/{id}/delete` (typed email match): atomic last-active-admin guard
   (below) → set `deleted_at=now`, `status=disabled`, revoke sessions
   (+ P1 session-cache invalidation) → audit `user.soft_deleted`. Reversible.
2. `POST /users/{id}/restore` (within grace): clear `deleted_at`; status stays
   `disabled` (admin re-enables explicitly) → audit `user.restored`.
3. `PurgeJob` (scheduled, single-flighted): select users with
   `deleted_at < now - grace`; for each, in one transaction, delete owned rows in
   **FK-safe order** (improvements → applications → jobs → resumes →
   resume_versions/notifications/etc. → api_keys → sessions → oauth_identities →
   user), chunked; audit `user.purged`. Idempotent + resumable.
4. **Audit is never purged**; the purged user's `audit_log` rows keep their ids +
   metadata (the user PII row is gone). This satisfies erasure of personal data
   while preserving the security trail (R8.4). `target_user_id` becomes a dangling
   id by design (documented).
5. Backups (R12.4) run on the RPO schedule so a purged user is recoverable within
   RPO if needed.

### Atomic last-active-admin guard
Any disable/demote/delete of an admin executes, inside the write transaction,
a conditional check: the write is applied only if
`(SELECT count(*) FROM users WHERE role='admin' AND status='active' AND deleted_at IS NULL AND id <> :target) >= 1`
using `SELECT … FOR UPDATE` on the candidate admin rows (or an equivalent atomic
conditional UPDATE). Concurrent demotions therefore serialize; the second fails
with 409 `last_active_admin` (R10.2, R6.3, R7.2, R8.1).

## Security

Threat model & mitigations:
| Threat | Mitigation |
|---|---|
| Vertical priv-esc (non-admin → admin API) | capability dep on every route; 401/403 + audit; status re-checked server-side each request (not from cache) |
| Disabled/deleted admin still acting | per-request status recheck (R1.2); disable revokes sessions + cache |
| Self-lockout / zero admins | atomic active-admin guard across disable/demote/delete |
| Cross-user content/secret exposure | AdminRepo returns aggregates + allowlisted metadata only; serialization test bans hashes/tokens/keys; api-keys surfaced as `ai_configured:bool` |
| Untraceable admin snooping | sensitive reads audited (`admin.user_viewed`); per-admin rate limits + volume alerts |
| CSRF on mutations | P1 CSRF token required on all writes incl. `POST …/delete`; no body-stripping transport |
| Audit tampering / erasure | append-only; no mutate/delete API; purge excludes audit_log |
| Enumeration / scraping | admin-only + per-admin rate limit + bounded pages + audit + alert on abnormal volume |
| Log/CRLF injection via `q`/`meta` | sanitize + length-bound before logging/persisting |
| Mass destructive abuse (compromised admin) | bulk destructive out of scope; deletes have grace + confirm + alert; `ADMIN_DESTRUCTIVE_ACTIONS` kill-switch |
| Future least-privilege | capability model lets destructive ops require `superadmin` with no API change |

## Reliability & concurrency

- Idempotent mutations; no-op detection (R6.5). Atomic active-admin guard (above)
  prevents lost-update lockout. Rollup/purge single-flighted with KVStore lock
  (TTL + stuck-lock recovery) and resumable; rollup UPSERTs. Optimistic UI only
  for reversible toggles; delete is pessimistic (R10.4). Session revocation on
  disable/delete/role-change reuses the P1 revoke + cache-invalidate path so no
  stale session authorizes.

## Performance & scalability

- Cursor (keyset) pagination + composite indexes on all lists; no offset scans.
- Dashboards O(1) from rollup + bounded live-today; denormalized usage counters
  (no N+1). Search is index-usable (email prefix / name FTS-or-trigram), not
  `%q%`. Audit + metrics time-partition/rotate by month; audit view virtualized
  and page-bounded. p95 targets: lists/stats < 300ms at target scale.

## Observability & operations

- Metrics: admin API latency/error, `admin_action_total{action,result}`, cache
  hit ratio, dashboard staleness age, rollup lag, purge backlog size, authz-deny
  rate. Alerts: admin 5xx; spikes in role-change/disable/delete/authz-deny
  (compromised-admin); missed rollup; growing purge backlog.
- Runner: RollupJob (nightly UTC) and PurgeJob (hourly) run under `SCHEDULER_MODE`
  (ADR-15). On free tier `external_cron`: a free external cron invokes an
  authenticated internal endpoint (`POST /api/v1/admin/internal/run-jobs`, protected
  by a shared secret) that runs one single-flighted batch per call. On premium
  `internal`: an always-on APScheduler/cron worker triggers the same jobs. The job
  **logic** and the single-flighted KVStore lock are identical across both modes —
  only the trigger differs — so free→premium is a value change (ADR-14), not a
  rewrite. Runbook: re-run rollup/backfill, restore soft-deleted user, unstick a
  lock, resume a stuck purge, verify backups.

## Deployment

- Alembic migrations: `users.deleted_at/counters/last_active_at`, `metrics_daily`,
  indexes (`CONCURRENTLY` on Postgres). Reversible down-migrations verified on a
  copy. Flags/env: `ADMIN_ENABLED`, `ADMIN_DESTRUCTIVE_ACTIONS` (kill-switch),
  `ADMIN_DELETE_GRACE_DAYS`, `SCHEDULER_MODE` (`external_cron` free / `internal`
  premium — the free→premium switch is a value change per ADR-14, no code change).
  Rollout: migrate → deploy flag-off → enable read →
  enable manage → enable destructive. Rollback: flags off + down-migrate (purged
  data is recoverable only from backup — documented).

## Correctness Properties

### Property 1: Capability-gated access

**Validates: Requirements 1.1, 1.2, 1.3**

Every `/admin/*` request is authorized by capability against a freshly-loaded
user: anon→401, non-admin→403, disabled/deleted admin→403; reads need
`admin.read`, mutations `admin.manage`.

### Property 2: No content or secret ever leaves admin

**Validates: Requirements 2.3, 4.5, 5.2, 14.3**

Admin responses contain only aggregates + allowlisted user-management metadata;
resume/JD content, password hashes, session tokens, and API keys never serialize
(asserted by a serialization regression test); keys appear only as
`ai_configured: bool`.

### Property 3: At least one active admin always remains

**Validates: Requirements 6.3, 7.2, 8.1, 10.2**

No disable, demotion, or delete can reduce the count of active
(role=admin, status=active, not deleted) admins to zero; the guard is atomic, so
concurrent operations cannot both succeed into a lockout.

### Property 4: Audit is append-only and survives erasure

**Validates: Requirements 8.4, 9.2, 9.4**

No API mutates or deletes `audit_log`; user purge deletes owned data and PII but
retains audit rows (ids + metadata), preserving the security trail after erasure.

### Property 5: Deletion is recoverable then irreversibly complete

**Validates: Requirements 8.2, 8.3, 8.5**

A soft-deleted user is fully restorable during the grace period; after it, the
purge removes all owned data in FK-safe order, idempotently and resumably, and is
irreversible except via backup.

### Property 6: Metrics are precisely defined and never double-counted

**Validates: Requirements 2.1, 3.1, 3.2**

Every metric has an exact UTC-day definition; series read closed days from the
rollup and compute only the current partial day live, so no day is counted twice.

## Error Handling

- Standard envelope (ADR-7). 401 (anon), 403 (non-admin / disabled admin),
  404 (unknown/purged id), 400 (`unknown_metric`, bad cursor, confirm mismatch),
  409 (`last_active_admin`), 429 (rate limited). Metrics/list read failures
  degrade to last cache + `stale:true` (never a hard error on the dashboard).
  Purge failures are retried by the resumable worker and alerted; a stuck lock is
  auto-recovered after TTL. All user-supplied strings sanitized before
  logging/persisting.

## Testing Strategy

- **Unit:** capability derivation; atomic active-admin guard logic; metric
  definitions + UTC-day boundaries; rollup UPSERT idempotency; cursor
  encode/decode + tie-break; search strategy (prefix/trigram) query building;
  response allowlist (forbidden fields); log-injection sanitizer.
- **Integration:** authz matrix (anon/user/disabled-admin/active-admin ×
  read+manage → 401/403/200); each endpoint happy + negative; disable → sessions
  revoked → login blocked; role change → sessions revoked; delete → soft →
  restore → (grace) purge → data gone but audit retained; no-op returns
  `changed:false`; bulk-disable invariant; audit immutability (no mutate API);
  unknown/purged id → 404; sensitive-read audited.
- **Concurrency:** two simultaneous demotions/disables/deletes of admins → exactly
  one succeeds, ≥1 active admin remains; rollup running during a live query;
  purge crash-and-resume; cursor stability under concurrent insert/delete.
- **Security:** CSRF required on all mutations incl. delete; content/secret
  never returned (fuzz field allowlist); enumeration/rate-limit bounds; log
  injection via `q`/`meta`; capability gating for a would-be `superadmin` op.
- **Performance/scale:** users list + audit at millions of rows (keyset pagination
  latency); rollup timing; search latency; dashboard O(1) read.
- **Frontend/E2E:** admin sees dashboard/users/analytics/audit; disable/enable;
  role change with last-admin guard message; delete with typed-email confirm;
  restore within grace; non-admin blocked from `/admin`; stale-metrics banner;
  URL-synced filters + back button.
- **A11y:** keyboard table nav, focus-trapped confirm dialog, `aria-live` async
  results, chart `<title>`/data-table fallback, contrast, reduced-motion.
- **Mobile:** card layout + action sheet; confirm dialog usability; responsive
  charts.
- **Failure/recovery:** rollup missed → dashboard stale banner; purge worker
  down → backlog alert; DB restore of a purged user from backup.

## Self-critique loop

**Round 1**
- *Security:* Admin as a cross-user exfil vector. **Fix:** isolated `AdminRepo`,
  aggregate + allowlisted-metadata only, serialization test, `ai_configured`
  boolean (Property 2, §Security).
- *SRE:* Dashboard full-scans won't scale. **Fix:** `metrics_daily` rollup + O(1)
  reads + cache + staleness (§Metrics, R11.2).
- *Architect:* Hard delete risks irreversible loss + partial-failure corruption.
  **Fix:** grace-period soft-delete + restore + resumable purge (§Deletion).

**Round 2**
- *Security (High):* "≥1 admin" ignored status → disabling the last *active*
  admin locks everyone out. **Fix:** "≥1 **active** admin" everywhere (Property 3).
- *Backend (High):* check-then-act on the invariant races to zero admins.
  **Fix:** atomic guard (`FOR UPDATE`/conditional UPDATE) (§Atomic guard, R10.2).
- *Backend/Compliance (High):* purge erasing `audit_log` destroys the trail;
  FK order undefined. **Fix:** FK-safe order + audit retained through purge
  (Property 4/5, §Deletion).
- *Backend (High):* "indexed search" but `%q%` isn't index-usable. **Fix:**
  email prefix + name FTS/trigram; ban `%q%` on hot path (R4.2, §Performance).

**Round 3**
- *Architect:* cross-user reads silently break the P1 no-unscoped-query guarantee.
  **Fix:** isolated, CI-allowlisted `AdminRepo` (§Architecture).
- *Backend:* metric semantics + timezone ambiguous; "today" could double-count.
  **Fix:** UTC metric registry with exact definitions; rollup only closed days
  (Property 6, §Metrics).
- *Backend:* `DELETE` with a body is fragile. **Fix:** `POST …/delete` + CSRF
  (R14.1, §API).
- *Backend:* per-row usage counts = N+1. **Fix:** denormalized counters
  (R11.3, §Data Models).

**Round 4**
- *Security:* admin snooping untraceable; log injection. **Fix:** audit sensitive
  reads; sanitize `q`/`meta`; per-admin rate limits + alerts (R5.3, R9.3, R14).
- *Product/UX:* destructive optimistic UI is dangerous; no recovery affordance.
  **Fix:** delete pessimistic + grace-period restore action + typed-email confirm
  + "what will be removed" summary (R10.4, R13.5, §Deletion).
- *SRE:* job runner/DR/runbook undefined. **Fix:** defined worker/schedule,
  purge-backlog metric+alert, RPO/RTO + runbook, kill-switch flag (§Observability,
  R12).
- *Frontend:* state/invalidation, URL-synced filters, virtualization, chart a11y,
  mobile cards unspecified. **Fix:** R13 + §Frontend.

**Round 5**
- *Architect:* coarse `admin` role won't fit enterprise least-privilege. **Fix:**
  capability model (`admin.read`/`admin.manage`) as the extension point for
  `support`/`superadmin` with no API change (R1.3, R14.5).
- *QA:* concurrency/tz/purge-resume/audit-retention/cursor-stability untested.
  **Fix:** dedicated concurrency + scale + recovery test sections.

**Round 6 (final):** "Shipping to millions — what still bites?" Residuals, all
explicitly accepted: (a) rollup gives eventual consistency for closed days
(bounded; today is live); (b) purge target ids dangle in audit by design (erasure
vs trail); (c) impersonation and bulk-destructive intentionally out of scope;
(d) capability→role mapping is static until the future roles table lands. No open
critical/high/medium issues.
