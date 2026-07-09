# Implementation Plan — P1 Multi-User Foundation

## Overview

Production-grade authentication foundation: email/password + Google OAuth
(provider-abstracted), email verification, hardened password reset, server
sessions with device management + step-up/MFA readiness, capability RBAC,
hardened cookies + abuse controls, and user-scoped data with a data-preserving,
reversible migration. Inherits ADRs/standards from `../phase-2-roadmap.md`;
implement in wave order. Verify each parent task: backend `uv run pytest` (incl.
authz + security + timing suites), frontend `npm run build`/`test`/lint. Never
continue on a failing gate. `SINGLE_USER_MODE=on` keeps local dev identical to
today; hosted sets it off.

## Task Dependency Graph

```json
{
  "waves": [
    { "wave": 1, "tasks": ["0"], "depends_on": [] },
    { "wave": 2, "tasks": ["1"], "depends_on": ["0"] },
    { "wave": 3, "tasks": ["2", "3"], "depends_on": ["1"] },
    { "wave": 4, "tasks": ["4", "5", "6"], "depends_on": ["2", "3"] },
    { "wave": 5, "tasks": ["7"], "depends_on": ["4"] },
    { "wave": 6, "tasks": ["8", "9"], "depends_on": ["4", "5", "6", "7"] },
    { "wave": 7, "tasks": ["10", "11"], "depends_on": ["8", "9"] }
  ]
}
```

## Tasks

- [x] 0. Foundation: Alembic, KVStore, deps, config
  - [x] 0.1 Add `alembic`; baseline `0001` from current implicit schema (empty autogenerate diff verified on a copy)
    - _Requirements: 14.2_
  - [x] 0.2 Deps: `argon2-cffi`, `authlib`/manual OAuth, `itsdangerous`, redis client; pluggable `KVStore` (redis + local adapter) with rate-limit / transient-cookie / session-cache interfaces; pluggable `EmailSender`, `CaptchaVerifier`, `BreachedPasswordCheck` interfaces
    - _Requirements: 5.1, 13.2, 13.3, ADR-6_
  - [x] 0.3 Config/env surface with validation + safe defaults (session secret + prev for rotation, Google creds, redirect uri, owner, `SINGLE_USER_MODE`, `EMAIL_VERIFICATION`, TTLs, `STEP_UP_WINDOW`, `IP_HASH_SECRET`, Argon2 params, cookie settings)
    - _Requirements: 14.3, 16.3, 17.2_

- [x] 1. Data model & migrations (auth tables + scoping)
  - [x] 1.1 ORM: `User` (+mfa_enrolled), `OAuthIdentity`, `Session` (+aal/step_up_at/remember_me/device_label/ip_hash), `AuditLog`, `EmailVerificationToken`, `PasswordResetToken`
    - _Requirements: 7.1, 3.5, 5.1, 6.1, 9.2, 16.2_
  - [x] 1.2 Migrations `0002` (auth tables + the `kv` table for the DB-backed KVStore fallback — ADR-6), `0003` (nullable `user_id` + indexes)
    - _Requirements: 10.1_
  - [x] 1.3 Migration `0004` backfill owner (admin/active/verified) + assign owned rows + api_keys (idempotent, chunked)
    - _Requirements: 10.5, 14.1_
  - [x] 1.4 Migration `0005` enforce: NOT NULL `user_id`; partial unique `(user_id, is_master)`; dedupe `(user_id, job_id, resume_id)`; verify reversible down on a copy
    - _Requirements: 10.4, 14.2_

- [x] 2. Core auth services (passwords, sessions, csrf, ratelimit, audit)
  - [x] 2.1 Passwords: Argon2id hash/verify + policy (len/denylist/strength) + breach hook + **dummy-hash timing equalization**; length cap
    - _Requirements: 1.2, 1.3, 1.5, 2.2, 13.3_
  - [x] 2.2 Sessions: create/rotate/revoke/resolve; `sha256(token)`; sliding+absolute (write-behind); remember-me cap; **write-through cache eviction**; per-request status+revoked recheck; keyed `ip_hash`; device label; reaper
    - _Requirements: 2.1, 3.1, 3.3, 3.4, 3.6, 12.4, 12.5, 17.1, 17.3_
  - [x] 2.3 Principal middleware + `get_principal`/`require_capability`/`require_step_up` deps; `__Host-` cookies; CSRF (incl. logout) + `GET /auth/csrf` pre-session token; security headers + CSP
    - _Requirements: 8.1, 8.2, 9.1, 12.1, 12.2, 12.3_
  - [x] 2.4 Rate limiter + lockout + CAPTCHA hook (per-ip/account) via KVStore; audit writer with sanitized meta
    - _Requirements: 13.1, 13.2, 13.4, 13.5, 16.2_

