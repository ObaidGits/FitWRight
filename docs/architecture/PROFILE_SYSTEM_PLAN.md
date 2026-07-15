# Professional Profile System — Architecture & Design

> This document is the design of record for the Professional Profile System (the
> canonical career document behind resumes, tailoring, cover letters, interview
> prep, portfolios, and personal websites). It captures the data model,
> rationale, and architecture decisions the implementation follows. Backend code
> under `app/profile/` references its sections.

---

## 1. Current-system analysis (verified against the codebase)

### 1.1 How resumes are stored
- Table `resumes` (SQLAlchemy `app/models.py`). The **entire** structured resume
  lives in one native-JSON column `processed_data` — `personalInfo`, `summary`,
  `workExperience[]`, `education[]`, `personalProjects[]`, `additional{}`,
  **plus** `sectionMeta[]` and `customSections{}`. Other columns: `content`
  (markdown or JSON string), `content_type`, `is_master`, `parent_id`
  (master→tailored link), `version` (optimistic-concurrency CAS token),
  `processing_status`, `cover_letter`, `outreach_message`, `interview_prep`
  (a *serialized JSON string* — inconsistent with `processed_data`), `title`,
  `filename`, `original_markdown`, `created_at`, `updated_at`.
- Partial unique index `ux_resumes_single_master (user_id, is_master)` → one
  master per user.
- **Duplication reality:** a tailored resume is a **full copy** of `ResumeData`
  in its own `processed_data`. `parent_id` links it to the master but nothing is
  shared or referenced; master edits do NOT propagate.

### 1.2 The existing "profile"
- There is **no** profile table. Four columns on `users` (`avatar_url`,
  `avatar_key`, `headline`, `location`, `links` JSON) added by migration `0013`,
  accessed via `app/auth/accounts.py` (NOT the `db` facade), exposed at
  `GET/PATCH /users/me/profile` + `POST /users/me/avatar`.
- Avatar pipeline (`app/storage/avatar.py`): magic-byte sniff (jpeg/png/webp, no
  SVG), byte + pixel caps, center-crop, re-encode to **WebP**, EXIF strip;
  stored via `StorageProvider` (local or Cloudinary), URL set only after
  successful store, old object GC'd. This is production-grade and **reusable
  as-is** for profile pictures.

### 1.3 Parser, tailoring, versioning, facade
- Parser: `parse_resume_to_json(markdown) -> ResumeData dict` (LLM + date
  restore + `ResumeData.model_validate`). Upload flow creates the master, parses,
  and captures an `original` version snapshot.
- Tailoring: `improve/preview` (+ SSE stream) → `improve/confirm` creates a NEW
  tailored resume (full `processed_data` copy, `parent_id`=master) + an
  `improvement` row + an `ai` version snapshot + auto-creates a tracker
  Application.
- Versioning: `resume_versions` (owned, user-scoped) stores **gzip** snapshots
  of `processed_data`, deduped by `content_hash`, capped, always keeping the
  oldest `original`. `app/versions/service.py` is a clean, reusable pattern.
- Data access: `app/database.py` `Database` facade — every owned table is
  registered in `Repo.OWNED_TABLES` and every query is `user_id`-scoped. New
  owned tables must go through this facade.
- Frontend: React Query (`queryKeys`), atelier design system, `(app)` route
  group + `sidebar.tsx`/`nav-items.ts`, and a content-first single-surface
  resume editor (`app/(app)/resumes/[id]/page.tsx`) that already edits the full
  `ResumeData` incl. section reorder/custom sections.

### 1.4 Issues found (to fix as part of this work)
1. Extended profile data lives on the **auth identity table** (`users`) — mixes
   concerns and can't version/extend cleanly.
2. `interview_prep` is a serialized JSON string while `processed_data` is native
   JSON — normalize the profile design to native JSON to avoid repeating this.
3. Two `updateProfile` functions on the frontend (`lib/api/auth.ts` name-only vs
   `lib/api/profile.ts` extended) — will be disambiguated.
4. Tailored resumes duplicate the master wholesale with no provenance — we can’t
   currently answer "which fields did the user override?" (blocks smart sync).

---

## 2. The central design decision (zero-trust critique of "normalize everything")

The brief proposes full relational normalization (Experience IDs, Education IDs,
Skills IDs; resumes reference profile sections; only overrides stored). **I
recommend against full normalization** and instead a **document-oriented Profile
with provenance-tracked generation.** Justification (Principal-level):

- **Dominant access pattern is single-user, whole-document.** Every real read is
  "load *my* profile" / "load *my* resume", and every consumer (render engine,
  tailoring pipeline, PDF export, version snapshots, ATS scoring) needs the
  *entire* structured document at once. A per-user JSONB document is an O(1)
  single-row read. Full normalization turns each load into a multi-table join
  fan-out — **worse** latency at millions of users for exactly the hot path the
  brief says to optimize.
- **Sent/tailored resumes MUST be immutable snapshots.** A resume tailored and
  sent for Job X must not silently change when the profile is later edited.
  "Resume references profile; only overrides stored" breaks this invariant and
  is a correctness bug for the product’s core artifact. Resumes are
  point-in-time documents by nature.
- **Consistency with the existing engine.** `resumes.processed_data` is already
  a JSON document; reusing that shape lets the Profile share validators, the
  render engine, the tailoring pipeline, and the gzip version-snapshot
  infrastructure with **zero** new serialization formats.
- **The duplication that actually matters** is not "resume repeats profile
  fields" (a resume is a derived artifact — some duplication is *correct*); it’s
  (a) tailored resumes being full copies with **no provenance**, and (b) history
  blowing up storage. We solve (a) with **provenance metadata + selective sync**
  and (b) with the **existing gzip snapshots** (≈10× compression) — not by
  fragile body-diffing.

**Where light normalization DOES pay off (and will be used):** a global,
**shared skills dictionary** (`skill_taxonomy`) for normalization/autocomplete
(e.g. "JS"→"JavaScript") — a small reference table shared across all users, not
per-user rows. Optional, additive, and it powers AI skill-normalization and
future search without touching the profile document.

> Net: **canonical Profile = one JSONB document per user**; resumes are
> **generated snapshots** carrying provenance so we can offer safe, previewed,
> selective sync. This gives the brief’s single-source-of-truth + minimal
> *meaningful* duplication goals **and** the lowest-latency hot path.

---

## 3. Data model

### 3.1 New table `profiles` (owned, user-scoped)
```
profiles
  id            str  PK
  user_id       str  FK users.id ON DELETE CASCADE, UNIQUE, indexed  (one profile per user)
  data          JSON (JSONB on PG)   -- canonical ProfileData document (see 3.3)
  completeness  int  default 0       -- 0..100 cached completion score (cheap reads)
  version       int  NOT NULL default 1  -- optimistic-concurrency CAS (same pattern as resumes.version)
  created_at    str
  updated_at    str
```
- Registered in `Repo.OWNED_TABLES`; all access via the `db` facade, `user_id`-scoped.
- `data` is JSONB on Postgres → future GIN index available for skill/keyword
  search **without** schema change (future-proof, no migration debt).

