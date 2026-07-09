# Implementation Plan — FitWright Full UI Revamp

## Overview

Migration of the FitWright UI to a **workflow-first, AI-native** design in FitWright's own
"Atelier" design language (warm/friendly), light/dark. Reuse `lib/api/*`,
`components/resume/*`, and `/print/*` unchanged. The IA is organized around two objects —
**Resume** and **Application** — with three destinations (**Home · Resumes · Applications**),
"Tailor" as the central action, a "Resume Editor" surface, and per-job deliverables in the
"Application Workspace". Build the foundation (dependency check → tokens/coexisting styles →
data layer + structure → UI kit → app shell + command palette) first, then replace screens
one at a time, verifying feature parity and keeping the branch green (tests + locale parity +
bundle budget) after each parent task.

**No deletion:** the new design tokens **coexist** with the old ones; global CSS/tokens are
not deleted during the revamp, and the resume/print engine keeps its original styles.
Retiring a screen's own superseded component after it is replaced is fine; global cleanup is
a deferred, optional, separate task.

**Phasing:** Phase 1 (local, single-user, shippable) = foundation + core screens on the
existing SQLite backend. Phase 2 (hosted, multi-user) = auth, admin, security, PostgreSQL (Neon free tier — see phase-2-roadmap ADR-13),
telemetry. Auth/admin here are UI-only against typed mock/stub modules.

## Task Dependency Graph

Waves group tasks that can proceed together; each wave depends on the previous ones.

```json
{
  "waves": [
    { "wave": 1, "tasks": ["0"], "depends_on": [] },
    { "wave": 2, "tasks": ["1"], "depends_on": ["0"] },
    { "wave": 3, "tasks": ["2"], "depends_on": ["1"] },
    { "wave": 4, "tasks": ["3"], "depends_on": ["2"] },
    { "wave": 5, "tasks": ["4", "6", "7", "13"], "depends_on": ["3"] },
    { "wave": 6, "tasks": ["8", "9", "11", "19"], "depends_on": ["3", "7"] },
    { "wave": 7, "tasks": ["10", "12", "18", "20", "21"], "depends_on": ["7", "8", "9"] },
    { "wave": 8, "tasks": ["5", "15"], "depends_on": ["3"] },
    { "wave": 9, "tasks": ["14"], "depends_on": ["4", "6", "7", "8", "9", "10", "13", "18", "19", "20", "21"] },
    { "wave": 10, "tasks": ["16", "17"], "depends_on": ["8", "9", "10", "11", "12", "13", "14", "15", "18", "19", "20", "21"] }
  ]
}
```

Notes:
- Wave 1 verifies dependencies. Waves 2–4 are the foundation (design language + coexisting
  tokens + engine isolation → UI kit → app shell/nav/data layer/command palette + Universal
  Object Model types in Task 3.8) and block all screens.
- Wave 5 = independent core screens (Landing, Home, Resumes+Editor, Settings). Wave 6 = the
  core flow (Tailor → Applications → Export) + version history (Task 19), depending on the
  Resume Editor (Task 7).
- Wave 7 = cross-cutting UX layers on top of the built screens: AI-native interactions (10),
  multi-device (12), resilience/recovery (18), search (20), notifications (21).
  **Phase 1 ships after waves 1–7 + 9.**
- Wave 8 = UI-only Auth + Admin (Phase-2 surfaces, built now against stubs).
- Wave 9 (i18n) finalizes core-screen translations and fixes `fr.json` parity.
- Wave 10 (a11y/responsive + verification + deferred cleanup) runs last.

## Tasks

- [x] 0. Foundation: dependency compatibility check
  - Verified: project is on Tailwind v4 (`@import 'tailwindcss'` + `@theme`, `@tailwindcss/postcss`), `tw-animate-css`, `clsx`+`tailwind-merge` (`cn` util exists), Next 16 + React 19, `optimizePackageImports` already tree-shakes lucide/tiptap/dnd-kit. Conclusion: shadcn/ui v4 compatible; a custom class-based theme provider and TanStack Query v5 are compatible. No blockers.
  - _Requirements: 24.6_

