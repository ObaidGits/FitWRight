# FitWright - Implementation Plan (Architecture Migration Roadmap)

> Companion to `ARCHITECTURE.md` (the frozen source of truth). This document does **not** restate or amend the architecture. It answers one question: **"Exactly how do we migrate the current codebase into the target architecture while the app keeps working at every step?"**
>
> Execution style: incremental, production-safe, rollback-friendly, verifiable, dependency-ordered. Feature work continues in parallel wherever a phase does not touch the same seam.
>
> **Grounding note (zero-trust):** every claim about "current state" below was derived by reading the codebase, not assumed. Where the code already satisfies the target, the plan says *formalize/verify*, not *build*.

---

## 1. Executive Summary

### Purpose
Move the existing FastAPI modular backend (and its Next.js frontend) from an **implicit, service-locator-wired, `single_user_mode`-boolean** shape into the target: a **modular monolith with explicit deployment profiles, capability validation, a single composition root, a small set of contract-tested ports, and CI-enforced fitness functions.**

### Goals
1. Replace the `single_user_mode` boolean as a *behavioral axis* with **explicit deployment profiles + capability validation** (ARCHITECTURE §3-§5).
2. Introduce **one composition root** that builds and injects adapters, retiring scattered `get_*()` global singletons (§2, §10).
3. Formalize the adapters that already exist into **named ports with contract tests** (§11, §17).
4. Collapse the identity fork behind an **`IdentityProvider` port** (§9 identity, §14).
5. Make the dependency rules **mechanically enforced** (import-linter + profile boot tests + contract tests) (§18).
6. Delete every temporary shim and scattered conditional by the end (§13, §20).

### Non-goals (explicitly out of scope for this migration)
- No new product features are required by this plan (feature work proceeds independently).
- No microservices, event sourcing, org/multi-tenancy, queue workers, or plugin host (ARCHITECTURE §1 out-of-scope; deferred by trigger).
- No datastore change beyond what ADR-0002/0003 already decide; no schema redesign.
- No rewrite of business logic - the domain/services stay behaviorally identical.

### Success criteria
- All fitness functions green (§18 of ARCHITECTURE); zero `deployment_profile`/mode reads outside the composition root + boot validation.
- Every port has ≥2 implementations or a declared external boundary, each with contract tests.
- Every profile boots or fails fast, verified by profile smoke tests.
- No compatibility shims, no `get_*()` service-locators, no scattered `single_user_mode` reads remain (Deletion Plan §20 complete).
- The app is releasable at the end of **every** phase; no phase requires a big-bang cutover.

### Expected duration
Indicative for a 2-3 engineer team, feature work continuing in parallel: **~10-14 weeks**, phase-gated (not calendar-gated - a phase ships when its exit criteria pass). Phases 1-5 are the critical path (~6-8 weeks); 6-10 are hardening/cleanup and parallelizable.

### Migration philosophy
**Strangler-fig, inward-out.** Introduce the new seam (profiles -> composition root -> ports) *beside* the existing wiring, route call sites through it incrementally behind thin compatibility shims, then delete the old wiring once nothing references it. Never a parallel rewrite; never two critical seams cut at once.

### Zero-downtime philosophy
Each phase is **additive first, subtractive last**. New structures land dormant and backward-compatible; call sites migrate; only then is the old path deleted. Expand-contract for any schema touch (ARCHITECTURE Amendment G). The app boots and serves on every commit to `main`.

### Rollback philosophy
Every phase is a **revertible unit** with an explicit trigger and procedure. Because new code lands behind shims/flags and old code is deleted only after cutover, rollback is almost always "revert the PR" with no data implications. Schema changes are expand-only until a later contract phase, so a rollback never meets an unreadable schema.

### Migration at a glance (one-minute overview)
*Purpose:* let any engineer grasp the whole migration in under a minute. *How to use:* the linear spine below is the mental model; §5 is the authoritative dependency graph, §6 the detail. Each arrow is a phase that must reach its exit criteria before the next begins (except where §16 allows parallelism).

```
Current State
   v  P0  Safety harness (tests + dual-dialect CI)
   v  P1  Deployment profiles + capability validation
   v  P2  Fitness-function scaffold (ratcheting)
   v  P3  Composition root            <- critical seam #1
   v  P4  Ports + contract tests
   v  P5  Identity port               <- critical seam #2
   v  P6  Domain-purity enforcement
   v  P7  Module boundaries + mutation rights
   v  P8  Frontend profile alignment
   v  P9  Cleanup (delete shims/globals/flags)
   v  P10 Hardening scaffolds (seams only)
   v  P11 Production observation      <- migration closes here
Migration Complete -> archive this plan; ARCHITECTURE.md governs thereafter
```
*Completion:* the migration is **not** complete at code-merge (P10) - it closes only after P11 (§6) confirms the deployed system is healthy and the Final Audit (Appendix D) passes.

---

## 2. Current State Analysis

### Current architecture (as-built, verified)
- **Backend:** FastAPI modular monolith under `apps/backend/app`. Coherent modules already exist: `auth/`, `admin/`, `notifications/`, `scheduling/`, `search/`, `versions/`, `jd/`, `events/`, `retention/`, `prompts/`, `resilience/`, `services/` (AI pipeline), `storage/`, plus cross-cutting `config.py`, `database.py`, `db_engine.py`, `repository.py`, `llm.py`, `observability.py`, `main.py`.
- **Frontend:** Next.js app with route groups `(app)`, `(auth)`, `admin`, `(marketing)`; session provider already reads a `NEXT_PUBLIC_SINGLE_USER_MODE` flag.
- **Config:** a single pydantic `Settings` with **fail-fast validation** (`_validate_auth_surface`) - already aligned with the "fail fast" principle.
- **Infrastructure adapters already present:** `build_kvstore`/`build_email_sender`/`build_captcha_verifier`/`build_breached_password_check` (`auth/runtime.py`), `get_storage_provider` with Local + Cloudinary (`storage/provider.py`), `make_async_engine`/`make_sync_engine` (SQLite + Postgres, `db_engine.py`), LLM abstraction (`llm.py`).

### Current technical debt (relative to target)
- **D-Wiring - service-locator singletons.** Adapters are obtained via scattered lazy globals: `get_kvstore()`, `get_storage_provider()`, `get_session_service()`, `get_rate_limiter()`, `get_audit_service()`, `get_token_service()`, each with a `global _x` + inline `from app.config import settings` + a `reset_*()` test hook. This is the single biggest divergence from the target composition root.
- **D-Mode - boolean as behavioral axis.** `single_user_mode` is read in ~7 files (`config.py`, `auth/principal.py`, `routers/health.py`, `main.py`, `db_engine.py`, `diagnostics.py`, `routers/resume_wizard.py` docstring). Most reads are legitimate (identity fork, config validation); a few are behavioral forks that should live behind a profile/port.
- **D-Ports - informal.** Adapters exist but there is **no explicit port/interface layer** and **no contract-test harness**; "port-ness" is by convention.
- **D-Fitness - none.** No import-linter / architecture tests; dependency direction is maintained by discipline only.
- **D-Profiles - none.** No explicit deployment-profile concept; deployment shape is inferred from individual env vars + the mode boolean.

### Current coupling
- **Low domain coupling:** `services/` are import-pure (no ORM/framework). Good - the inner rings are close to target already.
- **Moderate infrastructure coupling:** call sites import `get_*()` singletons directly (service locator), so infra selection logic is smeared across modules rather than centralized.
- **Identity coupling:** `auth/principal.py` is the true behavioral fork (owner-vs-session); this is expected and will be formalized, not removed.

### Current risks
- Service-locator globals make **runtime composition and testing** harder and hide the real dependency graph.
- Capability inference from env vars risks **silent degradation** (ARCHITECTURE §3) - not yet fail-fast for every capability.
- Dual-dialect (SQLite/Postgres) drift is untested in CI (ARCHITECTURE Debt D1).

### Current strengths (do not disturb)
- Clean module decomposition; pure services; fail-fast config validation; existing adapter set; working auth/session/CSRF; existing test suite (backend ~1476 tests, frontend ~347) - a strong safety net for refactoring.

### Current blockers
- None hard. The migration can begin immediately; Phase 0 establishes the safety harness that makes the rest low-risk.

### Current migration complexity
**Moderate, front-loaded.** The high-blast-radius work is centralizing wiring (Phase 3) and the identity port (Phase 5). Everything else is mechanical formalization guarded by tests introduced in Phases 0/2.

---

## 3. Target State - What "Done" Means

Defined normatively in `ARCHITECTURE.md`; **not restated here.** "Done" for this migration =:

- Profiles + capability validation are the only place deployment differs (§3-§5); `single_user_mode` no longer read as a behavioral branch anywhere but the identity-adapter selection + config validation.
- A single composition root builds every adapter; no `get_*()` service-locator globals remain (§2, §10).
- The port set from ARCHITECTURE §11 exists as interfaces with ≥2 impls each and passing contract tests (§17, §19).
- The identity fork is an `IdentityProvider` port with `owner-auto` and `session` adapters (§9).
- All fitness functions from ARCHITECTURE §18 are enforced in CI and green.
- Zero compatibility shims, zero dead adapters, zero scattered conditionals (§13, §20).

If a behavior differs from ARCHITECTURE at completion, ARCHITECTURE wins and the code is the bug.

---

## 4. Migration Principles

**WHY:** these principles make the roadmap safe to execute over months with many hands, and they map 1:1 to how each phase is structured.

1. **Incremental over big-bang.** Every phase ships to `main` independently.
2. **Backward compatibility first.** New seams land dormant; old paths keep working until cutover.
3. **One source of truth at a time.** During a cutover, exactly one place *owns* a decision; shims delegate to it (never duplicate logic).
4. **Small PRs.** A PR does one reversible thing (see §17 sizing).
5. **Feature flag / shim before deletion.** Never delete an old path in the same PR that introduces the new one.
6. **Always deployable.** If `main` can't boot and serve all profiles, the PR is not done.
7. **Every phase independently releasable.** No phase depends on a *future* phase to be correct.
8. **No dead-code accumulation.** Each shim is created with a removal trigger (§20); shims are liabilities, not features.
9. **Delete old code quickly.** Cutover and deletion are scheduled within the same phase whenever safe.
10. **One critical migration at a time.** Never cut wiring (Phase 3) and identity (Phase 5) in the same window.

### 4.1 Never-Do Rules (immutable during the migration)
*Purpose:* the positive principles above say what to do; these say what will break the migration if violated. *Why it exists:* prohibitions are unambiguous under deadline pressure where guidance gets rationalized away. *How it is used:* a PR violating any of these is a **NO-GO** (§12.1) regardless of green tests.

- **Never** migrate two critical seams simultaneously (composition + identity) - see §16.
- **Never** remove old code before the new path is verified in the same phase (§8, §20).
- **Never** skip contract tests for an adapter change (§11, §18).
- **Never** bypass a phase's rollback trigger/procedure (§13).
- **Never** bypass a phase's exit criteria to "keep momentum" (§6, §12.1).
- **Never** merge architecture debt without an ADR **and** a Deletion-Plan row (§15, §20).
- **Never** introduce temporary code without a removal trigger (§8, §20).
- **Never** weaken or allow-list a fitness function for convenience - the ratchet only tightens (§18, ARCHITECTURE §18).
- **Never** let the client capability signal become a security boundary - server always enforces (ARCHITECTURE §K.1).

*Verification:* code review + fitness functions; any violation blocks merge.

---

## 5. Dependency Graph

**WHY dependency order, not feature order:** each layer is consumed by the ones below it; migrating a consumer before its provider forces throwaway scaffolding. The graph is derived from what actually imports what.

```
                +-----------------------------+
   Phase 0      |  Safety Harness (tests, CI)  |  guards everything after it
                +--------------+--------------+
                               ▼
   Phase 1      +-----------------------------+
                |  Config -> Profiles +         |  every wiring decision reads this
                |  Capability Validation       |
                +--------------+--------------+
                               ▼
   Phase 2      +-----------------------------+
                |  Fitness Functions scaffold  |  introduced early, enforced incrementally
                |  (import-linter, profile CI) |
                +--------------+--------------+
                               ▼
   Phase 3      +-----------------------------+
                |  Composition Root            |  builds adapters once; shims delegate here
                +--------------+--------------+
                               ▼
   Phase 4      +-----------------------------+
                |  Ports formalized +          |  interfaces extracted from existing adapters
                |  Contract tests              |
                +--------------+--------------+
                               ▼
   Phase 5      +-----------------------------+
                |  Identity Port (owner/       |  the one behavioral fork, consolidated
                |  session adapters)           |
                +--------------+--------------+
                               ▼
   Phase 6      +-----------------------------+
                |  Domain purity enforced      |  strict import rules turned ON
                +--------------+--------------+
                               ▼
   Phase 7      +-----------------------------+
                |  Module boundaries +         |  cross-module access via use-cases only
                |  mutation-rights rule        |
                +--------------+--------------+
                               ▼
   Phase 8      +-----------------------------+
                |  Frontend profile alignment  |  UI capability-driven, not mode-boolean
                +--------------+--------------+
                               ▼
   Phase 9      +-----------------------------+
                |  Cleanup (delete shims,      |  remove all compatibility layers
                |  globals, scattered flags)   |
                +--------------+--------------+
                               ▼
   Phase 10     +-----------------------------+
                |  Hardening scaffolds         |  observability correlation, cost metrics,
                |  (no premature infra)        |  Jobs-port shape for future queue adapter
                +-----------------------------+
```

**Justification of each edge:**
- **0 -> 1:** you cannot safely refactor wiring without a characterization-test net first.
- **1 -> 2:** fitness functions need the profile concept to assert "no mode reads outside composition."
- **2 -> 3:** the composition root is high-blast-radius; land it with guardrails already watching.
- **3 -> 4:** ports are only meaningful once a single place instantiates their implementations.
- **4 -> 5:** identity is *a* port; formalizing the generic port machinery first makes identity a routine application of it.
- **5 -> 6:** with identity centralized, the remaining infra imports in inner rings can be proven absent and locked.
- **6 -> 7:** module mutation-rights enforcement assumes the inner rings are already clean.
- **7 -> 8:** frontend alignment depends on the backend exposing a stable capability/profile signal.
- **8 -> 9:** cleanup only after every consumer uses the new seam.
- **9 -> 10:** optimization last, on a clean base.

---

## 6. Migration Phases

> Each phase lists Purpose, Goals, Scope, Files affected, Dependencies, Risk, Rollback, Verification, Success criteria, Exit criteria, Effort, Common mistakes. Effort is relative T-shirt sizing, not calendar. A phase is "done" only when its **exit criteria** pass.

---

### Phase 0 - Preparation & Safety Harness

- **Purpose:** create the net that makes every later refactor low-risk and reversible.
- **Goals:** baseline green suite; characterization tests around the seams we will move (wiring, identity, config validation); CI capable of running the dual-dialect and profile matrices later.
- **Scope:** test scaffolding + CI config only. No production code behavior changes.
- **Files affected:** `apps/backend/tests/**` (new characterization tests), CI workflow files under `.github/workflows/`, `pyproject.toml` (dev deps: import-linter, contract-test harness scaffolding). No `app/**` behavior change.
- **Dependencies:** none.
- **Risk:** Low. Worst case a flaky test is added.
- **Rollback:** revert the PR(s); no runtime impact.
- **Verification:** full backend + frontend suites green; new characterization tests pin current behavior of `get_effective_user_id`, config validation errors, and adapter selection.
- **Success criteria:** a documented, reproducible "green baseline" commit hash.
- **Exit criteria:** CI runs unit + integration on every PR; a nightly (or opt-in) job runs the suite against **both** SQLite and Postgres (seeds ADR-0002 dual-dialect requirement).
- **Effort:** S-M.
- **Common mistakes:** skipping characterization tests ("we have coverage") - the specific behaviors we're about to move must be pinned *before* moving them.

---

### Phase 1 - Deployment Profiles + Capability Validation

- **Purpose:** introduce the explicit profile concept that replaces `single_user_mode` as the *deployment axis* (ARCHITECTURE §3-§5), without changing behavior yet.
- **Goals:** a typed, immutable `DeploymentProfile` derived from config; a capability set computed + **validated fail-fast** per profile; `single_user_mode` becomes a *derived* view of the profile, not the source of truth.
- **Scope:** additive. Map current `SINGLE_USER_MODE=true/false` -> `desktop`/`saas` profiles so existing `.env` files keep working unchanged (backward-compatible).
- **Files affected:** `app/config.py` (add profile derivation + capability validation; keep `single_user_mode` property as a compatibility read), a new `app/platform/profiles.py` + `app/platform/capabilities.py` (new `platform` module per ARCHITECTURE §9).
- **Dependencies:** Phase 0.
- **Risk:** Medium - config validation is boot-critical; a wrong rule bricks startup. Mitigated by characterization tests on validation (Phase 0) and fail-fast being the desired behavior.
- **Rollback:** revert; `single_user_mode` reads still work because they were never removed.
- **Verification:** profile smoke test - each profile either boots or fails fast with a precise capability error; existing validation tests still pass.
- **Success criteria:** every current `.env` maps to exactly one profile; `settings.single_user_mode` is now computed from `profile`, not read directly from env.
- **Exit criteria:** capability validation covers DB, identity strategy, scheduler, shared-cache, email, storage; documented profile->capability matrix matches ARCHITECTURE §4.
- **Effort:** M.
- **Common mistakes:** re-implementing capability *inference* (silent) instead of *validation* (explicit) - ARCHITECTURE §3 forbids inference as the mechanism.

