# Requirements Document

_P2 Admin — Dashboard, Users, Roles, Analytics, Usage Metrics, Audit._

## Introduction

With P1 (auth, RBAC, user-scoped data, `audit_log`) in place, P2 replaces the
UI-only mock admin (`lib/api/admin.ts`) with a real, secure admin surface:
an overview dashboard, analytics/time-series, user search + detail, safe user
lifecycle (enable/disable/delete with a recoverable grace period), role
management, and an audit log. Access is enforced **server-side** by admin
capabilities (from P1 RBAC); hiding UI is never the boundary.

Admin is the **only** place cross-user reads are permitted; they are performed
through an isolated, audited read path that returns **aggregates and
user-management metadata only** — never resume/JD content, secrets, tokens, or
password hashes.

Inherits all ADRs/standards from `../phase-2-roadmap.md`. Depends on
`../auth-foundation/`.

### Goals
- Real admin APIs backed by aggregate queries + a daily rollup + `audit_log`.
- Safe, audited, **recoverable** user lifecycle (grace-period soft-delete →
  restore or purge).
- Privacy-respecting metrics with precisely defined semantics and time zones.
- Everything paginated (cursor), rate-limited, observable, and audited —
  including sensitive reads.
- Correct under concurrency (no lockout, no lost updates) and at scale.

### Non-goals
- Impersonation / "login as user" (deferred; would require its own audited,
  consent-aware design).
- Billing, per-plan quotas (future).
- Editing another user's resume/JD content (never — privacy boundary).
- Bulk **destructive** actions (bulk delete/role-change) — deferred as
  high-blast-radius; bulk **disable** is allowed (Requirement 6).

## Glossary
- **Admin capability**: a permission checked server-side. P2 defines `admin.read`
  (dashboards/users/audit) and `admin.manage` (lifecycle/role). Both are granted
  by the `admin` role today; the split is the extension point for a future
  `support` vs `superadmin` separation (least privilege) with no API change.
- **Active admin**: a user with role=`admin` AND status=`active` AND not
  soft-deleted. The lockout-prevention invariant counts only active admins.
- **Aggregate read**: a cross-user count/series via the isolated `AdminRepo`;
  returns numbers + user-management metadata only.
- **Grace period**: the window (default 7 days, configurable) between soft-delete
  and irreversible purge, during which an admin may restore the user.
- **Rollup**: `metrics_daily` — one row per `(day_utc, metric)` for CLOSED days.
- **Audit event**: an append-only `audit_log` row (P1) for a security-relevant
  action **or sensitive read**.
- **Day boundary**: all "daily"/"today" semantics use **UTC** calendar days
  (documented; a future per-admin tz preference is an additive display concern).

---

## Requirements

### Requirement 1: Admin access control & capability model
**User Story:** As the product owner, I want admin surfaces locked to admin
capabilities and safe from privilege escalation, so that privileged actions are
protected even if the UI is bypassed.

#### Acceptance Criteria

1. WHEN an unauthenticated request hits any `/api/v1/admin/*` endpoint, THE
   SYSTEM SHALL return 401; WHEN an authenticated non-admin hits it, THE SYSTEM
   SHALL return 403; both SHALL be audited as `authz.denied` with the route.
2. WHEN a **disabled or soft-deleted** admin (session somehow still present)
   hits an admin endpoint, THE SYSTEM SHALL return 403 (status is re-checked
   server-side each request, not trusted from the session/cache).
3. THE authorization check SHALL be capability-based (`admin.read` /
   `admin.manage`); read endpoints require `admin.read`, mutations require
   `admin.manage`. Today both map to role=`admin`.
4. THE FRONTEND SHALL only reveal the admin entry point (account-menu link +
   `/admin` routes) to admins, and middleware SHALL redirect non-admins — as UX
   only; the server is the boundary (Requirement 1.1).
5. THE SYSTEM SHALL audit every admin **mutation** (actor, target, action,
   before/after where applicable, ip_hash, request_id, ts).

### Requirement 2: Overview dashboard
**User Story:** As an admin, I want an at-a-glance, trustworthy overview, so I
can gauge product health without ambiguity.

#### Acceptance Criteria

