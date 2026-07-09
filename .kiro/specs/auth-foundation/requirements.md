# Requirements Document

_P1 Multi-User Foundation — Authentication, Sessions, RBAC, User-Scoped Data._

## Introduction

FitWright is currently a single-user, local application: no `users` table, no
`user_id` on any row, no authentication, and a hard-coded "local owner" session.
This spec turns FitWright into a secure, enterprise-ready multi-user product:
email/password + Google sign-in, email verification, password reset, server-
managed sessions with device management, RBAC with a forward-compatible
capability model, MFA/WebAuthn/step-up readiness, hardened cookies and abuse
controls, and — most importantly — **user-scoped data** so every resume, job,
application, improvement, and API key belongs to exactly one user and is
invisible to others.

Authentication is the foundation every later feature depends on, so it is
designed to be secure, scalable, maintainable, extensible, and operable. It
inherits every ADR/standard in `../phase-2-roadmap.md`. Existing data is
preserved: on first migration all current rows are assigned to a bootstrap
"owner" user.

### Goals
- Secure signup/login/logout with server-side sessions (httpOnly `__Host-` cookies).
- Email verification (required on hosted) and hardened password reset.
- Google OAuth (auth-code + PKCE) via a provider-abstracted flow.
- RBAC via a capability model (`user`/`admin` today; forward-compatible).
- MFA/WebAuthn/step-up **readiness** (data + session model leave room; step-up
  "sudo" gate for sensitive actions).
- Device/session management (list, revoke, revoke-all, remember-me).
- Abuse resistance: rate limits, lockout, breached-password + CAPTCHA hooks.
- Every owned resource strictly user-scoped; cross-user access impossible.
- Zero data loss for the existing local user; safe, reversible migration.

### Non-goals (deferred, with extension points reserved)
- Team/organization multi-tenancy, seats, invitations (scoping abstraction is
  built to extend to `(org_id, user_id)` later).
- SSO/SAML, additional OAuth providers (provider interface is defined now).
- **Building** MFA/passkeys (only readiness in P1; enforced flows are P-later).
- API tokens / service accounts (bearer-token path reserved, not built).
- Concrete email/CAPTCHA/breached-password providers (pluggable interfaces here).
- Avatar upload storage (P3); P1 ships the profile shell only.

## Glossary
- **Session**: a server-side record identified by an opaque cookie token; the DB
  is the source of truth, the KVStore is a cache.
- **Principal**: resolved `{user_id, role, capabilities, session_id, aal,
  csrf_secret, status}` for a request.
- **AAL (assurance level)**: `aal1` = password/OAuth; `aal2` = MFA-verified
  (reserved). Sensitive actions may require a minimum AAL or recent step-up.
- **Step-up ("sudo")**: a short-lived elevated window after re-authenticating,
  required for sensitive actions (password/email change, session revoke-all,
  future account deletion, admin-destructive).
- **Owner (bootstrap) user**: the single user existing data migrates to.
- **Owned resource**: any row with a `user_id`.
- **Capability**: a server-checked permission (`admin.read`, `admin.manage`,
  extension point for future roles).

---

## Requirements

### Requirement 1: Email/password signup
**User Story:** As a visitor, I want to create an account with my email and
password, so that my resumes and applications are private to me.

#### Acceptance Criteria
1. WHEN a visitor submits signup with a valid email, a policy-compliant
   password, and a name, THE SYSTEM SHALL create a `users` row (role=`user`,
   email normalized as NFKC+lowercase+trim, Argon2id hash) and start a session.
2. WHEN the email already exists, THE SYSTEM SHALL return a generic
   `email_unavailable` result with a response shape AND **timing** matching the
   success path — including performing a **dummy Argon2 hash** so the existing-
   email branch is not measurably faster (no enumeration).
