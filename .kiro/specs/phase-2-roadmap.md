# FitWright Phase 2 — Program Roadmap & Shared Architecture

Status: **Planning (specs only — no implementation yet)**
Owner: Obaidullah Zeeshan
Preceded by: `.kiro/specs/ui-revamp/` (complete)

This is the master plan for the remaining Phase-2 and advanced features. It
defines the **dependency order**, the **shared architecture decisions** (ADRs)
that every feature spec inherits, and the **cross-cutting standards** (security,
reliability, performance, testing, observability). Individual feature specs live
in sibling folders and reference the ADRs here instead of re-deciding them.

> Each feature spec is written to production-RFC quality: a senior engineer can
> implement it without making architectural decisions.

---

## 1. Feature inventory & spec map

| Phase | Spec folder | Features |
|------|-------------|----------|
| **P1** | `auth-foundation/` | Signup, Login, Logout, Session management, Google OAuth, User model, RBAC, **user-scoped data**, route guards, base profile |
| **P2** | `admin/` | Admin dashboard, users list, user management (enable/disable/delete), role management, analytics, usage metrics, admin APIs |
| **P3** | `productivity/` | Version history, Notifications, Global search, JD-from-URL, Follow-up reminders, Interview scheduling, Avatar upload, extended user profile |
| **P4** | `resilience/` | Streaming AI, Offline support, Conflict resolution, Advanced autosave, Recovery |

Each folder contains `requirements.md`, `design.md`, `tasks.md`.

---

## 2. Dependency graph (build order is non-negotiable)

```
P1 auth-foundation
  ├─ user model + sessions + RBAC        ← everything below assumes this
  ├─ user-scoped data (user_id + backfill migration)
  └─ route guards (middleware) + CSRF
        │
        ├─► P2 admin           (needs users, RBAC, server-side authz)
        ├─► P3 productivity    (needs user_id scoping for every new table)
        └─► P4 resilience      (needs sessions for server-side draft/conflict)
```

**Rule:** No P2/P3/P4 work starts until P1 ships user-scoping + sessions, because
every new table and endpoint must be user-scoped and auth-guarded from day one.
Retrofitting scoping later is the classic multi-tenant data-leak vector.

---

## 3. Architecture Decision Records (ADRs) — inherited by all specs

### ADR-1 — Sessions: httpOnly cookies, server-side session store (not JWT-in-JS)
- Session id = opaque 256-bit random token, stored **httpOnly + Secure +
  SameSite=Lax**, 30-day sliding expiry, 12-hour absolute idle re-check.
- Server keeps a `sessions` table (hashed token, user_id, csrf_secret,
  created_at, expires_at, user_agent, ip_hash, revoked_at). Enables revocation
  and "log out everywhere" — impossible with stateless JWT.
- **Rejected:** localStorage tokens (XSS-exfiltratable) and stateless JWT
  (no revocation, key-rotation pain). Documented in `auth-foundation/design.md`.

### ADR-2 — CSRF: double-submit + SameSite, per-session secret
- SameSite=Lax blocks cross-site form posts; as defense-in-depth, state-changing
  requests also require an `X-CSRF-Token` header matching a non-httpOnly
  `csrf` cookie derived from the session's `csrf_secret`. GET/HEAD are exempt.

### ADR-3 — Password hashing: Argon2id (argon2-cffi)
- Argon2id, m=64MB, t=3, p=4 (tunable via env). Never bcrypt/PBKDF2 for new code.
- Passwords: min 10 chars, checked against a small common-password denylist +
  zxcvbn-style length/entropy gate; never logged; never returned.

### ADR-4 — User-scoped data via `user_id` FK on every owned table
- Add `user_id: str` (indexed, FK→users.id) to `resumes`, `jobs`,
  `improvements`, `applications`, and every new P3 table. `api_keys` becomes
  per-user (composite PK `(user_id, provider)`).
- **Every** `db` facade read/write takes a `user_id` and filters by it. A shared
  `scoped(query, user_id)` helper + a repository-layer lint rule prevent
  unscoped queries. Ownership checks return **404 (not 403)** for another user's
  resource id (no existence disclosure).