### 3.2 New table `profile_versions` (owned) — mirrors `resume_versions`
Immutable gzip snapshots of `profile.data` with `source ∈
{manual, import, merge, ai, migration}`, `content_hash` dedupe, `label`, cap +
prune. Reuses `app/versions/*` compression + service pattern (new thin service
or a generalized `SnapshotService`).

### 3.3 `ProfileData` document schema (canonical, superset of `ResumeData`)
Pydantic `app/schemas/profile_data.py`. Backward-compatible superset so a
`ResumeData` can be **derived by projection** and a parsed resume can be
**merged in**:
```
ProfileData:
  # identity / header
  personalInfo: { name, title/headline, email, phone, location, timezone,
                  website, linkedin, github, avatarUrl (reference only) }
  summary: str
  availability: { status: open|not_looking|open_to_offers, noticePeriod?, updatedAt? }
  preferences: { roles[], remote: onsite|hybrid|remote|any, relocation: bool,
                 workAuthorization?, salaryExpectation? (future-ready, private) }
  # core sections (each item carries a STABLE uid — see 3.4)
  workExperience: Experience[]        # superset of resume Experience (+ uid, current, location, tech[])
  education:      Education[]          # + uid
  personalProjects: Project[]         # + uid
  skills: { technical[], soft[], languages[], tools[] }   # richer than additional{}
  certifications: Certification[]     # + uid, issuer, date, url
  achievements: Achievement[]
  awards: Award[]
  publications: Publication[]         # future sections modeled now
  patents: Patent[]
  volunteer: Volunteer[]
  organizations: Organization[]
  interests: str[]
  links: { label, url, kind }[]       # migrates users.links here
  customSections: { key -> CustomSection }   # same shape as resume custom sections
  sectionMeta: SectionMeta[]          # ordering/visibility for a *generated* resume
  meta: { schemaVersion: int, source, lastImportedResumeId? }
```
- **Provenance is not stored in the document** (keeps it clean); it is computed
  at generation time and stored per-resume (see 3.5).

### 3.4 Stable entity UIDs
Every list item (experience/education/project/cert/…) gets a stable `uid`
(uuid) on creation. This is the key that powers: merge/dedup (match by uid or
similarity), reordering, selective sync, and "which resume items came from which
profile item" — **without** a normalized join table. UIDs live inside the JSON.

### 3.5 Resume ↔ Profile link (provenance, minimal + additive)
Add to `resumes.processed_data.meta` (no new columns): when a resume is
**generated from** a profile, store `derivedFromProfileVersion` and a per-item
`profileUid` on each generated item, plus an `overrides` set marking items/fields
the user edited after generation. This makes sync **precise and safe**:
- "Update this resume from profile" → refresh only non-overridden items.
- "Update profile from this resume" → propose only changed items for review.
- No physical references, no join cost, immutable-snapshot invariant preserved.

### 3.6 Profile picture (decoupled, already correct)
Keep `users.avatar_url`/`avatar_key` (identity-level, already in the session).
`ProfileData.personalInfo.avatarUrl` is a **reference** to that URL. Resumes and
future portfolio/website read the same URL via a per-artifact `includePhoto`
flag — bytes stored **once**. No future migration needed; no coupling to resumes.

---

## 4. Backend architecture (mirrors existing domains)

```
app/profile/
  schemas.py        # ProfileData + request/response DTOs (+ MergePlan, SyncPlan)
  service.py        # ProfileService: get/upsert (CAS), completeness score, derive-resume
  merge.py          # MergeEngine: parsed-resume → profile diff w/ similarity+confidence
  sync.py           # SyncService: profile↔resume selective sync plans + apply
  versions.py       # thin wrapper over the shared snapshot service (source=profile)
app/routers/profile.py   # REST endpoints (below)
app/database.py          # + profiles / profile_versions facade methods (scoped)
```
- **Merge engine** (pure, testable, no I/O): inputs = existing `ProfileData` +
  newly parsed `ResumeData`; output = `MergePlan { additions[], updates[]
  (field-level, with confidence 0..1), conflicts[], duplicates[] }`. Similarity:
  normalized string + date-range overlap for experiences/education; exact/alias
  for skills (via `skill_taxonomy`). **Never auto-applies** — the user previews
  and selects. Applying writes one new profile version (`source=merge|import`).
- **Resume generation from profile** (`derive-resume`): pure projection
  `ProfileData → ResumeData` (+ chosen `sectionMeta`/template), reusing the
  existing render/export path. Optionally persists as a new resume (master or
  variant) stamped with provenance (3.5).
- Reuses: avatar pipeline, `complete_json` LLM layer (summary/bullets/skill
  normalization — **never fabricates**, truthfulness rules already exist),
  rate-limiting deps, error envelope, event bus, notifications.

---

## 5. REST API (all `user_id`-scoped, CSRF + rate-limited)
```
GET    /profile                      -> ProfileData + completeness + version
PATCH  /profile                      -> partial update (If-Match version CAS)  [inline edits/autosave]
POST   /profile/import/preview       -> {resumeId|upload} -> MergePlan (no write)
POST   /profile/import/apply         -> apply selected MergePlan entries -> new version
POST   /profile/photo                -> reuse avatar pipeline (or alias /users/me/avatar)
DELETE /profile/photo
POST   /profile/generate-resume      -> derive ResumeData; optional persist -> resume_id
POST   /profile/sync/preview         -> {resumeId, direction} -> SyncPlan
POST   /profile/sync/apply           -> apply selected sync entries (CAS both sides)
GET    /profile/versions             -> metadata list
GET    /profile/versions/{id}        -> full snapshot
POST   /profile/versions/{id}/restore
GET    /profile/completeness         -> score + prioritized "missing info" suggestions
POST   /profile/ai/{action}          -> improve-summary | rewrite-bullets | suggest-skills | normalize-skills
GET    /skills/search?q=             -> taxonomy autocomplete/normalization
```
CAS uses the existing `If-Match`/409-envelope pattern from resumes (frontend
`ResumeConflictError` analog).

---

## 6. Frontend — a premium `/profile` workspace

- New route `app/(app)/profile/page.tsx` inside the fixed dashboard shell; add a
  **Profile** entry to `nav-items.ts` (top of PRIMARY_NAV — it’s the new home
  base) with graceful mobile bottom-nav handling.
- Architecture: reuse atelier primitives + the resume editor’s proven
  single-surface, inline-edit, autosave, draft-recovery, unsaved-guard, and
  section-reorder patterns (share `CustomSectionsEditor`, `ItemEditor`,
  `RenderTemplate`). No duplicated UX.
- Layout: sticky **profile header** (avatar, name, headline, availability pill,
  completeness ring, actions) → **quick stats** → collapsible **sections**
  (Experience timeline, Education, Projects, Skills, Certifications,
  Achievements, Languages, Links, Custom) → **Resume actions** (Generate /
  Tailor / Export) → **Recent activity / Version history**.
- UX: inline editing, optimistic updates + React Query invalidation (reuse the
  auto-refresh helpers), drag-reorder, collapsible sections, beautiful empty
  states, a completeness score with "add X to reach 90%" nudges, guided
  first-run, keyboard shortcuts (⌘S), AI assists (explicit, cost-aware,
  never-fabricate). Import flow = a **MergePlan review UI** (accept/reject per
  entry, conflict + duplicate badges, diff preview).
