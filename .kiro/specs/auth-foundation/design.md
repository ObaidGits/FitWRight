# Design — P1 Multi-User Foundation

## Overview

Inherits all ADRs/standards from `../phase-2-roadmap.md`. This document makes
every decision needed to implement authentication (email/password + Google
OAuth), email verification, password reset, server-managed sessions with device
management, RBAC via a capability model, step-up/MFA readiness, hardened cookies
and abuse controls, user-scoped data, and a data-preserving migration — designed
to be secure, scalable, and extensible to SSO/orgs/passkeys later without
architectural rework.

## Architecture

```
Browser (Next.js)
  ├─ (auth) /login /signup /verify /reset  ─► POST /api/v1/auth/* (+ pre-session CSRF)
  ├─ "Sign in with Google"                 ─► GET /api/v1/auth/oauth/google/start → IdP → /callback
  ├─ (app)/* : middleware presence-guard + SSR /auth/session authoritative check
  └─ fetch(credentials:'include') + X-CSRF-Token on mutations
        │  __Host-session (httpOnly) + csrf (JS-readable) cookies
        ▼
FastAPI  ─ AuthMiddleware ─► resolve Principal (session→user, status+revoked recheck)
             │                   │
             │                   ├─ RBAC/capability dep · step-up dep
             │                   └─ user_id scoping in every repo call (Repo.scoped)
             ├─ auth package: passwords · sessions · oauth(provider iface) · csrf
             │                verification · reset · ratelimit · stepup · audit · principal
             ├─ KVStore (cache: sessions, rate-limits, transient oauth)  [ADR-6]
             │    free tier = Upstash Redis (free) or DB-backed fallback (no
             │    Redis); premium = Redis — selected by KVSTORE_URL, no code change
             └─ SQLAlchemy async + Alembic  (SQLite local / Postgres hosted)
```

**Module boundaries** (`app/auth/` package): `passwords.py`, `sessions.py`,
`oauth/` (`base.py` provider interface + `google.py`), `csrf.py`,
`verification.py`, `reset.py`, `ratelimit.py`, `stepup.py`, `principal.py`
(middleware + deps), `audit.py`. Routers call services; only the `Repo` layer
issues owned queries. This separation keeps auth cohesive and testable and makes
adding a provider or an MFA method a localized change.

> **DB portability:** SQLite local-dev only; Postgres-safe SQL for hosted. The
> hosted free-tier target is **Neon** (serverless Postgres) using the **pooled
> connection string**, selected by `DATABASE_URL` (ADR-13). Email uniqueness =
> normalized (NFKC+lowercase+trim) + unique index. Timestamps are zero-padded
> UTC ISO strings so lexical comparison is correct.

## Data Models

### New tables

**users**
| col | type | notes |
|---|---|---|
| id | uuid PK | |
| email | str unique (normalized) | `ux_users_email` |
| name | str | |
| password_hash | str? | null for OAuth-only |
| role | str | `user`\|`admin` (default `user`) |
| status | str | `active`\|`disabled`\|`pending_verification` |
| avatar_url | str? | P3 |
| email_verified_at | str? | |
| mfa_enrolled | bool | default false (reserved for MFA) |
| created_at / updated_at | str (UTC ISO) | |

**oauth_identities**
| provider | str | `google` (extensible) |
| subject | str | IdP stable `sub` |
| user_id | uuid FK→users(id) ON DELETE CASCADE, index |
| email_at_link | str | audit |
| created_at | str | PK `(provider, subject)` |

**sessions**
| id | uuid PK |
| token_hash | str unique | `sha256(raw)`; raw only in the cookie |
| user_id | uuid FK→users(id) ON DELETE CASCADE, index |
| csrf_secret | str | per-session; derives the csrf cookie |
| aal | str | `aal1`\|`aal2` (reserved) |
| step_up_at | str? | last step-up (sudo) time |
| remember_me | bool | drives absolute cap |
| device_label | str? | parsed from UA (e.g. "Chrome on macOS") |
| ip_hash | str? | keyed HMAC (salted) |
| created_at / last_seen_at | str | sliding-expiry driver = last_seen_at |
| expires_at | str, index | absolute cap |
| revoked_at | str? | non-null ⇒ dead |

**email_verification_tokens** / **password_reset_tokens** / **email_change_tokens**
| token_hash | PK (`sha256`) |
| user_id | FK→users ON DELETE CASCADE, index |
| new_email | str (**email_change_tokens only** — the pending address to switch to) |
| expires_at | str | short TTL |
| used_at | str? | single-use |
| _new tokens invalidate prior unused tokens (of the same kind) for the user_ |
| _email_change_tokens back verify-before-switch email change (R7.4): the raw_
| _token is sent to the **new** address and the primary email switches only once_
| _that link is confirmed via `POST /users/me/email/confirm`. Created by_
| _`create_all` locally and Alembic `0006` on hosted; reaped with the others._

**audit_log** (append-only)
| id uuid PK | ts (UTC ISO, index) | actor_user_id? | target_user_id? |
| event (str, index) | ip_hash? | request_id? | meta (json, sanitized) |

**kv** (DB-backed KVStore fallback — ADR-6)
| key | str PK | caller key, or an internal lock key |
| value | text? | stored value, or a lock holder's token |
| expires_at | float? | epoch seconds; NULL = no expiry |
| _created on demand by the DB adapter (SQLAlchemy Core, portable DDL) so the_
| _app runs with **no Redis**; migration `0002` also declares it for hosted PG_ |

**(reserved, not created in P1)** `authenticators` (WebAuthn/TOTP), `api_tokens`,
`organizations`/`memberships` — documented extension points; no code in P1.

### KVStore interface (ADR-6)
`KVStore` exposes exactly what the later waves need: `get`/`set`/`delete`
(optional TTL), `incr` (atomic; TTL applied on key creation → rate-limit windows),
and `lock` (TTL-bound single-flight, returned as an async context manager). Three
adapters — `LocalKVStore` (in-proc, single-worker dev), `RedisKVStore`
(Redis/Upstash), `DBKVStore` (the `kv` table above) — are chosen by `KVSTORE_URL`
via `kvstore_from_url(url, db_engine=...)`; empty/`local`/`memory` → local,
`redis://`/`rediss://` → Redis, `db`/`database` → DB fallback. Locks use a holder
token so a lapsed holder never frees a newer holder's lock; the DB adapter also
serializes read-modify-write within a worker (SQLite ignores `FOR UPDATE`) while
Postgres row locks cover cross-worker atomicity.

### Changed tables (user-scoping — ADR-4)
`resumes`/`jobs`/`improvements`/`applications` gain `user_id` (FK, index);
`resumes` single-master index → partial unique `(user_id, is_master)`;
`applications` dedupe → `(user_id, job_id, resume_id)`; `api_keys` PK →
`(user_id, provider)`.

### Indexing
`(user_id)` on every owned table; `(user_id, updated_at)`/`(user_id, status)` on
`applications`; `sessions(user_id, revoked_at)` (device list),
`sessions(expires_at)` (reaper); token tables `(user_id)`, `(expires_at)`;
`users(status)`; `audit_log(ts)`, `(event, ts)`, `(actor_user_id, ts)`.

## Components and Interfaces