- Backfill migration assigns all existing rows to a single bootstrap "owner"
  user (the current local user), preserving today's data. See ADR-9.

### ADR-5 — OAuth: Google Authorization Code + PKCE + state, server-side callback
- `state` (CSRF/replay) and PKCE `code_verifier` stored in short-lived signed,
  httpOnly cookies. Backend exchanges the code, verifies `id_token` (issuer,
  aud, exp, nonce), links/creates the user, issues the session cookie. Tokens
  never touch the browser.

### ADR-6 — Multi-worker readiness (removes today's single-worker assumption)
- Phase 2 runs behind ≥2 workers. All shared mutable state moves out of process
  memory: sessions/rate-limits/locks/notifications-cursor → a shared store.
  **Decision:** introduce a pluggable `KVStore` (Redis in prod; SQLite/in-proc
  adapter for local single-worker dev). The `_master_resume_lock` becomes a
  per-user DB advisory lock (the single-master invariant is now per-user).
- **Free-tier adapter:** the `KVStore` interface has three concrete adapters
  selected by `KVSTORE_URL`: in-proc (local dev), **Upstash Redis** (free serverless
  Redis, HTTP/TLS — the free-tier hosted default), and full Redis (premium). A
  **DB-backed fallback** adapter (a `kv` table) exists so the app runs with **no
  Redis at all** on the strictest free tier (accepted trade-off: coarser rate-limit
  granularity, DB-hit session cache). Swapping adapters is one env var — see ADR-14.

### ADR-7 — API versioning & error envelope
- All new endpoints under `/api/v1`. Standard error envelope:
  `{ "error": { "code": "snake_case", "message": "human", "details"?: {...} } }`.
  Client messages are generic; specifics are logged server-side (existing rule).

### ADR-8 — Rate limiting & abuse controls (baseline for all write/auth endpoints)
- Token-bucket per `(ip, route-class)` and per `user_id`. Auth endpoints get
  strict limits + exponential backoff + account-level lockout after N failures.
  Enforced in middleware via the `KVStore`.

### ADR-9 — Migrations: Alembic, forward + reversible, data-safe
- Introduce **Alembic** (currently schema is created implicitly). Every schema
  change ships an up/down migration; data backfills are idempotent and chunked.
  Migrations run on deploy before the app accepts traffic.

### ADR-10 — Storage for binaries (avatars): provider abstraction, presigned, never public-by-default
- Binaries (avatars now; future attachments) go through a single
  **`StorageProvider` interface** (`upload`, `get_url`, `delete`), selected by
  `STORAGE_PROVIDER`. Concrete adapters: **Cloudinary** (25 GB free — the free-tier
  hosted default), `S3` (premium / self-host), and `Local` (dev only). Uploads use a
  short-lived signed direct-to-provider PUT; served via signed GET/CDN. Validated
  server-side (type, size, re-encode). No user-controlled paths
  (path-traversal/SSRF safe). Resume text stays in the DB (unchanged).
- **Free-tier note:** direct **browser→Cloudinary** upload (signed by a tiny backend
  endpoint) is preferred so uploads don't traverse the (sleeping, bandwidth-limited)
  backend; Cloudinary URL transforms (`w_,h_,q_auto,f_auto`) replace server-side image
  processing. Moving to S3 on premium is a new adapter + `STORAGE_PROVIDER=s3`, no
  call-site change (ADR-14).

### ADR-11 — Observability: structured logs, request-id, metrics, audit log
- JSON structured logs with `request_id`, `user_id` (never PII/secrets), route,
  latency. Prometheus-style metrics. An append-only **`audit_log`** for
  security-relevant events (login, logout, role change, user disable, data
  reset, admin actions) — immutable, queryable in admin.

### ADR-12 — Feature flags & config
- Server-driven feature flags (`/config/flags`) gate risky rollouts (OAuth,
  streaming, offline). Flags are per-environment; admin-togglable ones are
  audited.