3. WHEN the password fails policy (≥12 chars, not in a common-password denylist,
   passes a zxcvbn-style strength gate, and — when the breached-password check
   is enabled — is not found via HIBP k-anonymity range), THE SYSTEM SHALL
   return `weak_password`/`breached_password` with the unmet rule and create no
   user.
3a. THE password policy SHALL accept passphrases (no forced composition rules;
   length + strength + breach check instead), and SHALL cap length (e.g. 128) to
   bound Argon2 cost.
4. WHEN signup succeeds, THE SYSTEM SHALL set the httpOnly `__Host-` session
   cookie + the CSRF cookie and return the safe profile (id, name, email, role,
   status, avatarUrl, emailVerified, aal).
5. THE SYSTEM SHALL NEVER store or log the plaintext password or full hash.
6. WHEN email verification is enabled (default ON for hosted, OFF for
   `SINGLE_USER_MODE`), THE SYSTEM SHALL create the user `pending_verification`,
   issue a verification token, and gate **sensitive** actions until verified
   while allowing basic use.

### Requirement 2: Email/password login
**User Story:** As a returning user, I want to log in, so that I can access my
data.

#### Acceptance Criteria
1. WHEN a user submits correct credentials, THE SYSTEM SHALL create a fresh
   session (new id — fixation defense) and set the session + CSRF cookies.
2. WHEN credentials are wrong OR the email is unknown, THE SYSTEM SHALL return a
   single generic `invalid_credentials` with constant-time verify and uniform
   timing (a dummy hash runs for unknown emails); no enumeration.
3. WHEN failed attempts for an account or IP exceed thresholds, THE SYSTEM SHALL
   apply exponential backoff + temporary lockout, require a CAPTCHA challenge
   (when configured) beyond a soft threshold, and audit `auth.login_failed`.
4. WHEN a `disabled` user (or a user whose status is not `active`) attempts
   login, THE SYSTEM SHALL reject (`account_disabled`) and create no session.
5. Login SHALL be protected against **login-CSRF** via a pre-session CSRF token
   (double-submit token issued to the auth page) + SameSite=Lax.
6. WHEN "remember me" is chosen, THE SYSTEM SHALL use the longer absolute session
   cap; otherwise the shorter default.

### Requirement 3: Logout, sessions & device management
**User Story:** As a user, I want to control my sessions across devices, so my
account stays secure.

#### Acceptance Criteria
1. WHEN a user logs out, THE SYSTEM SHALL revoke the current session (DB
   `revoked_at` + **evict the KVStore cache entry**) and clear the session + CSRF
   cookies; logout SHALL require the CSRF token.
2. WHEN a user chooses "log out everywhere", THE SYSTEM SHALL revoke all their
   sessions (requires step-up) and evict all their cache entries.
3. WHEN a session exceeds its absolute lifetime OR idle timeout, THE SYSTEM SHALL
   treat it as expired (401) and require re-login; sliding activity extends
   `expires_at` up to the absolute cap (via write-behind).
4. WHEN a session is revoked or the user is disabled, THE SYSTEM SHALL reject any
   further use **within one request cycle** — resolution re-checks `revoked_at`
   AND `user.status == active` and honors cache eviction.
5. THE SYSTEM SHALL provide session/device management: list active sessions
   (device label, last-active, current-device flag, ip region), revoke one,
   revoke others; the list SHALL never expose the raw token.
6. THE SYSTEM MAY enforce a configurable per-user concurrent-session cap
   (default unlimited); when exceeded, the oldest is revoked (documented).

### Requirement 4: Google OAuth sign-in (provider-abstracted)
**User Story:** As a user, I want to sign in with Google, so I don't manage
another password.

#### Acceptance Criteria
1. WHEN a user starts OAuth, THE SYSTEM SHALL redirect to the provider with
   `response_type=code`, `state`, `nonce`, PKCE `code_challenge`, storing
   `state`/`nonce`/`code_verifier` (+ optional `next`) in short-lived **signed
   httpOnly** cookies, and SHALL implement this via a provider-agnostic
   `OAuthProvider` interface (Google now; GitHub/Microsoft later without API
   change).
