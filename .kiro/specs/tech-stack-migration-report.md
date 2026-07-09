# FitWright — Technology Stack & Migration Report

Status: **Reference document** (planning-time; no implementation yet)
Owner: Obaidullah Zeeshan
Companion to: `phase-2-roadmap.md` (ADRs) · the four Phase-2 specs · the completed `ui-revamp/`

> **Purpose.** A single map of *what technology is used today*, *what is planned*,
> *which migrations will occur and why*, and *whether each choice is justified* — so
> the whole architecture is understood before coding begins. This report **decides
> nothing new**; it consolidates decisions already made in `phase-2-roadmap.md`
> (ADRs) and the specs. Where they disagree, the roadmap ADR wins.

## How to read this

- **Current** = present in the repo today (`apps/frontend/package.json`,
  `apps/backend/pyproject.toml`, existing code).
- **Planned** = specified in a Phase-2 spec / ADR, not yet built.
- **Spec** = the owning spec that introduces or wires the technology. Legend:

| Tag | Spec folder | Phase | Scope |
|---|---|---|---|
| **P1** | `auth-foundation/` | Phase 2 | Auth, sessions, RBAC, user-scoping, migrations |
| **P2** | `admin/` | Phase 2 | Admin APIs, metrics rollup, settings store, scheduled jobs |
| **P3** | `productivity/` | Phase 2 | Versions, notifications, search, JD-URL, reminders, avatars |
| **P4** | `resilience/` | Phase 2 | Streaming AI, offline, autosave, conflict, recovery |
| **UI** | `ui-revamp/` | Phase 1 (**done**) | Frontend revamp — *excluded from action items per request* |
| **ADR** | `phase-2-roadmap.md` | Shared | Cross-cutting decisions inherited by P1–P4 |