### ADR-13 — Database: SQLite (local dev) → PostgreSQL (hosted). **MongoDB rejected.**
- **Decision:** one relational store selected by a single `DATABASE_URL`:
  **SQLite** for local/single-user dev, **PostgreSQL** for hosted multi-user. All
  SQL is Postgres-safe (see auth/admin/productivity designs). No code path is
  DB-engine-specific beyond migrations and FTS (SQLite FTS5 / Postgres `tsvector`).
- **Free-tier target:** **Neon** (preferred) — serverless Postgres that
  **auto-scales to zero and wakes in ~0.5 s**, so an idle DB is not a UX problem;
  free tier ~0.5 GB. **Supabase** is an accepted alternative but its free project
  **pauses after ~7 days idle** (manual resume), which is worse for a sporadically-
  visited open-source demo — hence Neon is the default. Use the **pooled connection
  string** (Neon pooler / Supabase PgBouncer) because free Postgres has low
  connection caps and the backend restarts often (ADR-15 cold starts).
- **Why not stay on SQLite hosted:** free backend hosts (Render/Fly free) use an
  **ephemeral filesystem** — the single SQLite file is wiped on every
  restart/redeploy/sleep, destroying all user data. Persistent disk is a paid
  add-on. SQLite is therefore *dev-only* once hosted.
- **Why not MongoDB (explicit rejection):** the entire backend is **relational
  SQLAlchemy + Alembic** (users, sessions, FKs, user-scoping, migrations). Mongo
  would require **rewriting the whole data layer, every query, and all migrations**
  for no gain, while the 512 MB Atlas free tier is smaller than the effort saved.
  SQLite→Postgres is a **connection-string change**; SQLite→Mongo is a rewrite. This
  ADR is the single source of truth; any spec text implying MongoDB is superseded.
- **Schema discipline for 0.5 GB:** cap/rotate high-volume tables (`audit_log`,
  `metrics_daily`) and keep all binaries in object storage (ADR-10), never the DB.

### ADR-14 — Configurable Free/Premium Profile ("free-tier is config, not a code path")
- **Principle:** every free-tier workaround is exposed as a **toggle** — an env var
  (deploy-time, infra choices) or an **admin-panel setting** (runtime, no redeploy) —
  never a hardcoded assumption or a separate code path. Flipping free→premium is a
  checklist of setting changes, not an engineering task.
- **Placement rule:** if you'd want to change it *without a redeploy* it is an
  admin-panel setting (persisted in the DB config store the admin spec owns); if it
  is chosen once per environment it is an env var.
- **Canonical toggle registry** (each spec implements its slice):

  | Concern | Toggle | Kind | Free value | Premium value |
  |---|---|---|---|---|
  | Keep-warm ping | `keepalive_enabled` / `keepalive_interval_minutes` / `keepalive_target_url` | admin setting | on / 10 / `/health` | off |
  | Database | `DATABASE_URL` | env | Neon free | managed Postgres |
  | DB pool | `db_pool_size` / `db_use_pooler` | env | small / pooler | larger |
  | KVStore | `KVSTORE_URL` | env | Upstash free / DB-backed | Redis |
  | Storage | `STORAGE_PROVIDER` | env | `cloudinary` | `s3` |
  | Scheduler | `SCHEDULER_MODE` | env | `external_cron` | `internal` |
  | Notifications | `NOTIFICATION_TRANSPORT` / `polling_interval_seconds` | admin setting | `polling` / 30 | `sse` |
  | AI limits | `ai_rate_limit_per_user` / `ai_daily_token_cap` | admin setting | conservative | raised / off |
  | Upload caps, cache TTLs, page sizes | admin settings | admin setting | conservative | raised |

- Toggle *logic* is identical across profiles; only the value differs. Adapters
  behind interfaces (KVStore ADR-6, StorageProvider ADR-10, Scheduler ADR-15) make
  each switch a value change, not a rewrite.

