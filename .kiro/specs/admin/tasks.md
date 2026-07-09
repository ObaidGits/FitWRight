# Implementation Plan — P2 Admin

## Overview

Production-grade admin subsystem on P1 RBAC: capability-gated APIs, dashboards +
daily rollup, cursor-paginated search + audited detail, recoverable grace-period
deletion + resumable purge (audit retained), role management with an atomic
active-admin guard, an append-only audit view, and the frontend rewire — correct
under concurrency and at scale. Depends on `../auth-foundation/`; inherits
`../phase-2-roadmap.md`. Verify each parent task: backend `uv run pytest` (incl.
authz + concurrency + security suites), frontend `npm run build`/`test`/lint.
Never continue on a failing gate.

## Task Dependency Graph
```json
{
  "waves": [
    { "wave": 1, "tasks": ["0", "1"], "depends_on": [] },
    { "wave": 2, "tasks": ["2", "3"], "depends_on": ["1"] },
    { "wave": 3, "tasks": ["4", "5", "6"], "depends_on": ["2"] },
    { "wave": 4, "tasks": ["7"], "depends_on": ["3", "4", "5", "6"] },
    { "wave": 5, "tasks": ["8", "9"], "depends_on": ["4", "5", "6", "7"] }
  ]
}
```

## Tasks

- [ ] 0. Migrations, capabilities & job runner scaffold
  - [ ] 0.1 Alembic migration: `users.deleted_at`, `users.resume_count`, `users.application_count`, `users.last_active_at`, `metrics_daily(day_utc,metric,value,computed_at)`, and all admin indexes (email prefix, `(role,status)`, `(created_at,id)`, `deleted_at`, session + audit indexes); `CONCURRENTLY` on Postgres; reversible down verified on a copy
    - _Requirements: 8.1, 11.1, 11.3, 3.2_
  - [ ] 0.2 `capabilities_for(user)` deriving `admin.read`/`admin.manage` from role (single extension point) + `require_capability` FastAPI dep with per-request status recheck
    - _Requirements: 1.1, 1.2, 1.3, 14.5_
  - [ ] 0.3 Scheduled worker runner (rollup nightly UTC, purge hourly), KVStore single-flight lock (TTL + stuck-lock recovery); flags `ADMIN_ENABLED`, `ADMIN_DESTRUCTIVE_ACTIONS`, `ADMIN_DELETE_GRACE_DAYS`
    - _Requirements: 10.3, 12.3, 12.4_

- [ ] 1. Guarded router, AdminRepo & audit plumbing
  - [ ] 1.1 `/api/v1/admin/*` router; read routes require `admin.read`, mutations `admin.manage`; standard envelope; CSRF on mutations; per-admin rate limits (read/write buckets)
    - _Requirements: 1.1, 1.3, 14.1, 14.2_
  - [ ] 1.2 Isolated `AdminRepo` (only cross-user read path; CI unscoped-query allowlist) + `AdminUserRow/Detail/Stats/UsageSeries/AuditEntry` response models with a **field allowlist**
    - _Requirements: 2.3, 4.5, 5.2, 14.3_
  - [ ] 1.3 Audit service usage for mutations + sensitive reads (`admin.user_viewed`, `authz.denied`); log-injection sanitizer for `q`/`meta`
    - _Requirements: 1.5, 5.3, 9.3_

- [ ] 2. Metrics: rollup + dashboards
  - [ ] 2.1 Metric registry (signups/active_users/resumes_tailored) with exact UTC-day definitions; `RollupJob` (UPSERT closed days) + idempotent `backfill(from,to)`; drift reconciliation of usage counters
    - _Requirements: 3.1, 3.2, 10.3, 11.3_
  - [ ] 2.2 `GET /stats` (rollup + bounded live-today, `computed_at`, cache 60s, `stale` fallback) and `GET /usage-series` (closed-days + live-today merge, `unknown_metric`→400)
    - _Requirements: 2.1, 2.2, 2.4, 3.2, 3.3_

