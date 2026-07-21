# FitWright - Architecture Blueprint & Engineering Constitution

> Status: **Adopted** - Audience: every engineer, present and future - Nature: living document, but with an explicitly immutable core (see §20).
>
> This document is the single source of truth for *how the system is shaped and why*. It is implementation-agnostic. Code changes; this document changes only through an ADR (§7). If code and this document disagree, one of them is a bug.

---

## How to read this document

- **Golden Rules (§20)** are immutable. Changing one is a re-founding event, not a refactor.
- **ADRs (§7)** record *why* a decision was made and *when to revisit it*. Superseding an ADR requires a new ADR.
- **Fitness Functions (§18)** make the important rules **mechanically enforced** in CI, not matters of discipline.
- Everything else is guidance that evolves as the system matures (§8).

A one-line summary of the whole philosophy: **Business logic is shared and pure; infrastructure is replaceable; deployment is composition; and we abstract only what has earned it.**

---

## 1. Architecture Vision

### Philosophy
We build **one product, one codebase, one deployable** whose behavior is determined by **composition at startup**, not by branches in the code. "Local desktop" and "hosted SaaS" are not two systems - they are two **configurations** of the same system.

### Core design principles
1. **Shared core, swappable edges.** The domain (resume parsing, tailoring, scoring, application tracking) is identical everywhere. Only infrastructure adapters differ.
2. **Explicit over implicit.** Deployment intent is *declared* (a profile) and *validated* (fail fast), never *guessed* from the presence of an env var.
3. **Abstraction must be earned.** A port exists only when there are ≥2 real implementations or a genuine external boundary that must be faked in tests. Everything else stays concrete.
4. **Fail fast, boot clean.** A misconfigured deployment must refuse to start, not silently degrade.
5. **Erosion is the enemy.** The rules that matter are enforced by automated fitness functions, because discipline does not survive 20 engineers and 5 years.

### Long-term goals
- Add a new deployment target (enterprise, self-hosted, air-gapped) as a **profile + maybe one adapter**, never a fork.
- Onboard an engineer to a feature without them needing to understand infrastructure.
- Keep the domain unit-testable with zero infrastructure.

### What this architecture intentionally does NOT solve
- **Microservices / independent scaling of sub-domains.** We are a modular monolith by choice (ADR-0001). We will not pre-split.
- **Runtime plugin marketplace.** Deferred; the composition seam is designed not to *forbid* it, but we do not build it now.
- **Per-tenant physical isolation** (separate DB per tenant). Logical isolation via a scoping key is designed in now (ADR-0012); physical isolation is a future stage, not a current requirement.
- **Event sourcing / CQRS.** Explicitly rejected for this domain (ADR-0011).

---

## 2. Core Architecture - Modular Monolith with Selective Hexagonal Boundaries

**Decision:** A **modular monolith**. Inside it, a **thin hexagonal seam only at true infrastructure boundaries**. Not full hexagonal. Not microservices. Not layered-everywhere Clean Architecture.