- Performance: skeletons, lazy sections, image optimization (WebP already),
  minimal rerenders, `staleTime` caching, no CLS.
- A11y: WCAG AA — semantic headings, labeled fields, focus management, reduced
  motion, touch targets (reuse the audited components).

---

## 7. Migration & backward compatibility (no data loss, no downtime)

- **Alembic `0015`**: create `profiles` + `profile_versions` (nullable/no
  backfill in the migration itself — additive, reversible). Zero impact on
  existing reads.
- **Backfill (lazy + batch, non-destructive):** on first `/profile` load (or a
  one-shot idempotent backfill), if a user has no profile, derive `ProfileData`
  from their **master resume’s `processed_data`** (falling back to
  `users.headline/location/links/avatar_url`), write it as
  `source=migration` v1. Resumes are **never modified**. Users with no resume
  get an empty profile + onboarding.
- **`users.headline/location/links`** remain readable; the profile becomes the
  source of truth and settings’ Profile card is repointed to the new document.
  Columns can be dropped in a later migration once fully cut over (kept for
  rollback safety initially). `avatar_url/avatar_key` stay on `users`.
- Fully backward compatible: existing resume upload/edit/tailor/export flows are
  untouched; the Profile is additive and derives from what already exists.

---

## 8. Security
Reuse existing guards: session + `user_id` scoping on every facade query, CSRF
on mutations, per-user rate limits on AI + import + photo, the hardened avatar
pipeline (magic-byte sniff, caps, re-encode, EXIF strip), Pydantic
`extra="forbid"` + length bounds + control-char rejection on all text, mass-
assignment prevention (explicit DTOs, never persist raw client JSON), CAS to
prevent lost updates, and moderation-safe handling of any future public profile.

---

## 9. Testing (must all pass before "done")
- **Unit:** ProfileData validators; projection `ProfileData→ResumeData`;
  completeness scoring; merge similarity/confidence/dedup; sync plan generation.
- **Integration (API):** get/patch (+CAS 409), import preview/apply, generate-
  resume, sync preview/apply, photo upload, versions list/restore, skills search.
- **Migration:** backfill from master resume → correct ProfileData; idempotency;
  no resume mutation; empty-profile path.
- **Merge/sync/conflict:** no duplicate experiences; no silent overwrite;
  override-preservation; immutable sent-resume invariant.
- **Frontend:** profile render, inline edit + autosave + CAS conflict UX, import
  review UI, generate-from-profile, a11y (radiogroup/labels/focus), responsive.
- **Storage/security:** avatar hardening reuse; rate-limit; validation-bypass.
- **Regression:** full existing backend + frontend suites stay green; build ok.

---

## 10. Phased delivery (each phase independently shippable + verified)
1. **P1 — Foundation:** `profiles`/`profile_versions` tables (alembic 0015),
   `ProfileData` schema + validators, facade methods, `ProfileService`
   (get/patch/CAS/completeness), lazy backfill from master resume, `GET/PATCH
   /profile`. Tests. *No UX change yet beyond data.*
2. **P2 — Profile workspace UI:** `/profile` page + nav entry, inline edit /
   autosave / reorder / sections (reusing editor components), completeness,
   empty states, a11y. Repoint settings Profile card.
3. **P3 — Import & Merge engine:** upload/select resume → MergePlan preview UI →
   apply; provenance stamping. Wire the existing parser.
4. **P4 — Generate & Sync:** generate resume from profile; profile↔resume
   selective sync (preview/apply) with provenance + version snapshots.
5. **P5 — AI assists + skills taxonomy + versions UI:** explicit AI actions,
   skill normalization/search, profile version history/restore UI.
6. **P6 — Future hooks (design-only stubs, no dead code):** portfolio/website
   projection interface, JSON-Resume/LinkedIn export contracts — enabled by the
   canonical document with no schema change.

---

## 11. Open risks & mitigations
- **Scope is large** → strictly phased; each phase ships behind the previous and
  is fully tested; no phase leaves TODOs.
- **Merge false-positives/negatives** → conservative similarity thresholds,
  everything user-reviewed, nothing auto-applied, full undo via versions.
- **Profile/resume drift confusion** → provenance + explicit, previewed,
  selective sync; never surprise the user; immutable sent resumes.
- **JSONB query needs later** → GIN-indexable already; skills taxonomy covers
  the near-term search need without per-user normalization.
- **Double source of truth during rollout** → profile derives from master; write
  paths cut over section-by-section; `users.*` columns retained for rollback.

---