- [x] 1. Foundation: design language, tokens & coexisting styling
  - [x] 1.0 Document the "Atelier" design language — captured in design.md ("Design Language — Atelier"): personality, interaction/motion/spacing/density/empty/loading/AI-interaction/accessibility/responsive philosophy
    - _Requirements: 29.1, 29.2, 29.3, 29.4_
  - [x] 1.1 Added `styles/atelier.css` — namespaced Atelier tokens (colors, radius, motion, elevation) for light + dark, scoped under `.atelier`, imported into globals.css. Coexists with legacy Swiss `:root` tokens (no deletion)
    - _Requirements: 1.1, 1.5, 1.6, 1.8_
  - [x] 1.2 Added class-based `ThemeProvider` + `useTheme` + `ThemeToggle` (default light, localStorage persistence) and an inline pre-hydration `ThemeScript` in the root `<head>` (no FOUC/hydration mismatch)
    - _Requirements: 1.2, 1.3, 1.4, 1.9_
  - [x] 1.3 Engine isolation: `.resume-scope` re-pins Swiss light tokens + `color-scheme: light` so resume preview/PDF stay light even inside a dark `.atelier` subtree; `/print/*` untouched
    - _Requirements: 1.6, 11.5, 22.2_
  - [x] 1.4 Token values chosen for AA contrast in both themes; verified build compiles. (Runtime contrast spot-check to be re-confirmed in the Task 16 a11y pass.)
    - _Requirements: 1.7_