### ADR-15 — Free-tier hosting topology & cold-start mitigation
- **Topology:** frontend on **Netlify/Vercel edge** (always warm — carries perceived
  speed); backend on **Render free** (ephemeral disk, **sleeps after ~15 min idle,
  30–60 s cold wake**); DB on **Neon** (ADR-13); binaries on **Cloudinary** (ADR-10);
  KVStore on **Upstash / DB-backed** (ADR-6).
- **Cold-start mitigation (the biggest free-tier UX risk):**
  1. **Keep-warm ping** — a free external cron (GitHub Actions schedule /
     cron-job.org / UptimeRobot) hits `/health` on `keepalive_interval_minutes`
     (ADR-14) to keep the dyno awake in active hours. Cheapest, biggest win.
  2. **Optimistic UI + graceful "waking up" state** — the always-warm frontend
     renders shell/skeletons/cached data instantly; a request exceeding ~3 s shows a
     friendly "starting the server…" state, not a frozen spinner.
  3. **Client durability** — IndexedDB/localStorage cache + Service Worker
     (stale-while-revalidate) + draft autosave (owned by the resilience spec) make
     returning-user and offline experiences instant despite a sleeping backend.
- **`SCHEDULER_MODE` (ADR-14):** free hosts can't run always-on worker dynos, so
  scheduled work (metrics rollup, purge, retention, reminder/interview firing) runs
  as `external_cron` — a free cron hits an authenticated internal endpoint that runs
  one single-flighted batch; premium switches to `internal` (APScheduler/worker) with
  identical job logic.
- **AI on the slow/expensive path:** stream responses (SSE, resilience spec) so
  output feels instant even stacked on a cold start; **BYO API key** shifts cost off
  the host and removes the shared rate-limit ceiling; cache identical
  prompts/results.

---

## 4. Cross-cutting standards (apply to every feature spec)

### 4.1 Security baseline
- Server-side authz on **every** endpoint (session → user → ownership/role).
  Hiding UI is never the boundary.
- Ownership mismatch → 404. Missing/expired session on protected route → 401.
  Authenticated-but-forbidden (e.g. non-admin hitting `/admin/*`) → 403.
- All inputs validated (Pydantic v2). Output-encode all rendered strings (React
  handles XSS by default; dangerouslySetInnerHTML is banned outside the vetted
  resume HTML sanitizer that already exists).
- Secrets only in the encrypted store / env; never in logs, errors, or client.
- Per-feature threat model + mitigations table is mandatory in each `design.md`.

### 4.2 Reliability baseline
- Mutations are **idempotent** where retriable (idempotency-key header for
  create endpoints that a client may retry; unique constraints as the backstop).
- Optimistic concurrency via `updated_at` / `version` compare-and-set; 409 on
  conflict with a typed payload the UI can resolve.
- Every external call (LLM, OAuth, storage) has timeout + bounded retries +
  circuit-breaker; partial failures degrade gracefully.
- Cancellation: long AI/streaming requests are cancellable server-side (abort
  propagates to the provider call).

### 4.3 Performance & scalability baseline
- All list endpoints are **paginated** (keyset/cursor, not offset) + indexed on
  `(user_id, sort_key)`. No unbounded queries.
- N+1 avoided (batch/join). Response caching where safe (ETag / short TTL).
- Frontend: route code-split, lazy-load heavy panels, virtualized long lists,
  per-route First-Load JS ≤ 250KB (existing budget).
- Targets (p95): auth < 300ms; list endpoints < 200ms; search < 300ms; PDF
  export unchanged; streaming first-token < 2s.

### 4.4 Testing standard (every feature)
- **Unit** (services, validation, state, utils) — deterministic, LLM/network
  mocked. **Integration** (endpoints via httpx ASGITransport + real temp DB,
  incl. authz/ownership/negative cases). **E2E** (Playwright, gated for AI/quota
  as in ui-revamp). **Security tests** (authz matrix, CSRF, injection, upload,
  rate-limit, OAuth state/replay). **Perf** (load/concurrency on hot paths).
  **A11y** (keyboard, SR, contrast, reduced-motion, focus). **Mobile**
  (responsive/gesture/touch). A feature is "done" only when its test matrix is
  green + the ui-revamp gates (build, lint, typecheck, locale parity) pass.