### API (all `/api/v1`, error envelope ADR-7)
| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/auth/csrf` | public | issue pre-session CSRF token (login-CSRF defense) |
| POST | `/auth/signup` | public (RL) | create user + session (+ verify email) |
| POST | `/auth/login` | public (RL) | session (remember_me option) |
| POST | `/auth/logout` | session+CSRF | revoke current |
| POST | `/auth/logout-all` | session+CSRF+step-up | revoke all |
| GET | `/auth/session` | optional | principal (SafeUser+aal) or 401 |
| GET | `/auth/oauth/{provider}/start` | public | begin OAuth (provider iface) |
| GET | `/auth/oauth/{provider}/callback` | public | finish OAuth |
| POST | `/auth/verify/request` | session/public (RL) | (re)send verification |
| POST | `/auth/verify/confirm` | token | mark verified |
| POST | `/auth/password/change` | session+step-up | change password |
| POST | `/auth/password/forgot` | public (RL) | issue reset (uniform) |
| POST | `/auth/password/reset` | token | set new password + fresh session |
| POST | `/auth/step-up` | session (RL) | re-auth to open a sudo window |
| GET | `/users/me` | session | SafeUser |
| PATCH | `/users/me` | session | update name (role/status ignored) |
| POST | `/users/me/email` | session+step-up | begin email change (verify new) |
| POST | `/users/me/email/confirm` | token | confirm the new address → switch (verify-before-switch) |
| GET | `/users/me/sessions` | session | device list (no raw token) |
| DELETE | `/users/me/sessions/{id}` | session+CSRF | revoke one |
| POST | `/internal/run-jobs` | internal token | run the single-flighted reaper batch (external-cron hook, ADR-15) |
| GET | `/internal/metrics` | internal token | `AuthMetrics.snapshot()` JSON (monitoring) |

- **Internal machine endpoints (ADR-15).** `/internal/*` are machine-to-machine,
  not user endpoints: they carry **no session** and are guarded by a shared
  secret (`INTERNAL_JOB_TOKEN`) sent in the `X-Internal-Job-Token` header and
  compared in **constant time** (`secrets.compare_digest`). A missing token →
  `401 unauthorized`, a wrong token → `403 forbidden`, and when no token is
  configured (the zero-config local default) *every* caller is rejected — auth
  metrics and job control are never exposed unauthenticated. Because they carry
  no session principal, the per-session CSRF check in `AuthMiddleware` never
  applies to them (it is gated on the presence of a principal), so a free
  external cron (GitHub Actions / cron-job.org) can `POST /internal/run-jobs`
  with just the token. They live in `app/routers/internal.py` and render errors
  through the ADR-7 envelope.

- Pydantic v2 request/response models; `SafeUser` is the only user shape returned
  (never hash/tokens/internal flags beyond role/status/emailVerified/aal). The
  projection is enforced structurally: `SafeUser` is `extra="forbid"` and is
  built via an explicit-field classmethod (never `**row`), and
  `assert_safe_user()` (used at the boundary and in tests) fails loudly if a
  serialized user ever carries a field outside the whitelist — a
  serialization safeguard so a new `users` column can't silently leak.
- **Error envelope is opt-in (Task 4).** The ADR-7 envelope
  (`{ "error": { code, message, details? } }`) is rendered by raising
  `app.errors.ApiError` (handler installed on the app); the versioned auth/user
  routes use it uniformly (`invalid_credentials`, `email_unavailable`,
  `weak_password`/`breached_password` (400), `account_disabled` (403),
  `rate_limited` (429 + `Retry-After`), `step_up_required`/`unauthorized` (401),
  `conflict` (409), `not_found` (404), `csrf_failed` (403)). The pre-P1 routers
  keep FastAPI's default `{ "detail": … }` shape (no breaking change), as does
  the middleware CSRF rejection.
- `next` validation: must start with a single `/` (reject `//`, scheme, backslash)
  — open-redirect guard, shared by login + OAuth.
- Cookies set/cleared via `Response`; `__Host-` session cookie; per-session CSRF.

### OAuth provider interface
`OAuthProvider` = `{ authorize_url(state,nonce,challenge,next), exchange(code,
verifier) -> tokens, verify_id_token(token,nonce) -> {sub,email,email_verified,
name} }`. `google.py` implements it; the router is provider-generic
(`/auth/oauth/{provider}/…` with an allow-list registry). Adding GitHub/Microsoft
= a new impl + registry entry, no router/UI change.

### Scoping enforcement (repository layer)
`Repo.scoped(statement, model, user_id)` (in `app/repository.py`) composes
`WHERE user_id=:uid` into every owned `select`/`update`/`delete`; the `db`
facade owned methods all require `user_id` as their first parameter and load
single rows through scoped helpers (never a bare `session.get` on an owned
model), so a foreign/absent id resolves to `None`. The scope key lives once in
`Repo.SCOPE_KEYS` so a future `(org_id, user_id)` scope is a one-line change
(the `Repo.scoped(query, user_id)` signature gained an explicit `model` argument
so the composer knows which column to filter — a small, documented refinement).

**CI guard.** `app/scripts/check_scoping.py` is an AST-based guard (run standalone
and in `tests/unit/test_scoping_guard.py`) enforcing two rules: (1) owned-table
queries appear *only* in the repository layer (`app/database.py`) — a router or
service building one is rejected; (2) every `database.py` method that queries an
owned table references `user_id`. Schema (`models.py`), the composer
(`repository.py`), the owner bootstrap/backfill (`auth/owner.py`), one-time
importers (`app/scripts/*`), and Alembic migrations are allow-listed system paths.

**Effective `user_id` resolution.** Owned-resource endpoints depend on
`get_effective_user_id` (in `app/auth/principal.py`): hosted returns
`principal.user_id` (401 if anonymous); local `SINGLE_USER_MODE` returns the
lazily-ensured **bootstrap owner** (`app/auth/owner.ensure_owner`), so local
zero-config behaves exactly like today while still routing every query through
the `user_id` scope. A foreign id ⇒ 404 (the scoped `db` method returns `None`
and the router raises 404 — no existence disclosure).

**Per-user api-key resolution (R10.6).** Threading `user_id` through the entire
synchronous LLM call graph would touch dozens of unrelated services, so the
effective user id is also published on a request-scoped `ContextVar`
(`app/auth/context.py`) by `get_effective_user_id`; `get_llm_config(user_id=…)` /
`load_config_file` / `get_api_keys_from_config` resolve the caller's encrypted
keys from it (or an explicit `user_id`, or the owner). The encrypted key store
methods are per-user, so one user's provider key can never serve another's calls.

**Per-user single-master lock.** The global `_master_resume_lock` is replaced by
per-user `asyncio.Lock`s (`Database._master_locks`), since the single-master
invariant is now per user (Property 2).

## Session mechanics (ADR-1/2)

- **`__Host-session`**: opaque 32-byte base64url token; `HttpOnly, Secure,
  SameSite=Lax, Path=/`, no Domain. DB stores `sha256(token)` only.
- **`csrf`**: JS-readable, `SameSite=Lax`, value = HMAC(session.csrf_secret,
  session.id). Pre-session login/signup use a separate short-lived double-submit
  token from `GET /auth/csrf`.
- **Resolution:** read cookie → `sha256` → KVStore cache (short TTL) → DB
  fallback → assert `revoked_at IS NULL` AND `now < expires_at` AND
  `user.status == active` → build Principal (role, capabilities, aal, step_up_at).
  Disabled/revoked ⇒ 401 and cache eviction.
- **Sliding expiry (write-behind):** if `now > last_seen_at + refresh_window`,
  update `last_seen_at`/`expires_at` (bounded by absolute cap; remember_me →
  larger cap). A crash loses at most one window (accepted).
- **Cache invalidation:** logout/revoke/logout-all/role-change/disable/password-
  change **delete** the KVStore key(s) (write-through), guaranteeing rejection
  within one request cycle (R3.4) even before TTL.
- **CSRF check:** mutations require `X-CSRF-Token` == derived value; GET/HEAD/
  OPTIONS exempt; logout included. In the `AuthMiddleware`, per-session CSRF
  enforcement is **gated on `SINGLE_USER_MODE`** (active hosted, skipped local)
  so local zero-config boot and the pre-scoping owned-resource routes keep
  working before Task 3/4 thread auth through them; the pre-session token
  (`GET /auth/csrf`) protects login/signup regardless. The `Principal` carries
  the session's `csrf_secret` so the middleware can derive/verify without a DB
  round-trip.
- **Cache recheck:** the KVStore session snapshot stores `status`/`expires_at`;
  every resolution re-checks them against the snapshot (defense in depth), while
  write-through eviction on revoke/disable/role-change guarantees a changed
  session is rejected within one request cycle (R3.4) even before the short TTL
  lapses. The DB path additionally reloads `user.status` authoritatively.
- **Rotation:** new session id on login, password change, and role change.
- **Reaper:** batched deletion of expired / long-revoked sessions + expired
  tokens (scheduled, single-flighted). Runs under `SCHEDULER_MODE` (ADR-15): free
  tier as an `external_cron`-driven batch via an authenticated internal endpoint;
  premium as an `internal` scheduled worker — identical logic and single-flight
  either way. **Wiring (Package C):** `external_cron` (free default) starts
  nothing in-process; an external scheduler calls the authenticated
  `POST /api/v1/internal/run-jobs`, which invokes `SessionService.reap()` and
  returns a small JSON summary (`{ "status": "ok", "reaped": {…} }`). `internal`
  (premium) runs `app/scheduler.py:reaper_loop` as an asyncio task started in the
  app `lifespan` on a bounded interval (`REAPER_INTERVAL_SECONDS`, hourly
  default) and cancelled cleanly on shutdown (no task leak). Both paths call the
  **same** `reap()`, which acquires the non-blocking KVStore lock
  (`session:reaper`) so overlapping schedulers/workers never double-run — a
  concurrent call simply returns all-zero counts. Local zero-config boot starts
  no loop (default `external_cron`) and is unaffected.

## Auth flows

### Passwords
Argon2id (m/t/p tunable, ~50–100ms; length cap 128). Policy: ≥12 chars, denylist,
strength gate, optional HIBP breach check (fail-open if provider down, logged).
Verify is constant-time; unknown-email/existing-email branches run a **dummy
hash** to equalize timing.

### Signup enumeration handling (R1.2/R1.6, Property 4 — Task 4.1 refinement)
Signup evaluates the password policy/breach gate **first** (it depends only on
the submitted password, so a `weak_password`/`breached_password` reply leaks
nothing about the email), then branches by whether email verification is
enabled:
- **Verification ON (hosted, multi-user):** the response is **uniform** for both
  a new and an already-registered email — `200 { "status": "pending_verification" }`
  with **no session** and exactly one Argon2 op on each path (real hash for a new
  user, dummy hash for an existing one). Nothing distinguishes the two, closing
  the enumeration channel while creating the new user `pending_verification`
  (token issuance/resend is owned by Task 5's `verify/request`).
- **Verification OFF (`SINGLE_USER_MODE`/local):** signup signs the user in
  immediately (R1.4 — session + `SafeUser`), and an already-registered email
  returns `email_unavailable` (409) after a dummy hash. Enumeration is not a
  concern in single-user local mode. This keeps local zero-config boot behaving
  like today.

### Email verification
Signup (hosted) → `pending_verification` + hashed single-use TTL token → email
link → `/auth/verify/confirm` sets `email_verified_at` + `active`. Resend
rate-limited (per-IP **and** per-account), invalidates prior tokens. OAuth-verified
emails skip this. Tokens are `sha256`-at-rest with a configurable TTL
(`EMAIL_VERIFICATION_TTL`, default 24h); `verify/request` and `verify/confirm`
return a **uniform** ack (`{status}`), and a missing/used/expired token all
collapse to one generic `invalid_token` (400) so nothing is disclosed (R5.5).

**Sensitive-action gate (Task 5).** The gate is implemented as a single
dependency, `require_verified_user_id` (in `app/auth/principal.py`), a drop-in
replacement for `get_effective_user_id` on the provider-cost endpoints (resume
tailoring: `improve`/`improve/preview`/`improve/confirm`; generation:
`generate-cover-letter`/`generate-outreach`/`generate-interview-prep`; enrichment:
`analyze`/`enhance`/`regenerate`; **the resume wizard `resume-wizard/turn`** —
its `answer`/`skip` actions call the LLM, so it is user-scoped + verification-
gated like the others, closing the one endpoint that shipped unscoped before
Task 10.1). It resolves the owning `user_id` exactly like
`get_effective_user_id` (so the LLM api-key context var is still published and
anonymous hosted requests still 401) and then, **only when
`email_verification_enabled`**, refuses an unverified principal with
`403 email_verification_required`. Basic use (browsing, **upload**, listing) is
never gated, and OAuth sign-ups arrive verified so they are never gated (R5.6).
The gate keys off the principal's `email_verified` flag, not `status`: a
`pending_verification` account cannot log in (R2.4) and instead completes
verification via the emailed link (no session required) before it becomes
`active`; the gate therefore protects any *active-but-unverified* session
(e.g. verification toggled on for a deployment with pre-existing active users).

### Password reset
`forgot` → uniform response; if email exists, hashed single-use short-TTL token
(`PASSWORD_RESET_TTL`, default 30m; prior unused invalidated) via email. `reset`
(token + new password) → **validate policy/breach first (read-only token peek so
a typo does not burn the single-use link)**, then atomically consume the token,
set hash, **revoke ALL sessions**, issue a fresh session, audit `password_reset`.
A missing/used/expired token collapses to one generic `invalid_token` (400).
OAuth-only accounts (no password) can **set** a password here (links password
auth); a successful reset also verifies a still-`pending_verification` account
(the link proved email ownership).

### Step-up ("sudo") & MFA readiness
Sensitive actions require `now - session.step_up_at < STEP_UP_WINDOW` else 401
`step_up_required`; `POST /auth/step-up` re-verifies password (future: MFA),
bumps `step_up_at`/`aal`, audited + rate-limited. `mfa_enrolled` + `aal2` +
reserved `authenticators` table make TOTP/WebAuthn additive later.

### Password & email change (Task 6.2)
Both require a recent step-up (`require_step_up`/`require_stepped_up_session` →
401 `step_up_required`, Property 6). `POST /auth/password/change` re-verifies the
current password (constant-time), enforces policy + breach on the new one,
rehashes, and **revokes every OTHER session** (the initiating device stays signed
in, R7.3), audit `password_changed`. Email change is **verify-before-switch**
(R7.4): `POST /users/me/email` (step-up) enforces uniqueness and issues a hashed
single-use `email_change_tokens` row bound to the **new** address, emailing a
confirmation link — the primary `email` is left untouched. `POST
/users/me/email/confirm` (token-only, single-use) redeems that token, switches
the primary email to the now-verified address (uniqueness re-enforced by the
`ux_users_email` unique index — a lost race → 409 `email_unavailable`), marks it
verified, and audits `email_changed`. The account therefore never moves to an
unverified address.

### Google OAuth
`start`: gen state/nonce/verifier → signed httpOnly transient cookie (5-min) +
optional validated `next` → redirect. `callback`: constant-time state check →
exchange with verifier → verify id_token (JWKS cached w/ rotation, iss/aud/exp/
iat±skew/nonce) → require `email_verified` → link/create per R4.4 rules → issue
session → clear transient cookies → redirect to validated `next` or `/home`.

**Module layout (Task 7).** `app/auth/oauth/`: `base.py` (the provider-agnostic
`OAuthProvider` ABC + `OAuthTokens`/`OAuthUserInfo`/`OAuthError`), `google.py`
(the Google OIDC impl with **injected** token-HTTP client + JWKS provider + clock
so id_token verification is unit-tested against a mock IdP/JWKS with a fixed
clock — never Google), `registry.py` (the `name → factory` allow-list; only known
providers are routable, unknown → `UnknownProvider`, known-but-unconfigured →
`ProviderNotConfigured`), `state.py` (PKCE helpers + the signed transient cookie),
and `linking.py` (the safe link/create decision, Property 5 / R4.4). Google's
authorize/token/JWKS endpoints are fixed constants (SSRF-safe).

**Transient state cookie (refinement).** The `state`/`nonce`/PKCE `code_verifier`
(+ optional `next`) are packed into a **single** signed, httpOnly, `SameSite=Lax`
cookie (`oauth_txn`, 5-min TTL) via `itsdangerous.URLSafeTimedSerializer` (signed
with `SESSION_SECRET`, dual-key verify window for rotation). One atomic blob
(rather than several cookies) keeps the transient state all-or-nothing and is set
once at `/start` and cleared as a unit on both success and failure at `/callback`.

**Error surface (refinement).** `/start` rejects an unknown provider with `404
unknown_provider` and a configured-but-missing provider (e.g. Google with no
creds, or no `OAUTH_REDIRECT_URI`) with `400 oauth_not_configured` — a clean
error, so local zero-config boot is unaffected. `/callback` collapses **every**
failure (bad/missing/expired state cookie, state mismatch, missing code, PKCE/
exchange failure, id_token verification failure, unverified provider email, or a
refused anti-hijack link) to a single uniform outcome: clear the transient
cookie, create no session, and `302` to `{FRONTEND}/login?error=oauth_failed`
(R4.6). A truly unknown `{provider}` on `/callback` is still the routing-level
`404 unknown_provider`. Both `/start` and `/callback` are rate-limited via the
`oauth` KVStore rule (R13.1).

**Safe link/create matrix (R4.4, as implemented in `linking.py`).** Evaluated in
order: (1) an `oauth_identities` row for `(provider, subject)` exists → log that
user in; (2) no identity and no account with the verified email → create a new,
already-verified OAuth-only user + identity; (3) no identity but an account with
that email exists → link **only if** the provider email is verified AND (the
account has no password OR its email is already verified OR the request is
authenticated as that same user, i.e. linking from Settings); (4) otherwise
(a password account with an unverified email, request not authenticated) →
**refuse** (`link_required` → `oauth_failed`, no session, no row) so an unverified
account is never hijacked and no duplicate is created.

## RBAC & capabilities
`capabilities_for(user)` maps role→capabilities in one place; deps
`get_principal` (401), `require_capability(cap)` (403). P2 admin uses it. Role
change revokes sessions; self role/status change refused.

## Migration plan (ADR-9, Alembic)
1. `0001` baseline (current implicit schema; empty autogenerate diff verified).
2. `0002` new auth tables (users/sessions/oauth/audit/verification/reset) **plus
   the `kv` table** for the DB-backed KVStore fallback. `0002` owns the canonical
   `kv` schema (`key` PK / nullable `value` / `expires_at` epoch-seconds float),
   mirroring `app.auth.kvstore.db`; locally the adapter still self-creates it on
   demand (`create_all` is checkfirst) so the two paths never conflict.
3. `0003` nullable `user_id` (FK→users.id ON DELETE CASCADE, named
   `fk_<table>_user_id_users`) + `ix_<table>_user_id` on every owned table
   (resumes/jobs/improvements/applications/api_keys). Added via `batch_alter_table`
   so SQLite recreates the table by copy-and-move (preserving rows, the resumes
   partial-unique index, and the applications unique constraint) while Postgres
   does an in-place `ALTER` — the named FK is required for SQLite batch.
4. `0004` backfill: create owner (role=admin, active, verified; email
   normalized NFKC+lowercase+trim from `OWNER_EMAIL`; password Argon2id-hashed
   only if `OWNER_PASSWORD` set, else NULL/OAuth-only), assign all owned rows
   + api_keys to owner. Idempotent (owner created only if the normalized email is
   absent; rows updated only `WHERE user_id IS NULL`) and chunked (paged by PK in
   bounded batches). Reverse un-assigns those rows and deletes the owner.
5. `0005` enforce: `user_id` NOT NULL on owned tables; global single-master
   index → partial unique `(user_id, is_master)` (both `sqlite_where` and
   `postgresql_where`); applications dedupe on `(user_id, job_id, resume_id)` +
   unique constraint moved there; `api_keys` PK `(provider)` → `(user_id,
   provider)` via an explicit table rebuild (create-copy-drop-rename — a PK
   change SQLite cannot do in place). Constraint/NOT-NULL changes use
   `batch_alter_table`.
6. `0006` add `email_change_tokens` (hashed single-use TTL token + pending
   `new_email`) for the verify-before-switch email change (R7.4). Reverse drops
   the table; locally `create_all` provides the same table (zero-config boot).

**ORM vs. migration (transitional state).** The ORM (`app/models.py`) is the
single schema definition used by the local zero-config `create_all` path. Now
that Task 3 threads `user_id` through the repository layer, the ORM is
**reconciled to the enforced per-user shape** for the constraints that must be
per-user for correctness: `resumes` single-master is a partial unique index on
`(user_id, is_master)`; `applications` dedupe is unique on
`(user_id, job_id, resume_id)`; and `api_keys` is keyed by the composite PK
`(user_id, provider)` (so its `user_id` is NOT NULL — a user always owns its
keys). The document tables (`resumes`/`jobs`/`improvements`/`applications`) keep
`user_id` **nullable** at the column level during P1 so pre-existing local rows
load; migrations `0003→0005` phase in the enforced hosted shape (NOT NULL,
per-user unique, per-user `api_keys` PK) on the Alembic path.

**Local schema evolution + owner backfill (zero data loss).** `create_all`
creates the full `user_id`-scoped schema for fresh local DBs but never ALTERs
existing ones, so `init_models_sync` adds a nullable `user_id` column (+ index)
to any owned table missing it — the local equivalent of migration `0003`.
Existing local rows created before scoping therefore have `user_id IS NULL`; on
boot (and lazily on first request) `app/auth/owner.ensure_owner` creates the
bootstrap owner (mirroring migration `0004`: role=admin, active, verified, email
normalized from `OWNER_EMAIL`) and **backfills every `user_id IS NULL` owned row
to the owner**. The backfill is idempotent (updates only NULLs), runs at most
once per process, and causes zero data loss. Because hosted uses the Alembic
chain and local uses `create_all` + this backfill, single-user local (one owner)
behaves identically to the enforced per-user constraints.

**Alembic wiring.** `alembic/env.py` resolves the database URL from (in order)
an `-x db_url=…` argument, the `ALEMBIC_DATABASE_URL` env var (used to run the
chain against a **throwaway copy** — the ops/test path that must never touch the
real dev DB), then `settings.effective_database_url` (ADR-13). It normalizes to
an async driver and loads logging with `disable_existing_loggers=False` so
running the chain in-process never mutes the app's loggers.

Down-migrations reverse each step (verified on a seeded copy: up→head preserves
100% of owned rows and enforces the constraints; down→base loses no owned rows
on the way down); run pre-traffic; DB backed up first.

**Runtime DB wiring (engine selection, ADR-13).** The runtime consumes the same
`settings.effective_database_url` as Alembic, so both talk to the same database
(there is no separate hardcoded SQLite path). `app/db_engine.py` selects the
dialect from the resolved URL: **SQLite** → `sqlite+aiosqlite://` (async) /
`sqlite://` (sync) with the WAL/`busy_timeout`/`foreign_keys` PRAGMAs applied
per connection; **Postgres** → `postgresql+asyncpg://` (async) /
`postgresql+psycopg://` (sync, psycopg v3, on the encrypted-`api_keys` hot path),
normalizing a bare `postgresql://` (and `postgres://`/`postgresql+psycopg2://`)
to those drivers exactly as `env.py` does. Postgres pooling is configured from
`db_pool_size`/`db_use_pooler`: with a transaction pooler (Neon/PgBouncer,
`db_use_pooler=true`) server-side prepared statements are unsafe, so asyncpg's
`statement_cache_size` and SQLAlchemy's `prepared_statement_cache_size` are set
to `0`, psycopg's `prepare_threshold` is `None`, and pooling is deferred to the
external pooler via `NullPool`; direct connections use an in-process `QueuePool`
(`pool_size=db_pool_size`, `pool_pre_ping=True`). The two-engine design (async
docs + sync api_keys) is preserved on both backends, and `Database` accepts an
explicit URL/path override for isolated tests. **Schema ownership** differs by
backend: locally, SQLite schema evolves via `create_all` + `init_models_sync`
(the zero-config boot path); on Postgres the schema is owned by the Alembic
chain, so `init_models_sync` is a **no-op guarded on the SQLite dialect** — it
never runs `create_all`/`ALTER`/`PRAGMA` against Postgres. Hosted also fails
fast: config validation already requires a Postgres `DATABASE_URL` when
`SINGLE_USER_MODE=false`, and the runtime now uses it directly, so an
unreachable DB surfaces a connection error at boot rather than silently writing
to a local SQLite file. The single-master partial-unique index predicate is
dialect-specific (`is_master = 1` on SQLite, `is_master = true` on Postgres —
Postgres rejects `boolean = integer`) in both the ORM and migrations `0001`/
`0005`.

## Frontend design

**Single-user flag mirror (Task 8 refinement).** The backend `SINGLE_USER_MODE`
is mirrored on the client as `NEXT_PUBLIC_SINGLE_USER_MODE` (default `true`;
hosted sets `false`) in `lib/config/auth.ts`. It only changes *UX* (whether the
app hydrates a real session / guards routes) — the server is always the access
boundary. In single-user mode the whole auth layer is a no-op: the session is
the synthetic bootstrap **owner** (admin), guards don't redirect, and the
account-security surface is hidden — so local zero-config boot is byte-for-byte
unchanged (R14.3/15.5).

- **`SessionProvider`** hydrates from `/auth/session` via TanStack Query (short
  `staleTime`, `retry:false`); status `loading|authenticated|guest`. It is
  seeded with the SSR-resolved user (`initialData`): a real SSR user renders
  authenticated with no flash and no first-paint fetch; an SSR `null` (guest or
  backend unreachable) leaves `initialData` undefined so the client does one
  authoritative fetch, surfacing a brief `loading` (not a wrong `guest` flash).
  In `SINGLE_USER_MODE` it short-circuits to the owner with no hydration.
- **SSR authoritative check.** A server-only `getServerSession()`
  (`lib/api/session-server.ts`) reads the request cookies (incl. the httpOnly
  session cookie) and calls the backend `/auth/session` per-request. It is used
  in the **root layout** (to seed the provider) and in the **`(app)`** and
  **`admin`** layouts, which redirect (`/login?next=…`, or `/home` for a
  non-admin) *before* render. In `SINGLE_USER_MODE` it returns the owner
  synchronously **without** touching cookies, so local pages stay statically
  renderable.
- **`middleware.ts`** is a presence-only edge fast-path (documented UX-vs-
  boundary split): it redirects to `/login?next=<path+query>` when the session
  cookie is *absent* on a protected route (the `(app)`/`admin` URL prefixes),
  and is a no-op in `SINGLE_USER_MODE`. It also forwards the resolved pathname
  as `x-pathname` so the SSR layouts can build an accurate `next`. (Next 16
  renames this convention to `proxy`; the file keeps working as middleware.)
- **`apiFetch`** always sends `credentials:'include'`, injects `X-CSRF-Token` on
  every mutating request from the JS-readable `csrf` cookie (covering both the
  per-session token and the pre-session token from `GET /auth/csrf`), and runs a
  single 401 interceptor that invokes a handler registered by the
  `SessionProvider` — clear the session query, broadcast a multi-tab logout
  (BroadcastChannel + a `localStorage` storage-event fallback), and route to
  `/login?next`. Auth-flow calls pass `skipAuthHandling` so their *expected*
  401s (guest probe, wrong password, `step_up_required`) are handled inline and
  never trigger the global redirect; CSRF injection still applies to them.
- **`lib/api/auth.ts`** is the typed client for the whole auth/user surface
  (session, login/signup, logout(+all), verify request/confirm, forgot/reset,
  step-up, password/email change, profile, device list/revoke, OAuth start
  URL). Failures throw `AuthApiError` carrying the ADR-7 `code` (+ `Retry-After`
  for `rate_limited`) so the UI branches precisely and renders a uniform banner.
- **Screens (reuse Atelier):** login/signup wired to `authApi` (inline
  validation, single non-leaky error banner via `describeAuthError`, validated
  same-origin `next`, forgot link, Google button → `/auth/oauth/google/start`,
  `autocomplete`, password reveal + caps-lock hint, password cleared on
  failure); verify-email (`/verify` landing + resend + a persistent pending
  banner in the app shell); forgot (`/forgot`) / reset (`/reset`); email-change
  confirm landing (`/verify-email`, matching the backend link); OAuth-failure
  banner on `/login?error=oauth_failed` (retry + password/link fallback, which
  doubles as the account-linking prompt); a **step-up modal** exposed as
  `useStepUp().run(action)` that transparently re-auths on `step_up_required`
  and retries the original action; Settings→Account (change password, verify-
  before-switch email change, device list + revoke, log-out-everywhere) —
  sensitive ones flow through `run(...)`. The account-security surface renders
  only in hosted mode.

## Security — threat model & mitigations
| Threat | Mitigation |
|---|---|
| XSS token theft | httpOnly `__Host-` cookie; no token in JS; strict CSP; React escaping; sanitizer-only HTML |
| CSRF (incl. login-CSRF, logout) | SameSite=Lax + double-submit on all mutations + pre-session token for login/signup |
| Session fixation | rotate id on login/priv-change/password-change |
| Session hijack (DB leak) | store `sha256(token)`; short absolute TTL; revocation + cache eviction |
| Stale cache after revoke/disable | write-through cache eviction + per-request status/revoked recheck |
| User enumeration | uniform shape+timing (dummy hash) on signup/login/forgot/verify/resend |
| Brute force / stuffing | per-ip+account RL, backoff, lockout, CAPTCHA hook, HIBP breach check |
| Vertical priv-esc | capability checks server-side; self role/status change refused; role change revokes sessions |
| IDOR / cross-user | mandatory `user_id` scoping; 404 on foreign id; repo guard + tests |
| OAuth CSRF/replay | state+nonce+PKCE; full id_token verify; single-use transient cookies; exact redirect allow-list |
| Account linking hijack | link only on provider-verified email + (no-password OR verified OR authenticated) |
| Open redirect | `next` single-slash same-origin validation |
| Token/secret in logs | scrubbing; never log cookies/tokens/keys; sanitized audit meta |
| SSRF | fixed IdP + JWKS endpoints; no user-supplied URLs |
| IP correlation | keyed-HMAC `ip_hash` (salted) |
| Sensitive-action abuse on hijacked session | step-up/sudo window; audit |
| Clickjacking / MIME / TLS strip | frame-ancestors none, X-CTO nosniff, HSTS |
| Secret compromise | `SESSION_SECRET` dual-key rotation window; provider secret rotation runbook |

## Reliability
Signup/login race-safe (unique email + dummy hash still runs); session create
transactional. OAuth callback single-use (transient cookies cleared). `PATCH
/users/me` optimistic-concurrency (`updated_at` → 409). KVStore outage: auth RL
fail-closed (Retry-After), read scoping fail-open (DB source of truth), session
cache miss → DB. Migrations transactional + idempotent + reversible.

## Performance & scalability
O(1) session resolution via short-TTL cache + write-behind sliding expiry;
cached user row (invalidated on role/status/password change). Argon2 ~50–100ms,
rate-limited so cost is bounded; login p95 <300ms excl. Argon2. Indexed
sessions/users/tokens; batched reaper; audit monthly partition/rotate. Stateless
workers behind LB; KVStore (Redis) horizontal; session store in DB survives
cache loss (cold-start re-login not required — DB is truth).

## Observability & operations
Metrics per R16.1 (incl. cache hit ratio, oauth-failure-by-reason, step-up).
Audit per R16.2. Alerts per R16.4. Secret rotation: dual-key verify window for
`SESSION_SECRET`/CSRF derivation; provider secret rotation; runbook (rotate keys,
force logout-all, unstick reaper, re-run migration, restore from backup). DR:
KVStore is a cache (loss ⇒ cold DB reads, no logout); DB backups on RPO;
sessions/audit recoverable from backup.

**Implementation (Task 9.2).**
- **Structured logs.** `app/observability.py` installs a `JsonLogFormatter` on
  the root logger at startup (in `lifespan`, never at import, so tests are not
  affected). Every line is one JSON object carrying `ts`/`level`/`logger`/`msg`
  and — from `contextvars` set per request — `request_id` and (when known)
  `user_id`. Every value is run through the shared audit sanitizer
  (`sanitize_log_value`), so secret-bearing keys are dropped and CR/LF is
  neutralized: **no secrets/tokens/PII beyond `user_id` are ever logged**
  (R16.1). `RequestContextMiddleware` mints/propagates the `request_id`
  (honoring an inbound `X-Request-ID`, echoed on the response), and emits one
  `app.access` line per request (method/path/status/duration/`user_id`).
- **Metrics.** `app/auth/metrics.py` is a small in-process counter registry
  (`AuthMetrics`, process singleton via `get_metrics()`) recording R16.1's
  signals: `login_success`/`login_failure`, `signup`, `verification_sent`/
  `verification_confirmed`, `reset_requested`/`reset_completed`, `lockout`,
  `rate_limited`, `captcha_required`, `step_up_success`/`step_up_failure`,
  `oauth_success`, `oauth_failure` + a labelled `oauth_failure_by_reason` map,
  and `session_cache_hit`/`session_cache_miss` (from which `snapshot()` derives
  the **session-cache hit ratio**). The auth router and `SessionService.resolve`
  call the `record_*` helpers; `snapshot()` is JSON-serializable so a real
  exporter (ADR-11) binds to it later without touching call sites. It is exposed
  (Package C) via the **authenticated** internal endpoint
  `GET /api/v1/internal/metrics` (same `INTERNAL_JOB_TOKEN` shared-secret guard
  as the reaper hook, constant-time compared) — auth metrics are never exposed
  unauthenticated. JSON is the shipped format; a Prometheus text exporter can
  bind to the same `snapshot()` later.
- **Audit.** Security-relevant events are written via `AuditService`
  (`app/auth/audit.py`, sanitized meta): `signup`, `login`, `auth.login_failed`,
  `logout`, `logout_all`, `password_changed`, `password_reset`, `email_verified`,
  `email_changed`, `oauth_link`, `auth.step_up`, `session_revoked`,
  `authz.denied`, plus `role.changed` / `user_disabled` (emitted by the P2 admin
  surface — the events + writer live here so the admin wave only calls them).

### Secret-rotation runbook (`SESSION_SECRET`) — R16.3
`SESSION_SECRET` signs the **pre-session CSRF token** (login/signup double-submit)
and the **transient OAuth state cookie** (`oauth_txn`). It is *not* used for
session tokens (opaque, stored as `sha256`) or the per-session CSRF cookie
(keyed by the session's own random `csrf_secret`), so rotating it never forcibly
logs anyone out. Both signature verifiers accept the current **or** the previous
key (`verify_presession_token` and `deserialize_transaction` try
`SESSION_SECRET` then `SESSION_SECRET_PREV`), giving a zero-downtime overlap
window. To rotate:
1. **Stage the overlap.** Set `SESSION_SECRET_PREV` to the *current*
   `SESSION_SECRET`, and set `SESSION_SECRET` to a fresh ≥16-char random value.
   Deploy. New tokens are signed with the new key; tokens signed with the old key
   still verify during the window.
2. **Bake.** Leave both keys in place for at least the longest signed-artifact
   TTL (pre-session CSRF cookie = 1h; `oauth_txn` = 5m) plus a safety margin, so
   every in-flight login/OAuth round-trip completes.
3. **Retire the old key.** Remove `SESSION_SECRET_PREV` (or set it empty) and
   deploy. Tokens signed with the retired key are now rejected — verify by
   issuing a token before removal and confirming it fails after.
4. **Emergency rotation (suspected compromise).** Do steps 1+3 together (skip the
   bake): rotate `SESSION_SECRET`, leave `SESSION_SECRET_PREV` empty. In-flight
   login/OAuth flows must restart (acceptable under compromise). Follow with a
   forced `logout-all` sweep if session-token confidentiality is also in doubt.

Provider client secrets (Google) rotate independently at the IdP + env var (no
dual-key needed — only used server-side at exchange time). Owner/DB credentials
rotate via the platform secret store.

### Backup & restore runbook (migrations) — R14.1/14.2
The DB is the source of truth for users, sessions, audit, and every owned row, so
**every migration is preceded by a backup and validated on a throwaway copy
before it touches production** (Property 7). The `ALEMBIC_DATABASE_URL` env var
overrides the resolved app URL (see `alembic/env.py`) precisely so the chain can
be exercised against a copy — the integration suite
(`tests/integration/test_auth_migrations.py`) uses the same override and **never
touches `data/resume_matcher.db`**.

1. **Back up first (always, pre-migration).**
   - *SQLite (local/free):* stop writers, then copy the file:
     `sqlite3 data/resume_matcher.db ".backup 'backups/resume_matcher-$(date +%FT%H%M%S).db'"`
     (or a plain file copy while the app is stopped).
   - *Postgres (hosted):* `pg_dump "$DATABASE_URL" -Fc -f backups/rm-$(date +%FT%H%M%S).dump`
     (Neon: also keep the platform's point-in-time restore window).
2. **Dry-run the chain on a copy.** Point Alembic at a *copy*, never the live DB:
   - SQLite: `cp data/resume_matcher.db /tmp/rm-copy.db` then
     `ALEMBIC_DATABASE_URL="sqlite+aiosqlite:////tmp/rm-copy.db" uv run alembic upgrade head`
     and confirm `alembic downgrade base` succeeds with no owned-row loss.
   - Postgres: restore the dump into a scratch database and run
     `ALEMBIC_DATABASE_URL="<scratch-url>" uv run alembic upgrade head`.
   Verify owned-row counts are unchanged and the owner backfill assigned every
   row (the assertions in the migration suite mirror this check).
3. **Migrate production pre-traffic.** With the backup in hand and the dry-run
   green, run `uv run alembic upgrade head` against production during a
   maintenance window (the backfill `0004` is idempotent + chunked, so a retry is
   safe).
4. **Verify.** Confirm `alembic current` is at head, spot-check that a known
   user's owned rows are visible, and watch the migration-failure alert (R16.4).
5. **Rollback.** If a step misbehaves: `uv run alembic downgrade <prev>` (each
   step has a verified reversible down path), and if data integrity is in doubt,
   **restore from the step-1 backup** (`.backup` file copy for SQLite; `pg_restore
   --clean` for Postgres) — the authoritative recovery path. Sessions/audit are
   recoverable from the same backup; the KVStore is only a cache (its loss forces
   cold DB reads, never a mass logout).

## Deployment
Deps: `argon2-cffi`, `alembic`, `authlib` (OAuth), `itsdangerous` (signed
transient cookies), `redis` client (async; serves Redis + Upstash) plus the
in-proc and DB-backed KVStore adapters. Pluggable `EmailSender` /
`CaptchaVerifier` / `BreachedPasswordCheck` interfaces ship with real dev-safe
defaults (log-only email; fail-open-logged captcha/breach) selected when no
provider is configured, **plus real per-deploy adapters** selected by value
(ADR-14): `EMAIL_PROVIDER=smtp` (stdlib `smtplib`/`email`, STARTTLS) or
`resend` (HTTP API); `CAPTCHA_PROVIDER=turnstile` (Cloudflare siteverify);
`BREACH_PROVIDER=hibp` (HaveIBeenPwned k-anonymity range API — sends only the
5-char SHA-1 prefix, never the password/full hash, fail-open on outage, **needs
no credentials**). The factories in `app.auth.runtime` **never raise for a
missing/misconfigured provider**: a recognized provider missing its delivery
credentials, or an unrecognized value, gracefully degrades to the dev-safe
default with a single logged warning naming the problem, so the app always
boots. **Deploy-time credentials needed for live delivery:** SMTP
(`EMAIL_SMTP_HOST`/`PORT`/`USER`/`PASSWORD` + `EMAIL_FROM`) or Resend
(`EMAIL_API_KEY` + `EMAIL_FROM`) for real email; `CAPTCHA_SECRET` for Turnstile.
HIBP breach checking needs no credentials. hCaptcha/reCAPTCHA remain documented
future CAPTCHA variants. Env: `SESSION_SECRET`
(+ `SESSION_SECRET_PREV` for rotation), `GOOGLE_CLIENT_ID/SECRET`,
`OAUTH_REDIRECT_URI`, `OWNER_EMAIL`, `SINGLE_USER_MODE`, `EMAIL_VERIFICATION`,
`KVSTORE_URL` (Upstash free / DB-backed fallback on free tier), `DATABASE_URL`
(Neon pooled on free tier), Argon2 params, `STEP_UP_WINDOW`,
`SESSION_ABSOLUTE_TTL`/`REMEMBER_ME_TTL`/`IDLE_TTL`,
`EMAIL_VERIFICATION_TTL`/`PASSWORD_RESET_TTL`, `IP_HASH_SECRET`, CAPTCHA +
breached-password + email provider config, cookie settings. `SCHEDULER_MODE`
(reaper: `external_cron` free / `internal` premium) and `STORAGE_PROVIDER` are
inherited free/premium env toggles (ADR-14). `INTERNAL_JOB_TOKEN` is the shared
secret guarding the internal reaper/metrics endpoints (required for the free-tier
external-cron reaper to run and for the metrics poll; when unset the `/internal/*`
endpoints reject every caller); `REAPER_INTERVAL_SECONDS` (hourly default) tunes
the `internal`-mode in-process loop. Rollout: migrate → deploy
`SINGLE_USER_MODE=on` (identical to today) → enable auth + verification flags →
verify → hosted sets `SINGLE_USER_MODE=off`. Rollback: flags off + `alembic
downgrade` (verified) + restore backup if needed.

## Correctness Properties

### Property 1: User isolation

**Validates: Requirements 10.2, 10.3**

For any two distinct users A and B, no request authenticated as A can read or
mutate a row owned by B; a foreign id returns 404 (no existence disclosure).

### Property 2: Single master per user

**Validates: Requirements 10.4**

At most one `resumes` row per user has `is_master=1` (partial unique index).

### Property 3: Session integrity & prompt revocation

**Validates: Requirements 3.4, 12.4**

A revoked/expired session, or a session whose user is not `active`, never
authorizes a request; revocation takes effect within one request cycle via
write-through cache eviction; only `sha256(token)` is stored so a DB leak cannot
mint sessions.

### Property 4: No enumeration

**Validates: Requirements 1.2, 2.2, 6.1, 13.4**

Signup, login, forgot-password, verify, and resend are uniform in response shape
and timing (dummy Argon2 on non-hashing branches), disclosing nothing about
whether an email is registered.

### Property 5: OAuth authenticity & safe linking

**Validates: Requirements 4.2, 4.4, 4.6**

A session is issued only after full id_token verification and constant-time state
match; an identity links to an existing account only on a provider-verified email
under the linking rules, never silently hijacking or duplicating an account.

### Property 6: Sensitive actions require step-up

**Validates: Requirements 9.1, 7.3, 7.4**

Password change, email change, revoke-all, and future account deletion require a
recent step-up; a merely-hijacked session (no recent re-auth) cannot perform them.

### Property 7: Migration preserves data and is reversible

**Validates: Requirements 14.1, 14.2**

Every pre-existing owned row maps to exactly one owner user after backfill; each
migration has a verified reversible down path.

## Error Handling
Standard envelope; generic client messages, specifics logged. Codes:
`invalid_credentials`, `email_unavailable`, `weak_password`/`breached_password`,
`account_disabled`, `oauth_failed`, `step_up_required` (401), `rate_limited`
(429 + Retry-After), `captcha_required` (403 — a configured CAPTCHA challenge is
required past the soft failure threshold on login/signup; fail-open when no
provider is configured), `invalid_token` (400 — verification/reset link
missing/used/expired, uniform), `email_verification_required` (403 — unverified
account hitting a gated provider-cost action), 401 (no/expired session), 403
(capability denied), 404 (foreign resource), 409 (profile version conflict), 422
(validation). OAuth errors clear transient cookies and create no session. All
user-supplied strings sanitized before logging/audit.

## Testing Strategy
- **Unit:** password policy + Argon2 + dummy-hash timing; breach/CAPTCHA hooks;
  session token hash/rotation/sliding + cache eviction; csrf derive/verify +
  pre-session token; `next` validation; id_token verifier (mock JWKS + rotation +
  skew); linking decision matrix; step-up window; capability mapping; scoping
  composer; `ip_hash` keyed.
- **Integration:** every endpoint (happy+negative); authz matrix (anon/user/
  admin/disabled/pending × owned+admin routes); ownership 404; per-user single-
  master; api-key per-user isolation; verification + reset (single-use, revoke-
  all, OAuth-set-password); email change (verify-before-switch, uniqueness);
  step-up gate; disabled-user cached-session rejection; migration up/down on a
  seeded copy; rate-limit/lockout; CSRF reject (incl. login-CSRF, logout).
- **Live datastore validation (ADR-13/ADR-6).** Beyond the SQLite/local-KVStore
  default suite, gated best-effort integration tests exercise the runtime against
  a **real Postgres** (`test_postgres_backend.py`: Alembic `upgrade head` →
  scoped CRUD round-trip proving the async **asyncpg** + sync **psycopg** engines
  actually talk to Postgres, not the local SQLite file → `downgrade base`) and a
  **live Redis** (`test_redis_kvstore.py`: the `RedisKVStore` get/set/incr-TTL/
  lock contract against a running server). Both use `TEST_DATABASE_URL` /
  Redis when supplied, otherwise spin a disposable container via Docker, and skip
  with a clear reason when neither is available — so CI runs them for real while
  local zero-config stays green.
- **Frontend↔backend integration trace + E2E auth bootstrap.** A completion-pass
  trace verifies every `lib/api/auth.ts` method maps to a current backend route
  (path/method/shape); the gated hosted Playwright journeys are made runnable by
  a `globalSetup` (`e2e/auth.setup.ts`) that performs a real signup/login to seed
  the session `storageState` (+ a 2nd device for the revoke test) behind
  `RUN_AUTH_E2E=1`.
- **Security:** IDOR cross-user (404); CSRF without header; fixation (id rotates);
  enumeration timing (statistical, incl. signup dummy-hash); open-redirect;
  OAuth state/nonce mismatch + replay; linking-hijack attempts; breached-password
  reject; step-up bypass attempts; cookie attribute assertions (`__Host-`, Secure,
  HttpOnly, SameSite).
- **Perf:** login + session-resolve under concurrency; cache hit path; lockout
  under burst; Argon2 budget.
- **A11y/Mobile:** keyboard/SR/contrast/reduced-motion on all auth forms +
  step-up modal; mobile login + OAuth top-level redirect + cookie persistence +
  browser-switch.
- **E2E (Playwright):** signup→verify→home; login(+remember-me)→next; logout +
  multi-tab logout; Google (mocked IdP or gated); forgot→reset→login; step-up on
  password change; session-expiry redirect; device list + revoke; admin-vs-user
  guard.
- **Failure/recovery:** KVStore down (RL fail-closed, scoping fail-open, session
  DB fallback); JWKS fetch failure (stale cache); email provider down (queue/
  retry); migration failure rollback.

## Self-critique loop

**Round 1**
- *Security:* Edge middleware can't verify sessions. **Fix:** presence-only
  redirect + authoritative SSR/`get_principal` check (§Architecture/Frontend).
- *Backend:* per-request `last_seen_at` write kills SQLite. **Fix:** write-behind
  sliding expiry (§Sessions).
- *Architect:* global single-master conflicts with per-user. **Fix:** partial
  unique `(user_id,is_master)` (Property 2).

**Round 2**
- *Security (High):* signup existing-email skips Argon2 → timing enumeration.
  **Fix:** dummy hash equalizes (R1.2, Property 4).
- *Security (High):* login-CSRF (pre-session) unaddressed. **Fix:** `/auth/csrf`
  pre-session double-submit + SameSite (R12.2).
- *Security (High):* account linking could hijack an unverified account.
  **Fix:** verified-or-authenticated linking rules (R4.4, Property 5).
- *Security (High):* cached session for a now-disabled user still authorizes.
  **Fix:** per-request status recheck + write-through eviction (R3.4, Property 3).

**Round 3**
- *IAM:* no MFA/step-up → hijacked session can change password/email. **Fix:**
  step-up/sudo window + AAL + MFA-ready model (R9, Property 6).
- *IAM:* email verification off/underspecified → pre-hijack/spam. **Fix:**
  required-on-hosted verification with hashed single-use tokens (R5).
- *IAM:* credential stuffing beyond rate limit. **Fix:** HIBP breach check +
  CAPTCHA hooks (R13).
- *DB:* malformed `sessions` row; missing token tables. **Fix:** corrected schema
  + verification/reset token tables (§Data Models).

**Round 4**
- *Architect:* single-provider OAuth + no orgs/API-token path limits evolution.
  **Fix:** `OAuthProvider` interface + reserved `authenticators`/`api_tokens`/
  `organizations` + `(org_id,user_id)` scope extension point (§Interfaces/Data).
- *Cookie hardening:* plain cookie name + reversible ip hash. **Fix:** `__Host-`
  prefix + keyed-HMAC `ip_hash` (R12).
- *Product:* no email-change flow. **Fix:** verify-before-switch email change
  with step-up (R7.4).
- *SRE:* secret rotation undefined. **Fix:** dual-key `SESSION_SECRET` window +
  runbook (R16.3, §Ops).

**Round 5**
- *Frontend:* multi-tab logout, CSRF bootstrap, unauth flash unspecified.
  **Fix:** BroadcastChannel logout, `/auth/csrf` bootstrap, SSR authoritative
  check (§Frontend).
- *QA:* concurrency/timing/linking/disabled-cache/rotation untested. **Fix:**
  dedicated security + failure/recovery + perf suites (§Testing).

**Round 6 (final — "millions of users, enterprise, sensitive PII: what still
bites?")** Residuals, explicitly accepted: (a) middleware presence-only check
(authoritative server check compensates); (b) MFA/passkeys/orgs/API-tokens are
readiness-only in P1 (extension points defined, no debt); (c) breached-password/
CAPTCHA/email providers are pluggable interfaces with real adapters shipped
(HIBP breach; SMTP/Resend email; Turnstile CAPTCHA) selected per deploy by
value, gracefully degrading to dev-safe defaults when a provider is
missing/misconfigured — live email/CAPTCHA delivery still needs deploy-time
credentials (HIBP needs none);
(d) write-behind sliding expiry can lose ≤1 window on crash (bounded, safe).
No open critical/high/medium issue remains.