1. THE SYSTEM SHALL expose overview stats with **precisely defined** semantics:
   total users (not soft-deleted), active users (distinct users with session
   activity in the last N days, default 30), disabled users, total resumes,
   tailored resumes, applications, cover letters / interview-prep / outreach
   generated, and signups in the current period.
2. THE SYSTEM SHALL return each stat with a `computed_at` timestamp and indicate
   when values are served from cache, so the UI can show "as of <time>".
3. Stats SHALL be computed from indexed aggregate queries + the rollup (no full
   table scans on hot paths) and SHALL NOT expose resume/JD content, secrets,
   tokens, or hashes.
4. WHEN the rollup or a live query is unavailable, THE SYSTEM SHALL degrade to
   the last cached value with an explicit staleness indicator rather than error.

### Requirement 3: Analytics time-series
**User Story:** As an admin, I want daily trends over a selectable window, so I
can see how usage changes over time.

#### Acceptance Criteria

1. THE SYSTEM SHALL expose daily time-series for a documented **metric registry**
   (signups, active_users, resumes_tailored initially) over 7/30/90-day windows,
   each metric's daily value defined exactly (e.g. signups=users created that UTC
   day; active_users=distinct users with session activity that UTC day;
   resumes_tailored=improvements created that UTC day).
2. Closed UTC days SHALL be read from `metrics_daily`; the current (partial) day
   SHALL be computed live and appended — never double-counted.
3. Requesting an unknown metric SHALL return 400 `unknown_metric`; the registry
   SHALL be extensible without breaking existing clients.

### Requirement 4: Users list, search & pagination
**User Story:** As an admin, I want to find users quickly and see their status at
a glance, so I can support and manage them at scale.

#### Acceptance Criteria

1. THE SYSTEM SHALL provide a **cursor-paginated** user list (default sort
   `created_at desc, id desc`; opaque cursor encodes the last `(sort_key, id)`)
   with role, status, verified state, joined date, and **precomputed** usage
   counters (resumes, applications) — never per-row N+1 counts.
2. Search SHALL use an **index-usable** strategy: case-insensitive **prefix**
   match on email (btree index) and prefix/trigram match on name (or FTS where
   available). Substring `%q%` scans SHALL NOT be used on the hot path. The
   strategy is documented so results are predictable.
3. Filters (`status`, `role`, `verified`) SHALL be indexed; results paginated
   and page-size bounded (max 100). The `q` value SHALL be length-bounded and
   sanitized (no log injection).
4. THE list SHALL NOT include soft-deleted users by default; a `deleted` filter
   SHALL surface them (for restore) with their `deleted_at` and purge-due time.
5. Responses SHALL only include an **allowlisted** field set; password hashes,
   session tokens, API keys (even masked), and OAuth tokens SHALL never appear.

### Requirement 5: User detail (audited, content-free)
**User Story:** As an admin, I want a user's account overview and recent
activity, so I can support them — without ever seeing their private content.

#### Acceptance Criteria

1. THE SYSTEM SHALL provide a detail view: profile (name, email, role, status,
   verified, created/updated, deleted_at?), an **activity summary** (counts of
   resumes/tailored/applications, last-active timestamp, signup method,
   AI-configured boolean), and that user's recent audit events.
2. THE detail view SHALL NOT expose resume/JD content, cover letters, API keys
   (only an `ai_configured: bool`), tokens, or hashes.
3. THE SYSTEM SHALL audit sensitive reads (`admin.user_viewed`, target,
   actor, ts) so cross-user access by admins is itself traceable.
4. A request for an unknown/purged user id SHALL return 404.

### Requirement 6: Enable / disable users
**User Story:** As an admin, I want to disable or re-enable accounts safely, so I
can respond to abuse without risking a lockout.

#### Acceptance Criteria

1. WHEN an admin disables a user, THE SYSTEM SHALL set status=`disabled`, revoke
   all that user's sessions immediately (including P1 session-cache
   invalidation), block future login, and audit `user.disabled`.
2. WHEN an admin enables a user, THE SYSTEM SHALL set status=`active`, allow
   login, and audit `user.enabled`.
3. THE SYSTEM SHALL refuse (409 `last_active_admin`) any disable that would leave
   **zero active admins**; the check SHALL be evaluated atomically with the write
   (Requirement 10.2).
4. THE SYSTEM SHALL allow bulk **disable** (bounded batch) with per-target audit
   and the same invariant; bulk delete/role-change are out of scope.