### 4.5 Deployment / rollout
- Alembic migrate → deploy behind flag → canary → enable flag. Each spec lists
  its migration, flag, rollback (flag off + down-migration), and backup step.

### 4.6 Free-tier UX & performance standard (applies to every feature)
- **Cold-start aware (ADR-15):** never let a sleeping backend look broken. Any
  network call must have an optimistic/skeleton state and a "waking up" affordance
  past ~3 s; returning-user surfaces must read from client cache first.
- **Config-driven, not hardcoded (ADR-14):** any limit, interval, TTL, page size,
  or provider a free/premium deployment would tune is an env var or admin setting
  with a conservative free default — never a literal.
- **Client carries perceived speed:** cache on IndexedDB/localStorage + Service
  Worker (stale-while-revalidate); the always-warm frontend renders instantly while
  the backend catches up.
- **Cheap by default:** direct browser→Cloudinary uploads (ADR-10); `external_cron`
  scheduling (ADR-15); polling (not WebSockets) for notifications on free
  (ADR-14); AI streaming + BYO key + result caching on the AI path.
- **Free datastore discipline:** paginate/cap everything (§4.3), rotate high-volume
  tables (ADR-13), keep binaries out of the DB (ADR-10), use the DB pooler (ADR-13).

---

## 5. Self-critique cadence

Every feature spec ends with a **Self-Critique Loop** section capturing review
rounds from seven lenses (Principal Architect, Security Engineer, Product
Designer, Staff Backend, Staff Frontend, QA Lead, SRE) and the resulting
changes, repeated until no significant weakness remains. This roadmap's own
critique log:

- **R1 (Architect):** Original plan retrofitted scoping after features → moved
  user-scoping into P1 as a hard gate (ADR-4). ✅
- **R1 (SRE):** Single-worker assumption would break sessions/locks under
  scale → ADR-6 (KVStore + multi-worker). ✅
- **R1 (Security):** JWT-in-localStorage tempting for SPA → rejected for
  httpOnly server sessions + CSRF (ADR-1/2). ✅
- **R2 (Backend):** Implicit schema creation can't evolve safely → ADR-9
  (Alembic) added as a P1 prerequisite. ✅
- **R2 (Security):** Ownership 403 leaks existence → standardized on 404
  (§4.1). ✅
- **R2 (Product):** Bootstrap migration must not lose the current local user's
  data → ADR-4/9 assign existing rows to an owner user. ✅
- **R3 (Architect):** `ui-revamp` named MongoDB for Phase 2 while every backend
  spec assumed relational Postgres → **contradiction**. Resolved: relational
  codebase makes Postgres a config change and Mongo a rewrite → **ADR-13** pins
  SQLite→Postgres (Neon), rejects Mongo, and supersedes any Mongo text. ✅
- **R3 (SRE):** Free hosting = ephemeral disk + cold starts + no worker dynos, none
  of which the specs accounted for → **ADR-15** (topology, keepalive, `external_cron`)
  + **§4.6** (free-tier UX standard). ✅
- **R3 (Product/Backend):** Free-tier workarounds risked hardcoding, making the
  premium migration a rewrite → **ADR-14** "free-tier is config, not a code path"
  with a canonical toggle registry (env vars + admin settings). ✅

No further program-level blockers open. Feature-level critique continues in each
spec.

---

## 6. Reading order for implementers

1. This roadmap (ADRs + standards).
2. `tech-stack-migration-report.md` — consolidated map of current vs planned tech,
   every migration + reason, the free⇄premium toggle registry (ADR-14), and a
   per-spec technology index. Read after the ADRs for the full stack picture.
3. `auth-foundation/` (P1) — implement first, end to end.
4. `admin/` (P2).
5. `productivity/` (P3).
6. `resilience/` (P4).