## 12. Definition of done (zero-trust checklist)
Backend + frontend suites green; build succeeds; profile create (manual +
from-resume) works; parser populates profile via reviewed merge; generate-from-
profile + export works; profile↔resume sync verified; photo upload verified;
mobile + a11y + performance verified; migration verified (no data loss);
**no new unnecessary duplication** (resumes are derived snapshots with
provenance, not silent copies); no architectural violations; no TODOs/
placeholders. Final Principal-level review pass repeated until no significant
architectural/UX/perf/storage weakness remains.
```

---

# Part II — Strengthened v2 architecture (integrating the 20 focus areas)

> This section **extends** (does not replace) Parts 1–12. It folds in twenty
> cross-cutting concerns and records the binding architecture decisions (ADRs).
> The core stance is unchanged and reaffirmed: **the canonical Profile is one
> document per user; resumes are generated, provenance-stamped snapshots.**

## 13. The twenty focus areas — how each is satisfied

1. **Professional Identity layer.** A first-class `identity` block on
   `ProfileData` (headline, currentRole/Company, yearsExperience, industry,
   careerStage, targetRoles, careerObjective, employmentStatus, availability,
   remotePreference, relocation, noticePeriod, workAuthorization, visaStatus,
   preferredLocations, salaryExpectation (private), careerVisibility). This is
   the "who am I professionally" header that every projection and AI assist
   reads first.

2. **Canonical Skill Engine.** Each `Skill` carries `canonical` (normalized id),
   `aliases[]`, `displayName`, `category`/`subcategory`, `yearsExperience`,
   `proficiency`, `lastUsed`, plus `aiNormalizedName`. A global `skill_taxonomy`
   reference table (shared, not per-user; added in a later phase) powers
   alias→canonical normalization and autocomplete. The engine is a **pure**
   normalizer (`app/profile/skills.py`) so it is deterministic and testable with
   no I/O; the taxonomy is an optional accelerator, never a hard dependency.

3. **Knowledge-Graph relations (lightweight).** Rather than a physical graph DB,
   relations are expressed by **stable uids + typed references inside the
   document**: an achievement/project may reference `experienceUid`, a skill may
   list `evidenceUids` (experiences/projects that demonstrate it). This yields
   the graph's query value ("which roles prove Python?") with zero join cost and
   no new store. Documented as ADR-7.

4. **AI Memory (separate namespace).** `aiMemory` is a distinct top-level block
   (writing style, tone, ATS preference, template preference, target
   companies/industries, dos/don'ts). It is **never** projected into a resume —
   it steers generation, it is not resume content. Kept separate so profile
   reads for rendering never accidentally leak preferences.

5. **Field Provenance.** Provenance is **not** stored inline on every field
   (keeps the document clean and diff-friendly). Instead a compact
   `meta.provenance` map keys entity-uid/field-path → `{source, at, confidence,
   verificationSource}`. `source ∈ {manual, import, merge, ai, migration}`.
   Absent entry ⇒ `manual` (the safe default). ADR-9.

6. **Confidence metadata.** Confidence (0..1) rides alongside provenance for
   AI/merge-derived values, and per-`Skill` as a first-class field. The UI
   surfaces low-confidence values for review; merge never auto-applies < a
   configurable threshold.

7. **Stable IDs everywhere.** Every list item has a `uid` (uuid4) minted on
   creation and never reused. UIDs are the join key for provenance, merge/dedup,
   reordering, KG relations, and resume↔profile sync. ADR-8.

8. **Merge Engine (subsystem).** `app/profile/merge.py` — pure functions:
   `build_merge_plan(existing: ProfileData, incoming: ResumeData) -> MergePlan`.
   Similarity: normalized-string + date-range overlap for experience/education,
   canonical/alias match for skills. Output = `{additions, updates (field-level
   + confidence), conflicts, duplicates}`. Never auto-applies; the user reviews.
   Applying writes one `source=merge|import` version. (Phase P3.)

9. **Completion Engine.** `app/profile/completion.py` — weighted score (0..100)
   + prioritized suggestions ("add a summary to reach 80%"). Weights are a
   single documented table so the score is explainable and tunable. Cached in
   `profiles.completeness` for O(1) list reads; recomputed on every write.

10. **Projection Engine (sole boundary).** `app/profile/projection.py` —
    `ProjectionEngine.project_resume(profile, options) -> ResumeData dict` is the
    **only** code path that turns a profile into resume-shaped data. All
    resume generation (manual "generate", future tailoring-from-profile,
    portfolio export) funnels through it, so the contract is enforced in exactly
    one place. ADR-6.

11. **Event Architecture.** Profile writes emit domain events on the **existing
    transactional `outbox`** (`profile.upserted`, `profile.version.created`) in
    the same transaction as the write — reusing the established consumer/idempotency
    platform. Search indexing / analytics consume asynchronously; a consumer
    failure never fails the user's write.

12. **Analytics.** Derived, content-safe metrics only (completeness over time,
    skill counts, section coverage) computed from events/snapshots — never PII in
    analytics rows. Reuses the `metrics_daily` rollup pattern. Design-only in P1;
    wired opportunistically.

13. **Storage Optimization.** Live document in `profiles.data` (JSONB on PG,
    GIN-indexable later with no migration). History in `profile_versions` as
    **gzip** snapshots (~10× smaller), content-hash **deduped**, **debounced**,
    **capped** with prune — identical mechanics to `resume_versions`. No
    body-diffing, no per-field history tables.

14. **Public Profile (future-ready).** `identity.careerVisibility` +
    `salaryExpectation` privacy are modeled now; a future public projection is
    just another Projection Engine mode + a moderation gate. No schema change
    required. Design-only.

15. **Search Strategy.** Near-term: the existing `search_documents` + FTS/GIN
    indexer gains a content-safe profile projection. Skill search rides the
    canonical taxonomy. Long-term JSONB GIN is available without migration.

16. **Import Architecture.** Import = parse (existing `parse_resume_to_json`) →
    `MergePlan` (preview, no write) → user selects → apply (one version). Sources:
    existing resume, uploaded file, and future JSON-Resume/LinkedIn — all normalize
    to `ResumeData`/partial-`ProfileData` then go through the same merge path.

17. **Extension Architecture.** New sections are additive: add a typed list with
    uids + a `sectionMeta` entry; the Projection Engine and editor iterate
    `sectionMeta`, so a new section needs **no** projection/editor rewrite.
    `customSections` already covers fully user-defined sections.

18. **ADRs.** Recorded in §15 below (9 decisions).

19. **Synchronization Strategy.** Resume↔profile sync is **explicit, previewed,
    selective, provenance-aware** (never silent). Profile→resume refreshes only
    non-overridden items; resume→profile proposes changed items for review.
    Sent/tailored resumes remain immutable snapshots. Both sides CAS-guarded.
    ADR-10. (Phase P4.)

20. **Scalability.** Hot path is an O(1) single-row read/write (CAS). No fan-out
    joins. History is compressed + capped. Events are async. Taxonomy is a small
    shared table. The design scales linearly with users, not with a join
    explosion — the core reason we chose document-oriented over full
    normalization.

## 14. Concrete P1 module surface (what ships first)

```
app/profile/
  __init__.py
  schemas.py       # ProfileData (+ identity/skills/aiMemory/meta) + DTOs
  projection.py    # ProjectionEngine.project_resume  (sole profile→resume path)
  completion.py    # weighted completeness + suggestions (pure)
  backfill.py      # ResumeData/processed_data → ProfileData (non-destructive)
  versions.py      # thin snapshot service (source=profile), mirrors versions/service
  service.py       # ProfileService: get_or_create/patch(CAS)/completeness/version
