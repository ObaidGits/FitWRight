# Custom Tailoring Instructions — Plan & Implementation

## 1. Goal

Give the user an optional free-text **"Extra instructions (optional)"** field on the
Tailor page so they can steer a tailoring run per-job — e.g.

- "Emphasize backend and system-design work over frontend."
- "Prioritize the Kubernetes and Postgres keywords."
- "Keep bullets concise; aim for one page."
- "Put Projects above Experience."
- "I actually know Docker — please include it." (real content the user attests to)

This complements the existing preset **Tailoring style** dropdown (`prompt_id`) with
run-specific guidance, without weakening the anti-fabrication guarantees.

## 2. Non-goals / guardrails

- The field **must not** become a backdoor to fabricate. The deterministic gates stay:
  - `build_skill_target_plan` + `apply_diffs` skill whitelist (only resume-supported or
    verified JD skills can be added).
  - `verify_diff_result`, `_preserve_personal_info`, `_preserve_original_skills`,
    date-restoration.
- Instructions are **steering**, injected as a clearly-bounded, lower-priority section that
  explicitly cannot override the truthfulness rules.
- Input is **sanitized** (`_sanitize_user_input`, existing prompt-injection stripping) and
  **length-capped** (2000 chars) to bound tokens and abuse.

## 3. Design

### Backend
1. **Schema** (`app/schemas/models.py`): add
   `custom_instructions: str | None = Field(default=None, max_length=2000)` to
   `ImproveResumeRequest`. Flows automatically to `/resumes/improve`,
   `/resumes/improve/preview`, and `/resumes/improve/preview/stream`.
2. **Prompt** (`app/prompts/templates.py`):
   - Add a `{user_instructions}` placeholder to `DIFF_IMPROVE_PROMPT` (the primary,
     structured-data path), positioned after the RULES and before the JD.
   - Add `format_user_instructions(text)` helper that returns a bounded, safety-framed
     block or `""` when empty.
3. **Service** (`app/services/improver.py`):
   - `generate_resume_diffs(..., custom_instructions=None)` — sanitize, build the block,
     format into the prompt.
   - `improve_resume(..., custom_instructions=None)` — fallback (no structured data) path;
     augment the system prompt with the same bounded block.
4. **Wiring** (`app/routers/resumes.py`):
   - `_improve_preview_flow` passes `request.custom_instructions` into both
     `generate_resume_diffs` and `improve_resume`.
   - Legacy `improve_resume_endpoint` passes it into `improve_resume`.

### Frontend
5. **API client** (`lib/api/resume.ts`): thread an optional `customInstructions` through
   `improveResume`, `previewImproveResume`, and `streamImproveResume` (sent as
   `custom_instructions`).
6. **Tailor page** (`app/(app)/tailor/page.tsx`): a textarea inside the existing
   **Options** disclosure, with a short helper line and examples, wired into
   `onGenerate` (stream + non-stream fallback).

## 4. Impactful usage demonstration

- Backend service test: identical resume + JD, with vs without instructions
  ("prioritize Kubernetes"), asserts the instruction text reaches the LLM prompt and that
  the anti-fabrication gate still rejects an out-of-whitelist skill.
- Endpoint test: `custom_instructions` in the request body is forwarded to
  `generate_resume_diffs`.
- Frontend test: typing instructions and generating forwards them to the stream call.

## 5. Verification

- Backend: `uv run pytest -q` (targeted improver/resumes/schema suites + full run).
- Frontend: `npx vitest run`, `tsc --noEmit`, `eslint`.

## 6. Status

- [x] Plan written
- [x] Schema field
- [x] Prompt placeholder + helper
- [x] Service threading (diff + fallback)
- [x] Router wiring (preview/stream + legacy)
- [x] Frontend API + UI
- [x] Tests (backend + frontend) and full verification

---

## 7. Extension — User-attested additions (add real content)

The initial version only steered emphasis/ordering. This extension lets the
instructions field **add the user's own real content** while still blocking the
*model* from inventing anything on its own.

### Rule
Content the user explicitly names in their instructions is **user-attested** and
allowed. Content the model would add on its own (name/title absent from the
instructions) is **rejected**. This "grounding in the instruction text" is the
deterministic line between allowed and blocked.