### Why
- A monolith is the simplest thing that can serve desktop *and* SaaS from one artifact.
- Full hexagonal (ports for everything) buys nothing for single-implementation concerns and taxes every reader. Selective ports give us the swap-ability we actually need (DB, storage, identity, LLM, jobs, mail) without ceremony everywhere else.
- Modules give us clear ownership (§9) and a natural future extraction seam *if* we ever must (we don't plan to).

### Tradeoffs accepted
- One deploy unit scales as a unit. Acceptable: our load is LLM-bound and I/O-bound, scaled horizontally behind a load balancer; we do not need independent sub-domain scaling.
- Module boundaries are enforced by convention + fitness functions, not by process isolation. Cheaper, and sufficient.

### The four rings

```
        +-----------------------------------------------+
        |                 PRESENTATION                    |  HTTP routers, request/response schemas,
        |        (FastAPI routers, Next.js UI)            |  serialization, auth middleware wiring
        +-----------------------+-------------------------+
                                 | depends on
        +-----------------------▼-------------------------+
        |                 APPLICATION                      |  use-cases / orchestration, transactions,
        |      (tailor_resume, create_application, ...)      |  calls ports, enforces authorization
        +-----------------------+-------------------------+
                                 | depends on
        +-----------------------▼-------------------------+
        |                    DOMAIN                        |  entities, value objects, domain rules,
        |   (Resume, Application, Score, Principal, ...)     |  PURE - no I/O, no framework
        +-----------------------+-------------------------+
                                 | depends on (interfaces only)
        +-----------------------▼-------------------------+
        |                     PORTS                        |  Repository, Storage, KVStore, Mailer,
        |              (abstract interfaces)               |  IdentityProvider, LLMProvider, Jobs, Clock
        +-----------------------▲-------------------------+
                                 | implemented by
        +-----------------------+-------------------------+
        |                INFRASTRUCTURE                    |  SQLAlchemy repos, Cloudinary, Redis,
        |       (adapters - the only ring that knows       |  SMTP, session store, LLM SDKs, cron
        |        SQLAlchemy / httpx / Redis / etc.)        |
        +-----------------------▲-------------------------+
                                 | wired once by
        +-----------------------+-------------------------+
        |              COMPOSITION ROOT                    |  reads profile+config, instantiates
        |            (startup wiring, one place)           |  adapters, injects them. Runs once.
        +--------------------------------------------------+
```

**The dependency rule (non-negotiable):** dependencies point **inward**. Infrastructure depends on Ports/Domain; the Domain depends on nothing but itself. The Composition Root is the only place allowed to know every concrete adapter.

---

## 3. Deployment Philosophy - No "Modes"

**The system has no `single_user_mode` behavioral branch.** It has **deployment profiles** resolved into **capabilities** and realized by **composition**.

### Why "mode" is wrong
A boolean `mode` conflates many independent axes (identity, DB, storage, scheduler, email...) that do not have to move together, and it metastasizes into `if mode:` checks across every layer. That is the exact erosion we refuse.

### The chosen model
```
Deployment Profile (declared intent)
        |  e.g. "desktop", "saas", "enterprise"
        ▼
Required Capabilities (validated at boot; fail fast if missing)
        |  e.g. saas => Postgres + Redis + real Mailer + sessions
        ▼
Composition (adapters chosen once, injected everywhere)
```

### Compared against alternatives
| Approach | Verdict | Why |
|---|---|---|
| **Mode flag** | [ ] | Conflated axes; scattered conditionals; erosion. |
| **Feature flags for topology** | [ ] | Wrong tool. Flags are for runtime rollout/experiments, not deployment shape. |
| **Multiple builds** | [ ] | Guarantees drift; two artifacts to test and ship. |
| **Separate branches** | [ ] | Worst case of drift; merge hell. |
| **Runtime capability inference** (use Redis iff REDIS_URL set) | [ ] | Implicit. A config typo silently downgrades prod (real Mailer -> noop) with no alarm. |
| **Profile + capability validation** | [x] | Explicit intent, fail-fast validation, adapter-driven realization. Superior on clarity, safety, and testability. |

**Long-term impact:** new targets are additive (a new profile), never structural. This is the single most important decision for 5-year maintainability.

---

## 4. Deployment Profiles

A profile is a **named, declared intent** with a required-capability contract. Boot validation refuses to start if the contract is unmet.

| Profile | Purpose | DB | Identity | Storage | Scheduler | Email | KV/Cache | Observability | Security posture | Failure strategy |
|---|---|---|---|---|---|---|---|---|---|---|
| **desktop** | Local-first single-user app | SQLite (or Postgres if that pillar is dropped - see ADR-0002) | Auto-login owner via a **real** session (not bypass) | Local disk | In-process loop | Noop / log | In-process | Local structured logs | Trusted single user; CSRF still on | Degrade gracefully; never lose local data |
| **saas** | Multi-user hosted | Postgres (pooled) | Cookie sessions + optional OAuth | Cloudinary/S3 | External cron -> internal endpoint (-> queue at scale) | SMTP/Resend | Redis | Shipped metrics + traces | Full: rate limits, lockout, verification | Fail fast on missing capability |
| **enterprise** | Customer-hosted, hardened | Postgres (customer-managed) | SSO/OIDC + sessions | Customer object store or Cloudinary | Queue workers | Customer SMTP | Redis (customer) | Export to customer stack | Strict + audit + data residency | Fail fast; explicit capability report |
| **self-hosted** | Single-tenant, hobbyist/org | Postgres (Docker) | Sessions + optional OAuth | Local or S3-compatible | External cron | Optional SMTP | Redis optional (in-proc if single instance) | Local logs, optional export | Configurable | Warn on non-scalable choices; boot |
| **development** | Engineer laptop | Postgres (Docker) *or* SQLite | Auto-login owner | Local disk | In-process | Log | In-process | Verbose logs | Relaxed | Loud errors, no silent fallback |
| **test / ci** | Automated suites | SQLite in-mem + Postgres (matrix) | Fake IdentityProvider | In-memory/temp | Manual trigger | In-memory capture | In-memory | Assertions | N/A | Deterministic; hermetic |

**Rule:** profiles are the *only* place deployment differences live. A profile maps to capabilities; capabilities map to adapters; the rest of the code is profile-blind.

---

## 5. Capability Model

A **capability** is a validated statement of "this deployment provides X." Capabilities are the contract between a profile and the composition root.

### Categories
- **Infrastructure capabilities:** `persistent_postgres`, `shared_cache` (Redis), `object_storage`, `outbound_email`, `durable_queue`.
- **Deployment capabilities:** `multi_user`, `external_scheduler`, `horizontal_scale`.
- **Policy capabilities:** `self_registration`, `oauth_login`, `email_verification`, `org_management` (future), `billing` (future).
- **AI capabilities:** `llm_available`, `streaming`, `cover_letter`, `interview_prep` - entitlements attached to the **principal**, not read from the profile in domain code.
- **Storage capabilities:** `avatar_uploads`, `cdn_delivery`.
- **Security capabilities:** `rate_limiting_shared`, `account_lockout`, `captcha`, `breach_check`, `audit_log`.

### Runtime-configurable vs deployment-time-only
| Capability | When set | Why |
|---|---|---|
| DB engine, identity strategy, scheduler kind, `multi_user`, `horizontal_scale` | **Deployment-time only** | Changing these changes the system's shape and safety guarantees; must be validated at boot and stable for the process lifetime. |
| LLM model, storage provider *within* a class, email provider, feature availability (cover letter on/off), prompt template | **Runtime-configurable** | These are user/admin choices with no structural impact; swapping them at runtime is safe and desirable. |

**Principle:** *If changing it at runtime could violate a safety or consistency guarantee, it is deployment-time. Otherwise it is runtime.*

---

## 6. Runtime vs Deployment Configuration

```
DEPLOYMENT-TIME (immutable for process lifetime, validated at boot, fail-fast)
  +- deployment_profile
  +- database engine + connection topology (pooled/direct)
  +- identity strategy (auto-owner | sessions | SSO)
  +- scheduler kind (in-process | external-cron | queue)
  +- shared-cache presence (in-process | Redis)
  +- secrets (session, ip-hash, job token)

RUNTIME (mutable, admin/user controlled, no restart)
  +- LLM provider + model + reasoning effort
  +- storage provider selection within available class
  +- email provider selection
  +- feature availability (cover letter, outreach, interview prep)
  +- prompt templates, content language
```

**Why the split:** deployment-time config defines the *contract with the outside world* and the *safety envelope*; it must be stable and verified before serving traffic. Runtime config is *product behavior* the operator tunes without a redeploy. Conflating them (e.g., letting "identity strategy" be a runtime toggle) creates security-relevant race conditions and untestable states.

---

## 7. Architecture Decision Records (ADRs)

### ADR template (copy for every new decision)
```
# ADR-XXXX: <title>
Status: Proposed | Adopted | Superseded by ADR-YYYY
Date: YYYY-MM-DD
Context:      <forces, constraints, problem>
Decision:     <what we chose>
Alternatives: <what we rejected and why>
Tradeoffs:    <what we gave up>
Consequences: <positive + negative, operational impact>
Reconsider when: <objective trigger that should reopen this>
```

> **Governance:** an ADR is required for every infrastructure dependency, every new port, every deployment profile, and any change to a Golden Rule. ADRs are append-only; you supersede, you don't edit history.

---

### ADR-0001: Modular Monolith (not Microservices)
- **Context:** Small team, one product, LLM/IO-bound load, need one artifact for desktop + SaaS.
- **Decision:** Single deployable modular monolith with enforced internal module boundaries.
- **Alternatives:** Microservices (independent deploy/scale); serverless functions.
- **Tradeoffs:** No independent sub-domain scaling; shared failure domain.
- **Consequences:** Simplest ops, one test matrix, easy transactions, clear ownership. Horizontal scaling by replication.
- **Reconsider when:** a single module has a *provably* different scaling/reliability profile that replication cannot serve, **and** team size supports service ownership.

### ADR-0002: SQLite as first-class *only if* desktop-offline is a product pillar
- **Context:** Desktop wants zero-install; SaaS needs Postgres. Dual dialect is a drift risk.
- **Decision:** **Postgres is canonical.** SQLite remains first-class **only** while local-first desktop is a shipped pillar, and only if the full suite runs on **both** dialects in CI. If desktop is merely a dev convenience, drop SQLite and run Postgres via Docker locally.
- **Alternatives:** Postgres-everywhere (Docker locally); embedded Postgres/pglite; DuckDB; LiteFS.
- **Tradeoffs:** Dual-dialect testing cost vs. desktop DX. Embedded Postgres packaging is fragile; DuckDB is OLAP (wrong workload); LiteFS solves replication we don't have.
- **Consequences:** One migration source of truth (Alembic) targeting Postgres; SQLite validated by contract + dialect tests.
- **Reconsider when:** desktop is deprecated (-> drop SQLite), or dialect drift incidents exceed the testing cost (-> drop SQLite, mandate Docker Postgres).

### ADR-0003: PostgreSQL as canonical datastore for hosted
- **Context:** Multi-user durability, concurrency, migrations, managed hosting (Supabase/Neon).
- **Decision:** Postgres over a transaction pooler at runtime; **direct/session endpoint for migrations** (DDL + advisory locks are unsafe on a transaction pooler).
- **Alternatives:** MySQL; NoSQL document store.
- **Tradeoffs:** Pooler nuances (no server-side prepared statements - handled in the adapter).
- **Consequences:** Relational integrity, `user_id` scoping, FTS available in-DB.
- **Reconsider when:** a workload emerges that Postgres serves poorly (unlikely for this domain).

### ADR-0004: Redis for shared state - *only in scale-out profiles*
- **Context:** Rate limits, lockouts, session cache, scheduler locks must be cluster-wide when >1 instance.
- **Decision:** KVStore is a **port**. In-process adapter for single-instance (desktop/self-hosted-single); Redis adapter when `horizontal_scale` capability is declared. Boot **fails fast** if a scale-out profile lacks a shared KV.
- **Alternatives:** Always Redis (heavy for desktop); DB-backed KV (slower, contention).
- **Tradeoffs:** Two adapters to contract-test.
- **Consequences:** Correct single-flight and abuse-control semantics per profile.
- **Reconsider when:** we adopt a different coordination primitive (e.g., managed queue with built-in locks).

### ADR-0005: Cookie sessions (not JWT) as the canonical browser auth
- **Context:** Browser clients; need revocation, CSRF defense, httpOnly secrecy.
- **Decision:** Server-side sessions in an httpOnly cookie + double-submit CSRF. Tokens introduced **only** for non-browser clients (CLI/API/native) when that need is real.
- **Alternatives:** JWT-in-localStorage (XSS-exposed, hard to revoke); JWT-in-cookie (still weaker revocation).
- **Tradeoffs:** Server-side session store required (already have KVStore/DB).
- **Consequences:** Strong revocation, step-up, device management.
- **Reconsider when:** a first-class programmatic API demands stateless bearer tokens - add them *alongside*, don't replace sessions for browsers.

### ADR-0006: Cloudinary via REST (no SDK) for object storage in hosted
- **Context:** Keep uploads out of the DB; free-tier CDN; avoid SDK bloat/lock-in.
- **Decision:** Storage is a **port**; local-disk and Cloudinary adapters. Cloudinary adapter uses signed REST over httpx.
- **Alternatives:** S3 (reserved, premium); DB blobs (rejected); vendor SDK (extra dependency, testability cost).
- **Tradeoffs:** Maintain signing logic ourselves (small, testable).
- **Consequences:** Swappable storage; offline-safe local adapter.
- **Reconsider when:** we need S3 features (lifecycle, presigned multipart) - add an S3 adapter.

### ADR-0007: External cron -> internal endpoint for background jobs (early hosted)
- **Context:** Low job volume early; simplest reliable scheduler.
- **Decision:** Jobs is a **port**. In-process loop (desktop), external cron hitting a token-guarded internal endpoint (early SaaS), **queue workers** when volume/reliability demand (ADR-0010 trigger).
- **Alternatives:** In-process scheduler in a multi-instance deploy (split-brain); distributed scheduler (premature).
- **Tradeoffs:** External dependency on a cron source.
- **Consequences:** No split-brain; simple ops early.
- **Reconsider when:** jobs need retries/backpressure/durability at volume -> migrate to queue workers.

### ADR-0008: Local-first desktop is a degenerate configuration, not a fork
- **Context:** Avoid two divergent code paths.
- **Decision:** Desktop = the SaaS architecture with fake/degenerate adapters and an auto-login owner who is a *real* principal scoped by `user_id`.
- **Alternatives:** Separate desktop codebase/branch (drift); mode branches (erosion).
- **Tradeoffs:** Desktop carries some multi-user machinery (cheap, dormant).
- **Consequences:** One path, tested once; "works locally, breaks hosted" largely eliminated.
- **Reconsider when:** never, without re-founding.

### ADR-0009: Selective Ports (not ports everywhere)
- **Context:** Abstraction has a cognitive cost.
- **Decision:** A port requires **≥2 real implementations OR a genuine external boundary faked in tests.** See the Ports Audit (§11).
- **Alternatives:** Full hexagonal (everything a port); no ports (infra bleeds into domain).
- **Tradeoffs:** Some concrete code the domain touches indirectly - acceptable, guarded by fitness functions.
- **Consequences:** Minimal, meaningful abstraction surface.
- **Reconsider when:** a concrete dependency grows a second real implementation -> promote to a port *then*.

### ADR-0010: Queue workers as the planned scaling step (not built yet)
- **Context:** Know the next scaling move without building it early.
- **Decision:** Keep the Jobs port shaped so a durable-queue adapter (Redis/SQS/pg-based) drops in without touching callers.
- **Reconsider when:** job failure/latency SLOs require durability, retries, and backpressure.

### ADR-0011: No Event Sourcing / CQRS
- **Context:** Domain is CRUD + AI transforms; no audit-by-replay requirement beyond an append-only audit log.
- **Decision:** Classic state persistence + a targeted audit log where compliance needs it.
- **Alternatives:** Event sourcing (huge complexity, eventual consistency, projections).
- **Tradeoffs:** No free time-travel; we add explicit version history where the product needs it (resume versions).
- **Reconsider when:** a regulatory or product need for full event replay emerges across the whole domain.

### ADR-0012: Choose the tenancy/scoping key now (`user_id`, owner = tenant-of-one)
- **Context:** Retrofitting the scoping key later means migrating every table + query - catastrophic.
- **Decision:** Every owned row is scoped by `user_id` today. All access flows through a **principal/tenant context**. If org-level tenancy arrives, `org_id` is added *beside* `user_id`, not instead of it.
- **Alternatives:** Unscoped local + scoped hosted (divergence, hosted-only bugs).
- **Tradeoffs:** Desktop carries a scoping column it "doesn't need."
- **Consequences:** Identical data path in all profiles; multi-tenancy is an additive future step.
- **Reconsider when:** introducing organizations -> new ADR for `org_id` composite scoping.

---

## 8. Evolution Roadmap

```
Stage 1: DESKTOP (local-first, single user)
   Infra: SQLite/local disk/in-proc everything - Identity: auto-owner session
   Same as always: domain, API contracts, scoping key
   Waits: Redis, queue, email, OAuth

Stage 2: HOSTED SaaS (single instance)
   + Postgres (pooled) + Cloudinary + external cron + real email + sessions/OAuth
   + fail-fast profile validation
   Waits: Redis (only when >1 instance), queue workers

Stage 3: SMALL TEAM / SCALE (multi-instance)
   + Redis (shared cache/locks/rate-limit) + horizontal replication
   Architecture unchanged - adapters swapped by profile
   Waits: org multi-tenancy, queue

Stage 4: ENTERPRISE / SELF-HOSTED
   + SSO/OIDC identity adapter + audit + data residency + customer infra adapters
   + organizations (org_id beside user_id - ADR required)
   Waits: marketplace/plugins

Stage 5: SCALE-OUT / PLATFORM
   + durable queue workers (ADR-0010) + read replicas + possible extraction of a
     hot module IF and only if replication is insufficient (ADR-0001 trigger)
```

**What remains identical across all stages:** the domain, application use-cases, API contracts, the scoping key, and the dependency rule. **What intentionally waits:** Redis, queues, orgs, SSO, plugins - each introduced only at its trigger.

---

## 9. Module Ownership

Each module owns its tables, its use-cases, and its public interface. **No module reaches into another module's tables** - it calls the owning module's application layer.

| Module | Owns | Public surface |
|---|---|---|
| **identity** | Authentication, sessions, CSRF, OAuth, step-up, users, device sessions | `IdentityProvider` port, `Principal`, `current_user()` |
| **resume** | Resume documents, parsing, versions/history, template settings | resume use-cases, `ResumeRepository` |
| **tailoring** | JD ingestion, tailoring pipeline, scoring, diffs | tailor use-cases (depends on resume + LLM port) |
| **applications** | Application tracker, stages, notes, duplication | application use-cases |
| **scheduling** | Reminders, interviews, agenda, ICS | scheduling use-cases (depends on Jobs + Mailer) |
| **notifications** | Persistent notifications, unread counter, prefs, digests | notification use-cases (depends on Mailer/KV - is *domain*, not infra) |
| **search** | Indexing, query, ranking | search use-cases (concrete Postgres FTS today) |
| **admin** | Stats, usage series, user lifecycle, audit | admin use-cases (read models) |
| **config** | LLM/feature/language/prompt settings, API-key store | runtime-config use-cases |
| **billing** *(future)* | Plans, entitlements, invoices | `PaymentProvider` port |
| **platform** | Composition root, profiles, capability validation, ports | wiring only - no business logic |

**Rule:** overlap is a design bug. If two modules want the same table, one of them owns it and exposes a use-case; the other calls it.

---

## 10. Dependency Rules

```
        Presentation --> Application --> Domain --> Ports
             |                |             ▲          ▲
             |                |             |          | implements
             +----------------+-------------+          |
                        (never skip inward)     Infrastructure
                                                        ▲
                                            wired by Composition Root
```

**Allowed**
- Presentation -> Application -> Domain -> Ports.
- Infrastructure -> Ports, Domain (to implement interfaces using domain types).
- Composition Root -> everything (it is the only omniscient place).
- Module A's Application -> Module B's Application (public use-cases only).

**Forbidden (CI-enforced, §18)**
- Domain -> Infrastructure, frameworks, SQLAlchemy, Redis, httpx, FastAPI, Cloudinary.
- Domain -> another module's internals.
- Presentation -> Infrastructure directly (must go through Application).
- Any circular dependency between modules.
- Reading `deployment_profile` anywhere except the Composition Root and boot validation.

---

## 11. Ports Audit

**Criterion (ADR-0009):** Port iff ≥2 real implementations **or** a genuine external boundary that must be faked in tests. Otherwise concrete.

| Candidate | Verdict | Reasoning |
|---|---|---|
| **Repository / Database** | **PORT** | SQLite + Postgres; also the primary test-fake boundary. |
| **Storage** | **PORT** | Local disk + Cloudinary (+ future S3). |
| **KVStore / cache** | **PORT** | In-process + Redis. |
| **Mailer** | **PORT** | Noop/log + SMTP/Resend. |
| **IdentityProvider** | **PORT** | Auto-owner + sessions + (future) SSO. External boundary (OAuth). |
| **LLMProvider** | **PORT** | Many providers already; external boundary; must fake in tests. |
| **Jobs / scheduler** | **PORT** | In-process + external cron + (future) queue. |
| **Clock** | **PORT** | Cheap, enormous testing/determinism win. |
| **PaymentProvider** | **PORT (future)** | Introduce with billing, not before. |
| **OAuth** | **CONCRETE (inside identity)** | An implementation detail of `IdentityProvider`, not a top-level port. |
| **Search** | **CONCRETE (for now)** | Single impl (Postgres FTS). Promote to a port only if we adopt Elastic/Meilisearch. |
| **Notifications** | **CONCRETE (domain)** | It *uses* Mailer/KV ports; it is business logic, not infrastructure. |
| **Logging** | **CONCRETE** | Cross-cutting; use a structured-logging facade, not a bespoke port. |
| **Metrics / tracing** | **CONCRETE** | Use OpenTelemetry; wrapping it adds nothing. |
| **Config** | **CONCRETE** | It is the *input* to composition, not an adapter. |
| **Secrets** | **CONCRETE** | Usually env/vault; abstract only with a real second backend. |

**Too early to abstract:** Search, Secrets, any "policy engine," any plugin host. Revisit at their triggers.

---

## 12. Complexity Budget

Every new **Port, Module, Layer, Policy, Service, or Abstraction** must pass **all** of:

1. **Second-implementation test.** Does a real second implementation exist now, or a genuine external boundary we must fake? (Ports)
2. **Deletion test (§13).** Can we describe exactly how to delete it and what breaks? If not, we don't understand it well enough to add it.
3. **Reader test.** Does it make the *next* engineer's job easier, or just the author's? Net cognitive load must go down.
4. **ADR test.** Is there an ADR with a reconsideration trigger?

**When NOT to abstract:** single implementation, no external boundary, "we might need it someday," symmetry for symmetry's sake, or to avoid a small amount of duplication. **Duplication is cheaper than the wrong abstraction.**

---

## 13. Deletion Strategy

Every abstraction ships with an **exit plan**. For each port/module, we can answer:
- **Can it be removed?** (What replaces it - concrete inlining, or a merge?)
- **How hard?** (Number of call sites, contract-test coverage.)
- **What breaks?** (Which profiles/adapters.)
- **Smell that it should never have existed:** a port with exactly one implementation for >1 release and no external boundary; an interface whose only implementer is a passthrough; a "manager/service" that only forwards calls.

**Policy:** a port that has had a single implementation for two releases and no test-fake justification is **auto-flagged for inlining**. Prefer deletion over keeping speculative seams.

---

## 14. Domain Boundaries

**The domain MAY know:**
- `Principal` (identity), `Tenant`/owner scope, `Permissions`/entitlements, domain entities and rules, `Clock` (via port).

**The domain MUST NEVER know:**
- SQLAlchemy, FastAPI, Redis, Cloudinary, httpx, HTTP request/response objects, the `deployment_profile`, which adapter it received, environment variables, or cookie/session mechanics.

**Litmus test:** the entire domain + application layer must be unit-testable with in-memory fakes, no network, no framework, no env. If a test needs a container, the boundary leaked.

---

## 15. Data Layer Philosophy

- **Canonical:** PostgreSQL (ADR-0003). One migration source of truth (Alembic), authored against Postgres.
- **SQLite:** first-class **only** while desktop-offline is a pillar (ADR-0002), and only with dual-dialect CI.
- **Migrations:** run on the **direct/session** endpoint (never the transaction pooler); idempotent; serialized by advisory lock in auto-migrate; **URL-encode credentials and escape `%` for config stores** (learned the hard way - now a fitness check).
- **Testing:** repository **contract tests** run against every dialect we ship; domain tests use in-memory fakes.
- **Future migration:** if SQLite is dropped, delete its adapter + dialect CI leg; no domain change. If read replicas arrive, add a read-routing adapter behind the Repository port.
- **What we reject:** DuckDB (OLAP), embedded Postgres (packaging fragility), LiteFS (replication we don't need), NoSQL (loses relational integrity + scoping guarantees).

---

## 16. Background Processing

```
In-process loop      -> desktop / single instance (simplest; dies with the process)
External cron -> API  -> early SaaS (no split-brain; token-guarded internal endpoint)
Durable queue        -> scale: retries, backpressure, durability, at-least-once (ADR-0010)
Distributed workers  -> only at scale we do not have; do not pre-build
```
**Justification to advance a stage:** the current mechanism cannot meet a *measured* reliability/latency/volume requirement. Not "it feels small."

---

## 17. Testing Philosophy

| Test type | Exists to prove | Runs against |
|---|---|---|
| **Unit** | Domain rules + use-cases are correct | In-memory fakes |
| **Adapter contract** | *Every* adapter honors its port's behavior identically | Real backends (Postgres, Redis, Cloudinary sandbox, SQLite) |
| **Integration** | Wiring + real I/O paths work end-to-end | A composed profile |
| **Architecture (fitness)** | Dependency rules are not violated | Static import graph |
| **Profile** | Each profile boots and its capability validation is correct | Each profile's composition |
| **Migration** | The Alembic chain applies cleanly forward on each dialect | Postgres (+ SQLite if shipped) |
| **Deployment / smoke** | A built artifact serves health + a golden path | Ephemeral env per profile |

**Highest-leverage, most-often-skipped:** **adapter contract tests.** One shared behavioral suite executed against each implementation is what actually prevents adapter drift. Treat it as mandatory (governance §19).

---

## 18. Architecture Fitness Functions (REQUIRED, CI-enforced)

Violations must be **impossible to merge**, not merely discouraged.

**CI must fail if:**
1. `domain/**` imports `sqlalchemy`, `fastapi`, `redis`, `httpx`, `cloudinary`, or any adapter package.
2. `application/**` imports a concrete adapter (must depend on ports only).
3. `presentation/**` imports infrastructure directly (must go through application).
4. Any **circular dependency** exists between modules.
5. `deployment_profile` / profile enum is referenced **outside** the composition root and boot-validation module.
6. A module reads another module's ORM models/tables directly.
7. Any **port contract test** fails, or a port has an implementation with **no** contract test.
8. Any **profile fails boot validation** in the profile test suite.
9. A DB URL is stored into the config parser without `%`-escaping (regression guard) / migrations are pointed at a transaction-pooler endpoint.
10. A new port is added without an ADR and ≥2 implementations (or a declared external-boundary exception).

**Tooling:** an import-graph linter (e.g., import-linter/grimp) for 1-6, the contract-test harness for 7, profile smoke tests for 8, targeted unit checks for 9-10.

---

## 19. Governance Rules

1. **Every port** needs ≥2 real implementations or a declared external boundary (ADR-0009).
2. **Every adapter** requires contract tests before merge.
3. **Every deployment profile** requires boot-validation + a profile smoke test.
4. **Every infrastructure dependency** requires an ADR with a reconsideration trigger.
5. **Every module owns its tables**; cross-module data access goes through use-cases.
6. **No module depends on infrastructure directly** (only on ports).
7. **No abstraction without measurable value** (Complexity Budget §12).
8. **Deployment-time config is validated fail-fast**; runtime config is typed and hot-swappable.
9. **Secrets never logged**; credentials rotated on exposure.
10. **ADRs are append-only**; supersede, never rewrite.

---

## 20. Golden Rules (Immutable)

1. **Business logic never knows where data comes from.**
2. **Infrastructure is replaceable.**
3. **Deployment is composition, not conditionals.**
4. **Business logic is shared across every profile.**
5. **There are no modes - only profiles, capabilities, and adapters.**
6. **Configuration is validated; the system fails fast, never degrades silently.**
7. **Every owned row is scoped by the tenancy key, in every profile.**
8. **Abstraction is earned; prefer deletion over speculative abstraction.**
9. **The domain is pure and framework-free.**
10. **Simple beats clever. Every time.**

Changing any Golden Rule is a re-founding event requiring a superseding ADR and explicit sign-off.

---

## 21. Long-Term Maintenance (5y / 10y / 20+ engineers)

**How this architecture resists erosion**
- Fitness functions (§18) turn architectural rules into build failures - discipline is not required, it is enforced.
- Selective ports keep the abstraction surface small, so onboarding cognitive load is low: a new engineer learns *one* module + the ports it touches.
- The dependency rule makes debugging directional: failures flow inward-to-outward along known edges.
- ADRs give every "why" a durable home; the 5-years-later engineer reads intent, not folklore.

**Supported futures (additively):** Desktop, SaaS, Enterprise, Self-hosted, Air-gapped (noop adapters + local everything), Offline-first (local adapters + sync later), Organizations/Multi-tenancy (org_id beside user_id - ADR), API-first (add token identity adapter), Plugins (composition seam via context resolution), Microservices (module extraction - last resort, ADR-0001 trigger).

**Future risks and mitigations**
| Risk | Mitigation |
|---|---|
| Abstraction leakage (SQL types through a "generic" port) | Ports expose domain types only; architecture tests. |
| Configuration explosion | Profiles cap the matrix; forbid arbitrary capability combos. |
| Profile drift (untested combos) | Profile smoke tests in CI. |
| Adapter drift | Shared contract-test suite per port. |
| Policy drift (billing/orgs added ad hoc) | New policy = new port/module + ADR + contract tests. |
| Testing explosion | Test the core once with fakes; test adapters in isolation - not the cross-product. |
| Erosion under deadline pressure | Fitness functions block the shortcut; the ADR makes the cost explicit. |

---

## 22. Final Architecture Review (self-challenge)

A last zero-trust pass. What did this document over-build, and is every section justified?

- **Cut candidates I explicitly reject as over-engineering right now:** a policy engine, a plugin host, a service registry, an org/billing abstraction, Search-as-a-port, Secrets-as-a-port, Logging/Metrics-as-ports. All correctly kept concrete or deferred (§11).
- **Ports list:** re-verified each against the two-implementations rule. `Clock` is the only "cheap" port kept without a second production implementation - justified purely by testing determinism, which is a genuine external boundary (real time). Accepted.
- **Profiles:** six profiles risk sprawl. Mitigation: `development`, `test`, `ci` are thin presets of `desktop`/`saas`, not new architecture. If they ever diverge structurally, that's a smell - collapse them.
- **Capability model:** guard against turning into a mini-DI framework. It is *data validated at boot*, nothing more. If it grows logic, stop.
- **Tenancy key (ADR-0012):** the one piece of "future-proofing" we do eagerly. Justified because it is the only decision that is *catastrophic* to retrofit; everything else is deferred. This asymmetry is deliberate and correct.
- **Biggest honest risk:** SQLite dual-dialect (ADR-0002). It is the one place we accept ongoing cost for product value. The exit (drop SQLite, Docker Postgres) is documented, so the debt is *managed*, not hidden.

**Conclusion:** the document favors *less* architecture than a naive "enterprise-grade" reading would produce - a modular monolith, a small set of contract-tested ports, explicit profiles, fail-fast config, one eagerly-decided tenancy key, and everything else deferred behind objective triggers. That restraint is the design. It should remain correct at 5+ years because it adds structure only where structure has paid for itself, and it enforces the few rules that matter with machines instead of memos.

---

*End of constitution. Amendments require a superseding ADR.*


---
---

# Part II - Amendments & Appendices (v1.1)

> These are **additive amendments** to the constitution above. Part I (§1-§22) is unchanged. Each amendment states what it adds, why it is not already covered, and which existing section it extends. Where an amendment extends a section, treat it as if inlined there; it is kept here only to preserve Part I's numbering and history. Same rules apply: Golden Rules remain immutable; changes require a superseding ADR.
>
> **Rejected candidates (with justification), so the decision is not relitigated:**
> - **Operational Runbooks** - *rejected as architecture content.* Outage response steps (DB/Redis/AI/email down, DR drills) are operational and change with infra/tooling; they belong in an on-call/ops runbook repo, not the constitution. The *architectural* residue - how the system behaves when a dependency fails - is captured as **Failure Semantics** in Appendix I.
> - **Canonical Repository Structure** - *rejected as architecture content.* Directory layout is implementation detail that drifts; the conceptual decomposition already lives in §9 (Module Ownership) and §2 (rings). Physical layout belongs in `CONTRIBUTING.md`.
> - **General Release Strategy (dev/CI/staging/prod/rollback)** - *rejected as operational.* CI/CD topology is not architecture. The one architectural sliver - migrations must be deploy-safe and rollback-safe - is added as Amendment G.

---

### Amendment A - Principle Precedence *(extends §1)*

**Why:** §1 lists principles and §20 lists Golden Rules, but neither says what wins when two collide (the real question in a code review is "security vs simplicity here - which yields?"). Without an explicit order, precedence is decided ad hoc and drifts by author.

**Why not already covered:** Golden Rule 10 ("simple beats clever") is a tie-breaker between two *simple-vs-clever* options, not an ordering across *different* concerns.

**Precedence (higher wins when principles conflict):**
```
1. Correctness        - a wrong answer fast/simple/cheap is still wrong
2. Security & Safety  - never trade user data/auth integrity for convenience
3. Simplicity         - the default bias; fewer moving parts
4. Maintainability    - optimize for the next engineer, not this commit
5. Performance        - only after the above; measure before optimizing
6. Extensibility      - earn it (Complexity Budget §12); never pre-build
7. Convenience        - the weakest force; never a justification on its own
```
**Tradeoff:** an explicit order occasionally forces the "less clever" choice. That is the intent. **Long-term impact:** design debates resolve in one sentence ("Security outranks Simplicity here"), which is exactly how a constitution should end arguments.

---

### Amendment B - Out-of-Scope, consolidated *(extends §1 "What this architecture intentionally does NOT solve")*

**Why:** §1 lists four non-goals; teams relitigate the rest quarterly. A consolidated, explicit list ends recurring debates.

**Additions to the existing non-goals (do not duplicate the four already listed):**
- **Offline sync / conflict resolution (CRDTs).** Desktop is local-first *single node*; we do not sync across devices. Revisit only if multi-device desktop becomes a product.
- **Distributed transactions / sagas.** The monolith uses one database and local transactions. No cross-service 2PC.
- **Multi-region writes / active-active.** Single write region; read replicas are the ceiling (Stage 5). Global write consistency is out.
- **Service mesh / sidecars.** Meaningless for a monolith; rejected until/if microservices ever exist (ADR-0001 trigger).
- **Real-time collaborative editing.** Single-owner documents; no OT/CRDT editor.
- **Self-serve plugin marketplace.** The composition seam must not *forbid* it (§2), but we do not build a plugin host now.

**Long-term impact:** any of these arriving is a **new ADR**, not an assumption baked into a PR.

---

### Amendment C - Runtime Invariants *(extends §18)*

**Why:** §18 enforces *static* dependency rules; §20 states *aspirational* Golden Rules. Neither asserts the *runtime data truths* that must always hold and can be checked by tests/assertions/DB constraints. These are the crash-barriers of the system.

**Why not already covered:** an import-graph linter cannot verify "every owned row has a tenancy key" - that is a schema constraint + test, a different enforcement surface.

**Invariants (each must be enforced by a DB constraint, a test, or a boot check - not by convention):**
1. **Tenancy:** every owned row has a non-null scoping key (`user_id`); enforced by NOT NULL + FK in migrations (hosted) and by repository contract tests.
2. **Principal:** every request that reaches the application layer carries a resolved `Principal` (real user or the owner); the identity middleware admits no request without one.
3. **Authorization at the boundary:** every owned-resource read/write filters by the caller's scope; contract tests assert cross-tenant access returns not-found, never another tenant's row.
4. **Auditability:** every security-sensitive mutation (auth, role/status, deletion, data reset) writes an audit record; tested per endpoint.
5. **Approved egress:** every outbound call to an external system goes through a port adapter (no ad-hoc `httpx`/SDK calls in domain/application); enforced by Fitness Function §18.1 + review.
6. **Bootable profiles:** every declared profile passes capability validation or refuses to start; enforced by profile smoke tests.

**Long-term impact:** these become the regression net that survives refactors - the truths a reviewer can point at.

---

### Amendment D - Module Lifecycle States *(extends §9)*

**Why:** With 20+ engineers, "is this module safe to depend on?" must be answerable at a glance. §9 defines ownership but not stability.

**States:** `Experimental` -> `Stable` -> `Deprecated` -> `Removed`.
- **Experimental:** may change public surface without an ADR; other modules depend on it at their own risk (must be labeled).
- **Stable:** public surface changes require an ADR + deprecation path; the default state.
- **Deprecated:** replacement exists; no new dependencies allowed; removal date/trigger recorded.
- **Removed:** deleted; tombstoned in the debt register (Appendix N) with the superseding ADR.

**Rule:** a `Stable` module may not depend on an `Experimental` one (would inherit instability). Enforceable as a metadata check in the module manifest. **Long-term impact:** controlled evolution without freezing innovation.

---

### Amendment E - Mutation Rights *(extends §9)*

**Why:** §9 says a module owns its tables and others "call the owning use-case," but does not state the sharper rule that *write authority* is exclusive. Read-sharing is where erosion sneaks in (a read grows a sneaky write).

**Rule:** **the owning module is the only writer of its tables.** Other modules may obtain data *only* through the owner's use-cases; they never `UPDATE`/`INSERT`/`DELETE` another module's tables, even when convenient, even for a "quick" denormalization.
```
Resume        owns Resume writes      (Search may read via a use-case; never writes Resume)
Identity      owns User/session writes (Tailoring/Applications never mutate Identity)
Applications  owns Application writes  (Scheduling references by id; never writes Applications)
```
**Tradeoff:** occasionally an extra use-case call instead of a direct join-write. Cheap. **Long-term impact:** each module's invariants are enforceable in one place; cross-module corruption becomes structurally impossible.

---

### Amendment F - Decision Checklists *(extends §12)*

**Why:** §12 gives the *criteria*; engineers still ask "do I introduce a module or just a function?" A one-screen checklist makes the Complexity Budget operational.

**Introduce a MODULE when:** it owns a distinct set of tables **and** a distinct vocabulary/use-cases, **and** at least one other module needs its behavior through a stable surface. Otherwise: a package inside an existing module.

**Introduce a PORT when:** ≥2 real implementations exist **now**, **or** it is an external boundary you must fake to unit-test. Otherwise: concrete class (revisit at the second implementation).

**Introduce an ADAPTER when:** a new backend for an *existing* port appears. Ship it **with** contract tests (§19) or it does not merge.

**Introduce a DEPLOYMENT PROFILE when:** a target has a *different required-capability contract* (not just different values). Otherwise: it is a preset of an existing profile.

**Write an ADR when:** you add an infra dependency, add/remove a port, add a profile, or change anything in §20. If you cannot name a reconsideration trigger, you do not understand the decision yet.

---

### Amendment G - Expand-Contract Migrations & Rollback Safety *(extends §15)*

**Why:** §15 covers dialects and where migrations run, but not the rule that makes zero-downtime deploys and rollbacks *possible*. This is the architectural half of "release strategy" (the operational half is rejected, see Part II header).

**Rule:** schema changes follow **expand -> migrate -> contract**:
1. **Expand:** add new columns/tables as nullable/optional; deploy code that writes both old and new where needed.
2. **Migrate:** backfill; switch reads to the new shape.
3. **Contract:** remove the old shape only after the previous release is fully retired.

**Consequence:** every deploy is backward-compatible with the immediately previous release, so a rollback never meets a schema it cannot read. Destructive, non-reversible migrations require an explicit ADR. **Long-term impact:** rollback becomes a routine, safe operation instead of a data-loss event.

---

### Amendment H - Architecture Change Process & Periodic Review *(extends §19)*

**Why:** §7 defines the ADR *artifact* and §19 the governance *rules*, but not the *flow* a change travels or how decisions get re-examined. Without a cadence, "reconsider when" triggers are never actually checked and ADRs quietly rot.

**Change flow:**
```
Proposal (issue/RFC) -> ADR (draft) -> Review (owner + 1 non-owner) -> Adopted
        +-> Implementation -> Fitness Functions (§18) green -> Merge -> Periodic Review
```
**Periodic review (lightweight, quarterly):** walk the open ADRs and the registers (Appendices M, N); for each, ask "has its reconsideration trigger fired?" Close, supersede, or keep. This is the single mechanism that keeps the constitution honest over years. **Long-term impact:** decisions have a heartbeat; stale ones are found on a schedule, not by accident during an incident.

---

## Appendix I - Non-Functional Posture, Observability & Failure Semantics

> Architecture-level only. Concrete numbers are **defaults/ceilings that shape design**, not per-deployment SLAs (those live in deployment config). Extends the spirit of §4/§6.

### I.1 Non-Functional Posture *(candidate 2, accepted, trimmed)*
- **Bounded, cancellable AI work.** Every LLM call has a timeout and is cancellable; no unbounded waits. (Ceiling already in place: request timeout bounded to [30s, 30min], synchronized across frontend proxy and backend.)
- **No unbounded concurrency.** Expensive operations (PDF render, LLM) are capped and shed load with backpressure/queueing rather than exhausting the process.
- **Fail-fast boot.** A profile validates its capabilities at startup and refuses to serve if unmet; no half-configured runtime.
- **Testability as an NFR.** Domain + application must be unit-testable with zero infrastructure (the §14 litmus). This is a *first-class* non-functional requirement, not a nicety.
- **Statelessness of the web tier.** All session/rate-limit/lock state lives in a port (KVStore/DB), never in process memory in scale-out profiles - the precondition for horizontal replication.
- **Graceful startup/shutdown.** In-flight requests drain on shutdown; background loops stop cleanly.

**Why not already covered:** these were implicit in adapters/ADRs; stating them as posture prevents a future change from silently violating (e.g., stashing rate-limit counters in memory).

### I.2 Observability Philosophy *(candidate 17, accepted, tight)*
- **Correlation is mandatory.** Every request carries a request/correlation id, propagated through logs and any downstream call; every log line for a request is joinable on it. (Already practiced - logs emit `request_id` and `principal_user_id`; this makes it a rule.)
- **Structured logs, secrets never logged** (reinforces §19). Log the *shape* of failures, not credential values.
- **Three signals, ecosystem-native (no bespoke ports - see §11):** structured **logs**, **metrics**, **traces** via standard facades (e.g., OpenTelemetry).
- **SLO vs tuning distinction:** an SLO (e.g., availability target, p95 latency objective) is an *architectural commitment*; the thresholds/alert values are *deployment tuning*. The constitution names the SLOs that exist; values live in ops config.

### I.3 Dependency Failure Semantics *(architectural residue of rejected candidate 12)*
Each port declares, by profile, its behavior when its backend is unavailable - **fail-fast** or **degrade-to-safe**:
```
Database        : FAIL   (no meaningful service without it)
IdentityProvider: FAIL   (never fail open on auth)
LLMProvider     : DEGRADE (surface a clear error/queue; the rest of the app still works)
Storage         : DEGRADE where possible (defer/queue upload) else clear error
Mailer          : DEGRADE (queue/log; never block the core flow)
KVStore (cache) : DEGRADE (miss -> recompute; but scale-out rate-limit correctness may FAIL-safe)
Jobs            : DEGRADE (jobs delayed, not lost - durability is the queue adapter's job)
```
**Rule:** a dependency's failure mode is a property of its adapter+profile and must be tested. Auth and data integrity **never** fail open.

---

## Appendix J - AI Architecture *(candidate 10 - highest priority; incl. candidate 13 Cost)*

> The system is AI-first, yet Part I treats AI as a single `LLMProvider` port. That is the correct *boundary* but an insufficient *architecture*. This appendix defines the AI subsystem at the architecture level only - **not** prompt-engineering content.

### J.1 Position in the architecture
AI is **application-layer orchestration over a domain-owned pipeline**, using the `LLMProvider` port for egress. The domain owns *what* a good tailoring/scoring result is; the provider owns *how* tokens are produced. The domain never imports an SDK (§14).

```
Application: TailoringService -> Prompt Assembly -> LLMProvider (port)
     |                |                                  |
 domain rules   PromptTemplate(versioned)         provider adapters (OpenAI/Anthropic/...)
 (scoring,       + Context Builder                 structured-output + retry + fallback
  grounding)     (token-budgeted)
```

### J.2 Principles
1. **Provider-agnostic core.** All model access is behind `LLMProvider`; swapping providers/models is runtime config (§6), never a code change.
2. **Prompts are versioned artifacts.** A prompt has an id + version; changing a shipped prompt is a new version, not an edit. This enables evaluation, rollback, and reproducibility. (User-custom prompts are validated for required placeholders before use.)
3. **Structured outputs are contracts.** AI results the system consumes are parsed into typed structures and **validated**; a malformed model response is a handled error, never trusted blindly. Invalid output -> bounded retry -> typed failure.
4. **Grounding over generation.** Tailoring rewrites/reorders the user's real content; it must not invent experience. Grounding is a domain rule, enforced by the pipeline and evaluation, independent of provider.
5. **Determinism policy.** AI is non-deterministic by nature; the architecture isolates it so the *rest* of the system stays deterministic and testable. Tests use a fake `LLMProvider`; never a live model in unit/CI paths.
6. **Retry & fallback ladder.** Transient errors -> bounded retry with backoff; hard failure -> optional fallback model/provider (runtime-configured) -> typed error surfaced to the user. Every step bounded and cancellable (Appendix I.1).
7. **Context management.** A context builder assembles inputs under an explicit **token budget**; truncation/selection is deliberate and testable, not incidental.
8. **Safety.** User content and model output are treated as untrusted: no execution of model output, sanitize before render, and never let model text drive privileged actions (tool-calling, if added, is an explicit allow-listed port with its own ADR).

### J.3 Cost Governance *(candidate 13, folded here)*
Cost is an architectural constraint for an AI product, not an afterthought:
- **Budgets & ceilings.** Per-user and per-operation rate limits already exist for expensive endpoints; the architecture treats a **token/cost ceiling** as a first-class limit alongside them.
- **Cache hierarchy.** Deterministic/repeatable AI results are cacheable behind the KVStore port (e.g., identical JD+resume -> reuse). Caching is an optimization, never a correctness dependency.
- **Cost observability.** Token/cost per operation is a metric (Appendix I.2), so regressions (a prompt that doubles tokens) are visible.
- **Provider fallback for cost.** Model/provider selection is runtime config, enabling cheaper defaults and cost-based routing without a deploy.

### J.4 Evaluation
- **Offline eval harness** exists as a first-class test type (extends §17): scorers/evals run against fixtures to catch grounding and quality regressions when prompts or models change. Prompt/model changes should be gated on eval results, not vibes.

**Reconsideration trigger (new ADR when any fires):** adopting tool/function-calling; adding a vector store/RAG; introducing fine-tuned/self-hosted models; multi-step agentic orchestration. Each is a new capability with its own port/ADR - not an organic creep of the current pipeline.

---

## Appendix K - Security Architecture & Trust Boundaries *(candidate 11)*

> Part I has security *decisions* scattered (ADR-0005 sessions, §19 secrets, §5 security capabilities, §18 scoping). This appendix gives the consolidated *architectural* view - trust boundaries, the authZ model, key rotation, threat posture - **without** re-documenting mechanisms already decided.

### K.1 Trust boundaries
```
[ Browser / untrusted client ]
        |  httpOnly session cookie + CSRF (ADR-0005)   <- boundary 1: authenticate + verify CSRF
        ▼
[ Presentation ] -- resolves Principal --> [ Application ]   <- boundary 2: authorize (scope check)
        |                                        |
        |                                        ▼
        |                                 [ Domain ] (no I/O)
        ▼
[ Infrastructure adapters ] -- credentialed egress --> [ External systems ]  <- boundary 3: approved egress only
```
- **Boundary 1 (edge):** authenticity + CSRF; all input treated as hostile.
- **Boundary 2 (app):** authorization - every owned-resource access is scope-filtered (Invariant C.3). Hiding UI is never the boundary; the server always enforces.
- **Boundary 3 (egress):** external calls only via port adapters with managed credentials (Invariant C.5).

### K.2 Authorization model
- **Principal-centric.** Authorization is a function of the `Principal` (identity + role + entitlements) and the resource's scope, evaluated in the application layer - it is *business logic*, not middleware trivia.
- **Roles vs entitlements.** Role (`user`/`admin`) gates admin surfaces; **entitlements/capabilities** gate features (e.g., which AI features) and are attached to the principal, not read from the deployment profile (§14).
- **Deny by default.** Absent an explicit grant + matching scope, access is denied (returns not-found for owned resources to avoid existence disclosure).

### K.3 Secret & key management
- **Secrets are deployment-time, validated, never logged** (§6, §19). Hosted **fails fast** without required secrets.
- **Rotation is designed-in:** session-signing supports an overlap window (current + previous secret) so keys rotate without mass logout. Encrypted at-rest secrets (e.g., provider API keys) are stored via the key store, never returned to clients after save.
- **Exposure response:** a leaked secret is rotated, not hidden; credentials that appear in logs/chat are treated as compromised.

### K.4 Threat posture (architecture-level)
Primary threats the architecture actively mitigates: session theft/XSS (httpOnly + sanitize + CSP as an edge control), CSRF (double-submit), cross-tenant access (scoping invariant), enumeration (uniform responses), abuse/DoS (rate limits + concurrency caps + backpressure), injection (parameterized queries via the repository), and untrusted AI output (Appendix J.8). **Out of scope:** nation-state/physical attacks, and anything requiring a formal compliance regime until an enterprise ADR introduces it.

**Reconsideration trigger:** SSO/OIDC, org-level RBAC, or a compliance regime (SOC2/HIPAA/GDPR data-residency) - each a new ADR extending this appendix.

---

## Appendix L - API Evolution Policy *(candidate 8)*

> Part I fixes API *contracts as identical across profiles* but not how they *evolve*. For a system with a web client today and possible API-first clients later, evolution rules prevent silent breakage.

- **Versioning:** the HTTP surface is versioned by path prefix (`/api/v1`). Breaking changes ship under a new version; the old version remains until its consumers are retired.
- **Backward compatibility within a version:** additive only - new optional fields, new endpoints. Never repurpose or remove a field within a version (pairs with expand-contract, Amendment G).
- **Deprecation:** a deprecated endpoint/field is announced, documented, and given a removal trigger/date; removal is a new version or an ADR.
- **Uniform error model:** a single error envelope (`{ error: { code, message, details? } }`) with a machine-readable `code`; clients branch on `code`, not on prose. (This is why ad-hoc `status N` strings were removed.)
- **Idempotency:** unsafe retried operations (create-type mutations that a client may retry after a timeout) must be safe to repeat - via natural keys or idempotency handling - so a network retry never double-creates. Required for any future public/programmatic API.
- **Rate-limit contract:** expensive endpoints advertise limits; `429` is a documented, handled response with retry guidance (already surfaced in the client).

**Long-term impact:** the frontend, and any future CLI/partner integration, can evolve against a stable, predictable contract.

---

## Appendix M - Living Risk Register *(candidate 4)*

> Distinct from §21's mitigation table (which is descriptive). This is a **living, owned** register reviewed on the §H cadence. Architecture-level risks only. Seeded with real current risks; add rows via review, don't let it become a dumping ground.

| ID | Risk | Likelihood | Impact | Mitigation | Owner (role) | Review trigger |
|----|------|-----------|--------|------------|--------------|----------------|
| R1 | SQLite/Postgres dialect drift causes hosted-only bugs | Med | High | Dual-dialect contract+CI tests (ADR-0002) | Data owner | Any dialect-specific incident; or desktop deprecated |
| R2 | In-process KVStore used in a scale-out deploy (split state) | Med | High | Fail-fast validation for scale-out profiles (ADR-0004) | Platform owner | First multi-instance deploy |
| R3 | External-cron dependency skipped -> jobs silently stop | Med | High | Boot check requires job token; monitor last-run; queue adapter planned (ADR-0007/0010) | Platform owner | Missed-job incident; volume growth |
| R4 | Single AI provider outage halts core value | Med | Med | Provider/model fallback ladder (Appendix J.6), degrade-not-crash (I.3) | AI owner | Provider outage; cost spike |
| R5 | Tenancy-scope regression leaks cross-user data | Low | Critical | Scoping invariant + contract tests (C.1/C.3) | Identity owner | Any authz change |
| R6 | Secret exposure (shared creds, logs) | Med | High | Rotation window, no-log rule, fail-fast (K.3) | Security owner | Any suspected exposure |
| R7 | AI cost runaway (prompt/model change doubles tokens) | Med | Med | Cost ceilings + cost metric + eval gate (J.3/J.4) | AI owner | Cost metric regression |

---

## Appendix N - Technical Debt Register *(candidate 5)*

> Intentional, accepted debt only - with an explicit exit. Aligns with §13 (Deletion Strategy). Accidental debt goes to the issue tracker; this register is for debt we *chose* and must not forget.

| ID | Accepted debt | Reason | Tradeoff | Exit strategy | Removal trigger |
|----|---------------|--------|----------|---------------|-----------------|
| D1 | Dual datastore (SQLite + Postgres) | Zero-install desktop DX | Dialect drift risk; dual CI | Drop SQLite adapter + dialect CI leg | Desktop deprecated, or R1 cost > value (ADR-0002) |
| D2 | In-process KVStore adapter | Zero-config single instance | Not shared across workers | Redis adapter (exists) selected by profile | First scale-out (ADR-0004) |
| D3 | External-cron scheduler (not durable queue) | Simplicity at low volume | No retries/backpressure/durability | Queue-worker adapter behind Jobs port | Reliability/volume SLO breach (ADR-0010) |
| D4 | Search kept concrete (Postgres FTS), not a port | Single implementation today | Refactor needed if we swap engines | Promote to `SearchIndex` port at 2nd impl | Adopt Elastic/Meilisearch (§11) |
| D5 | Six deployment profiles incl. dev/test/ci as presets | Explicitness | Profile sprawl risk | Collapse presets if they never diverge structurally | Presets remain thin for 2 releases -> merge (§22) |

**Rule:** every row has an exit and a trigger. A debt with no exit is not "accepted debt" - it is a design flaw and must be fixed, not filed.

---

*End of Part II. Part I remains the immutable core; these amendments evolve with the system under the same ADR + fitness-function discipline.*