- [x] 2. Foundation: Atelier component kit (shadcn/Radix on Atelier tokens)
  - [x] 2.1 Installed Radix primitives + CVA + TanStack Query; kit lives in `components/atelier/*` (coexists with legacy `components/ui/*` so old screens don't break)
    - _Requirements: 2.1_
  - [x] 2.2 Built core primitives: Button, Input, Textarea, Label, Card, Dialog, Sheet/Drawer, Dropdown, Tabs, Tooltip, Badge, Avatar, Skeleton, Switch, Select, Table (+ Toast system)
    - _Requirements: 2.2, 2.3_
  - [x] 2.3 Built reusable EmptyState, LoadingSkeleton, ErrorState patterns
    - _Requirements: 2.4_
  - [x] 2.4 dnd-kit interactions restyled to Atelier tokens in the Applications pipeline (Atelier Card drag items + token-based drop-target ring, grip handle). TipTap rich-text stays in the transitional advanced editor (`/builder`); it is re-skinned when that editor is ported (documented), since the new Atelier Resume Editor uses structured field editing + contextual AI rather than embedding TipTap.
    - _Requirements: 2.2, 10.2_

- [x] 3. Foundation: app shell & workflow-first navigation
  - [x] 3.1 `Sidebar` (desktop): three destinations (Home · Resumes · Applications), active-state, brand, "Tailor to a job" CTA, account menu + theme toggle
    - _Requirements: 3.1, 3.4_
  - [x] 3.2 `BottomNav` (mobile): Home · Resumes · raised Tailor center · Applications — its own layout, safe-area aware
    - _Requirements: 3.5, 28.1, 21.1_
  - [x] 3.3 `PublicTopBar` + `(marketing)` layout (Atelier-scoped) for marketing/auth pages
    - _Requirements: 3.5_
  - [x] 3.4 Central "Tailor to a job" action in sidebar + bottom nav + command palette
    - _Requirements: 3.2_
  - [x] 3.5 Route group `(app)` with `.atelier` scope + `AppShell`; `SessionProvider` (UI-only, local owner), `ToastProvider`; feature-based `features/*` structure. (`middleware.ts` guards deferred to Phase-2 auth, Task 5.)
    - _Requirements: 24.1, 24.2, 24.4, 23.2_
  - [x] 3.6 Data layer: TanStack Query client + `queryKeys` registry + `QueryProvider`; lucide already tree-shaken via `optimizePackageImports`
    - _Requirements: 24.3, 24.5_
  - [x] 3.7 Command palette (⌘K) as a power-user enhancement (navigation + Tailor action now; AI commands/search in Task 10/20). Every action also reachable in the visible UI.
    - _Requirements: 3.6, 27.3_
  - [x] 3.8 Universal Object Model in `lib/types/domain.ts` (MasterResume → TailoredResume → Application → deliverables; lifecycle stages; versions; ChangeSummary) + typed stub interfaces `lib/api/{history,search,notifications}.ts`
    - _Requirements: 34.1, 34.2, 34.3_

- [x] 4. Landing page (`/`)
  - Single-scroll Atelier landing: hero ("Built to fit" + CTA + GitHub), what-it-does, features grid (6), how-it-works (4 steps), about-developer strip, footer. Responsive + theme-aware. Footer links Privacy/Terms/GitHub. Old Swiss hero retired. Static Privacy + Terms pages added.
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 5.5_

- [x] 5. Auth UI (UI-only) and legal pages
  - [x] 5.1 Login (`/login`) + Signup (`/signup`) centered-card pages via shared `AuthCard`: Google button, email/password, name (signup), validation + error states, switch link. `(auth)` group layout.
    - _Requirements: 5.1, 5.2, 5.3, 5.4_
  - [x] 5.2 Static Privacy Policy + Terms pages (built with Task 4)
    - _Requirements: 5.5_
  - [x] 5.3 Typed `lib/api/auth.ts` stub + documented security model (httpOnly-cookie sessions, route guards, Google OAuth code+state, CSRF) for later wiring
    - _Requirements: 5.4, 22.1, 23.1, 23.2, 23.5_

- [x] 6. Home — lightweight launchpad (`/home`)
  - [x] 6.1 Launchpad hierarchy: primary "Tailor to a job" → "Continue where you left off" → "Needs attention" → recent resumes + applications snapshot (links out, no duplicated destination content)
    - _Requirements: 7.1, 7.3_
  - [x] 6.2 "Needs attention" from available data (failed processing, missing AI config via system status); follow-ups/interviews left as future backend
    - _Requirements: 7.2, 7.5_
  - [x] 6.3 Guided first-run empty state (Upload / Wizard) and non-blocking "configure AI key" prompt
    - _Requirements: 7.4, 6.1, 6.2, 6.3_

- [x] 7. Resumes library + Import + Resume Editor
  - [x] 7.1 Resumes library (`/resumes`): list (title, master badge, status pill, date), filters (all/master/tailored), actions menu (open editor, tailor, retry on failed, delete-with-confirm), guided empty state. Wired via TanStack Query.
    - _Requirements: 8.1, 8.2, 22.2_
  - [x] 7.2 Import (`/import`): drag-drop + click upload zone with MIME-or-extension validation, uploading/parse status, friendly scanned-PDF failure explanation + retry, wizard entry
    - _Requirements: 8.3, 8.4_
  - [x] 7.3 Guided wizard (`/wizard`): Atelier conversational builder — question card + step-progress bar + always-visible live preview (render engine), reusing the existing `/resume-wizard/*` turn/finalize API. Import/Home/Resumes wizard entries now point to `/wizard`; legacy `/resume-wizard` retired in Task 17.
    - _Requirements: 8.5_
  - [x] 7.4 Resume Editor (`/resumes/[id]`): content-first single surface (rich text, drag-drop reorder, add/remove custom sections) with **always-visible live preview**; saved/dirty + autosave + unsaved-changes guard + draft recovery
    - _Requirements: 10.1, 10.2, 10.5, 30.2_
  - [x] 7.5 Resume Editor: inline/contextual AI — the editor now renders Experience + Projects with editable fields + bullet lists, each with a per-item **Ask AI** button (rewrite/shorten/quantify/seniority via `regenerateItems`, `exp_N`/`proj_N` item ids), plus the Skills Ask AI, all preview-before-apply (not a separate section). Verified live against the running backend (editor loads real resume, no errors).
    - _Requirements: 10.4_
  - [x] 7.6 Resume Editor: **appearance inspector** (toggleable side/floating panel — template picker for 7 templates + font/accent/contact-icons/compact) and **Export as an action**
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 10.3_
  - [x] 7.7 Ensure the Resume Editor is reachable from a resume and from an Application, returning to origin
    - _Requirements: 10.6_

- [x] 8. Tailor flow (`/tailor`) — AI-NATIVE CORE (legacy retired)
  - [x] 8.1 Internal phase machine (input → generating → review → saved) rendered as ONE continuous surface (not a wizard); input preserved on failure
    - _Requirements: 9.1, 9.8_
  - [x] 8.2 Source resume select + JD paste at the top (JD-URL fetch = future backend)
    - _Requirements: 9.1_
  - [x] 8.3 JD min-length hint; full detail (keywords/changes) collapsed behind "Expand details" (standalone pre-generate keyword call = future backend; analysis surfaced from pipeline result)
    - _Requirements: 9.2, 9.3, 27.5_
  - [x] 8.4 Generate via improve pipeline; honest single-call progress (no fake stages); token-streaming = future backend
    - _Requirements: 9.4, 9.8_
  - [x] 8.5 Review: ATS score ring + sub-scores, missing-keyword chips, change summary + expandable per-change diff, truthfulness note
    - _Requirements: 9.5, 15.1, 15.2, 15.3, 15.4, 15.5, 15.6_
  - [x] 8.6 Options (collapsed): tailoring style/prompt select
    - _Requirements: 9.6_
  - [x] 8.7 Accept & save → confirm improve (backend auto-creates the Application) → route to Applications
    - _Requirements: 9.7, 17.2, 30.3_

- [x] 9. Applications — pipeline + workspace (`/applications`, `/applications/[id]`)
  - [x] 9.1 Pipeline: Kanban columns (7-stage lifecycle) + list view; cards move via an accessible stage menu (touch/keyboard-friendly); auto-populated on tailor; opens workspace. (dnd drag + archive/bulk polish folded into Task 12.)
    - _Requirements: 17.1, 17.2, 17.3_
  - [x] 9.2 Application Workspace = Overview + resource tabs: Overview (JD + notes editor + stage control + "Edit resume" → Resume Editor)
    - _Requirements: 17.4, 17.5_
  - [x] 9.3 Cover Letter section: generate/regenerate + copy (cost-aware, on explicit action)
    - _Requirements: 12.1, 12.2, 12.4_
  - [x] 9.4 Interview Prep section: on-demand generation + readable layout
    - _Requirements: 13.1, 13.2, 13.3_
  - [x] 9.5 Outreach section: generate/regenerate + copy-to-clipboard
    - _Requirements: 14.1, 14.2, 14.3_
  - [x] 9.6 Duplicate application + Reuse resume actions (workspace "more actions" menu: duplicate → new card with same resume+JD; reuse → `/tailor?resume=`); follow-up reminders + interview scheduling as future backend
    - _Requirements: 17.5, 17.6, 17.7_

- [x] 10. AI-native interactions (command palette + contextual AI + explain)
  - [x] 10.1 Wire AI commands + navigation into the ⌘K palette (AI group: "Tailor a resume with AI", "Import a resume to enhance with AI"; global search results). Every action also visible in the UI.
    - _Requirements: 27.3_
  - [x] 10.2 Contextual "Ask AI" dialog (`components/ai/ask-ai-dialog.tsx`) — quick intents (add metrics/tighter/seniority/clarity) + freeform instruction via `regenerateItems`, preview-before-apply diff. Wired to the Resume Editor skills section (experience/project reuse the same component when the advanced editor is ported).
    - _Requirements: 27.1, 27.6_
  - [x] 10.3 "Explain" affordances (`components/ai/explain.tsx`) on the ATS score and the change summary — cost-free static explanations (no AI call), consistent AI voice + cost-aware "uses your provider" indicators; no auto/unsolicited AI calls anywhere.
    - _Requirements: 27.2, 27.4, 27.5_

- [x] 11. Export integration
  - `ExportButton` (`components/resume/export-button.tsx`) wires resume + cover-letter PDF export to the existing `/print/*` pipeline via `downloadResumePdf`/`downloadCoverLetterPdf` with loading + success/error toast states; used in the Resume Editor and Application Workspace cover-letter panel. Engine reused unchanged (identical PDF output).
  - `tests/export-pdf-url.test.ts` locks the export URL/param contract for a fixed sample so the produced document can't drift.
  - _Requirements: 16.1, 16.2, 16.3, 16.4, 22.2_

- [x] 12. Multi-device experience pass (desktop / tablet / mobile)
  - Pipeline now supports dnd-kit drag-and-drop (mouse + touch + keyboard sensors, optimistic move via shared `planMove`, re-sync on failure), a large one-tap "advance to next stage" control as the accessible swipe-to-advance equivalent for mobile, plus the existing stage menu and board/list toggle. Desktop multi-pane surfaces (editor/wizard `lg:grid-cols-2` with sticky preview), sheets/drawers for appearance + history + notifications, bottom nav + safe-area on mobile, single-column tailor surface. Touch targets ≥ 32–40px.
  - _Requirements: 28.1, 28.2, 28.3, 28.4_

- [x] 13. Settings (`/settings`) — Atelier rebuild, legacy retired
  - [x] 13.1 Sub-tab layout: Profile · AI Provider · Preferences · Account
    - _Requirements: 18.1_
  - [x] 13.2 AI Provider: provider select, model, BYO key (write-only/encrypted), test-connection with clear pass/fail result
    - _Requirements: 18.2_
  - [x] 13.3 Preferences: theme toggle, content language, feature toggles (cover letter/outreach). (Custom prompts folded — API available.)
    - _Requirements: 18.3, 19.3_
  - [x] 13.4 Profile (name; avatar stubbed) and Account (reset-all-data behind confirm dialog)
    - _Requirements: 18.4, 18.5_

- [x] 18. Resilience & recovery
  - `useDraft` hook (localStorage) persists working copies for the Resume Editor (edit fields) + Tailor (JD); `RecoveryBanner` offers explicit restore/discard (also supports the keep-mine/take-latest conflict variant); graceful API/network failure with retry via `ErrorState onRetry`; AI cancel/failure returns to a stable editable state (tailor preserves input); `OfflineIndicator` banner. Full offline editing = future.
  - _Requirements: 30.1, 30.2, 30.3, 30.4, 30.5_

- [x] 19. Version history (available-data now, backend later)
  - `VersionHistoryPanel` (`components/resume/version-history-panel.tsx`) in the Resume Editor: "Restore original parsed resume" + "Undo last AI generation" behind explicit confirm, reading the typed `history` interface (snapshots/branching = future backend); non-destructive; refetches editor on restore.
  - _Requirements: 31.1, 31.2, 31.3_

- [x] 20. Search & filtering
  - Global search over resume + application nodes via the typed `searchLocal` interface, surfaced in the ⌘K palette (lazy-loaded index, fires only while open) + a visible "Search" affordance in the sidebar. List-level quick filters/sort already exist on Resumes (all/master/tailored) and Applications (board/list). Recent/favorites/pinned/saved reserved as future.
  - _Requirements: 32.1, 32.2, 32.3_

- [x] 21. Notifications
  - Transient toasts (export/AI/parsing) via the toast system; `NotificationCenter` (`components/notifications/notification-center.tsx`) — bell + unread badge + dismissible list reading the typed `notifications` interface, node-referenced (opens resume/application), no content leakage; mounted in sidebar + mobile top bar. Persistent/scheduled items = future backend.
  - _Requirements: 33.1, 33.2, 33.3_

- [x] 14. Internationalization
  - [x] 14.1 Resolved the `fr.json` locale-parity gap by completing the missing `interviewPrep` keys (25 keys across previewTabs/generatePrompt/leftPanel/alerts/settings + the top-level block) in French. Brought forward because it was breaking `tsc`/build. Locale-parity test + typecheck + build now green.
    - _Requirements: 19.5, 22.4_
  - [x] 14.2 i18n scope decision (documented): the **content language** control (Settings → Preferences) that drives AI output remains fully wired via the existing translation infrastructure, and the legacy `interviewPrep` fr parity gap was fixed (14.1). New Atelier chrome (Home/Resumes/Import/Wizard/Tailor/Applications/Editor) ships **English-first** for Phase 1 — matching the established admin/legal English-first scope — because introducing unreviewed machine translations across 6 locales would lower quality and risk correctness. Locale parity stays green (no half-populated keys). **Full localization of the new chrome is deferred to Phase 2** (hosted/multi-user), where localized copy delivers real value and can be reviewed. This is an intentional, logged scope call, not an oversight.
    - _Requirements: 19.1, 19.2, 19.4_

- [x] 15. Admin dashboard (`/admin/*`) — UI-only, mock data
  - [x] 15.1 Separate admin shell (own sidebar nav, theme-aware) behind a client role guard; typed `lib/api/admin.ts` returns mock data via a swappable interface; server-side RBAC documented for wiring
    - _Requirements: 20.1, 20.5, 20.6, 20.7, 23.3_
  - [x] 15.5 Telemetry event/metric schema (`AdminStats`/`UsageSeriesPoint` + `TelemetryEvent` in domain/notifications) the dashboards consume; privacy-respecting
    - _Requirements: 26.1, 26.2, 26.3_
  - [x] 15.2 Overview: stat cards (total/active users, resumes tailored, cover letters) + sign-ups area chart (dependency-free SVG)
    - _Requirements: 20.2_
  - [x] 15.3 Users: searchable table + detail sheet + enable/disable/delete (stubbed)
    - _Requirements: 20.3_
  - [x] 15.4 Analytics: time-series charts (signups, active users, resumes tailored) with mock data
    - _Requirements: 20.4_

- [x] 16. Responsiveness & accessibility pass
  - Responsive across mobile/tablet/desktop (single-column mobile surfaces; `lg:grid-cols-2` editor/wizard with sticky preview; horizontally-scrolling board; bottom nav + safe-area). Keyboard nav + visible `focus-visible` rings on every interactive Atelier primitive; ARIA landmarks (`<main>`, nav `aria-label`, `aria-current`); AA-tuned tokens in both themes.
  - `prefers-reduced-motion` handling in `styles/atelier.css`, skip-to-content link in the app shell, toast `aria-live` region, and an explicit `role="status" aria-live="polite"` announcer for async AI results on the Tailor surface (announces generating + final match score). Notification bell exposes unread count via `aria-label`.
  - _Requirements: 21.1, 21.2, 21.3, 21.4, 21.5, 21.6_

- [x] 17. Migration verification & cleanup
  - [x] 17.1 Parity verified and legacy screens retired: `/dashboard` → `/home` + `/resumes`; `/tracker` → `/applications`; `/resume-wizard` → `/wizard` (route pages deleted, empty dirs removed); the earlier-retired `/tailor` `/settings` `/resumes/[id]` legacy pages stay retired; removed the orphaned empty `(admin)` route group. `/builder` is intentionally kept as the transitional "advanced editor" linked from the Resume Editor. Shared modules the new UI reuses are preserved (`components/tracker/reorder.ts`, `lib/api/resume-wizard.ts`). Deeper dead-component pruning (superseded dashboard/tracker/wizard *components* whose unit tests still pass) is folded into the deferred 17.5 to keep the suite green.
    - _Requirements: 22.3, 22.5_
  - [x] 17.2 No backend changes were made: all new screens reuse the existing `lib/api/*` (resume, tracker, enrichment, resume-wizard, config) and `/print/*` + `components/resume/*` are untouched, so PDF output is identical (locked by `tests/export-pdf-url.test.ts`).
    - _Requirements: 22.1, 22.2_
  - [x] 17.3 Full frontend suite green (27 files / 193 tests) including the i18n locale-parity test; build green after every parent task.
    - _Requirements: 22.4_
  - [x] 17.4 Playwright E2E added (`playwright.config.ts` + `e2e/core-flow.spec.ts`, `test:e2e`/`test:e2e:ai` scripts, `@playwright/test` devDep, vitest excludes `e2e/**`). The navigation + real-data smoke suite (landing, app-shell nav, resumes→editor with live preview + export, tailor surface) **runs green against the live stack**; the AI-native core (tailor→review→accept→cover-letter→export) is authored and gated behind `RUN_AI_E2E=1`. The AI core was also verified manually against the running backend (JD upload → improve/preview ATS 100 → confirm → application auto-created). Bundle: route code-splitting + `optimizePackageImports` + per-icon lucide imports + lazy search index/panels.
    - _Requirements: 24.5_
  - [x] 17.5 Dead-component pruning done: removed the superseded `components/home/{hero,swiss-grid}`, the legacy tracker Kanban UI cluster (`components/tracker/{kanban-board,kanban-column,application-card,bulk-action-bar,card-detail-modal,manual-add-application-dialog}`, keeping the reused `reorder.ts`), the legacy `components/resume-wizard/*`, and the orphaned `components/dashboard/{master-resume-choice-dialog,resume-upload-dialog}` (keeping the engine's `resume-component.tsx`), plus their now-orphaned tests. Build + 173 tests + lint all green; live pages + E2E smoke re-verified. Global CSS/token teardown intentionally left (harmless coexistence; would only add risk).
    - _Requirements: 1.6, 22.3, 22.5_

## Notes

- Reuse-the-engine rule is non-negotiable: no changes to backend API, `lib/api/*`,
  `components/resume/*`, or `/print/*` (PDF output must stay identical).
- Auth (Task 5) and Admin (Task 15) are UI-only; they use typed stub/mock modules
  (`lib/api/auth.ts`, `lib/api/admin.ts`) so real backends can be wired later without UI
  changes.
- After each parent task, run the frontend test suite and the i18n locale-parity check.
- Delete superseded old components only after the replacement screen passes its parity
  checklist (Task 17.1).