5. Setting a status that is already current SHALL be a no-op (200, no audit,
   `changed: false`).

### Requirement 7: Role management
**User Story:** As an admin, I want to grant or revoke admin access safely, so
that access stays correct and cannot be escalated improperly.

#### Acceptance Criteria

1. WHEN an admin changes a user's role, THE SYSTEM SHALL update it, revoke that
   user's sessions (force fresh authz), and audit `role.changed` with
   before/after.
2. THE SYSTEM SHALL refuse (409 `last_active_admin`) any demotion that would
   leave zero active admins, evaluated atomically (Requirement 10.2).
3. Role change SHALL require `admin.manage`; a user SHALL NOT change their own
   role via any endpoint (enforced in P1 and re-asserted here).
4. Granting `admin` to a user SHALL be audited and SHOULD be gated behind the
   (future) `superadmin` capability once that split lands — the endpoint checks a
   capability, not a hardcoded role.

### Requirement 8: User deletion with grace period & restore
**User Story:** As an admin, I want deletion to be safe and recoverable for a
window, then fully purge the user's data, so mistakes are recoverable but
erasure is honored.

#### Acceptance Criteria

1. WHEN an admin requests deletion, THE SYSTEM SHALL require an explicit typed
   confirmation matching the target's email, then set `deleted_at`,
   status=`disabled`, revoke sessions, and audit `user.soft_deleted`. It SHALL
   refuse deleting the last active admin (409, atomic).
2. During the **grace period** (default 7 days, configurable), an admin SHALL be
   able to **restore** the user (clear `deleted_at`, keep status disabled until
   explicitly enabled) — audited `user.restored`. Restore is impossible after
   purge.
3. AFTER the grace period, a purge worker SHALL irreversibly delete the user's
   owned data (resumes, jobs, improvements, applications, api_keys, sessions,
   resume_versions/notifications/etc. from P3) in FK-safe order, then the user
   and oauth_identities, and audit `user.purged`.
4. THE purge SHALL NOT delete `audit_log` rows; instead the purged user's
   references SHALL be **retained** (target/actor ids kept) so the security trail
   survives erasure. The user row's PII is gone; the audit keeps only ids +
   event metadata.
5. THE purge SHALL be idempotent, chunked, transactional per user, and resumable
   after a crash (re-scans grace-elapsed soft-deleted users).
6. Deletion/purge SHALL be preceded by the operational backup guarantee
   (Requirement 12.4) so recovery within RPO remains possible even post-purge.

### Requirement 9: Audit log view
**User Story:** As an admin, I want a searchable, tamper-proof audit trail, so
sensitive actions and accesses are always traceable.

#### Acceptance Criteria

1. THE SYSTEM SHALL provide a cursor-paginated, filterable (event, actor, target,
   date range) audit view.
2. Audit entries SHALL be append-only: no API SHALL update or delete them; the
   only writer is the server-side audit service.
3. Audit `meta`, and any user-supplied strings that reach logs/audit (e.g. search
   `q`), SHALL be sanitized against log/CRLF injection.
4. Audit retention SHALL survive user purge (Requirement 8.4) and be
   time-partition/rotatable at volume.

### Requirement 10: Reliability, concurrency & idempotency
**User Story:** As an admin, I want actions to behave correctly under concurrency
and retries, so the system never corrupts state or double-acts.

#### Acceptance Criteria

1. All mutations SHALL be idempotent where retriable (repeating a disable/role
   set converges; re-delete of an already-soft-deleted user is a no-op 200).
2. THE last-active-admin invariant SHALL be enforced **atomically** (a single
   conditional UPDATE or `SELECT … FOR UPDATE` within the txn) so concurrent
   demotions/disables cannot both succeed into zero admins.
3. Rollup and purge jobs SHALL be single-flighted (KVStore lock with TTL +
   stuck-lock recovery) and resumable; rollup writes SHALL UPSERT on
   `(day_utc, metric)` (safe to re-run).
4. Optimistic UI SHALL be used only for reversible toggles; **destructive
   actions (delete) SHALL be pessimistic** (await server) with an explicit
   pending state.

### Requirement 11: Performance & scalability
**User Story:** As the product owner, I want admin to stay fast at millions of
users/resumes/applications, so it remains usable at scale.

#### Acceptance Criteria