2. WHEN the provider redirects back, THE SYSTEM SHALL verify `state`
   (constant-time), exchange the code with `code_verifier`, and fully verify the
   `id_token` (signature via cached JWKS with rotation handling, `iss`, `aud`,
   `exp`/`iat` with bounded clock skew, `nonce`) before trusting any claim.
3. WHEN the verified provider email matches no user, THE SYSTEM SHALL create a
   `user` with a verified email and link the identity.
4. WHEN an existing account has the same email, THE SYSTEM SHALL link only if the
   provider email is verified AND (the existing account has no password OR its
   email is already verified OR the user is currently authenticated and links
   from Settings); otherwise it SHALL require the user to log in first to link
   — never silently creating a duplicate or hijacking an unverified account.
5. THE SYSTEM SHALL never expose provider access/refresh tokens to the browser
   and SHALL discard them after id_token verification (no provider API calls
   beyond sign-in in P1).
6. WHEN `state`/`nonce` is missing/mismatched, PKCE fails, or id_token
   verification fails, THE SYSTEM SHALL abort with `oauth_failed`, clear
   transient cookies, and create no session.
7. Redirect URIs SHALL be exact-match allow-listed per environment.

### Requirement 5: Email verification
**User Story:** As the product owner, I want email ownership verified, so
accounts can't be pre-hijacked and abuse/spam is reduced.

#### Acceptance Criteria
1. THE SYSTEM SHALL issue a **hashed, single-use, TTL-bound** verification token
   (stored as `sha256`), delivered via a pluggable email interface.
2. WHEN a valid token is presented, THE SYSTEM SHALL set `email_verified_at`,
   move `pending_verification`→`active`, invalidate the token, and audit it.
3. Resend SHALL be rate-limited (per account + IP) and SHALL invalidate prior
   unused tokens for that user.
4. OAuth sign-ups with a provider-verified email SHALL be created already
   verified (no email step).
5. Verification responses SHALL be uniform (no enumeration via resend/verify).
6. WHEN verification is required and unmet, THE SYSTEM SHALL gate sensitive
   actions (defined in Design) with a clear prompt, not block basic use.

### Requirement 6: Password reset & recovery
**User Story:** As a user who forgot my password, I want to reset it safely, so I
regain access without compromising security.

#### Acceptance Criteria
1. WHEN a user requests a reset, THE SYSTEM SHALL always respond uniformly
   (no enumeration), and — only if the email exists — issue a hashed, single-use,
   short-TTL reset token, invalidating prior unused reset tokens.
2. WHEN a valid reset token + new (policy-compliant) password is submitted, THE
   SYSTEM SHALL set the new hash, **revoke ALL of that user's sessions**,
   invalidate the token, audit `password_reset`, and start a fresh session.
3. WHEN the account is OAuth-only (no password), the reset flow SHALL allow the
   user to **set** a password (linking password auth) after token verification.
4. Reset requests and token verification SHALL be rate-limited; tokens SHALL be
   time-constant compared.
5. THE SYSTEM SHALL never reveal whether an email is registered at any step.

### Requirement 7: User model, profile & email change
**User Story:** As a user, I want to manage my profile and account securely, so I
control my identity.

#### Acceptance Criteria
1. THE SYSTEM SHALL persist users with: id (uuid), email (unique, normalized),
   name, password_hash (nullable for OAuth-only), role, status
   (`active`/`disabled`/`pending_verification`), avatar_url?, email_verified_at?,
   mfa_enrolled (bool, default false, reserved), created_at, updated_at.
2. WHEN a user updates their name, THE SYSTEM SHALL persist it and reflect it in
   the profile; `PATCH /users/me` SHALL ignore/refuse `role` and `status`.
3. WHEN a user changes their password (providing the current one, **within a
   step-up window**), THE SYSTEM SHALL verify current (constant-time), enforce
   policy + breach check on the new one, rehash, and revoke all **other**
   sessions.