- [x] 3. User-scoping enforcement (repository layer)
  - [x] 3.1 Mandatory `user_id` on every owned `db` method; `Repo.scoped(stmt, model, user_id)` (centralized scope key `Repo.SCOPE_KEYS` for future org scope); CI guard (`app/scripts/check_scoping.py`, AST-based) + unit test forbidding unscoped owned queries
    - _Requirements: 10.2, 10.6, 10.8_
  - [x] 3.2 Thread the effective `user_id` (`get_effective_user_id` dep: principal hosted, bootstrap owner locally) through resumes/jobs/applications/enrichment/resume_wizard/config-api-keys/health; foreign id → 404
    - _Requirements: 10.2, 10.3, 10.7_
  - [x] 3.3 Per-user single-master lock (replace global lock with per-user `_master_locks`); per-user api-key resolution in `llm.py` (via request-scoped `ContextVar` + explicit `user_id`)
    - _Requirements: 10.4, 10.6_

- [x] 4. Email/password endpoints + sessions API
  - [x] 4.1 `signup`, `login` (remember-me, pre-session CSRF, uniform errors, fixation rotation), `logout` (+CSRF), `logout-all` (+step-up), `GET /auth/session`
    - _Requirements: 1.*, 2.*, 3.1, 3.2_
  - [x] 4.2 `GET/PATCH /users/me` (role/status ignored), device mgmt `GET /users/me/sessions` + `DELETE …/{id}`
    - _Requirements: 7.2, 7.5, 3.5, 8.4_

- [x] 5. Email verification & password reset
  - [x] 5.1 Verification: `verify/request` (RL, invalidate prior) + `verify/confirm` (single-use, state transition, uniform); gate sensitive actions when unverified
    - _Requirements: 5.1, 5.2, 5.3, 5.5, 5.6_
  - [x] 5.2 Reset: `password/forgot` (uniform) + `password/reset` (single-use, revoke-all-sessions, OAuth-only set-password, fresh session)
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

- [x] 6. Step-up (sudo) + password/email change
  - [x] 6.1 `POST /auth/step-up` (re-verify password, bump `step_up_at`/aal, RL, audit); `require_step_up` gate
    - _Requirements: 9.1, 9.3_
  - [x] 6.2 `password/change` (step-up + current verify + policy/breach + revoke-other-sessions); `POST /users/me/email` (step-up + verify-new-before-switch + uniqueness)
    - _Requirements: 7.3, 7.4_

- [x] 7. Google OAuth (provider-abstracted)
  - [x] 7.1 `OAuthProvider` interface + Google impl + provider registry/allow-list; `/auth/oauth/{provider}/start` (state+nonce+PKCE, signed transient cookies, `next`)
    - _Requirements: 4.1, 4.7_
  - [x] 7.2 `/auth/oauth/{provider}/callback`: state check, exchange, id_token verify (JWKS rotation + clock skew), safe link/create rules, session issue, `next` validation
    - _Requirements: 4.2, 4.3, 4.4, 4.5, 4.6_

- [x] 8. Frontend session, guards & wiring
  - [x] 8.1 Real `SessionProvider` (hydrate `/auth/session`); SSR authoritative check in `(app)`/admin layout (no unauth flash); `SINGLE_USER_MODE` auto-login
    - _Requirements: 11.1, 14.3, 15.5_
  - [x] 8.2 `middleware.ts` presence-guard + `next`; `apiFetch` CSRF injection + 401 interceptor + multi-tab logout (BroadcastChannel)
    - _Requirements: 11.1, 11.2, 11.3, 11.4_
  - [x] 8.3 Wire login/signup (validation, uniform errors, `next`, Google, autocomplete, reveal, caps-lock); verify-email (banner+resend+landing); forgot/reset; OAuth-failure; account-linking; step-up modal; Settings→Account (change password/email, device list+revoke, log-out-everywhere)
    - _Requirements: 4.*, 5.*, 6.*, 7.*, 9.1, 15.1, 15.2, 15.3, 15.4_