### What's now possible
- Add a new project the user provides ("Add project KRIA: automates daily tasks…").
- Add a new role/experience the user provides.
- Add new bullets to an existing project/job (already supported via `append`).
- Add a real skill the user names (even if not a JD-derived verified target).

### Implementation
- **Schema**: `ResumeChange.action` adds `add_entry`; `value` may be a `dict`
  (the new entry object).
- **`apply_diffs`** (`services/improver.py`):
  - New allowed paths `personalProjects` / `workExperience` for `add_entry`.
  - `add_entry` only applied when `_build_attested_entry` confirms the entry's
    name/title/company appears in the (sanitized) `user_instructions`.
  - `add_skill` now also accepts a skill grounded in `user_instructions`.
- **`verify_diff_result`**: section-count check subtracts applied `add_entry`
  additions, so an attested add is not flagged (an *extra* unexplained entry
  still is).
- **`refiner.validate_master_alignment`**: skills/companies named in
  `user_instructions` are not flagged as fabricated (they'd otherwise be removed
  by the master-alignment pass since they're absent from the master resume).
  `refine_resume` threads `user_instructions` through.
- **Router wiring**: `_improve_preview_flow` and the legacy endpoint pass
  `request.custom_instructions` into `apply_diffs` and `refine_resume`.
- **Prompt**: RULE 3 now permits `add_entry` ONLY for user-requested entries;
  PATHS + a JSON example added; `add_skill` note updated.
- **UI**: helper copy updated — "add your own real content (a project, role, or
  skill you actually have); the AI won't invent experience on its own."

### Guarantee preserved
The *model* still cannot invent entries/skills/companies — anything not grounded
in the user's own words is rejected at `apply_diffs` and would be stripped by the
alignment pass. Only user-attested content flows through.

### Tests
- `test_apply_diffs.py::TestAddEntryUserAttested` (add project/role, reject
  invented/no-instructions/unsupported-section, attested skill).
- `test_verify_diffs.py` — attested addition not flagged; extra entry still is.
- `test_refiner.py` — attested company/skill not flagged as fabricated.
- Full backend suite green (2783 passed).

---

## 8. Caveat hardening (9 fixes)

1. **Anti-embellishment** — `_filter_bullets_to_grounded` keeps an added bullet
   only if >=50% of its content tokens appear in the user's own instructions;
   plus the extraction prompt got strict fidelity rules. ("web application /
   AI/ML" style embellishment is dropped.)
2. **No silent drops** — `merge_user_additions` returns human-readable notes
   (Added / already-present / couldn't-add), threaded into `response_warnings`
   and shown in a "Notes on your instructions" card on the review screen.
3. **Steering vs addition** — `has_addition_intent()` gates extraction; pure
   steering ("emphasize", "reorder") never triggers it.
4. **Latency/cost** — same gate skips the extra LLM call when there's no add
   intent.
5. **Metric fabrication in additions** — added bullets with a metric (30%, $2M,
   3x) not present in the instructions are dropped.
6. **Placement** — user-added projects are inserted at the TOP of the section
   (relevance-first for the target job), not appended last.
7. **Skill de-dup** — `dedupe_resume_skills` collapses near-duplicates
   (React/React.js, Node/Node.js) at the end of tailoring; never merges
   genuinely distinct skills (Java vs JavaScript).
8. **Removed latent surface** — `add_entry` was removed from the diff engine
   (schema, `apply_diffs`, prompt); additions are owned solely by the dedicated
   extraction+merge. Diff engine is edit-only again.
9. **Weak-model reliability** — additions use a small dedicated prompt; any
   provider/content failure degrades to empty (never breaks tailoring); zero-diff
   degrades gracefully with a surfaced "No changes were applied" note. Backed by
   `complete_json` lenient JSON repair + retries and the keyword heuristic
   fallback.

Verification: backend 2797 passed; frontend 654 passed; tsc + eslint clean.
Live-confirmed on the free model: single clean "KRIA" placed first with only a
grounded bullet, skills deduped, and an "Added project" note surfaced.