- **Free / Premium** columns reflect **ADR-14** ("free-tier is config, not a code
  path"): the same code, different toggle values.

---

## 1. Frontend Stack

The frontend was rebuilt in `ui-revamp` (Phase 1, complete). P1–P4 only *wire* new
data/flows into it; they add no new frontend frameworks.

| Concern | Current | Planned / notes | Spec |
|---|---|---|---|
| Framework | **Next.js 16** (App Router) + **React 19** | unchanged | UI |
| Language | **TypeScript 5** | unchanged | UI |
| Routing | Next App Router; route groups `(marketing)/(app)/(admin)`; `middleware.ts` guards | real session guard wired | UI → P1 |
| State (client) | React context providers (`ThemeProvider`, `SessionProvider`, `ToastProvider`, `CommandPaletteProvider`, `TailorFlowProvider`) | `SessionProvider` hydrates from real `/auth/session` | UI → P1 |
| Server state | **TanStack Query v5** (already installed) | query invalidation for new resources | UI → P1/P2/P3 |
| Styling | **Tailwind CSS v4** (`@tailwindcss/postcss`); tokens as CSS vars; `clsx`, `tailwind-merge`, `class-variance-authority` | unchanged | UI |
| Component library | **shadcn/ui** on **Radix UI** primitives (dialog, dropdown, select, tabs, tooltip, switch, avatar, label, slot) | new screens compose same kit | UI |
| Icons | **lucide-react** (no barrel imports — per-icon) | unchanged | UI |
| Forms | Controlled React + Radix Label; validation in-component | auth forms wired to `authApi` | UI → P1 |
| Rich text editor | **TipTap 3** (`starter-kit`, `link`, `underline`) — lazy-loaded | unchanged | UI |
| Drag & drop | **dnd-kit** (`core`, `sortable`, `utilities`) — pipeline + section reorder | unchanged | UI |
| Charts | **none yet** | lazy-loaded lib (Recharts vs minimal alt — gated by bundle budget) | UI → P2 |
| Tables | shadcn Table + custom; cursor pagination; virtualization for long lists | admin/audit virtualized tables | UI → P2 |
| Animations | **tw-animate-css** + transform/opacity; disabled under `prefers-reduced-motion` | unchanged | UI |
| Theme management | class-strategy provider + pre-hydration inline script (no FOUC); default light | unchanged | UI |
| Internationalization | JSON message catalogs (`messages/*.json`); locale-parity test | keep parity for new strings (resolve `fr.json` gap) | UI |
| Command palette (⌘K) | custom `CommandPaletteProvider` | wired to global search + AI commands | UI → P3 |
| Image handling | `next/image` | **Cloudinary** URL transforms for avatars | UI → P3 |
| HTML sanitization | **isomorphic-dompurify** (resume HTML only) | unchanged | UI |
| Offline / SW | **none yet** (draft persistence via localStorage only) | **Service Worker (Workbox)** + IndexedDB | UI → **P4** |
| Build tooling | **Turbopack** (dev), `next build`, ESLint 9, Prettier | unchanged | UI |

**Notable:** no new frontend framework is introduced by Phase 2. The heaviest new
frontend capability is the **Service Worker + IndexedDB** durability layer, owned by
**P4 (resilience)**.

---

## 2. Backend Stack

| Concern | Current | Planned / notes | Spec |
|---|---|---|---|
| Framework | **FastAPI 0.128** | unchanged | ADR |
| Runtime | **Python 3.13**, **Uvicorn** (ASGI) | ≥2 workers (multi-worker readiness) | ADR-6 |
| ORM | **SQLAlchemy 2 (async)** + `aiosqlite` | same ORM, Postgres driver hosted | ADR-13 |
| Legacy store | **TinyDB 4.8** (being retired — `migrate_tinydb_to_sqlite.py`) | fully removed once on SQLAlchemy | — |
| Validation | **Pydantic v2** + `pydantic-settings` | request/response models per endpoint | ADR-7 |
| Auth library | **none yet** | **argon2-cffi** (Argon2id), `authlib`/manual OAuth, `itsdangerous` (signed transient cookies) | **P1** |
| Session management | **none** (single-user today) | server-side `sessions` table + httpOnly cookie + KVStore cache | **P1** / ADR-1 |
| File processing | **markitdown[docx]**, **pdfminer.six**, **python-docx** (parse); **Playwright** (PDF render via `/print/*`) | unchanged | UI |
| AI provider integration | **LiteLLM 1.86** (multi-provider abstraction); keys encrypted via **cryptography** | streaming (SSE) added | UI → **P4** |
| Background jobs | **none** (no scheduler today) | rollup/purge/reaper/indexer/retention via `SCHEDULER_MODE` | **P2/P3** / ADR-15 |
| Queue / async work | in-process; **transactional outbox** table + async consumers | outbox pattern (no external broker) | **P3** |
| Email service | **none** | pluggable `EmailSender` (free provider default: Resend/Brevo) | **P1/P3** / ADR-14 |
| Search engine | **none** | **SQLite FTS5** (local) / **Postgres `tsvector` + GIN** (hosted) behind a `SearchIndex` port | **P3** |
| KVStore (cache/locks/rate-limit) | **none** (in-proc) | pluggable `KVStore`: in-proc (dev) / **Upstash Redis** (free) / DB-backed fallback / Redis (premium) | ADR-6 |
| Logging | Python logging | JSON structured logs + `request_id` + `user_id` (no PII/secrets) | ADR-11 |
| Config management | `pydantic-settings` + encrypted API-key store (SQLite) + env | + `.config.kiro`-style env toggles (ADR-14) + admin settings store (P2) | ADR-12/14 |
| Migrations | **Alembic** (present, baseline `0001`) | auth tables, user-scoping, metrics, versions | ADR-9 / P1 |

---

## 3. Database

**Current:** **SQLite** via SQLAlchemy async (`aiosqlite`), single file (`resume_matcher.db`),
WAL mode; a sync engine for the encrypted `api_keys` table. TinyDB is a legacy remnant
being migrated out.

**Planned production:** **PostgreSQL** (hosted), selected by a single `DATABASE_URL`.
SQLite stays for **local dev only**.

| Question | Answer |
|---|---|
| Migration planned? | **Yes** — SQLite (dev) → PostgreSQL (hosted). |
| Why needed? | Free backend hosts (Render/Fly free) use an **ephemeral filesystem**: the single SQLite file is wiped on every restart/redeploy/sleep, destroying all user data. Multi-user hosting needs a durable, concurrent, networked DB. |
| Free-tier target | **Neon** (preferred) — serverless Postgres, **auto-scales to zero, wakes in ~0.5 s**, ~0.5 GB free. **Supabase** is an accepted alternative but pauses after ~7 days idle (manual resume) — worse for a sporadically-visited demo. |
| Migration strategy | Same SQLAlchemy models + Alembic migrations; all SQL is **Postgres-safe**. Switch = change `DATABASE_URL` + use the **pooled** connection string (low free connection caps). |
| Backward compatibility | One codebase, two engines. Only migrations + FTS differ (SQLite FTS5 vs Postgres `tsvector`); everything else is engine-agnostic. |
| Data migration approach | P1 Alembic chain: baseline → new auth tables → nullable `user_id` → **backfill** existing rows to a bootstrap owner user → enforce NOT NULL + constraints. Forward + reversible, chunked, verified on a copy, DB backed up first. |
| SQLite → MongoDB? | **No — explicitly rejected (ADR-13).** The backend is relational SQLAlchemy + Alembic (users, sessions, FKs, user-scoping). Mongo = rewriting the entire data layer, every query, all migrations, for no gain; the 512 MB Atlas free tier is smaller than the effort saved. |
| SQLite → PostgreSQL? | **Yes.** A connection-string change; the intended path. |
| Stay on SQLite? | **Local dev: yes. Hosted: no** — ephemeral disk wipes it. |

**Reasoning:** the cheapest *and* most correct path. SQLite→Postgres is trivial for a
relational codebase; Postgres persists on free tiers (Neon) where SQLite cannot; Mongo
would be the most expensive option for zero benefit.

**Owning specs:** ADR-13 (decision) · **P1** (migration chain, user-scoping) · **P2**
(`metrics_daily`, indexes `CONCURRENTLY` on Postgres) · **P3** (FTS choice).

---

## 4. Storage

**Current:** none for binaries. Resume **text** lives in the DB; generated PDFs are
rendered on demand by Playwright from `/print/*` pages (not persisted). No avatar upload.

**Planned:** a single **`StorageProvider` interface** (`upload`, `get_url`, `delete`)
selected by `STORAGE_PROVIDER` (ADR-10).

| Adapter | Role | Stores |
|---|---|---|
| **Cloudinary** (25 GB free) | **free-tier hosted default** | profile **avatars** (P3); future attachments |
| **S3 / S3-compatible** | premium / self-host | same, at scale |
| **Local filesystem** | dev only | same, locally |
| **DB (unchanged)** | — | resume text, structured resume JSON, encrypted API keys |
| **On-demand render** | — | PDFs (resume + cover letter) via Playwright — not stored |

- **What each stores:** Cloudinary → avatars (and future attachments/images); DB →
  all resume/application text + encrypted secrets; ephemeral render → PDFs.
- **Free-tier pattern:** **direct browser→Cloudinary signed upload** (signed by a tiny
  backend endpoint) so uploads don't traverse the sleeping/bandwidth-limited backend;
  **Cloudinary URL transforms** (`w_,h_,q_auto,f_auto`) replace server-side image
  processing. Security (magic-byte sniff, re-encode, EXIF strip, no SVG) still enforced
  via signed params + post-upload verification.
- **Not used:** Vercel Blob / UploadThing — Cloudinary's free tier covers the need and is
  already named in ADR-10; adding another would fragment the abstraction.

**Owning specs:** ADR-10 (interface + Cloudinary default) · **P3** (avatar pipeline).

---

## 5. Authentication

**Current:** **none** — effectively single-user (`SINGLE_USER_MODE`), API keys encrypted
at rest. `ui-revamp` shipped auth **UI only** against typed stubs.

**Planned (P1, the largest new subsystem):**

| Aspect | Decision | Spec |
|---|---|---|
| Session strategy | Server-side sessions (opaque 256-bit token; DB stores `sha256` only); sliding expiry + absolute cap; revocation + "log out everywhere" | ADR-1 / P1 |
| Cookie strategy | `__Host-` prefixed, **httpOnly + Secure + SameSite=Lax**, no readable token in JS; separate JS-readable `csrf` cookie | ADR-1/2 / P1 |
| CSRF | Double-submit token + SameSite; pre-session token for login/signup (login-CSRF) | ADR-2 / P1 |
| OAuth provider | **Google** (Authorization Code + PKCE + state/nonce); provider-generic interface (GitHub/MS later = new adapter) | ADR-5 / P1 |
| Password hashing | **Argon2id** (`argon2-cffi`, m/t/p tunable); denylist + strength gate + optional HIBP | ADR-3 / P1 |
| RBAC | Capability model (`admin.read`/`admin.manage`) derived from role; `require_capability` deps | P1 → P2 |
| Step-up / sudo | Recent re-auth window (`STEP_UP_WINDOW`) for sensitive actions; `aal` field | P1 |
| Future MFA | `mfa_enrolled` + `aal2` + reserved `authenticators` table — readiness only, no P1 code | P1 |
| Passkey readiness | Same reserved `authenticators` (WebAuthn) table; additive later, no rework | P1 |
| Abuse controls | Per-ip + per-user token bucket, lockout, CAPTCHA hook, breached-password hook | ADR-8 / P1 |

**Migration:** stub → real via a flag rollout: deploy `SINGLE_USER_MODE=on` (identical to
today) → enable auth + verification flags → hosted sets `SINGLE_USER_MODE=off`.

**Owning spec:** **P1 (auth-foundation)**, consumed by **P2** (admin RBAC).

---

## 6. Search

**Current:** client-side filtering over already-loaded lists (resumes, applications) in
the revamped UI; no server search.

**Planned:**

| Aspect | Decision | Spec |
|---|---|---|
| Client-side search | quick filters + sort on loaded data; command-palette client fallback | UI / P3 |
| Server-side search | `GET /search` — scoped, ranked, cursor-paginated | **P3** |
| Full-text search | **SQLite FTS5** (local) / **Postgres `tsvector` + GIN** (hosted), behind a `SearchIndex` port | **P3** / ADR-13 |
| Search indexing | **transactional outbox → async `SearchIndexer`** upserts `search_documents`; rebuild command + drift detection | **P3** |
| Search technology | keyword FTS now; **embeddings/semantic** deferred behind the same port (no rework) | **P3** |
| Injection safety | `q` parameterized, scoped **in SQL** (never string-built) so no crafted query crosses users | P3 / ADR-4 |

**Reasoning:** FTS5/`tsvector` are built into the DB engines already chosen — **no new
search service** (no Elasticsearch/Meilisearch), which keeps the free-tier footprint at
zero extra infrastructure. Async indexing means a search failure never fails a user write.

**Owning spec:** **P3 (productivity)**.

---

## 7. Notifications

**Current:** transient **toasts** only (export done, generation failed, parse complete) in
the revamped UI.

**Planned:**

| Aspect | Decision | Spec |
|---|---|---|
| Persistent in-app | `notifications` table + `NotificationCenter`; O(1) unread badge via `user_unread_counts` | **P3** |
| Polling vs Push | **Config toggle `notification_transport`** — **polling on free** (active-tab poll every `polling_interval_seconds`) / **SSE on premium** | **P3** / ADR-14 |
| WebSockets? | **No** — avoided on free tier (they hold the sleeping dyno open, fighting the cold-start model). SSE is the premium push path. | ADR-15 |
| Email | pluggable `EmailSender` (free provider default: Resend/Brevo); content-safe (title + deep link only, never resume/JD content) | **P3** / ADR-14 |
| Background scheduler | reminders/interviews fire via a **claim-based scheduler** under `SCHEDULER_MODE` (external_cron free / internal premium) | **P3** / ADR-15 |
| Delivery guarantee | `dedupe_key` + single-flighted claim → exactly-once effect, even across workers/retries | **P3** |

**Reasoning:** polling + `external_cron` needs no always-on infrastructure (free-tier
friendly); SSE/internal-worker is a value flip for premium. Same data model either way.

**Owning spec:** **P3 (productivity)**; SSE transport shared with **P4** streaming.

---

## 8. AI Infrastructure

**Current:** **LiteLLM** multi-provider abstraction; per-user API keys **encrypted** at
rest (`cryptography`) in SQLite; synchronous (non-streaming) generation; staged UI results
in the tailor flow.

**Planned:**

| Aspect | Decision | Spec |
|---|---|---|
| Supported providers | LiteLLM-backed (OpenAI, Anthropic, etc.) — provider-agnostic | UI (current) |
| BYO API key | user-supplied, encrypted, write-only/masked; **shifts cost off the host + removes shared rate-limit ceiling** (ideal for open-source) | UI / ADR-14 |
| Prompt storage | prompt templates in code (`app/prompts/*`) | current |
| Streaming strategy | **SSE token streaming** (`STREAMING_AI` flag) with heartbeat/done/error events; transparent fallback to non-stream | **P4** |
| Token counting | `done`/cancel events report provider token usage → feeds the cost-guard | **P4** |
| Caching | analysis/keywords cached by **content hash**; identical prompts/results reused (avoids duplicate spend) | UI / P3 |
| Rate limiting | `ai_rate_limit_per_user` (admin setting) + ADR-8 token bucket | **P2/P3** / ADR-14 |
| Cost tracking | `ai_daily_token_cap` (admin setting); AI is opt-in, bounded, never auto-fired | **P2/P3** |
| Cancellation | server-side task registry keyed by `(user_id, request_id)` in KVStore; cancel aborts the provider call; reaper + per-user concurrent-stream cap | **P4** |

**Reasoning:** LiteLLM already abstracts providers, so no lock-in. BYO-key + streaming +
caching is the free-friendly combination: the host pays nothing for inference and slow
generation *feels* fast.

**Owning specs:** current (LiteLLM) · **P4** (streaming, cancellation) · **P2/P3** (limits,
caching, cost).

---

## 9. Resilience Technologies

**Current:** localStorage draft persistence + `RecoveryBanner`/`OfflineIndicator`
primitives (from `ui-revamp`); optimistic-concurrency compare on save.

**Planned (P4 — three durability layers: editor memory → IndexedDB → server):**

| Aspect | Decision | Spec |
|---|---|---|
| Offline storage | **IndexedDB** — `draft` (crash safety net) + `outbox` (ordered op-log) + `quarantine` | **P4** |
| LocalStorage | retained for lightweight prefs/theme; drafts move to IndexedDB (encrypted) | UI → P4 |
| Service Worker | **Workbox** — app-shell precache + safe-GET stale-while-revalidate; never caches auth/CSRF/keys; versioned + safe-update | **P4** |
| Background Sync | `SyncController` replays the outbox (FIFO) via version-CAS on reconnect; a 409 pauses and raises conflict | **P4** |
| Recovery system | on-load reconcile (draft vs server `version`/`updated_at`); coherent single recovery surface; non-destructive default | **P4** |
| Conflict resolution | atomic **version CAS** (`If-Match`) → typed **409** with current data; keep-mine / take-latest / disjoint field-merge + diff | **P4** / ADR-4.2 |
| Multi-tab coordination | **Web Locks API** leader election + BroadcastChannel fan-out + draft/outbox mutex | **P4** |
| Integrity / safety | per-record hash + schema version → quarantine on mismatch; **WebCrypto AES-GCM** encryption at rest; per-`user_id` namespacing | **P4** |
| Cold-start masking | SW cache + IndexedDB render instantly while the slept backend wakes; `/health` probe doubles as keep-warm target | **P4** / ADR-15 |

**Reasoning:** deliberately **not CRDT/real-time** (over-scope). Client-side durability
also happens to be the best free-tier UX weapon — it hides Render cold starts entirely for
returning users.

**Owning spec:** **P4 (resilience)**.

---

## 10. DevOps & Infrastructure

| Aspect | Current | Planned (free tier) | Premium | Spec |
|---|---|---|---|---|
| Frontend hosting | local / Docker | **Netlify or Vercel** (edge CDN, always warm) | same | ADR-15 |
| Backend hosting | local / Docker | **Render free** (ephemeral disk, sleeps ~15 min, 30–60 s cold wake) | Render/Fly paid (always-on) | ADR-15 |
| Database hosting | local SQLite file | **Neon** (serverless Postgres, pooled) | managed Postgres (Neon paid / RDS) | ADR-13 |
| Object storage | none | **Cloudinary** (25 GB free) | S3 | ADR-10 |
| KVStore | none | **Upstash Redis (free)** or DB-backed fallback | Redis | ADR-6 |
| Scheduler | none | **`external_cron`** (GitHub Actions / cron-job.org hits an authenticated endpoint) | `internal` (APScheduler/worker) | ADR-15 |
| Keep-warm | n/a | free cron pings `/health` every `keepalive_interval_minutes` | disabled | ADR-14/15 |
| Deployment | Dockerfile + `docker-publish.yml` | migrate → deploy behind flag → canary → enable | same | ADR-9/4.5 |
| CI/CD | GitHub Actions (`docker-publish.yml`); `.githooks/pre-push` | + cron workflows for keep-warm & scheduled jobs | same | — |
| Env management | `.env` + `pydantic-settings` + encrypted key store | env toggles (ADR-14) + admin settings store (P2) | flip values | ADR-14 |
| Monitoring / metrics | none | Prometheus-style counters (per-feature) | + dashboards | ADR-11 / P2/P3/P4 |
| Logging | Python logging | JSON structured + `request_id`/`user_id` | + aggregation | ADR-11 |
| Analytics | none | admin telemetry (aggregated events, no PII) | same | P2 |
| Error reporting | none | structured logs + alerts; (Sentry-class optional, not required) | optional Sentry | ADR-11 |

**Owning specs:** ADR-6/9/11/13/14/15 · **P2** (metrics, settings, jobs).

---

## 11. Testing Stack

**Current (real, in repo):** frontend **Vitest** + **Testing Library** + **Playwright**;
backend **pytest** + `pytest-asyncio` + **httpx** (ASGITransport) + **respx** (HTTP mock);
a `pypdf` PDF render probe (`e2e-monitor`).

| Test type | Technology | Scope | Spec |
|---|---|---|---|
| Unit | Vitest (FE) / pytest (BE) | services, validation, state machines, utils; LLM/network mocked | all |
| Integration | httpx ASGITransport + real temp DB | every endpoint incl. authz/ownership/negative | ADR-4.4 / P1–P4 |
| E2E | Playwright | core path + auth guards + admin RBAC + offline/conflict flows | P1/P2/P4 |
| Accessibility | keyboard/SR/contrast/reduced-motion/focus checks per screen | AA floor | all (UI standard) |
| Performance | load/concurrency on hot paths (login, session-resolve, search, autosave, retry-storm) | p95 targets (§4.3) | P1/P3/P4 |
| Security | authz matrix, CSRF, IDOR, injection, upload, rate-limit, OAuth state/replay, SSRF | per-spec threat model | P1/P3 |
| Property-based | correctness properties per spec (isolation, exactly-once, version-CAS, SSRF containment, etc.) | validated as executable properties | P1–P4 |

**Reasoning:** no new test frameworks needed — the existing stack (Vitest/pytest/Playwright/
httpx/respx) covers every planned test type. WCAG conformance still needs manual assistive-tech
review beyond automated checks.

---

## 12. Tech Stack Migration Summary

Every migration across the project. "Migration?" = does existing code/data change (vs a
net-new addition).

| Area | Current | Planned | Migration? | Reason | Spec |
|---|---|---|---|---|---|
| Database (hosted) | SQLite | PostgreSQL (Neon) | **Yes** | Ephemeral disk wipes SQLite; multi-user durability | ADR-13 / P1 |
| Legacy store | TinyDB | SQLAlchemy/SQLite | **Yes (in progress)** | Unify on one ORM; enable migrations | current |
| Data ownership | global rows | `user_id`-scoped + backfill | **Yes** | Multi-tenant isolation | ADR-4 / P1 |
| Auth | none (single-user) | sessions + Google OAuth + Argon2 | **Yes (net-new subsystem)** | Production multi-user auth | P1 |
| Schema management | implicit `create_all` | Alembic up/down | **Yes** | Safe, reversible evolution | ADR-9 |
| KVStore | in-process | Upstash / DB-backed / Redis | **Yes** | Multi-worker shared state | ADR-6 |
| Avatar storage | none | Cloudinary (StorageProvider) | **New** | Image hosting off-DB, off-dyno | ADR-10 / P3 |
| AI generation | synchronous | + SSE streaming (toggle) | **New (additive)** | Perceived latency under cold start | P4 |
| Search | client-side | FTS5 / `tsvector` server search | **New** | Scale + cross-object search | P3 |
| Notifications | toasts | persistent + polling/SSE + email | **New** | Reminders, follow-ups, digests | P3 |
| Offline/durability | localStorage draft | IndexedDB + Service Worker | **New** | No-data-loss + cold-start masking | P4 |
| Scheduling | none | `external_cron` → `internal` | **New** | Rollups, purge, reaper, reminders | ADR-15 / P2/P3 |
| Charts | none | lazy-loaded charts lib | **New** | Admin analytics | P2 |
| Email | none | pluggable EmailSender (free provider) | **New** | Verification, reset, notifications | P1/P3 |
| Hosting | local/Docker | Netlify + Render + Neon + Cloudinary | **New** | Free public multi-user deploy | ADR-15 |
| Observability | basic logging | structured logs + metrics + audit | **New** | Operability at scale | ADR-11 |
| Config | env + key store | + admin settings store + toggles | **New** | Free/premium without redeploy | ADR-14 / P2 |

---

## 13. External Services

| Service | Why needed | Optional? | Alternatives | Future migration | Spec |
|---|---|---|---|---|---|
| **Neon** (Postgres) | Persistent hosted DB on free tier (fast wake) | No (hosted) | Supabase, managed Postgres, self-host | → any Postgres via `DATABASE_URL` | ADR-13 / P1 |
| **Cloudinary** | Avatar/image hosting + transforms off-DB | Yes (avatars only) | S3, Vercel Blob, UploadThing, R2 | → S3 via `STORAGE_PROVIDER` | ADR-10 / P3 |
| **Google OAuth** | Social sign-in | Yes (email/password works alone) | GitHub/Microsoft (same provider iface) | add providers = new adapter | ADR-5 / P1 |
| **AI providers** (via LiteLLM) | Resume tailoring / generation | No (core feature); **BYO key** shifts cost | any LiteLLM-supported provider | swap freely, no lock-in | current / P4 |
| **Email provider** (Resend/Brevo) | Verification, reset, notification email | Partly (verification gates hosted) | SES, Postmark, SMTP | swap via `EmailSender` config | ADR-14 / P1/P3 |
| **Upstash Redis** | Multi-worker KVStore on free tier | Yes (DB-backed fallback exists) | Redis, Memurai, in-proc (dev) | → Redis via `KVSTORE_URL` | ADR-6 |
| **Cron** (GitHub Actions / cron-job.org / UptimeRobot) | Keep-warm ping + `external_cron` jobs | Yes on premium (internal scheduler) | any scheduler hitting the endpoint | → `SCHEDULER_MODE=internal` | ADR-15 / P2/P3 |
| **Netlify/Vercel** | Frontend hosting/CDN | No (hosted) | Cloudflare Pages, static host | portable (Next.js) | ADR-15 |
| **Render** | Backend hosting | No (hosted) | Fly.io, Railway, self-host | portable (Docker) | ADR-15 |
| Monitoring/error (Sentry-class) | Error aggregation | **Yes (optional)** | self-hosted logs/metrics | add later | ADR-11 |

**Principle:** every third-party service sits behind an interface or a single env var, so
each is swappable and none creates hard lock-in (see §14).

---

## 14. Dependency Audit (challenge each choice)

Each row: *why chosen · best choice? · alternatives · trade-offs · scales? · production-proven? · lock-in?*

- **Next.js 16 / React 19** — Chosen: existing app, industry standard. Best? Yes for this
  app. Alternatives: Remix, SvelteKit. Trade-offs: App Router churn, RSC complexity.
  Scales: yes (CDN). Proven: yes. Lock-in: moderate (portable to any Node host).
- **Tailwind v4** — Chosen: existing; fast styling. Best? Yes. Alt: CSS Modules, vanilla-extract.
  Trade-off: v4 is newer (verify shadcn compat — flagged in `ui-revamp`). Scales/proven: yes. Lock-in: low.
- **shadcn/ui + Radix** — Chosen: unstyled-accessible primitives you own. Best? Yes (a11y). Alt: MUI, Mantine.
  Trade-off: you maintain the copied components. Scales/proven: yes. Lock-in: none (code is yours).
- **TanStack Query** — Chosen: server-state standard. Best? Yes. Alt: SWR, RTK Query.
  Trade-off: learning curve. Scales/proven: yes. Lock-in: low.
- **TipTap / dnd-kit** — Chosen: editor + DnD, lazy-loaded. Best? Yes for the use. Alt: Slate/Lexical, react-dnd.
  Trade-off: bundle weight (mitigated by lazy-load). Proven: yes. Lock-in: moderate (editor schema).
- **FastAPI + Pydantic v2** — Chosen: async, typed, existing. Best? Yes for Python. Alt: Litestar, Django.
  Trade-off: DIY structure. Scales: yes (async + workers). Proven: yes. Lock-in: low.
- **SQLAlchemy 2 + Alembic** — Chosen: de-facto Python ORM; **the reason Postgres is a config change**.
  Best? Yes. Alt: Tortoise, SQLModel, Prisma-py. Trade-off: verbosity. Scales/proven: yes. Lock-in: low (SQL-standard).
- **PostgreSQL (Neon)** — Chosen: durable relational fit; Neon wakes fast on free tier.
  Best? Yes given SQLAlchemy. Alt: Supabase, managed PG. Trade-off: free connection caps → pooler. Scales/proven: yes.
  Lock-in: **low** (standard Postgres; Neon-specific features avoided).
- **SQLite (dev)** — Chosen: zero-config local. Best? Yes for dev. Trade-off: not hosted-durable (accepted, dev-only).
- **LiteLLM** — Chosen: one API across providers + BYO key. Best? Yes for provider-agnostic. Alt: native SDKs, LangChain.
  Trade-off: abstraction lag behind new provider features. Scales/proven: yes. Lock-in: **low** (that's its purpose).
- **Argon2id (argon2-cffi)** — Chosen: current best password KDF. Best? Yes (OWASP). Alt: bcrypt/scrypt (weaker/older).
  Trade-off: CPU cost (tunable + rate-limited). Proven: yes. Lock-in: none.
- **KVStore (Upstash/DB-backed/Redis)** — Chosen: pluggable so free tier needs no Redis. Best? Yes (flexibility).
  Alt: hard Redis dependency (rejected for free tier). Trade-off: DB-backed fallback is coarser. Scales/proven: yes. Lock-in: none (interface).
- **Cloudinary** — Chosen: 25 GB free + transforms. Best? For free tier, yes. Alt: S3+CloudFront, R2, Vercel Blob, UploadThing.
  Trade-off: proprietary transform URLs (mitigated by StorageProvider interface). Scales: yes (paid). Lock-in: **moderate**, contained behind the interface.
- **FTS5 / Postgres tsvector** — Chosen: built into the DB, zero extra infra. Best? Yes at this scale. Alt: Meilisearch, Elasticsearch, pgvector (semantic).
  Trade-off: keyword-only now (semantic deferred behind the `SearchIndex` port). Scales: to mid-size; re-evaluate at very large scale. Lock-in: low (port).
- **Service Worker (Workbox) + IndexedDB + Web Locks + WebCrypto** — Chosen: browser-native, no deps.
  Best? Yes (platform APIs). Alt: heavier offline libs. Trade-off: browser-API complexity (mitigated by pure state machines). Proven: yes. Lock-in: none.
- **Transactional outbox (no broker)** — Chosen: reliability without Kafka/RabbitMQ (free-tier friendly).
  Best? Yes at this scale. Alt: external queue/broker. Trade-off: eventual consistency (bounded, monitored). Scales: to mid-size; broker later if needed. Lock-in: none.
- **external_cron scheduling** — Chosen: free hosts can't run worker dynos. Best? For free tier, yes. Alt: internal APScheduler/Celery (premium).
  Trade-off: coarser cadence + an exposed authenticated endpoint. Scales: flip to `internal`. Lock-in: none (`SCHEDULER_MODE`).
- **Playwright (PDF render + E2E)** — Chosen: existing; pixel-accurate PDF from `/print/*`. Best? Yes.
  Trade-off: heavy runtime (Chromium) — memory pressure on a small dyno (watch on free tier). Proven: yes. Lock-in: moderate.

**Overall verdict:** the stack is deliberately **low-lock-in** — every hosted dependency
(DB, storage, KV, scheduler, email, AI) sits behind an interface or a single env var, so
free→premium and vendor swaps are configuration, not rewrites. The one thing to watch on
the free tier is **Playwright's memory footprint** on a small backend dyno.

---

## Appendix A — Free ⇄ Premium toggle registry (ADR-14)

Migrating free→premium is a checklist of value changes, no code:

| Concern | Toggle | Kind | Free | Premium | Spec |
|---|---|---|---|---|---|
| Keep-warm ping | `keepalive_enabled` / `_interval_minutes` / `_target_url` | admin setting | on / 10 / `/health` | off | P2 |
| Database | `DATABASE_URL` | env | Neon free (pooled) | managed Postgres | P1 |
| DB pool | `db_pool_size` / `db_use_pooler` | env | small / pooler | larger | P1 |
| KVStore | `KVSTORE_URL` | env | Upstash free / DB-backed | Redis | ADR-6 |
| Storage | `STORAGE_PROVIDER` | env | `cloudinary` | `s3` | P3 |
| Scheduler | `SCHEDULER_MODE` | env | `external_cron` | `internal` | P2/P3/P4 |
| Notifications | `notification_transport` / `polling_interval_seconds` | admin setting | `polling` / 30 | `sse` | P3 |
| Streaming AI | `STREAMING_AI` | flag | off (fallback) or on | on | P4 |
| AI limits | `ai_rate_limit_per_user` / `ai_daily_token_cap` | admin setting | conservative | raised / off | P2/P3 |
| Email provider | `EmailSender` config | env | Resend/Brevo free | any | P1/P3 |
| Upload caps / TTLs / page sizes | various | admin setting | conservative | raised | P2/P3 |

---

## Appendix B — Per-Spec Reference Index

What each spec owns technology-wise (use alongside its `design.md`):

### P1 — `auth-foundation/`
Argon2id · server-side sessions + `__Host-` cookies · CSRF · Google OAuth (PKCE) ·
RBAC/capabilities · step-up + MFA/passkey readiness · Alembic migration chain + `user_id`
backfill · KVStore (session cache / rate-limit) · `EmailSender` (verification/reset) ·
session reaper under `SCHEDULER_MODE`. **DB:** SQLite→Neon Postgres via `DATABASE_URL`.

### P2 — `admin/`
Capability-gated admin APIs · `metrics_daily` rollup + live · cursor pagination ·
grace-period soft-delete + purge · **runtime settings store** (`/admin/settings` — powers
ADR-14 toggles) · charts lib (lazy) · RollupJob/PurgeJob under `SCHEDULER_MODE`
(`external_cron` free) · structured metrics/alerts.

### P3 — `productivity/`
Version history (gzip snapshots) · **transactional outbox** + async consumers ·
`NotificationService` (polling/SSE toggle, email) · **FTS search** (FTS5 / `tsvector`) ·
JD-from-URL (SSRF-guarded) · claim-based scheduler (reminders/interviews) · **Cloudinary**
avatars (StorageProvider) · retention jobs. All under `SCHEDULER_MODE`.

### P4 — `resilience/`
**SSE streaming AI** + task registry/cancel/reaper · **Service Worker (Workbox)** ·
**IndexedDB** draft/outbox/quarantine · Background Sync (version-CAS replay) · optimistic
concurrency (409 + resolution UI) · **Web Locks** multi-tab · **WebCrypto** encryption at
rest · cold-start masking. Flags `STREAMING_AI`/`OFFLINE_SUPPORT`/`ADVANCED_AUTOSAVE`.

### Shared — `phase-2-roadmap.md` (ADRs)
ADR-1/2 sessions+CSRF · ADR-3 Argon2 · ADR-4 user-scoping · ADR-5 OAuth · ADR-6 KVStore ·
ADR-7 API envelope · ADR-8 rate-limit · ADR-9 Alembic · ADR-10 storage · ADR-11
observability · ADR-12 flags · **ADR-13 DB (Postgres, Mongo rejected)** · **ADR-14
free/premium profile** · **ADR-15 free-tier hosting & cold-start**.

*(UI — `ui-revamp/` — complete; excluded from action items. Its stack: Next 16, React 19,
Tailwind v4, shadcn/Radix, TanStack Query, TipTap, dnd-kit, lucide, Vitest/Playwright.)*