- [x] 9. Abuse, headers & observability
  - [x] 9.1 CAPTCHA + breached-password providers wired (fail-open logged); lockout/backoff UX (retry-after, no enumeration)
    - _Requirements: 13.1, 13.2, 13.3_
  - [x] 9.2 Security headers/CSP verified (no console violations); structured logs + auth metrics + audit events; secret-rotation dual-key path + runbook
    - _Requirements: 12.3, 16.1, 16.2, 16.3, 16.4_

- [x] 10. Verification, security & migration sign-off
  - [x] 10.1 Authz/ownership matrix (anon/user/admin/disabled/pending × owned/admin; cross-user→404; disabled cached-session rejected)
    - _Requirements: 10.2, 10.3, 8.2, 3.4_
  - [x] 10.2 Security suite: CSRF (incl. login-CSRF, logout), fixation, enumeration **timing** (incl. signup dummy-hash), open-redirect, OAuth state/nonce/replay, linking-hijack, step-up bypass, breached-password, `__Host-`/cookie attributes, IDOR
    - _Requirements: 1.2, 2.2, 4.4, 6.5, 9.1, 12.1, 12.2, 13.4_
  - [x] 10.3 Migration up/down on a seeded copy (data preserved, reversible) + backup runbook
    - _Requirements: 14.1, 14.2_

- [x] 11. Perf, a11y, mobile & E2E
  - [x] 11.1 Perf: login + session-resolve concurrency, cache-hit path, lockout burst, Argon2 budget; failure/recovery (KVStore down, JWKS fail, email provider down)
    - _Requirements: 17.1, 17.2, 13.5_
  - [x] 11.2 A11y + mobile on all auth forms + step-up modal; E2E (signup→verify→home; login+remember-me→next; logout+multi-tab; Google gated; forgot→reset; step-up; expiry redirect; device revoke; admin-vs-user guard)
    - _Requirements: 15.*, 11.*, 4.*, 5.*, 6.*_

## Wave 8 — Production completion pass

Honest record of the post-implementation production-readiness remediation
(Packages A–E) that closed the deployment audit's findings. These are additive
to Waves 0–7 (whose original checkboxes are unchanged above); each is marked
complete only because the code, wiring, and tests are in place and the full
gates (backend `uv run pytest` incl. live PG+Redis via Docker, `check_scoping`,
frontend `build`/`lint`/`test`, deterministic Playwright) pass.

- [x] 12. Package A — live Postgres runtime wiring (audit C-1)
  - [x] 12.1 Runtime consumes `settings.effective_database_url` and selects the
        engine by dialect in `app/db_engine.py`: SQLite (aiosqlite/sqlite +
        PRAGMAs) vs Postgres (asyncpg async + psycopg v3 sync), normalizing bare
        `postgresql://`/`postgres://`/`psycopg2`; pooling from
        `db_pool_size`/`db_use_pooler` (NullPool + prepared-statement caches
        disabled behind a transaction pooler, QueuePool + pre-ping for direct).
        `init_models_sync` is a SQLite-only no-op so `create_all`/`ALTER`/`PRAGMA`
        never touch Postgres (Alembic owns hosted schema). Hosted fails fast on an
        unreachable DB.
    - _Requirements: 14.2, 17.1; audit C-1; design §Runtime DB wiring_
  - [x] 12.2 Live-Postgres validation test (`tests/integration/test_postgres_backend.py`):
        Alembic `upgrade head` → scoped CRUD round-trip (asyncpg + encrypted
        `api_keys` psycopg hot path + cross-user isolation) → `downgrade base`,
        gated on `TEST_DATABASE_URL`/Docker with a clean skip otherwise.
    - _Requirements: 14.1, 14.2, 10.2; Property 1, Property 7_