1. All list endpoints SHALL be cursor-paginated + backed by composite indexes;
   p95 < 300ms at target scale; no offset pagination on large tables.
2. Dashboards SHALL read O(1) from the rollup + a bounded live-today query;
   never a full scan.
3. Usage counters SHALL be precomputed/denormalized (maintained incrementally or
   via the rollup), not computed per row per request.
4. Audit and metrics tables SHALL be time-partition/rotatable; the audit view
   SHALL be virtualization-friendly (bounded page size).

### Requirement 12: Observability, DR & operations
**User Story:** As an SRE, I want the admin subsystem observable and recoverable,
so I can operate it safely in production.

#### Acceptance Criteria

1. THE SYSTEM SHALL emit metrics: admin API latency/error rate,
   `admin_action_total{action,result}`, cache hit ratio, dashboard staleness,
   rollup lag, and **purge backlog** (soft-deleted awaiting purge).
2. THE SYSTEM SHALL alert on: admin 5xx, spikes in `authz.denied`, role
   changes, disables, or deletes (compromised-admin signal), missed rollup, and
   growing purge backlog.
3. Scheduled jobs (rollup, purge) SHALL have a defined runner, schedule (UTC),
   retry/alert on failure, and a runbook (re-run rollup, restore soft-deleted
   user, unstick a lock, recover a stuck purge).
4. Backups SHALL run on a defined RPO/RTO; a purged user SHALL be recoverable
   from backup within RPO. Destructive flags (`ADMIN_DESTRUCTIVE_ACTIONS`
   kill-switch, grace-period length) SHALL be configurable.

### Requirement 13: Frontend UX, accessibility & responsiveness
**User Story:** As an admin, I want a clear, fast, accessible console on any
device, so management is smooth and low-risk.

#### Acceptance Criteria

1. Every table/chart SHALL have explicit loading (skeleton), empty (contextual:
   no results / no audit for filter / metrics unavailable→stale), and error
   (retry) states; the dashboard SHALL show "as of <time>" + manual refresh.
2. Lists SHALL sync filters/search/cursor to the URL (shareable, back-button
   safe); long lists (audit) SHALL be virtualized.
3. State SHALL use typed query keys with correct invalidation (mutations
   invalidate the user list, detail, stats); toggles optimistic with rollback,
   delete pessimistic.
4. THE UI SHALL disable/guard self-targeting destructive actions and the
   last-active-admin case with clear messaging mirroring the server.
5. THE delete confirm dialog SHALL require typing the target email, warn it is
   irreversible after the grace period, and summarize what will be removed.
6. Charts SHALL be accessible (SVG + `<title>`/`aria` + a data-table fallback);
   tables keyboard-navigable; dialogs focus-trapped; async results announced via
   an `aria-live` region; timestamps show local time with a UTC tooltip.
7. On mobile/tablet, tables SHALL adapt to card lists with an action sheet;
   confirm dialogs SHALL be reachable and usable at small sizes.

### Requirement 14: Security hardening
**User Story:** As a security engineer, I want the admin surface hardened against
abuse even by a compromised admin, so blast radius is contained.

#### Acceptance Criteria

1. All admin mutations SHALL require the P1 CSRF token (including delete); the
   delete transport SHALL NOT rely on a request body that intermediaries may
   strip (Requirement uses `POST /users/{id}/delete`, see Design).
2. Admin endpoints SHALL be rate-limited **per admin** (read and write buckets);
   abnormal volume SHALL be audited and alerted (scraping / compromised admin).
3. Responses SHALL enforce the field allowlist (Requirement 4.5, 5.2); a
   regression test SHALL assert forbidden fields never serialize.
4. Sensitive reads (user detail) SHALL be audited (Requirement 5.3); search
   volume SHALL be bounded and logged.
5. THE capability model (Requirement 1.3) SHALL make it possible to later require
   `superadmin` for destructive actions without changing endpoint shapes.

---

## Traceability
R1 → Design §Access, §Capabilities. R2/R3 → §Metrics & rollup. R4/R5 → §Users
& detail. R6/R7 → §Lifecycle & roles. R8 → §Deletion, grace & purge. R9 →
§Audit. R10 → §Reliability/Concurrency. R11 → §Performance. R12 → §Observability
& Ops. R13 → §Frontend. R14 → §Security. All → `tasks.md`.