4. WHEN a user changes their email, THE SYSTEM SHALL require step-up, verify the
   **new** email (send verification, keep old until verified), enforce
   uniqueness, then switch and audit — never switching to an unverified address.
5. THE SYSTEM SHALL expose only the `SafeUser` shape (never hash, tokens,
   internal flags beyond role/status/emailVerified/aal).

### Requirement 8: RBAC & capability model
**User Story:** As the product owner, I want privileged actions protected and the
model extensible, so access stays correct as the product grows.

#### Acceptance Criteria
1. THE SYSTEM SHALL attach a role and derived **capabilities** to the principal;
   authorization checks SHALL be capability-based (extension point for future
   `support`/`superadmin`/org roles) with role→capability mapping in one place.
2. WHEN a non-admin calls an admin-guarded API, THE SYSTEM SHALL return 403 and
   audit `authz.denied`; anon → 401.
3. WHEN a role changes, THE SYSTEM SHALL revoke the affected user's sessions
   (force fresh authz) and audit `role.changed`.
4. A user SHALL NOT change their own role/status via any non-admin endpoint.
5. The bootstrap owner SHALL be role=`admin`.

### Requirement 9: Step-up ("sudo") & MFA readiness
**User Story:** As a security-conscious user, I want sensitive actions to require
recent re-authentication and be MFA-ready, so a hijacked session has limited
blast radius.

#### Acceptance Criteria
1. THE session SHALL carry an `aal` and a `step_up_at` timestamp; sensitive
   actions (password/email change, revoke-all, future delete-account, admin-
   destructive) SHALL require a recent step-up (re-enter password / future MFA)
   within a configurable window, else 401 `step_up_required`.
2. THE data/session model SHALL leave room for MFA/WebAuthn (mfa_enrolled flag,
   `aal2`, a future `authenticators` table) without breaking changes.
3. Step-up SHALL be rate-limited and audited (`auth.step_up`).

### Requirement 10: User-scoped data (core isolation guarantee)
**User Story:** As a user, I want to see only my own data, so it stays private.

#### Acceptance Criteria
1. THE SYSTEM SHALL add `user_id` to `resumes`, `jobs`, `improvements`,
   `applications`, and make `api_keys` per-user (PK `(user_id, provider)`).
2. WHEN any owned-resource endpoint runs, THE SYSTEM SHALL filter strictly by the
   authenticated `user_id`; cross-user reads/writes SHALL be impossible.
3. A request for a resource id owned by another user SHALL return 404 (no
   existence disclosure).
4. THE single-master invariant SHALL be **per user** (partial unique
   `(user_id, is_master)`).
5. THE migration SHALL assign every existing row to the bootstrap owner with no
   data loss.
6. Provider API keys SHALL be per-user; one user's key SHALL never serve
   another's LLM calls.
7. PDF/print and all generation endpoints SHALL be scoped to the owning user.
8. THE scoping abstraction SHALL be built so a future `(org_id, user_id)` scope
   is a localized change (extension point).

### Requirement 11: Route guards & protected navigation (frontend)
**User Story:** As a user, I want protected pages to require login and to be sent
to login on expiry, so the app behaves predictably.

#### Acceptance Criteria
1. WHEN an unauthenticated request hits an `(app)` route, `middleware.ts` SHALL
   redirect to `/login?next=<path>` before render (presence-only fast path).
2. WHEN a non-admin hits `admin/*`, middleware SHALL redirect and the server
   SHALL independently 403 admin APIs (authoritative).
3. WHEN a client API call returns 401, THE FRONTEND SHALL clear session state and
   route to `/login?next=<path>`; a 401 across tabs SHALL propagate (multi-tab
   logout) via a broadcast/storage event.
4. WHEN login succeeds with `next`, THE SYSTEM SHALL redirect only to a validated
   same-origin app path (no open redirect: must start with a single `/`).