app/routers/profile.py         # GET/PATCH /profile, /completeness, /generate-resume, versions
app/database.py                # + profiles / profile_versions facade methods (scoped)
app/models.py                  # Profile + ProfileVersion
app/repository.py              # OWNED_TABLES += profiles, profile_versions
alembic/versions/0015_*.py     # create both tables (additive, reversible)
app/config.py                  # profile_* settings (enabled, cap, debounce)
```
Phases P2–P5 (workspace UI, import/merge, generate/sync, AI+taxonomy+versions UI)
are unchanged from §10 and build strictly on this foundation.

## 15. Architecture Decision Records (binding)

- **ADR-6 — Projection Engine is the single profile→resume boundary.** Every
  resume produced from a profile goes through `project_resume`. *Why:* one place
  to enforce the contract, evolve templates, and guarantee immutable snapshots.
- **ADR-7 — Knowledge-graph via in-document uid references, not a graph store.**
  *Why:* delivers relational query value at zero join/infra cost; reversible if a
  real graph is ever justified.
- **ADR-8 — Stable per-item uids inside the JSON document.** *Why:* the join key
  for provenance/merge/sync/reorder without a normalized schema.
- **ADR-9 — Compact provenance map in `meta`, not inline per field.** *Why:*
  keeps the document clean/diffable; default-to-`manual` needs no backfill.
- **ADR-10 — Sync is explicit, previewed, selective, CAS-guarded; sent resumes
  are immutable.** *Why:* correctness of the product's core artifact.
- **ADR-11 — AI Memory is a separate namespace, never projected.** *Why:*
  preferences steer generation but are not resume content; prevents leakage.
- **ADR-12 — Canonical skills via a pure normalizer + optional shared taxonomy.**
  *Why:* deterministic/testable; the taxonomy accelerates but is never required.
- **ADR-13 — History via gzip/dedupe/cap snapshots, reusing `resume_versions`
  mechanics.** *Why:* proven, cheap, no new serialization format.
- **ADR-14 — Profile is additive + reversible; `users.*` profile columns retained
  during rollout.** *Why:* zero-downtime, rollback-safe cutover.

## 16. Synchronization policy (detail for P4)
- **Profile→Resume (generate/refresh):** projection stamps each generated item
  with its `profileUid` and records `derivedFromProfileVersion` in
  `resume.processed_data.meta`. A later "refresh from profile" recomputes only
  items whose `profileUid` is **not** in the resume's `overrides` set.
- **Resume→Profile (adopt edits):** diff the resume's items (by `profileUid`)
  against the profile; propose changed/added items as a `MergePlan` for review.
- **Invariants:** never mutate a resume that was *sent* (tracked via the
  application record); always CAS both sides; nothing auto-applies.


---
---

# v2 — Enterprise Hardening (zero-trust review + integrated improvements)

This section supersedes/refines v1 where they differ. It integrates the missing
capabilities into the architecture (not as bolt-ons) and records ADRs.

## A. Strengthened `ProfileData` document (canonical, JSONB)

Four **top-level layers**, cleanly separated (fixes "too resume-centric" + "AI
memory mixed with professional data"):

```
ProfileData v2:
  schemaVersion: int                         # explicit, for forward migration

  identity: ProfessionalIdentity             # LONG-TERM career identity (§1)
    headline, currentRole, currentCompany, yearsExperience, careerStage,
    primaryIndustry, careerObjective, targetRoles[], preferredDomains[],
    employmentStatus, availability{status,noticePeriodDays,updatedAt},
    remotePreference, relocation, preferredLocations[], workAuthorization,
    visaStatus, salaryExpectation{min,max,currency,period,private:true},
    visibility: private|unlisted|public       # §14 public-profile future

  contact: ContactInfo                        # name,email,phone,location,timezone,avatarUrl(ref),links[]

  content: ProfileContent                     # the "resume-able" material
    summary
    experiences: Experience[]                 # each: uid, + skillRefs[](uids) §3
    education: Education[]                     # uid
    projects: Project[]                       # uid, skillRefs[]
    certifications: Certification[]           # uid, issuer, issuedAt, url, skillRefs[]
    achievements, awards, publications, patents, volunteer, organizations,
    talks, courses, openSource: Entity[]      # §17 all modeled now
    interests: str[]
    skills: Skill[]                           # canonical Skill objects §2
    customSections: {key -> CustomSection}
    sectionMeta: SectionMeta[]                # default projection ordering

  aiMemory: AIMemory                          # §4 SEPARATE from professional data
    writingStyle, resumeTone, atsStrictness, preferredTemplate,
    favoriteWording[], targetCompanies[], preferredIndustries[],
    aiBehavior{}, coachingPrefs{}             # never fed as "facts", only as prefs

  provenance: { <fieldPath|uid> -> Provenance }   # §5 §6 (see below)
  meta: { createdAt, updatedAt, lastImport{source,at,resumeId}, counters{} }
```

### Stable IDs (§7) — invariant
Every reusable object (experience/education/project/certificate/achievement/
skill/language/publication/patent/custom-section/…) carries a **stable `uid`**
(uuid) assigned on creation. **Array position is never an identity.** All merge,
sync, reorder, provenance, and knowledge-relations key off `uid`.

### Knowledge relations (§3) — no graph DB
Relationships are modeled as `uid` references **inside** the document:
`Experience.skillRefs: [skillUid]`, `Project.skillRefs`, `Certification.skillRefs`.
This yields an in-document graph (Experience→Skills→Projects→Certs) that AI can
reason over, with zero join cost and no new infrastructure. A `relations`
resolver in the service materializes adjacency on demand.

### Canonical Skill model (§2)
```
Skill: { uid, canonical, displayName, aliases[], category, subcategory,
         yearsExperience?, proficiency: novice|intermediate|advanced|expert?,
         lastUsed?, confidence: 0..1, source: Provenance.source,
         normalizedFrom?, embedding?: null (reserved) }
```
Backed by an optional **global `skill_taxonomy`** table (shared, not per-user)
for normalization/aliases/autocomplete — powers `/skills/search`, AI
normalization, and future ontology/embeddings without per-user normalization.

### Provenance + confidence (§5 §6)
```
Provenance: { source: manual|resume_upload|linkedin_import|github_import|
              ai_suggestion|previous_resume|merge|migration|unknown,
              confidence: 0..1, at: iso, detail?: str }
```
Stored in `ProfileData.provenance` keyed by field-path (scalars) or `uid`
(entities). Every write records/updates provenance. **Provenance is never
lost**; merges attach source+confidence to each accepted change.

## B. Subsystems (integrated, not appended)

### Projection Engine (§10) — the ONLY projection boundary
`app/profile/projection.py`. **Nothing** generates a resume/portfolio/website/
export directly from `ProfileData`. All outputs go through one boundary:
```
ProfileData --ProjectionEngine.project(target, options)--> TargetDocument
  targets: resume(ResumeData) | cover_letter_context | portfolio | website |
           linkedin | json_resume | europass | public_profile