- [x] 13. Package B — real provider adapters + graceful degradation (audit)
  - [x] 13.1 Real per-deploy adapters selected by value in `app/auth/runtime.py`:
        `EMAIL_PROVIDER=smtp|resend`, `CAPTCHA_PROVIDER=turnstile`,
        `BREACH_PROVIDER=hibp` (k-anonymity range API, sends only the 5-char
        SHA-1 prefix, needs no credentials).
    - _Requirements: 13.1, 13.2, 13.3, 5.1, ADR-14_
  - [x] 13.2 Factories NEVER raise: a recognized-but-uncredentialed or
        unrecognized provider degrades to the dev-safe default (log-only email /
        fail-open captcha+breach) with one logged warning, so the app always
        boots (live delivery still needs deploy-time credentials).
    - _Requirements: 13.3, 5.1; §Reliability, §Deployment_

- [x] 14. Package C — reaper wiring + authenticated internal endpoints (audit)
  - [x] 14.1 `POST /api/v1/internal/run-jobs` invokes the single-flighted
        `SessionService.reap()` (KVStore lock → concurrent calls return all-zero);
        `internal` mode runs `app/scheduler.py:reaper_loop` in the app lifespan on
        `REAPER_INTERVAL_SECONDS`, cancelled cleanly on shutdown; `external_cron`
        (free default) starts no loop.
    - _Requirements: 3.6, 17.3, ADR-15_
  - [x] 14.2 `GET /api/v1/internal/metrics` exposes `AuthMetrics.snapshot()`
        (login/signup/verification/reset/oauth/step-up counters + session-cache
        hit ratio). Both `/internal/*` endpoints are guarded by the
        `INTERNAL_JOB_TOKEN` shared secret (`X-Internal-Job-Token`, constant-time
        compare): missing → 401, wrong → 403, unconfigured → reject all; no
        session ⇒ outside the per-session CSRF check.
    - _Requirements: 16.1, 16.2; audit §internal endpoints_

- [x] 15. Package D — live Redis contract + strengthened suites (audit)
  - [x] 15.1 Live-Redis contract test (`tests/integration/test_redis_kvstore.py`):
        `RedisKVStore` get/set/delete, atomic `incr` with TTL-on-create, and the
        TTL-bound single-flight `lock` against a running server; gated on a
        reachable Redis/Docker with a clean skip otherwise.
    - _Requirements: 13.2, 3.6, ADR-6_
  - [x] 15.2 Strengthened enumeration-timing test (statistical, incl. signup
        dummy-hash) and repaired 3 broken gated E2E specs so the deterministic
        Playwright run stays green.
    - _Requirements: 1.2, 2.2, 13.4; Property 4_

- [x] 16. Package E — integration trace, E2E bootstrap, spec reconciliation (audit §7/§8/§10/§12)
  - [x] 16.1 Frontend↔backend integration trace: verified all 19 `lib/api/auth.ts`
        methods map to current `routers/{auth,users}.py` routes (path + method +
        camelCase `SafeUser`/session shapes) with zero mismatches — no drift, no
        outdated/nonexistent endpoint calls.
    - _Requirements: 11.1, 7.5; audit §7/§8_
  - [x] 16.2 E2E auth-bootstrap scaffolding: Playwright `globalSetup`
        (`e2e/auth.setup.ts`) performs a real signup/login through the backend to
        seed the session `storageState` and a SECOND login to seed a 2nd device
        for the revoke test; the gated `describe` wires it via `test.use({
        storageState })`. No-op unless `RUN_AUTH_E2E=1`, so the deterministic
        7-test run is untouched. Exact run command + HTTPS/`__Host-` caveat
        documented in the spec header.
    - _Requirements: 11.1, 11.2, 3.5, 4.*, 5.*, 6.*, 9.1, 15.*_
  - [x] 16.3 Spec reconciliation: design.md ↔ requirements.md ↔ tasks.md ↔
        implementation agree — URL-driven engine selection + PG drivers/pooling,
        graceful-degradation provider contract, reaper wiring + internal
        endpoints, metrics endpoint, and live PG/Redis validation are all
        reflected; no remaining contradictions.
    - _Requirements: 14.2, 16.1; audit §10/§12_

## Notes
- Server authz + `user_id` scoping is the boundary; middleware is UX only.
- No owned-resource endpoint ships without scoping + an authz test.
- Sensitive actions require step-up; sessions revoke-on-change with cache eviction.
- MFA/passkeys, orgs, API tokens, extra OAuth providers are readiness-only in P1
  (interfaces/columns reserved; no debt).
- Wave 8 is the production completion pass (Packages A–E) closing the deployment
  audit; it is additive and does not change the Wave 0–7 scope above.
