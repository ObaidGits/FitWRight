# Persistent AI Analysis Cache - "Compute Once, Reuse Everywhere"

A generic cache for the reusable results of expensive AI/analysis operations, so
identical work (resume parsing, job analysis, auxiliary generation) is never
recomputed. It complements the version/profile snapshot systems (user-facing
edit history) rather than duplicating them.

---

## 1. What is already persisted (and therefore not cached here)

Most resume state already survives a refresh via the database, so the cache
deliberately does not re-implement it. The following recover after a refresh /
navigation on their own:

| Data | Where it lives | Recovers on refresh? |
| --- | --- | --- |
| Raw + structured resume (`processed_data`) | `resumes` table | [x] yes |
| Tailored resumes (after confirm) | `resumes` (`parent_id`) + `improvements` | [x] yes |
| Cover letter / outreach / interview prep | columns on `resumes` | [x] yes (but were regenerated - see §3) |
| Job descriptions | `jobs` table | [x] yes |
| Profile + completeness | `profiles` + `profile_versions` | [x] yes |
| Resume edit history | `resume_versions` (gzip, content-hash dedup) | [x] yes |
| Wizard state | `localStorage` draft (`useDraft`) | [x] yes |
| Events / outbox, KV store | `outbox`, `kv` | [x] yes |

**Genuine gaps (where AI work was actually recomputed or lost):**

1. **Resume parse** - re-uploading an identical file re-ran the parsing LLM.
2. **Job analysis** (`/jobs/analyze`) - the LLM keyword extraction was **never
   cached**; every analysis re-called the LLM.
3. **Aux generation** - cover letter / outreach / interview prep were
   **regenerated unconditionally** on every request even when already stored.
4. **Tailor preview** - the preview result is client `useState` only; lost on
   refresh (must re-run the LLM). *(Not cached - see §5.)*

The right design was therefore a **focused, generic AI-result cache** that
eliminates redundant LLM calls - *complementary* to the existing version /
profile snapshot systems (which are user-facing edit history, a different
concern), not a parallel re-implementation of persistence that already works.

---

## 2. The Universal Analysis Object - `analysis_artifacts`

A single generic table (migration `0019`) stores the reusable result of any
expensive AI/analysis operation.

```
analysis_artifacts(
  id, user_id,
  artifact_type,        -- resume_parse | job_analysis | tailor_preview | ...
  source_id,            -- primary owning key (content hash, or a resource id)
  related_id,           -- optional secondary dependency (e.g. resume_id)
  checksum,             -- sha256 of the canonical input
  version,              -- "<algo_version>|<model>"  -> prompt/model change = miss
  status,               -- ready | failed
  analysis_data (JSON), -- the cached payload
  confidence,
  created_at, updated_at
)
```

- **Reuse key** `(user_id, artifact_type, source_id, checksum, version)` is
  **UNIQUE** -> an exact lookup is a cache hit and concurrent producers converge
  on one row (idempotent upsert; insert-race collapses via the unique index).
- **Version awareness / lazy migration**: `version` embeds the algorithm
  version *and* the model name, so a prompt bump (edit `_ALGO_VERSION` in
  `app/services/analysis_cache.py`) or a provider/model switch simply **misses**
  and recomputes - we never serve a result produced by a different algorithm.
- **Dependency-aware invalidation**: deleting by a `resource_id` removes rows
  whose `source_id` **or** `related_id` matches, optionally filtered by
  `artifact_type` (so a resume edit can drop tailoring/fit caches while leaving
  unrelated kinds intact).
- **User-scoped** - registered in `Repo.OWNED_TABLES`; every query lives in the
  `app.database` facade, scoped by `user_id` (passes the CI scoping guard).

### Service API - `app/services/analysis_cache.py`

```python
result, from_cache = await analysis_cache.get_or_compute(
    user_id=..., artifact_type=..., source_id=..., checksum=..., version=...,
    compute=lambda: <expensive async op>,   # only called on a miss
    force=False,                             # True = explicit "Regenerate"
)
await analysis_cache.invalidate(user_id, resource_id, artifact_types=[...])
```

Honesty guarantees: a hit requires an **exact** content + algorithm-version
match; a failed `compute` **propagates and is never cached** as a hit; a cache
read/write failure never breaks the underlying operation (best-effort).

---

## 3. Wired workflows

| Workflow | Reuse strategy | Effect |
| --- | --- | --- |
| **Resume parse** (`/resumes/upload`, `/upload/stream`, `retry-processing`) | content-addressed: `source_id = checksum = sha256(markdown)`, `version = algo|model`, via `_parse_resume_cached` | Re-uploading identical resume text (or retrying unchanged content) reuses the structured parse - **no second LLM call**. |
| **Job analysis** (`/jobs/analyze`) | cache the LLM keyword extraction keyed by `sha256(jd)`; matched/missing/fit are recomputed cheaply against the *current* resume | Re-analyzing an identical JD reuses keywords with **no LLM call**; fit never goes stale. |
| **Cover letter / outreach / interview prep** (`generate-*`) | return the stored column unless `?regenerate=true` | "**Never regenerate unless the user clicks Regenerate**" - reopening/refresh spends no LLM call. Frontend `generateCoverLetter/...(id, regenerate?)` threads the flag. |

Content-addressed caches (parse, job analysis) are **self-invalidating**:
changed content produces a new checksum, so a stale result can never be served -
no explicit invalidation is required for these.

---

## 4. Tests

`tests/integration/test_analysis_cache.py` covers:
- **Facade**: exact-key hit; miss on version/checksum change; idempotent upsert;
  invalidation by `source_id`/`related_id`; type-filtered invalidation; user
  scoping (cross-user read denied).
- **Service**: compute-on-miss then reuse (compute runs once); `force` bypass;
  failed compute not cached; deterministic content-addressed checksum; model
  changes the version key.
- **Endpoints**: identical JD reuses keywords (LLM called once across two
  requests); cover letter + interview prep return stored copy without invoking
  the LLM.

Migration `0019` upgrades/downgrades/re-upgrades cleanly on SQLite and passes the
user-scoping guard.

---

## 5. Scope boundaries

The cache currently covers resume parsing, job analysis, and auxiliary
generation. The following operations are intentionally not cached; they build on
the same substrate (the dependency-aware `invalidate` primitive is already in
place) if added later:

- **Tailor preview** - the `/improve/preview` payload could be keyed by
  `(resume_id, job_id, prompt_id, resume_version, jd_checksum)` with
  `related_id = resume_id`, invalidated on resume content edits.
- **ATS score artifact** for tailored resumes - currently deterministic and
  cheap to recompute.
- **JD dedup** - collapsing identical `jobs` rows via a content hash (the
  fingerprint/simhash infrastructure in `app/jd/` already exists).
- **Payload compression** - gzipping large `analysis_data` values with a TTL
  sweep, mirroring `resume_versions`.