5. Client guards are UX only; server authz is the boundary.

### Requirement 12: Cookies, headers & transport hardening
**User Story:** As a security engineer, I want cookies and headers hardened, so
common web attacks are mitigated.

#### Acceptance Criteria
1. THE session cookie SHALL use the `__Host-` prefix (Secure, Path=/, no Domain),
   `HttpOnly`, `SameSite=Lax`. The CSRF cookie SHALL be readable by JS,
   `SameSite=Lax`, and derived per-session.
2. THE SYSTEM SHALL enforce CSRF (double-submit `X-CSRF-Token`) on ALL state-
   changing requests including logout, and a pre-session token for login/signup.
3. THE SYSTEM SHALL set HSTS, X-Content-Type-Options, Referrer-Policy, and a
   strict CSP compatible with Next.js, `frame-ancestors 'none'`.
4. THE SYSTEM SHALL store only `sha256(token)` for sessions and hashed tokens for
   verification/reset; raw tokens exist only in the cookie/email link.
5. `ip_hash` SHALL be a **keyed HMAC** (salted) so IPs are not brute-forceable.
6. CORS SHALL be same-origin by default; any cross-origin allowance is explicit
   and credential-aware.

### Requirement 13: Abuse resistance
**User Story:** As the product owner, I want auth abuse contained, so attackers
can't stuff credentials, enumerate, or DoS the auth surface.

#### Acceptance Criteria
1. THE SYSTEM SHALL rate-limit signup/login/oauth/verify/reset/step-up per IP and
   per account with lockout + exponential backoff (KVStore-backed).
2. THE SYSTEM SHALL support a pluggable CAPTCHA/Turnstile challenge after a soft
   failure threshold. A real Cloudflare Turnstile verifier ships
   (`CAPTCHA_PROVIDER=turnstile`, `CAPTCHA_SECRET`); a missing secret or
   provider error SHALL fail open (allow) with a logged warning, never blocking
   auth or crashing construction.
3. THE SYSTEM SHALL support a pluggable breached-password check (HIBP
   k-anonymity) at signup/change (fail-open if the provider is down, logged). A
   real HIBP range-API adapter ships (`BREACH_PROVIDER=hibp`, no credentials
   required) that transmits only the 5-char SHA-1 prefix — never the password or
   full hash.
6. THE SYSTEM SHALL ship real transactional-email adapters (`EMAIL_PROVIDER=smtp`
   via stdlib SMTP/TLS, or `resend` via HTTP API); live delivery requires
   deploy-time credentials (`EMAIL_FROM` + SMTP host/creds, or `EMAIL_API_KEY`).
   A missing-credential or unrecognized provider SHALL gracefully degrade to the
   dev logging sender with a logged warning, never crashing construction.
4. All enumeration vectors (signup, login, forgot, verify, resend) SHALL be
   uniform in shape and timing.
5. KVStore outage SHALL fail **closed** for auth rate limits (deny with
   Retry-After) and **open** for read scoping (DB is source of truth).

### Requirement 14: Backward compatibility & migration safety
**User Story:** As the current local user, I want my data to remain mine after the
upgrade.

#### Acceptance Criteria
1. WHEN the app first boots post-migration, THE SYSTEM SHALL create the bootstrap
   owner (role=admin, active, verified) and assign all pre-existing rows +
   api_keys to it.
2. THE SYSTEM SHALL provide reversible Alembic down-migrations verified on a copy.
3. WHEN `SINGLE_USER_MODE` is on (local), THE SYSTEM SHALL auto-login the owner
   and skip verification; the flag is OFF for any hosted deployment.
4. Existing owned-resource API request/response shapes SHALL remain unchanged
   except for the added auth requirement (no breaking field changes).

### Requirement 15: Error, loading, empty & edge-case UX
**User Story:** As a user, I want clear feedback and no lost input when auth
fails or my session expires, so signing in never feels fragile.