```
v1 delivers `resume`; other targets are registered projectors added later with
**no core change**. Projection is a pure function (testable, cacheable). Tailoring
consumes the projected `ResumeData` exactly as today.

### Merge Engine (§8) — dedicated subsystem
`app/profile/merge/` — pure, deterministic, no I/O:
`MergeEngine.plan(existing: ProfileData, incoming: ParsedResume|Import) -> MergePlan`.
`MergePlan { additions[], updates[](field-level w/ confidence), conflicts[],
duplicates[](similarity score + matched uid) }`. Similarity: normalized string +
date-range overlap (experience/education), taxonomy alias match (skills). UI does
accept/reject/accept-all/reject-all with side-by-side diff; **apply** writes one
version (`source=merge|import`) and records provenance per change; **undo** via
version restore. **Never silently overwrites.** All importers (§16: resume,
LinkedIn, GitHub, JSON-Resume, Europass, Indeed, portfolio, manual) normalize to
a common `Import` shape and share this one pipeline.

### Completion Engine (§9)
`app/profile/completion.py` — weighted scoring (not naive %). Section weights +
per-field weights, returns `{ score, band, missing[](prioritized), nextActions[] }`.
Extensible weight table; future AI recommendations plug in as extra `nextActions`.

### Synchronization (§19) — explicit rules
- **Profile → Resume:** only via Projection. Generating a resume stamps
  `derivedFromProfileVersion` + per-item `profileUid` (in `processed_data.meta`).
- **"Refresh resume from profile":** updates only items **not** marked overridden
  (provenance/override set); previewed + selective; CAS on both sides.
- **Resume edits → Profile:** never automatic; an explicit, previewed "promote to
  profile" produces a MergePlan.
- **Immutable rule:** a profile change **never** mutates any existing resume
  (historical/sent resumes are frozen snapshots). Tailored/export/portfolio/
  public are all downstream projections, never live-linked.

### Domain events (§11) + analytics (§12)
`app/profile/events.py` defines event constants (`ProfileUpdated`,
`ResumeGenerated`, `ResumeImported`, `MergeCompleted`, `AvatarUpdated`,
`ProfileCompleted`, `VersionCreated`). Emitted through the **existing** event bus
(`app/events`) — no new infra. Analytics is architected as **event consumers**
(counters in `meta.counters` + emitted events); no analytics store built now.

## C. Storage decisions (§13) — every choice justified
1. **One `profiles` row (JSONB `data`) per user** — the hot path is whole-
   document single-user reads; JSONB = O(1) read, GIN-indexable later. Chosen
   over normalized tables to avoid join fan-out at millions of users.
2. **`profile_versions` reuses the gzip snapshot engine** — ~10× compression,
   dedupe by `content_hash`, cap + prune, always keep oldest — identical to
   `resume_versions` (code reuse, no new format).
3. **No resume body-diffing** — gzip already compresses; diffing is fragile/low-
   ROI. Duplication that matters (silent tailored copies) is solved by
   provenance, not physical dedup.
4. **Skill taxonomy is global/shared**, not per-user rows — the only place light
   normalization pays off (search/normalize/autocomplete).
5. **Avatar bytes stored once** (existing pipeline); documents hold a URL
   reference only — decoupled from resumes (§ profile picture).
6. **Provenance/confidence live inside the JSON** (no extra tables/joins).

## D. Search (§15) & scalability (§20)
- **Search:** JSONB GIN index reserved (PG); skills covered by the taxonomy +
  `/skills/search`; the existing `search_documents` FTS store gains a
  `profile` node type later (same pattern as resumes). Semantic/embeddings:
  `Skill.embedding` field reserved; no redesign needed.
- **Scalability:** single-row reads (cache in React Query + short server TTL);
  writes via atomic CAS (no lost updates); gzip-capped versions; avatar on
  CDN/Cloudinary; projection is pure + cacheable; merge is bounded (per-import,
  user-reviewed, not a background fan-out). No cross-user queries on the hot path.

## E. Architectural Decision Records (§18)

- **ADR-P1 Document-oriented Professional Profile.** One JSONB doc/user.
  *Why:* hot path is whole-document single-user reads; matches existing
  `processed_data`; O(1) reads; GIN-indexable. *Over:* full normalization
  (join fan-out, worse latency, breaks immutable-resume invariant).
- **ADR-P2 Projection Engine as sole boundary.** All outputs derive via one pure
  projector. *Why:* one place to evolve resume/portfolio/website/export; testable;
  prevents divergent generation paths. *Over:* ad-hoc per-feature generation.
- **ADR-P3 Provenance + confidence on every field/entity.** *Why:* enables safe
  merge/sync, audit, and never-overwrite guarantees. *Over:* sourceless data
  (unsafe merges, silent overwrites).
- **ADR-P4 Merge Engine as pure, user-reviewed subsystem.** Plan→review→apply→
  version. *Why:* correctness + no silent overwrite + shared by all importers.
  *Over:* inline auto-merge (data loss risk).
- **ADR-P5 Synchronization = one-way via Projection; resumes are immutable
  snapshots.** *Why:* a sent resume must never change under the user. *Over:*
  live references (correctness bug).
- **ADR-P6 Stable UIDs, never array positions.** *Why:* merge/sync/reorder/
  relations integrity. *Over:* positional identity (fragile).
- **ADR-P7 Canonical Skill model + global taxonomy.** *Why:* search/AI/analytics/
  normalization foundation. *Over:* plain strings (no reasoning, dup skills).
- **ADR-P8 Versioning reuses the gzip snapshot engine.** *Why:* proven, compact,
  deduped, consistent with resumes. *Over:* new versioning format.
- **ADR-P9 Extension via typed sections + customSections, modeled now.** *Why:*
  volunteer/research/patents/publications/talks/courses/open-source added with no
  schema change. *Over:* per-feature migrations.
- **ADR-P10 AI Memory separated from professional data.** *Why:* preferences are
  not facts; keeps truthfulness guarantees; independent evolution. *Over:*
  mixing prefs into content (fabrication risk, coupling).

## F. UX & product refinements (integrated)
- Single-surface `/profile` reusing the editor’s inline-edit/autosave/reorder/
  section components (no duplicate UX); MergePlan review UI (side-by-side diff,
  accept/reject/all, conflict + duplicate badges); completeness ring + prioritized
  next-actions; guided first-run; optimistic updates + CAS-conflict UX; keyboard
  shortcuts; drag-reorder; beautiful empty states; progressive disclosure of
  advanced identity/AI-memory fields; AI assists explicit + cost-aware + never-
  fabricate; full a11y + responsive (reuse audited components).

## G. Revised phasing (implementation order)
- **P1 Foundation (this pass):** models `profiles`+`profile_versions` (alembic
  0015), `ProfileData v2` schema (identity/content/aiMemory/provenance/skills/
  stable-uids/relations/extensions), facade + OWNED_TABLES, `ProfileService`
  (get-or-create + backfill-from-master, CAS patch, version snapshot),
  **Completion Engine**, **Projection Engine (resume target)**, events wired,
  endpoints `GET/PATCH /profile`, `GET /profile/completeness`,
  `POST /profile/generate-resume`, `GET /profile/versions[/{id}][/restore]`.
  Full backend tests. Backward compatible; resumes untouched.
- **P2** Frontend `/profile` workspace + nav. **P3** Merge/import pipeline + UI.
  **P4** Sync (profile↔resume) + provenance-aware refresh. **P5** AI assists +
  skill taxonomy + versions UI. **P6** portfolio/website/export projectors.

---

# Implementation status — P3–P6 (delivered)

All phases are implemented, wired, and tested (backend `pytest`, frontend
`vitest`, `tsc`, `eslint` all green; no regressions). This section documents the
as-built subsystems and the additional ADRs adopted during implementation.

## P3 — Intelligent Import & Merge Engine
- **Similarity Engine** (`app/profile/similarity.py`): pure, deterministic,
  field-weighted entity matching (experience/education/project/certification/
  achievement/skill) via `difflib` + token-set overlap. Two thresholds:
  `MATCH_THRESHOLD` (likely match → review) and `DUPLICATE_THRESHOLD` (same
  entity). An AI-assisted matcher can later refine the borderline band without
  changing the contract.
- **Merge Engine** (`app/profile/merge.py`): `build_merge_plan(existing, incoming)`
  → deterministic `MergePlan` of typed operations (`add`/`update`/`duplicate`/
  `conflict`) with per-op `default_resolution` (always non-destructive) and
  `allowed_resolutions`; `apply_merge_plan(existing, incoming, resolutions)` folds
  the resolved plan into a new `ProfileData`. **Stateless apply**: identical
  `(existing, incoming)` yields identical operation ids, so the client echoes the
  incoming candidate + resolutions and the server re-derives the plan. Manual data
  is never overwritten by default; updates preserve the existing `uid`
  (provenance/relations survive); added/replaced items stamped in
  `meta.provenance` (`source=import|merge`).
- **Import adapters** (`app/profile/import_adapters.py`): open/closed registry;
  `resume` and `json_resume` implemented; `linkedin`/`github`/`europass`/
  `portfolio` declared as discoverable stubs — wiring them needs no pipeline
  change.
- **API**: `POST /profile/import/preview`, `POST /profile/import/apply` (version
  CAS). **UI**: `components/profile/import-dialog.tsx` (source pick → side-by-side
  plan review with per-op resolution radios → apply).

## P4 — Projection & Synchronization Engine
- **Projection Engine** expanded (`projection.py`): `template`, per-section
  `sections` visibility overrides (non-mutating), and resume-specific top-level
  `overrides`, in addition to `include_photo`/`section_meta`. Still the *sole*
  ProfileData→ResumeData boundary; provenance-stamped.
- **Sync Engine** (`app/profile/sync.py`): `preview_sync` diffs a resume’s current
  data against a fresh projection (reusing the resume version-diff), `apply_sync`
  applies it under the resume’s **version CAS** and snapshots the pre-sync state
  (`manual`, "Synced from profile"). **Invariant enforced:** a resume referenced
  by any application in a non-`saved` status is **immutable** — apply is refused
  (409 `resume_locked`); the user regenerates instead. Preview stays read-only.
- **API**: `GET /profile/sync/{resume_id}` (preview diff), `POST /profile/sync/{resume_id}`
  (apply, CAS). Field-level diff surfaced for a future diff UI.

## P5 — AI Intelligence Layer
- **AI Memory** kept a separate namespace (never projected — ADR-P10). Endpoint
  `PUT /profile/ai-memory` (CAS); editable in the workspace "AI memory" tab.
- **Canonical Skill Engine** expanded (`skills.py`): `suggest_skills(query)`
  autocomplete over the known-canonical corpus; `ai.normalize_skills(profile)`
  pure dedupe+canonicalize (alias-merge). Endpoint `GET /profile/skills/suggest`.
- **AI Suggestions** (`app/profile/ai.py`): `suggest_summary` /
  `suggest_experience_bullets` improve *existing* content only, with a
  truthfulness-constrained prompt; **never fabricate** (refuse when there is
  nothing to improve) and degrade gracefully when no model is configured.
  Endpoint `POST /profile/ai/suggest`; "Improve with AI" wired on the summary.
- **Completion Engine** expanded with `compute_ats_readiness` and
  `compute_ai_readiness` (returned alongside the weighted score + suggestions).
- **Version UI**: `components/profile/version-history.tsx` (timeline + restore).

## P6 — Public Projection Platform
- **Projectors** (`app/profile/public.py`): `project_public_profile`
  (visibility-aware, **no private fields** — salary/visa/phone never leaked),
  `project_portfolio` (projects-first + certifications), `export_json_resume`
  (JSON Resume schema; **round-trips** with the json_resume import adapter).
- **API**: `GET /profile/public`, `GET /profile/portfolio`,
  `GET /profile/export/json-resume`. **UI**: `components/profile/export-menu.tsx`.
- Adding a future output (personal website theme, LinkedIn export) is a new pure
  projector — no storage/API redesign.

## Events & platform
- New event types (`app/events/types.py`): `profile.imported`, `merge.completed`,
  `resume.synced` (plus P1’s `profile.created/updated/completed/resume_generated/
  version_created`). Emitted best-effort to the transactional outbox so analytics/
  notification consumers stay decoupled from the write path.

## Additional ADRs (P3–P6)
- **ADR-P11 Deterministic similarity + stateless merge apply.** *Why:* pure,
  testable, reproducible; enables echo-back apply with no server session state.
  *Over:* server-cached merge sessions (stateful, harder to scale/test).
- **ADR-P12 Non-destructive defaults, escalate to replace.** *Why:* a blind
  "apply" can never lose manual data; user explicitly opts into overwrites.
- **ADR-P13 Submitted resumes are immutable; sync targets drafts only.** *Why:*
  the record of what was actually sent must stay truthful. *Over:* editable
  history (rewrites the past).
- **ADR-P14 AI improves, never invents; refuses without source content.** *Why:*
  truthfulness guarantee; keeps generated content defensible.
- **ADR-P15 Public/portfolio/JSON-Resume are pure projectors sharing a
  no-private-field invariant.** *Why:* one source of truth; safe sharing; trivial
  to extend.

## Test coverage (P3–P6)
- Backend unit: `tests/unit/test_profile_merge.py` (similarity/plan/apply/adapters),
  `tests/unit/test_profile_platform.py` (projection options, public/portfolio/
  JSON-Resume round-trip + no-leak, ATS/AI readiness, skill engine, AI guardrails).
- Backend integration: `tests/integration/test_profile_p3_p6_api.py` (import
  preview/apply, sync draft-apply + submitted-lock, AI memory, skill autocomplete,
  AI normalize, public/portfolio/export) + authz-matrix coverage for every route.
- Frontend: `tests/profile-import-history.test.tsx` (import dialog + version
  history) plus the existing `tests/profile-workspace.test.tsx`.

---

# Finalization pass — production hardening (delta)

Closed the concrete, high-value gaps found in a zero-trust audit of the P3–P6
build; each is implemented with tests and verified green.

- **Generate-resume tailoring options.** `GenerateResumeRequest` now accepts
  `template` and `sections` (per-section visibility, keyed by section `key`),
  threaded service → Projection Engine (no profile mutation). Tests:
  `test_profile_api.py::TestGenerateResume::test_generate_with_template_and_section_visibility`.
- **Import quality score + statistics + warnings.** `ImportPreviewResponse`
  now carries `ImportStatistics` (incoming-candidate `quality_score`, per-section
  counts, and add/update/conflict/duplicate tallies) plus human `warnings`
  (sparse-parse / nothing-new). Surfaced in the Import dialog header. Tests:
  `test_profile_p3_p6_api.py::TestImport::test_preview_includes_statistics`.
- **Adapter contract tests.** `test_profile_adapters_contract.py` asserts every
  registered adapter honors the contract (valid `ProfileData` or a coded
  `ImportError_`), the registry is the single source of truth, and stubs raise
  `unsupported` — the guardrail that keeps the ecosystem Open/Closed.
- **Skill autocomplete tag editor.** `components/profile/skill-tag-input.tsx`
  replaces comma-text with chips + keyboard nav + debounced backend autocomplete
  (`/profile/skills/suggest`) for technical/tools. Tests:
  `tests/profile-skill-tag-input.test.tsx`.
- **Per-experience "Improve with AI".** Grounded bullet improvement wired per
  experience (guarded to saved state), alongside the existing summary assist.

## Honest completion status (finalization)
- **Fully implemented & verified:** everything in P1–P6 above + this delta.
  Backend 1947 tests pass; frontend 429 pass; `tsc` + `eslint` clean; Next.js
  production build passes.
- **Partial (justified):** public/portfolio/JSON-Resume exist as tested pure
  projectors + downloads, but rendered public **share pages** (unauth route,
  persisted slug, SEO/OpenGraph, themes, view analytics) are not built;
  similarity is deterministic/explainable but not yet semantic/embedding-based;
  the AI catalog covers summary/bullets/skill-normalize (highest-value grounded
  assists) but not the full achievement/education/project/roadmap set; events are
  emitted (observable) but analytics aggregation/dashboards are not built; profile
  JSON is GIN-indexable but a dedicated profile-search endpoint is not exposed.
- **Deferred (rationale):** website generator/deploy, public themes, embeddings/
  semantic search, analytics dashboards, bulk sync — each a self-contained
  vertical enabled additively by the current architecture (pure projectors,
  outbox events, JSONB) with no redesign required.
- **Not verifiable in this environment:** Docker image build, Lighthouse, and
  cross-browser/mobile Playwright E2E (no browser/daemon; the repo's live
  Playwright test is deselected per project convention); real LLM suggestion
  output quality (no API key configured — guardrail/degradation paths are tested).

---

# P7 — Public Profile Platform (delivered)

A complete, anonymous, SEO-ready public profile surface built entirely on the
existing projections (no duplicated rendering logic). All green (backend 1956,
frontend 432, `tsc`/`eslint` clean, Next.js production build passes).

## Data & persistence
- Migration **0016** adds `profiles.public_slug` (globally UNIQUE, nullable) and
  `profiles.visibility` (`private` default | `unlisted` | `public`). Additive,
  reversible; the unique slug index powers a fast, JSON-free anonymous lookup and
  is created `CONCURRENTLY` on Postgres. Reversibility + create_all parity covered
  by the migration/model tests.
- Facade: `slug_exists`, `set_profile_publication`, `get_profile_by_slug`
  (unscoped — the public surface), all returning plain dicts.

## Service & API
- Authenticated (owner): `GET /profile/publication`, `POST /profile/publish`
  ({visibility, slug?} → unique slug, stable across re-publishes), `POST
  /profile/unpublish` (→ private; slug reserved). Slug generation is
  collision-safe (`_unique_slug` + DB unique index backstop + race retry).
- Anonymous (new `public_profile` router, no user scoping by design):
  `GET /public/profiles/{slug}` (visibility-gated: private → 404, indistinguishable
  from unknown to prevent enumeration; unlisted/public resolve) and
  `GET /public/profiles/{slug}/vcard` (RFC-6350). Per-IP fixed-window rate limit
  (60/min, fail-open) deters scraping.
- Projection additions (`public.py`): `build_vcard` (public-safe contact card),
  `public_json_ld` (schema.org `Person`). `project_public_profile` continues to
  omit private fields (salary/visa/phone) — enforced by test.

## Frontend
- `app/p/[slug]/page.tsx` — **server-rendered** for crawlers: `generateMetadata`
  emits title/description/OpenGraph/Twitter and marks `unlisted` as `noindex`;
  a schema.org `Person` JSON-LD block is injected; request-scoped `cache()`
  dedupes the metadata+page fetch into one backend call. `notFound()` on 404.
- `components/public/public-profile-view.tsx` — responsive, dark-mode, token-
  themed hero + summary + experience timeline + projects + skills + education +
  social links + "Save contact" (vCard). Renders only projected (public) fields.
- `components/profile/share-dialog.tsx` — publish public/unlisted, copy link,
  open page, unpublish; wired into the workspace header.
- Events: `public.shared` added to `EventType`.

## P7 honest status
- **Fully implemented & verified:** slug + visibility persistence, publish/
  unpublish/publication-state, anonymous visibility-gated page + vCard, SEO/OG/
  Twitter/JSON-LD, noindex-for-unlisted, rate limiting, no-private-leak (tested),
  server render + production build, owner share controls.
- **Partial/deferred (rationale):** multiple selectable public **themes** (one
  polished token-driven theme ships; the view is theme-ready), custom domains,
  and public-view **analytics collection** (the `public.shared` event + view
  hooks exist; a dashboard/collection pipeline is a separate analytics vertical).
- **Not verifiable here:** real crawler indexing / OpenGraph unfurl (needs public
  DNS + deployment); these depend on hosting, not code.

---

# Final verticals — Portfolio, Themes, Search, Analytics, Similarity provider, AI+ (delivered)

Implemented the previously-deferred product verticals (not just architecture),
each with tests. All green: backend **1978** tests, frontend **437** / 70 files,
`tsc` + `eslint` clean, Next.js production build passes, migration head **0017**
(reversible + create_all parity tested).

## Portfolio (P8)
- Anonymous `GET /public/profiles/{slug}/portfolio` (visibility-gated, view event)
  + frontend `/p/[slug]/portfolio` (server-rendered, SEO, reuses the public view
  → single rendering path, no duplicated layout). Projects-first projection via
  the existing `project_portfolio`.

## Public themes
- Migration **0017** adds `profiles.public_theme` (minimal | modern | developer).
  Set on publish (`POST /profile/publish {theme}`), returned in publication state
  + the public page payload, applied by `PublicProfileView` (token-driven hero /
  heading / font variations — dark-mode + responsive preserved). Theme picker in
  the Share dialog; `profile.theme_changed` event.

## Search Platform
- `app/profile/search.py`: pure, ranked, highlighted search across the whole
  profile (identity/summary/experience/education/projects/skills/certs/
  achievements) with a stable result contract (a Postgres FTS/embedding backend
  can replace the ranker later). `GET /profile/search?q=` + `profile.searched`
  event. Frontend `ProfileSearch` (debounced combobox, `[[…]]`→`<mark>`, jump to
  section) wired into the workspace.

## Analytics Platform (event-driven)
- `app/profile/analytics.py` (KVStore-backed per-user counters + completeness
  gauge) + `app/profile/analytics_consumer.py` (outbox handlers map domain events
  → counters; registered in `run_productivity_jobs`). Business services only
  *emit* events — no analytics logic in business logic. `GET /profile/analytics`
  + `AnalyticsCard` in the workspace. New events: `profile.ai_used`,
  `profile.exported`, `profile.searched`, `public.viewed`, `portfolio.viewed`,
  `portfolio.generated`, `profile.theme_changed`.

## Semantic Similarity — provider abstraction
- `app/profile/similarity_provider.py`: `SimilarityProvider` protocol +
  `DeterministicSimilarityProvider` (default, behavior-preserving),
  `HybridSimilarityProvider` (blends + explains), `EmbeddingSimilarityProvider`
  (vector seam; inert without an injected embedder → deterministic fallback, never
  fabricates). The Merge Engine now scores **through** the provider
  (`settings.profile_similarity_provider`), so a semantic backend drops in with no
  merge changes (dependency inversion).

## AI expansion (grounded)
- Added `skills_gap` (deterministic gap analysis vs. target roles — recommends
  skills to learn, never invents experience) and `keywords` (ATS keyword
  extraction from existing content). Both exposed via `POST /profile/ai/suggest`.

## Honest status of the ORIGINAL deferred list
- **Now implemented & tested:** Portfolio (public page + projection), Public
  themes (3), Search platform (deterministic FTS-style, provider-ready),
  Analytics platform (event-driven counters + endpoint + card), Similarity
  provider abstraction (deterministic/hybrid/embedding-seam), AI expansion
  (skills-gap + keywords + prior summary/bullets/normalize), expanded typed
  event platform.
- **Still genuinely infra-dependent (documented seams, not faked):**
  *semantic embeddings / vector DB* (needs an embedding model + vector store —
  `EmbeddingSimilarityProvider` is the drop-in seam), *Website Generator deploy /
  custom domains / hosting providers* (needs external deploy infra — projections
  already produce the site data), and *live analytics collection at scale /
  dashboards beyond the per-user card* (counters + events exist). These require
  external services unavailable in this repo/runtime and are implemented up to the
  architectural seam, not falsely claimed complete.