- [ ] 3. Users list, search & detail
  - [ ] 3.1 `GET /admin/users` cursor pagination (keyset `created_at desc,id`), index-usable search (email prefix / name FTS-or-trigram, no `%q%`), filters (status/role/verified/deleted), bounded page size + `q`
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 11.1_
  - [ ] 3.2 `GET /admin/users/{id}` detail (profile + activity summary + recent audit, `ai_configured` bool only), audited read; unknown/purged → 404
    - _Requirements: 5.1, 5.2, 5.3, 5.4_

- [ ] 4. Enable / disable (+ bulk) with atomic guard
  - [ ] 4.1 `POST /users/{id}/disable|enable` + `PATCH /users/{id}` status; revoke sessions + P1 cache invalidation on disable; no-op → `changed:false`; distinct audit events
    - _Requirements: 6.1, 6.2, 6.5, 10.1_
  - [ ] 4.2 Atomic **active-admin** guard (FOR UPDATE / conditional UPDATE) shared by disable/demote/delete → 409 `last_active_admin`; `POST /users/bulk-disable` (bounded, per-target audit + invariant)
    - _Requirements: 6.3, 6.4, 10.2_

- [ ] 5. Role management
  - [ ] 5.1 `PATCH /users/{id}` role change via `admin.manage` → revoke sessions + audit before/after; atomic active-admin guard; self-role-change blocked
    - _Requirements: 7.1, 7.2, 7.3, 7.4_

- [ ] 6. Deletion, grace & purge
  - [ ] 6.1 `POST /users/{id}/delete` (typed email match, CSRF) → atomic guard → soft-delete + revoke sessions + audit `user.soft_deleted`; `POST /users/{id}/restore` within grace → audit `user.restored`
    - _Requirements: 8.1, 8.2, 10.4, 14.1_
  - [ ] 6.2 `PurgeJob`: grace-elapsed users, FK-safe chunked transactional delete of owned data, **audit retained** (not purged), idempotent + resumable; `user.purged` audit
    - _Requirements: 8.3, 8.4, 8.5_

- [ ] 7. Audit view
  - [ ] 7.1 `GET /admin/audit` cursor + filters (event/actor/target/date); append-only (no mutate API); survives purge; virtualization-friendly page bounds
    - _Requirements: 9.1, 9.2, 9.4, 11.4_

- [ ] 8. Frontend rewire + UX
  - [ ] 8.1 Swap `lib/api/admin.ts` mock → real; typed query keys + invalidation (mutations invalidate list/detail/stats); capability-aware rendering; URL-synced filters/search/cursor
    - _Requirements: 13.2, 13.3_
  - [ ] 8.2 Dashboard (stat cards + charts with `computed_at`/stale banner + refresh), users table→mobile cards + action sheet, detail view, audit view (virtualized)
    - _Requirements: 2.2, 13.1, 13.6, 13.7_
  - [ ] 8.3 Actions: optimistic toggles w/ rollback; **pessimistic delete** with typed-email confirm + irreversible warning + removal summary; restore; bulk-disable; last-active-admin + self-action guards mirroring server; chart a11y + `aria-live`
    - _Requirements: 8.1, 8.2, 10.4, 13.4, 13.5, 13.6_

- [ ] 9. Observability, ops & verification
  - [ ] 9.1 Metrics (admin latency/error, action counts, cache hit, staleness, rollup lag, purge backlog, authz-deny) + alerts (5xx, role/disable/delete/authz spikes, missed rollup, growing backlog); runbook + backup/RPO doc
    - _Requirements: 12.1, 12.2, 12.3, 12.4_
  - [ ] 9.2 Verification suites: authz matrix (incl. disabled/deleted admin); concurrency (parallel demote/disable/delete → one wins, ≥1 active admin; purge crash-resume; cursor stability); security (CSRF/allowlist/enumeration/log-injection); scale (list+audit at volume, rollup timing); E2E (disable/enable/role/delete-confirm/restore/non-admin-blocked/stale-banner); a11y + mobile
    - _Requirements: 1.*, 6.3, 7.2, 8.*, 10.2, 11.*, 13.*, 14.*_

## Notes
- Admin NEVER returns resume/JD content or secrets — aggregates + allowlisted
  metadata only (`ai_configured` boolean at most).
- The active-admin invariant is atomic and status-aware; deletes are recoverable
  for a grace window then irreversibly purged, but audit is always retained.
- Only `AdminRepo` may issue cross-user queries; every other repo stays scoped.