#### Acceptance Criteria
1. Auth forms SHALL show inline validation, disabled-submit while pending, a
   single non-leaky error banner, password-manager `autocomplete` attributes, a
   password reveal toggle, and a caps-lock hint.
2. WHEN the network fails mid-auth, THE UI SHALL allow retry without losing
   non-secret input (password fields cleared on failure).
3. Expired-session mid-action SHALL surface a "session expired" prompt returning
   the user to where they were after re-login.
4. THE UI SHALL provide flows for verify-email (pending banner + resend + verify
   landing), forgot/reset password, OAuth failure (retry + password fallback),
   account linking, and step-up (re-enter password modal).
5. First-run: hosted with no users → signup; local `SINGLE_USER_MODE` → skip auth.

### Requirement 16: Observability, audit & operations
**User Story:** As an SRE, I want auth observable, auditable, and operable, so I
can run and secure it in production.

#### Acceptance Criteria
1. THE SYSTEM SHALL emit structured logs (request_id, user_id when known) with no
   secrets/PII beyond user_id, and metrics: login success/fail, signups,
   verification send/verify, reset requests, oauth outcomes (by reason),
   lockouts, active sessions, session-cache hit ratio, step-up challenges.
5. THE SYSTEM SHALL expose the metrics snapshot over an **authenticated** internal
   endpoint (`GET /api/v1/internal/metrics`) guarded by the `INTERNAL_JOB_TOKEN`
   shared secret (constant-time compared); auth metrics SHALL NOT be reachable
   unauthenticated, and when no token is configured the endpoint SHALL reject
   every caller.
2. THE `audit_log` SHALL capture: signup, login, login_failed, logout,
   logout_all, password_changed, password_reset, email_verified, email_changed,
   role_changed, user_disabled, oauth_link, session_revoked, step_up — with
   actor, target, ip_hash, request_id, ts.
3. Secret rotation SHALL be supported: `SESSION_SECRET` (and CSRF derivation) via
   a dual-key verify window; provider client secrets rotatable; documented
   runbook.
4. Alerts: login-fail/lockout spikes, oauth-failure spikes, 5xx on auth,
   migration failure, session-cache unavailability.

### Requirement 17: Performance & scalability
**User Story:** As the product owner, I want auth fast at scale, so growth
doesn't degrade sign-in.

#### Acceptance Criteria
1. Session resolution SHALL be O(1) via a short-TTL KVStore cache with DB
   fallback; sliding expiry SHALL be write-behind (no DB write per request).
2. Argon2 params SHALL target ~50–100ms and be tunable; login p95 < 300ms
   excluding the deliberate Argon2 budget.
3. Sessions/users/tokens SHALL be indexed; the session reaper SHALL batch-delete
   expired/old-revoked rows; audit partition/rotate monthly at volume. THE reaper
   SHALL run under `SCHEDULER_MODE` (ADR-15): `external_cron` (free) via the
   authenticated `POST /api/v1/internal/run-jobs` endpoint, or `internal`
   (premium) via an in-process loop started in the app lifespan on
   `REAPER_INTERVAL_SECONDS` and cancelled cleanly on shutdown; both paths are
   single-flighted via the KVStore lock so overlapping runs never double-delete.
4. THE architecture SHALL be stateless per app worker (session state in DB +
   KVStore) so it scales horizontally behind a load balancer.

---

## Traceability
R1–R2 → Design §Auth flows, §Passwords, §API. R3 → §Sessions, §Device mgmt.
R4 → §OAuth, §Provider abstraction. R5 → §Verification. R6 → §Reset.
R7 → §User model, §Email change. R8 → §RBAC/capabilities. R9 → §Step-up/MFA.
R10 → §Scoping, §Migration. R11 → §Frontend guards. R12 → §Cookies/headers.
R13 → §Abuse. R14 → §Migration/compat. R15 → §Frontend UX. R16 → §Observability
& Ops. R17 → §Performance. All → `tasks.md`.