---

### Phase 2 - Fitness Functions Scaffold (enforced incrementally)

- **Purpose:** make the architecture rules mechanical before touching high-blast-radius wiring, so regressions are caught the moment they appear.
- **Goals:** wire import-linter (or grimp) with the ARCHITECTURE §18 rule set; add the profile-boot test job; establish the contract-test harness skeleton.
- **Scope:** CI + test tooling. Rules start at **baseline (allow-listed current violations)** and tighten phase-by-phase - never merge a *new* violation, but don't block on pre-existing ones yet.
- **Files affected:** `pyproject.toml`/tooling config, `.github/workflows/`, `apps/backend/tests/architecture/**` (new).
- **Dependencies:** Phase 1 (needs the profile concept for rule #5: "no profile reads outside composition/validation").
- **Risk:** Low-Medium. Risk is *false confidence* if rules are too loose, or *blocked CI* if too strict too early. Mitigate with the allow-list baseline.
- **Rollback:** disable the CI gate; revert tooling.
- **Verification:** the linter fails on a deliberately-introduced violation (a test that adds `import sqlalchemy` to a domain module) and passes on `main`.
- **Success criteria:** CI blocks *new* violations of: domain->infra imports, presentation->infra imports, circular deps.
- **Exit criteria:** baseline allow-list documented with a shrink plan (each entry references the phase that removes it).
- **Effort:** M.
- **Common mistakes:** turning on all strict rules at once -> red CI everyone learns to ignore. Ratchet, don't slam.

---

### Phase 3 - Composition Root

- **Purpose:** replace scattered `get_*()` service-locator globals with **one** startup wiring point that builds adapters from the profile and injects them (ARCHITECTURE §2, §10). **Highest blast radius.**
- **Goals:** a `app/platform/composition.py` that constructs KVStore, Storage, Mailer, LLM, DB engines/session factory, and the auth services once; existing `get_kvstore()` / `get_storage_provider()` / `get_session_service()` / `get_rate_limiter()` / `get_audit_service()` / `get_token_service()` become **thin shims that delegate to the container** (compatibility layer, §8).
- **Scope:** wiring only; no adapter behavior changes. Call sites are untouched this phase (they still call `get_*()`, which now resolves from the container).
- **Files affected:** new `app/platform/composition.py`; edit `app/auth/runtime.py`, `app/auth/sessions.py`, `app/auth/ratelimit.py`, `app/auth/audit.py`, `app/auth/tokens.py`, `app/storage/provider.py`, `app/main.py` (build the container in the lifespan/startup). `reset_*()` test hooks are re-pointed to reset the container.
- **Dependencies:** Phases 1-2.
- **Risk:** **High** - every request path resolves infra through these shims. Mitigated by: (a) shims preserve exact signatures; (b) characterization tests; (c) this phase does *one* thing (no logic changes); (d) it runs in **no other critical phase's window** (§16).
- **Rollback:** revert the PR; shims are removed and the old globals restored (kept in git; small surface). Because signatures are unchanged, revert is clean.
- **Verification:** full suite green; boot every profile; assert (temporary test) that each `get_*()` returns the container-built instance; load-path smoke test (login, tailor, upload).
- **Success criteria:** all adapters are instantiated in exactly one place; `reset_*()` helpers reset via the container.
- **Exit criteria:** no adapter is constructed outside `composition.py` (grep + a fitness check for `build_*(` call sites outside `platform/`).
- **Effort:** L.
- **Common mistakes:** changing adapter behavior "while we're in here" (scope creep); building the container lazily per-request instead of once at startup (ARCHITECTURE §2 - startup-only, context-resolvable later).

---

### Phase 4 - Ports Formalization + Contract Tests

- **Purpose:** turn the informal adapters into named **ports** with a shared behavioral contract (ARCHITECTURE §11, §17, §19).
- **Goals:** define interface types for `Repository`, `Storage`, `KVStore`, `Mailer`, `LLMProvider`, `Jobs`, `Clock`; write **one contract-test suite per port** run against every implementation (SQLite+Postgres repo; local+Cloudinary storage; in-process+Redis KV; noop+SMTP mailer; each LLM adapter via a faked transport; in-process+external-cron jobs).
- **Scope:** extract interfaces from existing concretes; the composition root now returns port types. No new backends invented (S3, queue = future).
- **Files affected:** new `app/platform/ports/*.py`; annotate existing adapters as implementations; `app/platform/composition.py` return types; new `tests/contract/**`.
- **Dependencies:** Phase 3 (single construction point makes "run the suite against each impl" trivial).
- **Risk:** Medium - interface extraction can leak concrete types (e.g., SQLAlchemy rows) through a port. Mitigate: ports expose domain types only; a fitness check + review.
- **Rollback:** revert; adapters keep working (interfaces are additive).
- **Verification:** contract suite green against **every** implementation; a deliberately-broken adapter fails its contract test.
- **Success criteria:** each port has ≥2 impls or a declared external-boundary exception (Clock), each with contract tests (ARCHITECTURE §19 rule 2).
- **Exit criteria:** fitness rule added: a port implementation without a contract test fails CI (§18 rule 7).
- **Effort:** L.
- **Common mistakes:** creating ports for single-impl concerns (Search, Secrets, Logging) - ARCHITECTURE §11 forbids; keep them concrete.

---

### Phase 5 - Identity Port (the one behavioral fork)

- **Purpose:** consolidate the `single_user_mode` identity branches into an `IdentityProvider` port with two adapters, so downstream code only reads `current_user` (ARCHITECTURE §9 identity, §14).
- **Goals:** `owner-auto` adapter (desktop: mints/returns the bootstrap owner as a **real** principal - ADR-0008) and `session` adapter (hosted: existing cookie/session/CSRF). `get_effective_user_id` and the health-check owner path stop reading `single_user_mode` and instead consume the injected provider.
- **Scope:** the identity boundary only. Business logic and scoping remain identical (owner is a tenant-of-one - ADR-0012).
- **Files affected:** `app/auth/principal.py`, `app/auth/owner.py`, `app/routers/health.py`, the auth middleware selection; provider chosen in `composition.py` from the profile.
- **Dependencies:** Phases 3-4 (needs the container + port machinery).
- **Risk:** **High** - auth is the highest-stakes code. Mitigate: characterization tests on `get_effective_user_id` for both modes (Phase 0); contract tests for the identity port; ship behind a parity check (temporary assertion that old and new resolution agree) before deleting the old branch.
- **Rollback:** revert to the boolean branch (kept until cutover verified).
- **Verification:** for `desktop`, every request resolves the owner principal and flows through `user_id` scoping; for `saas`, sessions/CSRF behave exactly as today; cross-tenant access still returns not-found (Invariant C.3).
- **Success criteria:** `single_user_mode` is no longer read in `principal.py`/`health.py`; the only remaining reads are config validation + identity-adapter selection in composition.
- **Exit criteria:** fitness rule tightened: `single_user_mode`/profile referenced only in `platform/` (§18 rule 5) - allow-list entry for identity removed.
- **Effort:** M-L.
- **Common mistakes:** making the owner a "fake/bypass" principal that skips the session pipeline (ADR-0008 says mint a *real* owner session) - under-tests the hosted path.

---

### Phase 6 - Domain Purity Enforcement

- **Purpose:** lock in the dependency rule so inner rings can never import infrastructure (ARCHITECTURE §10, §14).
- **Goals:** prove `services/` and any domain modules import only ports/domain; move any stray infra import behind a port; flip the strict import rules from "baseline allow-list" to "hard fail."
- **Scope:** import graph correctness; minimal code moves where a leak is found.
- **Files affected:** wherever a leak exists (audit will enumerate); `services/**` are already clean (verified), so this is expected to be small.
- **Dependencies:** Phase 5 (identity was a common leak source; must be clean first).
- **Risk:** Low-Medium - a hidden import forces a small refactor.
- **Rollback:** re-add the allow-list entry; revert the strictness flip.
- **Verification:** import-linter passes with **zero** allow-list entries for domain->infra.
- **Success criteria:** ARCHITECTURE §18 rules 1-2 enforced with no exceptions.
- **Exit criteria:** the domain+application layers are unit-testable with in-memory fakes only (the §14 litmus), demonstrated by a fake-only test run.
- **Effort:** S-M.
- **Common mistakes:** "temporarily" allow-listing a new leak - the ratchet only tightens.

---

### Phase 7 - Module Boundaries & Mutation Rights

- **Purpose:** enforce that a module is the sole writer of its tables and cross-module access goes through use-cases (ARCHITECTURE §9, Amendment E).
- **Goals:** audit cross-module table access; route any offender through the owning module's use-case; add a fitness rule forbidding a module from importing another module's ORM models/tables.
- **Scope:** cross-module call sites; no schema change.
- **Files affected:** any router/service reaching into another module's `repo.py`/models (audit will list); likely small given current decomposition.
- **Dependencies:** Phase 6.
- **Risk:** Medium - a hidden cross-module write is a latent coupling; fixing it may add a use-case.
- **Rollback:** revert; re-add allow-list entry.
- **Verification:** fitness rule (§18 rule 6) green; contract tests unaffected.
- **Success criteria:** no module mutates another's tables; module lifecycle states (Amendment D) recorded in a module manifest.
- **Exit criteria:** module ownership table in ARCHITECTURE §9 matches reality; overlaps = zero.
- **Effort:** M.
- **Common mistakes:** collapsing two modules to "avoid a use-case call" - ownership clarity beats a saved function call.

---

### Phase 8 - Frontend Profile Alignment

- **Purpose:** make the UI capability-driven from a backend-provided signal rather than a build-time mode boolean, consistent with the backend profile model.
- **Goals:** the frontend consumes a **capabilities/profile signal** (e.g., from an existing status/config endpoint) to decide auth entry points, admin nav, OAuth button, feature availability - instead of only `NEXT_PUBLIC_SINGLE_USER_MODE`.
- **Scope:** frontend config/session layer; no visual redesign.
- **Files affected:** `apps/frontend/lib/config/auth.ts`, the session/status providers, the components already gated on single-user mode.
- **Dependencies:** Phase 5 (backend must expose a stable capability signal).
- **Risk:** Low-Medium - misreading the signal could hide/show the wrong UI. Mitigate with the existing frontend tests + a profile-driven UI test.
- **Rollback:** revert; `NEXT_PUBLIC_SINGLE_USER_MODE` remains as a fallback until cutover verified.
- **Verification:** desktop profile shows no login wall; saas profile shows login/signup + admin link for admins; capability-gated features (AI, cover letter) reflect server truth.
- **Success criteria:** UI decisions derive from server capabilities, not only a build flag.
- **Exit criteria:** `NEXT_PUBLIC_SINGLE_USER_MODE` reduced to a *default/fallback*, documented; no scattered mode checks in components beyond the config layer.
- **Effort:** M.
- **Common mistakes:** trusting the client capability signal for security - it is UX only; the server always enforces (ARCHITECTURE §K.1).

---

### Phase 9 - Cleanup (Delete Compatibility Layers)

- **Purpose:** remove every temporary shim, service-locator global, and scattered conditional introduced or exposed during migration (ARCHITECTURE §13, §20).
- **Goals:** delete the `get_*()` delegating shims (callers now use injected dependencies or the container accessor); remove dead `reset_*()` variants; delete any dual-path code; remove allow-list entries.
- **Scope:** subtractive only. No new behavior.
- **Files affected:** `auth/runtime.py`, `auth/sessions.py`, `auth/ratelimit.py`, `auth/audit.py`, `auth/tokens.py`, `storage/provider.py`, any module still importing the shims.
- **Dependencies:** Phases 3-8 fully cut over (nothing references the shims).
- **Risk:** Medium - deleting a still-referenced shim breaks a path. Mitigate: grep + fitness check proves zero references before deletion; do it per-shim, not all at once.
- **Rollback:** revert the specific deletion PR.
- **Verification:** full suite green; grep shows zero `get_kvstore(`/`get_storage_provider(`/etc. outside `platform/`; fitness rules all strict and green.
- **Success criteria:** Deletion Plan (§20) fully checked off.
- **Exit criteria:** no compatibility layer, no dead adapter, no scattered `single_user_mode` read remains.
- **Effort:** M.
- **Common mistakes:** leaving "harmless" shims "just in case" - ARCHITECTURE §13: prefer deletion; a lingering shim is debt with no exit.

---

### Phase 10 - Hardening Scaffolds (no premature infrastructure)

- **Purpose:** land the *architecture-enabling* scaffolds that future stages need, without building infrastructure we don't yet require (ARCHITECTURE §8 triggers).
- **Goals:** ensure the `Jobs` port shape admits a future durable-queue adapter (ADR-0010) without caller changes; add correlation-id propagation + `principal_user_id` in structured logs as a documented invariant (Appendix I.2); add token/cost metrics for AI ops (Appendix J.3).
- **Scope:** observability + port-shape only. **No** Redis/queue/S3 built now.
- **Files affected:** `observability.py`, `events/jobs.py`/scheduler wiring (shape only), AI service metrics hooks.
- **Dependencies:** Phase 9 (clean base).
- **Risk:** Low.
- **Rollback:** revert; scaffolds are additive.
- **Verification:** correlation id present on every request log; cost metric emitted per AI op; a stub queue adapter can be registered without changing callers (proof-of-seam test).
- **Success criteria:** future scaling steps are additive (a new adapter + profile), verified by the seam test.
- **Exit criteria:** ARCHITECTURE Appendices I.2 / J.3 invariants observable in a running instance.
- **Effort:** S-M.
- **Common mistakes:** building the queue/Redis "while we're here" - that's a triggered future stage, not this migration (ARCHITECTURE §8).

---

### Phase 11 - Production Observation (migration closes here)

- **Purpose:** a migration is not "done" at code-merge; it is done when the *deployed* system is proven healthy under real traffic. This phase watches production before the plan is formally closed and archived.
- **Objectives:** deploy the fully-migrated system; observe; confirm no regression in errors, latency, cost, or auth behavior; run the Final Audit (Appendix D); then close the migration.
- **Flow:**
```
Deploy -> Observe -> Collect metrics -> Monitor errors -> Monitor AI cost
       -> Fix regressions -> Architecture audit (Appendix D) -> Close migration
       -> Archive IMPLEMENTATION_PLAN.md
```
- **Goals:** a clean observation window with no migration-attributable regressions; all Success Metrics (Appendix A) at target; Final Audit (Appendix D) green.
- **Scope:** operational observation + regression fixes only. **No** new structural change (structural work ended at P10). Regression fixes are minimal and each independently revertible.
- **Files affected:** typically none (observation). Any regression fix is a small, targeted PR.
- **Dependencies:** Phase 9 complete (no shims) and Phase 10 (observability/correlation + cost metrics live - this is what makes observation possible).
- **Monitoring:** request error rate + p95 latency (watch for a step-change at deploy), auth error/lockout metrics (both profiles), AI cost/token per operation (Appendix J.3 of ARCHITECTURE), correlation-id joinable logs, profile boot health.
- **Success metrics:** error rate and latency within the pre-migration baseline band; zero migration-attributable incidents; AI cost per op flat or lower; all Appendix A metrics at target.
- **Rollback policy:** a migration-attributable regression that cannot be hot-fixed within the window triggers a **revert to the last pre-phase release** (each phase was independently revertible; the deployed artifact is too). Data is unaffected (no destructive schema change was introduced - §10).
- **Risk:** Low-Med (observation surfaces latent issues; the fix path is small reverts).
- **Duration:** **1-2 weeks** of observation (calendar, not effort - the system must run under real usage across a representative cycle).
- **Exit criteria:** observation window elapsed with metrics at target; Final Audit (Appendix D) fully passed; Deletion Plan (§20) and Final Verification (§21) checklists complete.
- **Verification:** the migration is declared complete and this document is archived (§22) **only** after this phase's exit criteria pass.
- **Common mistakes:** declaring victory at merge (P10) and reassigning the team before production proves healthy; treating P11 as a place to slip in new structural work.

---

## 7. Task Breakdown (representative)

> Not exhaustive - a template of how each phase decomposes into small, reversible tasks. Every task: Description - Reason - Dependencies - Risk - Validation - Rollback - Completion. Tasks map to one PR each (§17).

**Phase 1 - Profiles (sample tasks)**
- **T1.1 Add `DeploymentProfile` enum + `resolve_profile(settings)`.** *Reason:* explicit intent (§3). *Deps:* none. *Risk:* Low. *Validation:* unit test each env->profile mapping. *Rollback:* revert. *Done:* every current `.env` maps to one profile.
- **T1.2 Add `required_capabilities(profile)` + `validate_capabilities()` called at boot.** *Reason:* fail-fast (§5). *Deps:* T1.1. *Risk:* Med (boot path). *Validation:* profile smoke test asserts precise error on a missing capability. *Rollback:* revert. *Done:* saas without Postgres fails fast with a clear message.
- **T1.3 Make `settings.single_user_mode` a derived property of the profile.** *Reason:* single source of truth. *Deps:* T1.1. *Risk:* Med. *Validation:* characterization tests unchanged. *Rollback:* revert. *Done:* no env read of the raw boolean remains in config.

**Phase 3 - Composition Root (sample tasks)**
- **T3.1 Create `composition.py` building KVStore/Storage/Mailer/LLM/DB once.** *Risk:* High. *Validation:* container unit test; boot all profiles. *Rollback:* revert. *Done:* container returns port-typed adapters.
- **T3.2 Convert `get_kvstore()` to delegate to the container (shim).** *Risk:* High. *Validation:* test asserts identity with container instance. *Rollback:* revert restores global. *Done:* no behavior change, one construction site.
- **T3.3 ... repeat per `get_*()` (storage, sessions, ratelimit, audit, tokens), one PR each.** Serialized, not parallel (§16).

*(Phases 4-10 decompose identically: extract-one-port-PR, cutover-one-callsite-PR, tighten-one-fitness-rule-PR, delete-one-shim-PR.)*

---

## 8. Compatibility Strategy

**WHY:** old and new must coexist so `main` is always deployable.

- **Delegating shims (primary tool):** during Phase 3, `get_kvstore()` etc. keep their signatures but return the container-built instance. Callers are unaware; logic lives in exactly one place (no duplication).
- **Parity assertions (identity, Phase 5):** a temporary, test-only assertion that old-branch and new-provider resolve the same `user_id` for both modes, removed at cutover.
- **Dual-read, never dual-write:** where a value can come from old or new config (profile vs raw boolean), read prefers the new source and falls back to old; **writes/decisions have one owner**.
- **Frontend fallback (Phase 8):** UI prefers the server capability signal, falls back to `NEXT_PUBLIC_SINGLE_USER_MODE` until cutover verified.
- **Deprecation + removal triggers:** every shim is tagged (comment + tracking row §20) with the phase that deletes it. No shim outlives Phase 9.

---

## 9. Feature Flag Strategy

**WHY:** flags de-risk cutover, but permanent flags are debt (ARCHITECTURE forbids flags as topology switches).

- **Migration uses compatibility shims + parity checks, not runtime feature flags,** because these changes are structural (wiring/identity), not user-facing rollouts. Shims are compile-time seams removed on schedule - cleaner than flags here.
- **The one legitimate flag:** the frontend `NEXT_PUBLIC_SINGLE_USER_MODE` already exists and acts as a *fallback default*, not a topology switch; Phase 8 demotes it to a default and it is retained only as a documented fallback (not removed, but never a behavioral fork).
- **No new permanent flags.** If a temporary flag is introduced for a risky cutover, it carries a removal trigger and is deleted in Phase 9. **Zero migration flags remain at completion.**

---

## 10. Data Migration Strategy

**WHY:** the schema is already migrated to Postgres (Alembic chain `0001`->`0014`) and to SQLite via `init_models_sync`. This migration is **primarily structural, not data.** The rules below govern the few data touches that may arise.

- **Database:** no schema redesign. Any incidental column needed (unlikely) follows **expand-contract** (ARCHITECTURE Amendment G): additive/nullable first, backfill, contract later.
- **Indexes:** none added by this migration; if profiling later needs one, create it on the **direct/session** endpoint (never the transaction pooler - ADR-0003).
- **Migrations:** remain Alembic-owned, applied on the direct/session (5432) endpoint; `DB_AUTO_MIGRATE` policy unchanged. Regression guards from prior incidents stay (URL `%`-escaping; no pooler DDL).
- **Storage:** no data moves; the Storage port is a wiring change, not a re-upload.
- **Cache/KV:** ephemeral by definition; no migration. Switching in-process->Redis (future) is empty-cache-cold-start, safe.
- **Versioning:** resume version history is untouched.
- **Rollback:** because no destructive schema change is introduced, DB rollback = revert code; the schema remains forward/backward compatible across the migration window.
- **Forward/backward compatibility:** guaranteed by expand-contract + "no contract step until a later, separate phase."
- **Verification:** the existing dual-dialect suite (Phase 0/2) + migration tests confirm the chain applies cleanly on both targets.

---

## 11. Testing Strategy (per phase)

**WHY:** each phase changes a different risk surface; the test mix matches the surface (ARCHITECTURE §17).

| Phase | Unit | Integration | Contract | Architecture (fitness) | Perf | Regression | E2E | Manual | Prod verify |
|---|---|---|---|---|---|---|---|---|---|
| 0 Prep | [x] pin behavior | [x] | - | scaffold | - | [x] baseline | [x] existing | - | - |
| 1 Profiles | [x] mappings | [x] boot | - | rule #5 (new-only) | - | [x] validation | - | boot smoke | health check per profile |
| 2 Fitness | - | - | harness | [x] ratchet on | - | - | - | deliberate-violation test | CI gate live |
| 3 Composition | [x] container | [x] paths | - | "single construction site" | [x] startup time | [x] full suite | [x] login/tailor/upload | golden path | error rate + latency watch |
| 4 Ports | [x] | [x] | [x] per impl | port-without-contract fails | - | [x] | - | - | - |
| 5 Identity | [x] both modes | [x] auth flows | [x] identity port | rule #5 tightened | - | [x] auth regression | [x] signup/login | step-up, CSRF | auth error/lockout metrics |
| 6 Purity | [x] fake-only run | - | - | rules #1-2 strict | - | [x] | - | - | - |
| 7 Modules | [x] | [x] | [x] | rule #6 on | - | [x] | - | - | - |
| 8 Frontend | [x] FE | [x] | - | - | - | [x] FE suite | [x] profile UI | both profiles by hand | UI correctness |
| 9 Cleanup | [x] | [x] | [x] | all strict | - | [x] full | [x] | - | post-deploy smoke |
| 10 Hardening | [x] | [x] | [x] seam | - | [x] | [x] | - | observe correlation/cost | dashboards live |
| 11 Prod Observation | - | - | - | all green | [x] trend watch | [x] regression watch | [x] both profiles | metrics review | error/latency/cost within baseline |

**Non-negotiables:** contract tests (Phase 4+) are mandatory for every adapter (ARCHITECTURE §19). The domain fake-only run (Phase 6) is the §14 litmus.

---

## 12. Validation Checklist (per phase)

For every phase, before merge:
- **Pre-checks:** baseline green; characterization tests for the touched seam exist; rollback trigger written.
- **During:** PR is one reversible unit; shim (if any) preserves signatures; no adapter behavior change unless the phase is explicitly about that.
- **Post-checks:** full backend + frontend suites green; **all profiles boot or fail fast** (profile smoke test); fitness functions at the phase's required strictness are green.
- **Smoke tests:** login (saas) / owner-resolve (desktop); tailor a resume; upload/export; notifications unread-count; admin overview.
- **Architecture validation:** import-linter green at the current ratchet; no new allow-list entries.
- **Fitness functions:** the specific ARCHITECTURE §18 rule(s) this phase enables are switched from advisory to blocking.

### 12.1 Decision Gate (GO / NO-GO)
*Purpose:* make the transition between phases an explicit, recorded decision rather than a drift. *Why it exists:* the most common migration failure is starting the next phase while the previous one is "mostly done." *How it is used:* the phase owner + architecture reviewer sign off (Communication, Appendix C) before the next phase begins; the decision is announced.

**Before entering the next phase, confirm ALL:**
- [x] Exit criteria for the current phase (§6) met.
- [x] Full backend + frontend suites green; contract tests green (Phase 4+).
- [x] All profiles boot or fail fast (profile smoke test).
- [x] Rollback trigger + procedure verified (actually rehearsed for critical phases 3/5).
- [x] Performance acceptable (no error-rate/latency step-change post-deploy).
- [x] Fitness ratchet for this phase switched to blocking and green; zero new allow-list entries.
- [x] Deletion-Plan (§20) rows for this phase either removed or scheduled.
- [x] Documentation/tracking (§19, Appendix A/B) updated.

**If any item is [ ] -> NO-GO: STOP.** Do not begin the next phase. Fix or roll back. The migration never advances "automatically" past a red gate.

*Verification:* the gate decision (GO/NO-GO, date, sign-offs) is recorded in the progress tracker (§19).

---

## 13. Rollback Strategy (per phase)

| Phase | Rollback trigger | Procedure | Data recovery | Compatibility | Residual risk |
|---|---|---|---|---|---|
| 0 | Flaky/false tests | Revert PR | n/a | n/a | none |
| 1 | Boot fails / wrong profile mapping | Revert; boolean reads still present | n/a | full | Low |
| 2 | CI blocks legitimately-passing code | Disable gate; revert tooling | n/a | full | Low |
| 3 | Elevated error/latency after deploy | Revert PR -> globals restored (signatures preserved) | n/a | full | Med |
| 4 | Contract test exposes an adapter defect | Revert interface PR; adapter keeps working | n/a | full | Low |
| 5 | Auth anomaly (401s, wrong scope) | Revert to boolean branch (retained until parity verified) | n/a (no data change) | full | Med-High -> mitigated by parity gate |
| 6 | Hidden import forces breakage | Re-add allow-list entry; revert strictness | n/a | full | Low |
| 7 | Cross-module regression | Revert; re-add allow-list | n/a | full | Low |
| 8 | Wrong UI shown | Revert; FE falls back to build flag | n/a | full | Low |
| 9 | Deleted shim still referenced | Revert deletion PR (per-shim) | n/a | full | Med |
| 10 | Scaffold regression | Revert (additive) | n/a | full | Low |
| 11 | Migration-attributable prod regression | Revert to last pre-phase release; hot-fix if small | n/a (no destructive schema) | full | Low-Med |

**Universal rule:** because new lands beside old and old is deleted only after verified cutover, **rollback ≈ revert PR** with no data implications throughout.

---

## 14. Risk Register (migration-specific)

> Complements ARCHITECTURE Appendix M (steady-state risks). These are risks *of the migration itself.*

| ID | Risk | Category | Probability | Impact | Mitigation | Owner |
|----|------|----------|-------------|--------|------------|-------|
| M-1 | Composition-root cutover breaks a request path | Migration | Med | High | One-thing PRs; signature-preserving shims; golden-path E2E; isolated window (§16) | Platform |
| M-2 | Identity port changes auth behavior subtly | Security | Med | Critical | Parity assertion gate; both-mode characterization; contract tests | Identity |
| M-3 | Fitness rules too strict too early -> CI ignored | Developer | Med | Med | Baseline allow-list + ratchet; document shrink plan | Platform |
| M-4 | Silent capability inference reintroduced | Architecture | Low | High | Fitness/review; validation-not-inference in Phase 1 | Platform |
| M-5 | Port leaks concrete type (SQLAlchemy row) | Architecture | Med | Med | Ports expose domain types; review + contract tests | Data |
| M-6 | Dual-dialect drift surfaces during refactor | Migration | Med | High | Phase 0 dual-dialect CI; contract tests per dialect | Data |
| M-7 | Shim outlives its phase (permanent debt) | Developer | Med | Med | Removal trigger per shim (§20); Phase 9 gate | Tech lead |
| M-8 | Feature work collides with a critical-phase seam | Operational | Med | Med | Parallelization rules (§16); freeze the seam's files during its phase | Tech lead |
| M-9 | Prod config missing a required capability post-cutover | Operational | Low | High | Fail-fast boot validation catches at deploy, not at request time | Platform |
| M-10 | Under-testing identity in desktop (owner bypass) | Security | Low | High | ADR-0008 real owner session; identity contract tests run for both adapters | Identity |

---

## 15. Technical Debt Strategy (migration)

- **Temporary debt (allowed, tracked):** delegating `get_*()` shims (Phase 3), the identity parity assertion (Phase 5), fitness allow-list entries. Each has a removal trigger and a phase that deletes it.
- **Accepted debt (steady-state, already in ARCHITECTURE Appendix N):** dual datastore (D1), in-process KV (D2), external-cron scheduler (D3), Search-concrete (D4) - this migration does **not** resolve these; it only makes them explicit and swappable.
- **Removal plan:** every temporary item appears in the Deletion Plan (§20) with its trigger; Phase 9 fails if any remain.
- **Never allowed:** a migration hack with no exit. If a shim can't be described with a removal trigger, it doesn't merge.

---

## 16. Parallel Work Strategy

**Can run in parallel (different seams, no shared files):**
- Feature work in `services/`, `routers/*` product endpoints <-> Phases 1, 2, 4 (mostly additive/tooling).
- Phase 4 port extraction can proceed **per port in parallel** once the composition root (Phase 3) exists.
- Phase 8 (frontend) once Phase 5 exposes the capability signal.

**Must be serialized (shared, high-blast-radius seam):**
- **Phase 3 (composition) and Phase 5 (identity) never overlap** - both touch auth wiring; running together makes a regression un-bisectable. Golden Rule of this plan: **one critical migration at a time** (§4.10).
- Fitness ratchets (Phase 2/6/7) land one rule at a time.

**Must not run together:**
- Any refactor of `auth/` internals <-> Phase 5.
- Any change to `config.py` validation <-> Phase 1.

**Coordination mechanism:** during a critical phase, its touched files (listed per phase) are "soft-frozen" - changes to them go through the phase owner to avoid merge collisions.

### 16.1 Critical Path (single-engineer sequence)
*Purpose:* if only one engineer is available, this is the exact, unavoidable order - the phases that *cannot* be skipped or reordered because each strictly unlocks the next. *How to use:* a solo engineer executes this spine; a team layers the parallelizable work above onto it.

```
P0  Safety harness            (nothing safe without it)
 v
P1  Profiles + validation      (every wiring decision reads this)
 v
P2  Fitness scaffold           (guards the risky work that follows)
 v
P3  Composition root           (critical seam #1)
 v
P4  Ports + contract tests     (identity is just a port; needs the machinery)
 v
P5  Identity port              (critical seam #2)
 v
P6  Domain purity              (lock the inner rings)
 v
P9  Cleanup                    (delete shims/globals/flags)
 v
P10 Hardening seams
 v
P11 Production observation -> close
```
**On the critical path:** P0 -> P1 -> P2 -> P3 -> P4 -> P5 -> P6 -> P9 -> P10 -> P11.
**Deferrable by a solo engineer (do after, or fold in opportunistically):** **P7** (module mutation-rights - valuable but not blocking; can trail P6) and **P8** (frontend alignment - depends only on P5's capability signal, can be done any time after P5). They are *not* on the minimal spine because nothing downstream requires them to be complete before cleanup.

*Verification:* the spine matches §5's dependency graph with P7/P8 lifted out as non-blocking; removing any spine phase breaks a documented dependency edge.

---

## 17. Team Workflow

- **PR size:** one reversible change; target < ~400 lines diff. A phase = many small PRs, not one mega-PR.
- **Branch strategy:** short-lived feature branches off `main`; no long-running migration branch (would drift - ARCHITECTURE rejects branch-based divergence).
- **Code review:** every PR reviewed by one non-author; PRs touching `auth/`, `platform/`, or `config.py` require the **phase owner + one architecture reviewer**.
- **Testing expectations:** new/changed behavior has tests; contract tests accompany any adapter change; fitness functions must be green.
- **Architecture review:** any new port, module, profile, or infra dependency requires an **ADR** (ARCHITECTURE §7) before merge.
- **Merge requirements:** green CI (unit/integration/contract/fitness/profile), review approval, ADR (if applicable), and - for critical phases - a passing golden-path E2E.

### 17.1 Change Budget (per-PR limits)
*Purpose:* cap the blast radius and keep every change reversible and reviewable. *Why it exists:* migration risk scales with the size and coupling of a change; small, single-purpose PRs are the cheapest insurance and make `git revert` a reliable rollback. *How it is used:* a PR exceeding a limit is split, or (rarely) justified in the PR description and approved by the architecture reviewer.

| Dimension | Soft max per PR | Why the limit |
|---|---|---|
| LOC (net diff) | ~400 | Reviewable in one sitting; small revert surface |
| Files touched | ~15 | Keeps the change comprehensible; large fan-out hides coupling |
| Modules touched | 1 (2 only for a documented cutover) | Preserves module ownership (§7); avoids cross-cutting regressions |
| Architecture seams changed | **1** | Never cut two seams together (§4.10, §16) - the hard limit |
| DB migrations | 1, expand-only | One reversible schema step (§10, ARCHITECTURE Amendment G) |
| Rollback complexity | "revert the PR" | If rollback needs more than a revert, the PR is too big - split it |

*Verification:* PR template surfaces these; the architecture reviewer rejects over-budget PRs lacking a written justification. The **seams-per-PR = 1** limit is non-negotiable and mirrors the Never-Do rules (§4.1).

---

## 18. CI/CD Strategy

Pipeline gates (added incrementally, matching phases):
- **Architecture tests (Phase 2+):** import-linter rules from ARCHITECTURE §18 (domain/presentation->infra bans, no cycles, profile-read containment). Ratchet from advisory->blocking per phase.
- **Contract tests (Phase 4+):** every port impl runs the shared suite; a port impl without a contract test fails CI.
- **Import rules:** enforced as above; new violations block merge from Phase 2.
- **Boot validation (Phase 1+):** each deployment profile is booted in CI; missing-capability -> expected fail-fast asserted.
- **Migration tests:** Alembic chain applies forward on Postgres (and SQLite while first-class) in CI.
- **Profile tests:** smoke each profile's composition + a golden request path.
- **Deployment validation:** post-deploy health/readiness + a smoke path per environment; expand-contract check for any migration.

---

## 19. Progress Tracking

Track per phase: **Status** (Not started / In progress / Blocked / Done), **Blocked-by**, **Dependencies**, **Progress %**, **Completion definition** (= the phase's Exit Criteria in §6).

```
Phase  Status        Blocked-by   Deps      %     Completion = Exit criteria (§6)
0      Done          -            -         100   architecture-test scaffold + green baseline (1476-> suite)
1      Done          -            P0        100   every .env -> one profile; capability validation live
2      Done          -            P1        100   new mode-reads blocked (import/containment fitness tests)
3      Done          -            P1,P2     100   cache-inversion complete; adapters built only in container; ownership fitness
4      Done          -            P3        100   ports namespace + KVStore contract suite (Local+DB); ports-registry fitness
5      Done          -            P3,P4     100   identity owner-fallback behind IdentityProvider; health.py de-moded; parity green
6      Done          -            P5        100   domain-purity fitness strict on services/ (fake-only litmus holds)
7      Done          -            P6        100   mutation-rights enforced; retention/agenda/internal routed via services
8      Done          -            P5        100   FE session guard authoritative (SSR) + build-flag mirror; 347 FE tests green
9      Done          -            P3-P8     100   no shims/globals/scattered reads; dead imports removed; diagnostics clean
10     Done          -            P9        100   correlation-id + structured logs + Jobs seam present (no premature infra)
11     Deploy-gated  -            P9,P10    90    all realistically-possible checks green; live observation needs a real deploy
6      ___           P5           P5        ___   domain fake-only test run green; rules #1-2 strict
7      ___           P6           P6        ___   zero cross-module table writes; rule #6 on
8      ___           P5           P5        ___   UI derives from server capabilities; flag = fallback
9      ___           P3-P8        P3-P8     ___   zero shims/globals/scattered flags (Deletion Plan done)
10     ___           P9           P9        ___   correlation/cost invariants observable; queue seam proven
11     ___           P9,P10       P9,P10    ___   observation window clean; Final Audit (App. D) passed
```
A phase is **Blocked** if any dependency is not Done or its owner has soft-frozen a needed file.

---

## 20. Deletion Plan

**WHY:** the migration is only complete when the *old* shape is gone. Nothing temporary survives.

| Item | Introduced/exposed in | Removal trigger | Removed in | Verified by |
|------|----------------------|-----------------|------------|-------------|
| `get_kvstore()` shim | P3 | all callers use injected dep/container accessor | P9 | grep = 0 refs outside `platform/`; fitness |
| `get_storage_provider()` shim | P3 | same | P9 | grep + fitness |
| `get_session_service()` / `get_rate_limiter()` / `get_audit_service()` / `get_token_service()` shims | P3 | same | P9 | grep + fitness |
| `single_user_mode` behavioral reads (`principal.py`, `health.py`) | pre-existing | identity port cutover verified | P5 | fitness rule #5 (reads only in `platform/`) |
| Identity parity assertion (test-only) | P5 | parity holds for N days/CI runs | P5 (end) | removed with green auth suite |
| Fitness allow-list entries | P2 | each entry's phase completes | P2-P7 | allow-list empty at P9 |
| Frontend scattered mode checks | pre-existing | capability signal consumed | P8 | FE tests; grep in components |
| Duplicate `reset_*()` variants | P3 | container reset replaces them | P9 | grep |

**Rule:** Phase 9 CI **fails** if any row above is not "Removed". A residual shim is a migration failure, not a convenience.

---

## 21. Final Verification (production-readiness checklist)

At migration end, all must be true:
- [ ] Architecture compliance: code matches ARCHITECTURE §1-§22 + Part II.
- [ ] All ADRs satisfied (0001-0012 + any added during migration).
- [ ] All fitness functions green and **blocking** (no advisory-only rules).
- [ ] No compatibility layers remain (Deletion Plan §20 fully checked).
- [ ] No deprecated code / no `TODO: migration` markers.
- [ ] All contract tests passing for every port implementation.
- [ ] All profile boot tests passing (each profile boots or fails fast).
- [ ] No duplicated implementations; no dead adapters.
- [ ] No legacy configuration paths (profiles are the only deployment axis).
- [ ] No scattered `single_user_mode`/profile conditionals outside `platform/` + validation.
- [ ] No architecture violations (import-linter clean, zero allow-list).
- [ ] Domain + application unit-testable with fakes only (§14 litmus demonstrated).
- [ ] Dual-dialect CI green (while SQLite first-class); migrations apply forward on both.
- [ ] Golden-path E2E green on both `desktop` and `saas` profiles.

> This checklist is the **code-readiness** gate (end of P10). Formal migration closure additionally requires the Phase 11 observation window and the multi-domain **Final Audit (Appendix D)** to pass.

---

## 22. Long-term Maintenance

**How future engineers continue after this migration:**
- **Adding a backend:** implement the port + contract tests, register it in the composition root, select it by capability/profile. No caller changes.
- **Adding a deployment target:** add a profile + its required-capability contract + a boot test. No code fork.
- **When to create an ADR:** any new infra dependency, port, profile, or change to a Golden Rule (ARCHITECTURE §7/§19).
- **When to add a port:** only at the second real implementation or a genuine external boundary (ARCHITECTURE §11, Amendment F). Until then, concrete.
- **When to create a profile:** only when a target has a *different required-capability contract* (Amendment F). Otherwise it's a preset.
- **When to reject abstraction:** single impl, no external boundary, "might need it" - reject (Complexity Budget §12; Decision Checklists Amendment F).
- **How this plan evolves:** this document is *done when the migration is done.* Post-migration, evolution is governed solely by ARCHITECTURE (ADRs + fitness functions + the §8 stage triggers). Archive this plan; do not keep amending it as a living doc - that role belongs to ARCHITECTURE.

---

## 23. Final Self-Review (zero-trust, by the responsible Principal Engineer)

> I reviewed my own roadmap for missing phases, ordering errors, parallelization mistakes, rollback/testing/verification gaps, architecture violations, dependency mistakes, over/under-engineering, and hidden risks. Findings and the resulting fixes (already folded into the plan above):

- **Ordering - fitness before composition (fixed).** Initial instinct was Composition (P3) before Fitness (P2). Corrected: fitness scaffolding lands **before** the highest-blast-radius wiring change so regressions are caught immediately. Rule enforcement still ratchets, so this doesn't block feature work.
- **Parallelization - composition vs identity (fixed).** Both touch `auth/` wiring. Explicitly serialized (§16); running them together would make an auth regression un-bisectable. This is the single most important sequencing constraint.
- **Rollback gap - identity (fixed).** Added the **parity assertion gate** (old vs new resolution must agree for both modes) before deleting the boolean branch, so P5 rollback is "revert to a still-present branch," not "reconstruct auth."
- **Testing gap - dual dialect (fixed).** Moved dual-dialect CI to **Phase 0**, not "sometime later," because the refactor itself can surface latent SQLite/Postgres drift (Risk M-6, ARCHITECTURE Debt D1).
- **Under-engineering - desktop identity (fixed).** Enforced ADR-0008's "real owner session, not a bypass," with the identity contract suite running for **both** adapters, closing the "auth under-tested locally" risk (M-10).
- **Over-engineering check - no queue/Redis/S3 in this migration (confirmed).** Phase 10 explicitly builds only *seams* (Jobs-port shape, observability), not infrastructure; those are triggered future stages (ARCHITECTURE §8). Rejected the temptation to "do it while we're here."
- **Over-engineering check - feature flags (confirmed rejected).** For structural changes, compatibility shims + parity checks are cleaner than runtime flags; the plan uses no new permanent flags (§9).
- **Dependency check - frontend after identity (fixed).** P8 depends on P5 exposing a stable capability signal; sequencing corrected so the UI never reads a signal that doesn't exist yet.
- **Deletion completeness (fixed).** Every temporary artifact has a row in §20 with a removal trigger, and Phase 9 CI fails if any survive - closing the "permanent shim" anti-pattern (M-7).
- **Verification completeness (fixed).** §21 asserts the §14 litmus (fake-only domain tests) and golden-path E2E on **both** profiles, not just "tests pass."
- **Blast-radius realism (confirmed).** Grounded in the actual finding that `single_user_mode` lives in ~7 files and services are import-pure - so the plan is right-sized as *formalization*, not a rewrite; no phase invents work the codebase doesn't need.

**Residual (accepted, low) planning risks:** effort estimates are indicative and phase-gated by exit criteria rather than dates (intentional); the exact per-port contract-test surface (Phase 4) will be enumerated by the port audit at that phase rather than pre-listed here (deliberate - avoids stale detail).

**Verdict:** With the above folded in, I would approve this roadmap for execution. It is incremental, each phase is independently releasable and revertible, the two high-risk seams are isolated and gated, the rules become mechanical before the risky work, and the end state provably matches the frozen architecture with nothing temporary left behind.

---

*End of the core plan. Formal completion requires: §21 code-readiness checklist [x], Phase 11 observation window clean [x], and the Final Audit (Appendix D) [x]. Only then is this document archived and ARCHITECTURE.md governs ongoing evolution. The appendices below are execution/tracking aids, not architecture.*

---
---

# Appendices (execution & tracking aids)

> These appendices are **planning and coordination instruments**, not architecture. They exist to make the migration measurable, schedulable, and communicable for a multi-engineer team. They are consistent with - and subordinate to - Part I of this plan and ARCHITECTURE.md. When the migration closes (Phase 11), these are archived with the plan.

---

## Appendix A - Migration Success Metrics & Scorecard

*Purpose:* make progress measurable, not anecdotal. *Why it exists:* "are we done?" must have a numeric answer, and regressions in adoption must be visible. *How it is used:* metrics are recomputed each Decision Gate (§12.1) and reviewed weekly (Appendix B); the scorecard is the at-a-glance rollup for stakeholders.

### A.1 Metrics (Current -> Target, with measurement method)

| Metric | Current | Target | Measurement method |
|---|---|---|---|
| Service-locator (`get_*()`) references outside `platform/` | high (all call sites) | **0** | `grep -r "get_kvstore(\|get_storage_provider(\|get_session_service(\|get_rate_limiter(\|get_audit_service(\|get_token_service("` outside `app/platform/` |
| Composition-root adoption (adapters built in one place) | 0% | **100%** | count of `build_*(`/adapter constructions outside `platform/composition.py` = 0 |
| Architecture violations (import rules) | baseline allow-list | **0 (empty allow-list)** | import-linter report entry count |
| Port coverage (target ports defined) | partial (informal) | **100% of ARCHITECTURE §11 port set** | ports present in `platform/ports/` vs the §11 list |
| Adapter coverage (each port ≥2 impls or declared boundary) | partial | **100%** | per-port implementation count check |
| Contract-test coverage (impls with a contract test) | 0% | **100%** | CI: impls without a contract test = 0 (fitness rule §18.7) |
| Fitness-function coverage (rules blocking) | ~0 (none) | **all ARCHITECTURE §18 rules blocking** | CI config: advisory rules remaining = 0 |
| Deployment-profile coverage (profiles with boot test) | 0 | **100% of §4 profiles** | profile smoke-test suite count vs profile list |
| Compatibility-shim count | 0 (pre-migration) -> peaks mid-migration | **0 at close** | Deletion-Plan (§20) open rows = 0 |
| Remaining behavioral `single_user_mode`/profile reads outside `platform/`+validation | ~7 files | **0** | grep for the symbol outside allowed modules |
| Remaining service-locator globals (`global _x` singletons) | many (auth/*, storage) | **0** | grep `^\s*global _` in adapter/service modules |

### A.2 Scorecard (at-a-glance; [ ] not started - [~] in progress - [x] done)

| Capability | Current | Target |
|---|---|---|
| Deployment Profiles | [ ] | [x] |
| Capability Validation (fail-fast) | [~] (partial in config) | [x] |
| Fitness Functions | [ ] | [x] |
| Composition Root | [ ] | [x] |
| Ports (formalized) | [~] (adapters exist, informal) | [x] |
| Contract Tests | [ ] | [x] |
| Identity Port | [~] (fork exists in `principal.py`) | [x] |
| Domain Purity (enforced) | [~] (services import-pure, unenforced) | [x] |
| Module Mutation-Rights | [~] | [x] |
| Frontend Profile Alignment | [~] (build-flag only) | [x] |
| Cleanup (no shims/globals) | [ ] | [x] |
| Production Observation | [ ] | [x] |

*Exit/Completion:* migration is complete when every A.1 metric is at target **and** every A.2 row is [x]. *Verification:* the metric commands above are runnable in CI; the scorecard is derived from them (no manual guessing).

---

## Appendix B - Weekly Deliverables (indicative)

*Purpose:* a planning/tracking cadence for a 2-3 engineer team. *Why it exists:* phases are gated, not timed, but stakeholders need a rough schedule. *How it is used:* as a forecast only - a week slips to match the phase's exit criteria, never the reverse. **Phases remain gated by exit criteria (§6); these weeks are estimates, not commitments.**

| Week | Deliverable (target) |
|---|---|
| 1 | P0: green baseline + dual-dialect CI running |
| 2 | P1: profiles + capability validation; every `.env` maps to a profile |
| 3 | P2: fitness scaffold live (advisory->new-only blocking); allow-list baseline documented |
| 4 | P3 (part 1): composition root built; first `get_*()` shims delegating |
| 5 | P3 (part 2): all `get_*()` shims delegate; single construction site verified |
| 6 | P4 (part 1): ports extracted; contract-test harness + first ports covered |
| 7 | P4 (part 2): all ports covered by contract tests; §18.7 rule on |
| 8 | P5 (part 1): identity port + owner/session adapters behind parity gate |
| 9 | P5 (part 2): identity cutover; `single_user_mode` reads confined to `platform/` |
| 10 | P6: domain-purity rules strict; fake-only domain test run green |
| 11 | P7 + P8: module mutation-rights enforced; frontend consumes capability signal (parallelizable) |
| 12 | P9: shims/globals/flags deleted; Deletion Plan (§20) closed |
| 13 | P10: hardening seams (correlation, cost metrics, queue seam) |
| 14 | P11: production observation window opens; Final Audit (Appendix D) -> close & archive |

*Exit/Completion:* Week-14 is nominal; actual close is when P11 exit criteria pass. *Verification:* weekly review compares delivered vs planned and re-forecasts; no phase is marked done without its §6 exit criteria.

---

## Appendix C - Communication Plan

*Purpose:* keep a multi-engineer migration coordinated without heavyweight process. *Why it exists:* the two biggest team-level risks are colliding on a frozen seam (M-8) and advancing past a red gate; both are prevented by a few explicit announcements. *How it is used:* lightweight messages in the team channel + a recorded decision in the tracker (§19).

**Roles**
- **Phase Owner:** drives one phase, owns its soft-frozen files (§16), decides GO/NO-GO with the reviewer.
- **Architecture Reviewer:** guards ARCHITECTURE conformance; required reviewer on `auth/`, `platform/`, `config.py`, new ports/profiles/ADRs.

**Cadence:** a short weekly migration sync (progress vs Appendix A/B, blockers, next gate).

**Announcements (each is a one-line message + a tracker entry):**
- **Phase Start:** "Entering Pn - owner X - soft-frozen files: [...]."
- **Freeze:** "Files [...] soft-frozen for Pn; route changes through X."
- **GO/NO-GO:** "Pn gate: GO (or NO-GO - reason)." Recorded in §19.
- **Phase Completion:** "Pn complete - exit criteria met - files unfrozen."
- **Rollback:** "Rolling back Pn - trigger: [...] - action: revert PR #..." (see §13).

*Exit/Completion:* every phase has a recorded Start, GO/NO-GO, and Completion announcement. *Verification:* the progress tracker (§19) contains the gate decisions and dates.

---

## Appendix D - Final Audit Checklist

*Purpose:* the comprehensive, multi-domain gate that (with Phase 11) formally closes the migration. *Why it exists:* §21 proves *code* readiness; this proves the *system* is production-ready across every dimension a Principal Engineer signs off on. *How it is used:* run once at Phase 11; each audit has explicit pass criteria; any fail blocks closure.

| Audit | Pass criteria |
|---|---|
| **Architecture** | import-linter clean (zero allow-list); all §18 fitness rules blocking+green; code matches ARCHITECTURE §1-§22 + Part II |
| **Security** | auth flows pass both profiles; scoping invariant holds (no cross-tenant access); secrets fail-fast + never logged; session rotation window works (ARCHITECTURE §K) |
| **Performance** | no error-rate/latency step-change vs pre-migration baseline; startup within expected bound; AI calls bounded+cancellable (ARCHITECTURE §I.1) |
| **Dependency** | no domain->infra imports; no cross-module table access; no circular deps; every external call via an approved adapter (Invariant §C.5) |
| **Cost** | AI cost/token per op flat or lower; cost metric emitted per op; ceilings enforced (ARCHITECTURE §J.3) |
| **AI** | provider-agnostic (swap by config); prompts versioned; structured-output validation + retry/fallback exercised; eval harness green (ARCHITECTURE §J) |
| **Documentation** | ARCHITECTURE + this plan consistent; ADRs current; module ownership table matches reality; no stale mode/flag docs |
| **Code Quality** | full suites green (backend+frontend); contract tests green for every impl; lint/format clean |
| **Dead Code** | zero compatibility shims; zero `get_*()` globals; zero dead adapters; Deletion Plan (§20) fully closed |
| **Technical Debt** | only ARCHITECTURE Appendix-N accepted debt remains, each with an exit trigger; zero migration hacks without a trigger |
| **Operational Readiness** | all profiles boot or fail fast; health/readiness green; correlation IDs joinable in logs; rollback rehearsed for critical phases; on-call/runbook pointers exist (ops repo, not here) |

*Exit/Completion:* every audit **Pass**. Only then is the migration declared complete (Phase 11 exit) and this document archived. *Verification:* audits map to runnable checks (CI, metrics from Appendix A, grep gates); manual audits (Documentation, Operational) are signed off by the Phase Owner + Architecture Reviewer.

---

*End of Implementation Plan (with appendices). Complete when §21 [x], Phase 11 [x], and Appendix D [x]; then archived - ARCHITECTURE.md governs thereafter.*
