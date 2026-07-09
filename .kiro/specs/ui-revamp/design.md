# Design — FitWright Full UI Revamp

## Overview

A frontend-only revamp of FitWright into a **workflow-first, AI-native** interface with
FitWright's own warm/friendly design language (see Design Language) and light (default) /
dark themes. It prioritizes ease of use and a fast path to the core outcome — a tailored
resume + cover letter — while preserving the existing backend, API client, resume-render
components, and PDF pipeline.

The revamp is built inside the existing Next.js project. We rebuild the **interactive UI
layer** (design system, navigation, screens) and reuse the **engine** (API + rendering).

## Domain Model & Workflow (the organizing idea)

The UI is organized around **two first-class objects**, matching the user's mental model:

- **Resume** — the user's material: one **master** + **tailored** variants (a resume
  document: content, template, export).
- **Application** — one job pursuit: bundles the job description, its tailored resume, cover
  letter, interview prep, outreach, ATS score, notes, and pipeline status.

```
                    ┌──────────── Resume (master) ────────────┐
                    │  edit in Resume Editor                    │
                    └───────────────┬───────────────────────────┘
                                    │  Tailor to a job  (central action)
                                    ▼
   Home ─────────►  Tailor flow  ──────────►  Application  ──────────►  Applications
 (what's next)   (analyze → generate)      (workspace: resume +      (pipeline: track
                                            cover letter + prep +      through stages)
                                            outreach + score + status)
```

This reframing is what makes the app workflow-first: navigation follows *resume → tailor →
apply → track → interview → offer*, not internal feature names. "Tailor" is an action,
"Resume Editor" is a surface on a resume, and per-job deliverables live on the Application —
so there is no "Builder"/"Tracker"/"Tailor" mental model to learn.

### Universal Object Model (the canonical graph)

Every screen, route, data hook, and future API operates on this single graph. New
capabilities attach to nodes of it rather than creating parallel structures.

```
Master Resume
├── versions[]                     (Version — restore/compare; snapshots = future backend)
├── Tailored Resume
│   ├── Job Description            (the JD this variant targets)
│   ├── versions[]
│   └── Application                (1:1 with a tailored resume for a pursuit)
│       ├── status  (Tailoring│Applied│Interviewing│Offer│Rejected│Accepted│Withdrawn│Archived)
│       ├── Cover Letter
│       ├── Interview Prep
│       ├── Outreach
│       ├── ATS Score
│       ├── notes, timeline
│       └── reminders / interviews (future backend)
└── Tailored Resume → Application …

Cross-cutting (attach to nodes, not parallel trees):
  Search  → indexes Resume / Application / Job Description nodes
  Notifications → reference a node (e.g., "parsing complete" → Resume)
  Activity → events on nodes
```

Frontend types express these relationships (`lib/types`) so the future backend maps onto the
graph without UI restructuring. Ownership rule: resume-document concerns belong to a Resume
node (Resume Editor); job-pursuit concerns belong to an Application node (Application
Workspace) — nothing lives in both.

## Architecture

Layered architecture, with a hard boundary between the rebuilt UI and the reused engine:

```
┌─────────────────────────────────────────────────────────┐
│  Presentation (REBUILT)                                   │
│   • Theme provider (light/dark) + design tokens           │
│   • App shell: public top-bar layout / authenticated      │
│     sidebar layout / admin layout                          │
│   • Screens: landing, auth(stub), home, resumes +        │
│     resume-editor, tailor, applications + workspace,      │
│     settings, admin(mock)                                  │
│   • UI kit (shadcn/ui) + shared states                    │
├─────────────────────────────────────────────────────────┤
│  Integration (REUSED / EXTENDED)                          │
│   • lib/api/* existing client (UNCHANGED)                 │
│   • lib/api/auth.ts, lib/api/admin.ts (NEW, typed stubs)  │
│   • i18n message catalogs (extended)                      │
├─────────────────────────────────────────────────────────┤
│  Engine (UNCHANGED)                                       │
│   • Backend FastAPI API + contracts                       │
│   • components/resume/* template renderers                │
│   • /print/* PDF render pages (Playwright source)         │
└─────────────────────────────────────────────────────────┘
```

- **Routing:** Next.js App Router. Route groups: `(marketing)` (public top bar),
  `(app)` (authenticated shell: sidebar on desktop, bottom nav on mobile), and `(admin)`
  (separate shell). `/print/*` keeps its bare layout. `middleware.ts` guards `(app)` and
  `(admin)`.
- **Theming:** class-based provider on `<html>`, tokens as CSS variables, default light,
  persisted in an **httpOnly-safe cookie or `localStorage` mirror**, with an **inline
  pre-hydration script** in `<head>` that sets the theme class before first paint (prevents
  FOUC and hydration mismatch).
- **State (shared, typed providers):** `ThemeProvider`, `SessionProvider` (current user /
  role), `ToastProvider`, `CommandPaletteProvider` (⌘K), and a `TailorFlowProvider`
  implementing the tailor **state machine** (`start → analyzing → ready → generating →
  reviewing → saved`) with cancellation and input preservation. Everything else stays local
  to its route/component; server data goes through TanStack Query.

### Folder / code structure (feature-based)
```
apps/frontend/
  app/
    (marketing)/            # landing, privacy, terms  → PublicLayout
    (auth)/                 # login, signup            → centered card
    (app)/                  # authenticated shell (sidebar/bottom-nav)
      home/  resumes/  resumes/[id]/  import/  tailor/  applications/  applications/[id]/  settings/
    (admin)/                # admin shell
      page.tsx  users/  analytics/
    print/                  # UNCHANGED (PDF source)
    middleware.ts           # route guards
  features/                 # feature-scoped code (colocated), organized by workflow object
    home/                   # proactive workspace
    resumes/                # library + Resume Editor (content/template/preview/export)
    tailor/                 # AI-native tailor flow (state machine)
    applications/           # pipeline + Application Workspace (cover letter/prep/outreach)
    ai/                     # contextual AI actions, command palette, analysis panel
    settings/  admin/  auth/
      components/  hooks/  api.ts  types.ts
  components/
    ui/                     # shadcn-based primitives (shared)
    layout/                 # shells, sidebar, nav, theme toggle
    common/                 # EmptyState, LoadingSkeleton, ErrorState
    resume/                 # UNCHANGED template renderers
  lib/
    api/                    # existing client (UNCHANGED) + new auth.ts, admin.ts stubs
    query/                  # data-fetching setup (TanStack Query client, keys)
    i18n/  utils/  types/
  styles/                   # new design tokens + globals (coexists with old)
```
Conventions: feature-first colocation; shared UI in `components/ui`; one API access point
per feature (`features/*/api.ts` wrapping `lib/api`); PascalCase components, camelCase
hooks (`useX`), kebab-case files.

### Data layer
- Adopt **TanStack Query** as the single data-fetching layer: query keys per resource,
  consistent `isLoading`/`isError` handling, caching, background refetch, and optimistic
  updates for pipeline (application) moves and resume-editor edits. All screens consume it
  via `features/*/hooks` — no ad-hoc `fetch` in components.

### Performance discipline (preserved from the prior design)
- Per-route **First-Load JS budget ≤250KB**; enforce in CI or manual check.
- **No barrel imports** from `lucide-react` (import individual icons).
- **Lazy-load** heavy modules: TipTap editor, dnd-kit board, resume preview, and the admin
  charts library. Route-level code splitting via dynamic imports.

## Styling Strategy & Engine Isolation (no deletion)

The new design system is **added alongside** the existing styles; **nothing is deleted**.

- **Namespaced new tokens:** new tokens/utilities live in `styles/` and drive the app shell
  and rebuilt screens. Old Swiss tokens in `globals.css` remain until every consumer is
  migrated, so un-migrated screens keep working during the transition.
- **Engine isolation:** `components/resume/*` and `/print/*` are wrapped so they resolve
  the **original** tokens (e.g., a scoped `.resume-scope` wrapper or keeping their token
  definitions intact), guaranteeing the resume preview and PDF are visually unchanged.
- **Preview/print force light:** the resume preview container and all `/print/*` pages are
  pinned to light mode regardless of the app theme, so the on-screen preview matches the
  printed PDF (dark mode never affects resume output).
- **End-state cleanup is optional and deferred:** once all screens are migrated, unused old
  tokens *may* be removed in a separate, explicitly-scoped cleanup task — never during the
  revamp itself.

### Concrete design tokens (initial values, tunable)
- **Radius:** `sm 6px · md 10px · lg 14px · xl 20px` (cards/inputs use `lg`, buttons `md`).
- **Spacing scale (4px base):** `1=4 · 2=8 · 3=12 · 4=16 · 6=24 · 8=32 · 12=48 · 16=64`.
- **Type scale:** `xs 12 · sm 14 · base 16 · lg 18 · xl 20 · 2xl 24 · 3xl 30 · 4xl 36 · 5xl 48`,
  line-height 1.5 body / 1.2 headings; weights 400/500/600/700.
- **Breakpoints:** `sm 640 · md 768 · lg 1024 · xl 1280`.
- **Elevation:** `e1` subtle (cards), `e2` (popovers/menus), `e3` (modals) — soft, low-spread.
- **Motion:** durations `fast 120ms · base 180ms · slow 240ms`; easing `ease-out` for
  enter, `ease-in` for exit; **all motion disabled under `prefers-reduced-motion`**.
- **Color:** light + dark sets defined as CSS variables (surface, surface-2, text,
  text-muted, border, accent, accent-fg, success, warning, danger) meeting AA contrast.

### Component state matrix
Every interactive component defines: default · hover · active/pressed · focus-visible ·
disabled · loading · error — with tokens for each, verified in both themes.

## Design Language — "Atelier" (FitWright's own)

Inspired by, but distinct from, Notion/Tally/Cal.com. The metaphor is a **tailor's atelier**:
warm, precise, calm, and crafted — clarity over decoration. Named **Atelier** so the team
has one reference.

- **Visual personality:** warm neutral canvas (never stark white/black), soft rounded
  geometry, one confident accent, quiet hairline borders, gentle elevation. Typography does
  the hierarchy; color is used sparingly and meaningfully.
- **Interaction philosophy:** one obvious next step per screen; progressive disclosure of
  advanced options; direct manipulation (drag, inline edit) over dialogs where natural;
  keyboard-first power paths (⌘K).
- **Motion principles:** motion is functional, never decorative — it explains state changes
  and continuity (a card lifting, a panel sliding in). Durations `fast/base/slow`
  (120/180/240ms), `ease-out` enter / `ease-in` exit; transform+opacity only; **fully
  disabled under `prefers-reduced-motion`**.
- **Spacing philosophy:** generous, consistent 4px-based rhythm; whitespace creates
  hierarchy; asymmetric left-aligned layouts, not centered filler.
- **Information density:** calm by default (roomy) with an optional compact mode for
  data-dense surfaces (pipeline, admin tables). Never cram; reveal detail on demand.
- **Empty-state philosophy:** every empty state teaches the next action (never a blank
  screen) — it points to the single best step.
- **Loading philosophy:** show real structure (skeletons matching final layout) and real
  progress (staged AI results); never fake spinners/percentages.
- **AI-interaction philosophy:** AI is a **calm, transparent collaborator**, not a chatbot.
  A consistent "AI voice": suggestions are proposals (preview before apply), generation is
  staged and cancellable, outputs are explainable, and the truthfulness guard is always
  visible. AI presence has a consistent visual signature (a subtle accent/mark) and every
  AI action is cost-aware (indicates it will call the user's provider).
- **Accessibility philosophy:** AA is a floor, not a goal — keyboard-complete, focus-visible,
  reduced-motion honored, live-region announcements for async AI results.
- **Consistency rules:** one component kit, one token set, one accent-per-region rule; the
  same interaction pattern for the same job everywhere.
- **Responsive principles:** desktop and mobile are each intentionally designed (not
  auto-stacked); the same capabilities remain reachable on both (see Mobile Design).

## Design Principles

1. **One obvious next step.** Every screen has a single primary action. The core journey
   (resume → tailor → apply → track) is always the shortest path.
2. **Warm & calm (Atelier).** Rounded corners, soft neutral surfaces, generous whitespace,
   gentle shadows. One accent color. No harsh edges (resume-render output is exempt).
3. **Progressive disclosure.** Show the essentials first; reveal advanced options (prompt
   style, template settings, custom prompts) on demand.
4. **Trust through transparency.** Always show what the AI changed (diff) and never imply
   fabricated content (truthfulness guard surfaced in the UI).
5. **AI-native, cost-aware.** AI understanding is continuous (analysis, staged results,
   contextual actions); no AI call happens without the user's intent.
6. **Consistent kit.** Every screen composes the same shadcn/ui-based components.

## Design System

### Theming architecture
- CSS variables on `:root` (light) and `.dark`, toggled via a `next-themes`-style provider
  (class strategy) with `localStorage` persistence; default light.
- Tokens exposed to Tailwind v4 via `@theme` in `globals.css`.

### Color tokens (warm/friendly)
- **Surface:** warm off-white base in light (e.g. `#FBFAF8`/`#FFFFFF` cards), warm dark
  charcoal in dark (e.g. `#1A1A18`/`#232320` cards) — soft, never pure black/white glare.
- **Text:** near-black warm gray (light) / warm off-white (dark), with muted secondary.
- **Accent:** a single friendly accent (indigo/blue family, e.g. `#4F46E5`/`#6366F1`) used
  only for primary actions and active states.
- **Semantic:** success (green), warning (amber), danger (red) — muted, accessible.
- **Borders:** low-contrast hairline borders; elevation via soft shadows, not heavy lines.

### Typography
- Sans UI font (Geist or Inter — already available). Friendly, high legibility.
- Scale: display/H1 → H2 → H3 → body → small/caption. Hierarchy by size + weight, not color.
- Comfortable line-height; left-aligned by default.

### Shape & elevation
- Radius: medium-large on cards/inputs/buttons (rounded, Notion-like). No sharp corners.
- Shadows: soft, low-spread. Hover lifts subtly.

### Spacing & layout
- 4px base spacing scale. Roomy padding on cards and sections.
- Content max-width containers; asymmetric, left-aligned compositions.

### Component library
- **shadcn/ui** (Radix primitives + Tailwind), themed to the tokens above.
- Rebuild `components/ui/*` on shadcn: Button, Input, Textarea, Select, Dialog, Dropdown,
  Tabs, Card, Toast (sonner), Tooltip, Badge, Avatar, Skeleton, Progress, Switch, Table,
  Sidebar. Keep existing rich-text editor (TipTap) and dnd-kit, restyled to tokens.
- Standard states: `<EmptyState>`, `<LoadingSkeleton>`, `<ErrorState>` reusable wrappers.

## Information Architecture & Navigation (workflow-first)

**Three primary destinations** (+ Settings in the account menu). "Tailor" is the central
action; "Resume Editor" and "Application Workspace" are surfaces reached from an object, not
nav items.

### Route map
```
/                     Landing (public, top bar)
/login  /signup       Auth (public, centered card)     [UI-only this phase]
/privacy  /terms      Static legal (public)

(app)  Authenticated shell — sidebar (desktop) / bottom nav (mobile):
  /home                    Home — proactive workspace (next actions, pipeline, health)
  /resumes                 Resume library (master + tailored)
  /resumes/[id]            Resume Editor (content / template / preview / export / enrichment)
  /import                  Add resume (upload + wizard)      [entry from Home/Resumes]
  /tailor                  Tailor flow (AI-native: analyze → generate) → creates Application
  /applications            Applications pipeline (Kanban + list)
  /applications/[id]       Application Workspace (tailored resume + cover letter +
                           interview prep + outreach + score + status)
  /settings                Profile / AI / Preferences / Account (sub-tabs)

(admin)  Admin shell (separate layout):                     [UI-only this phase]
  /admin  /admin/users  /admin/analytics

/print/resumes/[id]        PDF render source (REUSED, unchanged)
/print/cover-letter/[id]   PDF render source (REUSED, unchanged)
```
Retired concepts: `/dashboard` → `/home`; `/builder/[id]` → `/resumes/[id]` (Resume
Editor); `/tracker` → `/applications`. Cover letter / interview prep / outreach are no
longer builder tabs — they live in `/applications/[id]`.

### Desktop navigation (sidebar)
- Brand (FW logo + "FitWright") → Home.
- Primary nav: **Home · Resumes · Applications**.
- **Central primary action:** prominent **"Tailor to a job"** button (starts the flow).
- Command palette hint (⌘K); footer: theme toggle, account menu (Settings, Logout).

### Mobile navigation (bottom bar)
- Bottom tabs: **Home · Resumes · [ Tailor ] · Applications**, with **Tailor as the raised
  center action**. Account/theme in a Home header menu. (Full mobile design in Mobile
  Design section.)

### Command palette (⌘K)
- Combines navigation ("Go to Applications"), object actions ("Tailor for <company>",
  "Open master resume"), and contextual AI commands ("Shorten summary"). Progressive,
  keyboard-first, discoverable.

### Public top bar (landing/auth)
- Logo, links (Features, How it works, GitHub), theme toggle, "Get Started" button.

## Screen Designs (feature mapping)

### Landing (`/`)
Single scroll: Hero (FW logo, "FitWright", tagline "Built to fit", primary CTA + GitHub) →
What it does (1–2 lines) → Features grid (tailoring, cover letter, interview prep, ATS
score, templates, application tracking) → How it works (Upload → Paste JD → Review → Export)
→ About the
developer (small strip: name + links) → Footer (Privacy, Terms, GitHub, LinkedIn, site).

### Auth (`/login`, `/signup`) — UI-only
Centered card: logo, heading, "Continue with Google" button, email field, submit,
switch-link (login↔signup), validation/error states. Stubbed submit handlers.

### Home (`/home`) — Req 6,7 (lightweight launchpad)
A short, prioritized launchpad (like Notion/Linear/Cursor's entry), **not a dashboard**:
1. **"Tailor to a job"** primary action.
2. **Continue where you left off** — the single most recent in-progress item (resume,
   application, or tailoring) with a resume-in-one-click affordance.
3. **Needs attention** — a short list only when relevant: failed processing, missing AI
   config; (future backend) follow-ups due / interviews soon.
4. **Recent resumes & applications** — a compact list linking into their destinations.
Everything else (full activity, deep stats) is hidden behind "See more" (progressive
disclosure). Pipeline analytics live in Applications; resume health lives in the Editor —
Home only links out, never duplicates. First-run empty state offers Upload / Wizard.

### Resumes library (`/resumes`) + Resume Editor (`/resumes/[id]`) — Req 8,10,11
- **Library:** resumes (master + tailored) with title, master badge, status pill, date;
  actions (Open in Editor, Tailor, Export, Delete); "Add resume" → upload/wizard.
- **Import:** upload zone (drag-drop + click, MIME-or-extension validation, progress,
  friendly parse-failure + retry); wizard (question card + live preview + step progress).
- **Resume Editor** (resume-document only) — a **content-first single surface**, not tabs:
  - Left: the content editor (rich text, drag-drop reorder, add/remove custom sections).
  - Right: **always-visible live preview**.
  - **Appearance inspector:** template choice + customization (font, accent, contact icons,
    compact) live in a **toggleable side/floating inspector** — not a dedicated tab.
  - **AI is inline/contextual:** enrichment (analyze / enhance bullet / regenerate item /
    skills) and **"ask AI"** appear on the relevant bullet/section, preview-before-apply.
  - **Export** is an action button; **version history** (restore original / undo last AI /
    compare) is an action opening a panel (Req 31).
  - Saved/dirty + autosave + unsaved-changes guard + draft recovery (Req 30).
  - No cover letter / prep / outreach here (those are per-application).

### Tailor flow (`/tailor`) — Req 9,15,27 (AI-NATIVE CORE)
**Internal** state machine `start → analyzing → ready → generating → reviewing → saved`
drives logic — but the **visible UX is ONE continuous surface, not a wizard** (no "Step 1
of 5"). Source + JD sit at the top; analysis and results appear **inline, in place** as they
become ready:
1. Pick/confirm source resume; paste JD (URL-fetch = future backend).
2. On JD entry, an inline **analysis summary** appears: e.g. "Backend Engineer · 18 keywords
   · 7 missing · looks good" with a single **Generate** button. Full detail (keyword list,
   skill gaps, fit breakdown) is collapsed behind **"Expand details"** (progressive
   disclosure) — no up-front overload.
3. **Generate** renders results **progressively in the same view** (keyword coverage →
   section rewrites → ATS score), cost-aware, cancellable, input preserved, no fake progress.
4. Results: tailored preview + ATS score (with **explain**) + keyword highlights + a
   **change summary** with per-change accept/discard (Req 15.5) and AI-vs-user visual
   distinction.
5. Save → creates/updates an **Application** and routes to its Workspace; cover letter
   offered inline so "resume + cover letter" is one continuous path.
Recovery: input/draft preserved across refresh/cancel/failure (Req 30).

### Application Workspace (`/applications/[id]`) — Req 12,13,14,17 (per-job hub)
**Overview + resource sections** (in-workspace tabs/segments), not one long scroll —
reduces cognitive load without deep navigation:
- **Overview** (default): company · role · stage control · notes; tailored-resume preview +
  ATS score + "Edit resume" (→ Resume Editor, returns here) + export; at-a-glance status of
  each deliverable (generated / not yet).
- **Cover Letter** section — generate/edit/preview/export (Req 12).
- **Interview Prep** section — generate/read (Req 13).
- **Outreach** section — generate/edit/copy (Req 14).
- **Actions:** move through the **full lifecycle** (incl. Rejected/Accepted/Withdrawn/
  Archived), **Duplicate application**, **Reuse resume** (new application from this variant).
- (Future backend) follow-up reminders + interview scheduling, shown when data exists.

### Applications pipeline (`/applications`) — Req 17
Kanban (dnd-kit) themed to tokens: columns for the full lifecycle (Tailoring → Applied →
Interviewing → Offer + terminal Rejected/Accepted/Withdrawn/Archived), draggable cards
(company, role, status, dates), list-view toggle, quick filters (Req 32), manual add, bulk
actions, archive. Cards open the Application Workspace. Auto-populated when a resume is
tailored. Mobile: swipe cards between stages.

### Settings (`/settings`) — Req 18,19
Sub-tabs:
- **Profile** — name, avatar (upload stubbed).
- **AI Providers & API Key** — provider select, BYO key entry (encrypted), test-connection
  with clear pass/fail.
- **Preferences** — UI language, content language, theme, feature toggles, custom prompts
  (tailoring style, cover letter, outreach).
- **Account** — logout, reset/delete behind confirm dialogs.

### Admin (`/admin/*`) — Req 20 (UI-only, mock data)
Separate admin shell (own sidebar/topbar, distinct accent tint):
- **Overview** — stat cards (total users, active users, resumes tailored, sign-ups),
  time-series charts, recent activity list.
- **Users** — searchable/sortable table (name, email, joined, status, usage), row detail
  drawer, enable/disable/delete (stubbed).
- **Analytics** — charts (signups over time, active users, feature usage).
- Data via an `adminApi` module returning mock data now; same interface swaps to real APIs
  later (Req 20.6).

## AI-Native Interaction Design

The product feels intelligent by making AI understanding continuous and trustworthy — not by
adding a chatbot.

- **Pre-generation analysis (summary-first):** on JD paste, run keyword extraction and show
  a **one-line summary** ("Backend Engineer · 18 keywords · 7 missing · looks good") with a
  single Generate action; full detail is collapsed behind "Expand details" (no overload).
  Cached per JD (by content hash) — cost-aware.
- **Staged generation:** decompose the existing pipeline so useful output appears
  continuously (keywords → section rewrites → score) instead of one long spinner. **Future
  backend:** token-level streaming (SSE) for live text; the staged model is the shippable
  approximation now.
- **Contextual AI (not chat):** an inline "ask AI" affordance on a bullet/section maps to
  the existing regenerate-with-instruction endpoint (rewrite/shorten/quantify/seniority),
  always preview-before-apply.
- **Trust surfacing:** change summaries ("14 bullets modified"), per-change accept/discard,
  AI-vs-user visual distinction, and "explain this score / this change" popovers (Req 15).
- **Command palette (⌘K) — enhancement, not required:** a power-user accelerator for
  navigation, global search, and AI commands; every action is also reachable in the visible
  UI. Not a primary interaction model.
- **Cost awareness:** every AI trigger is explicit and labeled; no background/auto AI calls
  the user didn't initiate; reuse cached analysis/keywords to avoid duplicate spend.
- **Consistent AI voice:** one visual signature for AI-generated/AI-suggested content and a
  consistent proposal→preview→apply pattern everywhere.

## Multi-Device Design (desktop / tablet / mobile — each intentional)

**Desktop:** sidebar shell; multi-pane surfaces (editor + preview, pipeline board);
hover/keyboard affordances; the primary target for deep resume editing.

**Tablet:** the desktop layout adapted for touch — larger targets, sheets instead of hover
menus, and multi-pane surfaces collapsible to one pane via a toggle. A defined adaptation,
not a third full redesign.

**Mobile (its own experience, not a stacked desktop):**
- **Navigation:** bottom tab bar (Home · Resumes · **Tailor** center · Applications);
  account/theme in a header menu. Thumb-reachable primary actions.
- **Task intent:** mobile optimizes **tailor, review/approve, track, export/share**; deep
  resume WYSIWYG editing is **desktop-first** (mobile offers streamlined per-section editing
  + full-screen preview, not a shrunk split view).
- **Tailor on mobile:** a focused single-column continuous surface (source → inline analysis
  summary → generate → review), large touch targets.
- **Applications on mobile:** vertical stage sections with **swipe-to-advance** gestures; the
  Application Workspace uses stacked sections / bottom sheets per deliverable.
- **Patterns:** bottom **sheets/drawers** instead of hover menus; sticky primary action;
  progressive disclosure; safe-area aware.
- **Continuity:** the same capabilities exist on all devices; mobile never dead-ends a task
  desktop can do (it may recommend desktop for deep editing).

## Resilience & Recovery (Req 30)

Productivity-grade robustness, mostly client-side now:
- **Draft persistence:** the Resume Editor and Tailor flow persist working state to local
  storage (debounced), keyed by object id; restored on refresh/crash via a `RecoveryBanner`
  ("restore unsaved changes?").
- **Graceful failure:** network/API errors show an actionable `ErrorState`/toast with retry;
  user input is never dropped on failure.
- **Cancel/failure of AI:** returns to the last stable state with input intact; no partial
  corrupt save (the tailor state machine treats generation as atomic wrt persistence).
- **Autosave conflict:** on save, compare a local draft token vs server `updated_at`; on
  mismatch, prompt keep-mine / take-latest (no silent overwrite).
- **Offline:** an offline indicator disables network actions while preserving local edits.
  **Full offline editing (service worker/PWA) is future/optional** — not built now.
- **Cold-start UX (hosted free tier, see roadmap ADR-15):** the always-warm frontend renders
  shell/skeletons/cached data optimistically; any backend request exceeding ~3s shows a
  friendly "starting the server…" waking state instead of a frozen spinner (the hosted
  backend may cold-start after idle).

## Version History (Req 31 — design now, backend later)

- **Available now (existing data):** "restore original parsed resume" (from
  `original_markdown`) and "undo last AI generation" (revert to pre-diff state).
- **Designed, future backend:** a `VersionHistoryPanel` listing versions with
  **compare (diff)** and **restore** (restore = new current state, non-destructive). It reads
  from a typed `history` interface returning available data now and full per-edit snapshots /
  branching later — no UI change on swap.
- No fabricated content; restore/compare operate only on stored states.

## Search & Filtering (Req 32)

- **List-level:** Resumes and Applications lists have quick filters (status/stage) + sort on
  loaded data.
- **Global search:** a typed `search` interface over Resume / Application / Job Description
  nodes of the object graph, surfaced via the **command palette** and a visible search
  affordance. Client-side over loaded data now; **server-side search is future backend** for
  scale (same interface).
- **Reserved (future backend):** recent, favorites, pinned, saved searches — defined on the
  interface, not built now.

## Notifications (Req 33)

- **Transient (now):** toasts for export finished / AI generation failed / parsing complete.
- **Persistent + scheduled (future backend):** interview tomorrow, API key expired,
  follow-up due — surfaced in a `NotificationCenter` reading a typed `notifications`
  interface (transient/local items now, server items later).
- Non-intrusive, dismissible; each notification references an object-graph node; never leaks
  resume content.

## Components and Interfaces

**Layout components:** `PublicLayout` (top bar), `AppLayout` (desktop sidebar), `BottomNav`
(mobile), `AdminLayout`, `ThemeProvider`, `ThemeToggle`, `AccountMenu`, `Sidebar`,
`CommandPalette` (⌘K).

**UI kit (shadcn/ui, themed):** Button, Input, Textarea, Select, Dialog, Sheet/Drawer,
Dropdown, Tabs, Card, Toast, Tooltip, Badge, Avatar, Skeleton, Progress, Switch, Table;
plus shared `EmptyState`, `LoadingSkeleton`, `ErrorState`.

**AI-native components:** `AnalysisPanel` (JD keywords/role/skill-gaps/fit snapshot),
`ContextualAiMenu` ("ask AI to change this" on a bullet/section), `ExplainPopover` (explain
score/change), `StagedResult` (progressive generation view), `AiActionButton` (cost-aware,
indicates an AI call).

**Cross-cutting components:** `RecoveryBanner`, `OfflineIndicator` (Req 30),
`VersionHistoryPanel` (Req 31), `GlobalSearch` (Req 32), `NotificationCenter` (Req 33),
`ChangeSummary` + `AiAuthoredMarker` (Req 15).

**Feature components (rebuilt, wired to existing APIs):**
- home (launchpad): `PrimaryTailorAction`, `ContinueCard`, `NeedsAttention`, `RecentList`.
- resumes: `ResumeLibrary` (+ filters), `UploadZone`, `Wizard`+`LivePreview`, `ResumeEditor`
  (`ContentEditor` + always-on `LivePreview`, `AppearanceInspector`, `ExportAction`),
  `SectionEditor`, inline `EnrichmentActions`.
- tailor: `TailorFlow` (internal state machine) rendered as one continuous surface —
  `Source+JDInput`, inline `AnalysisSummary` (+ expandable detail), `StagedResult`,
  `AtsScoreCard`, `KeywordHighlights`, `ChangeSummary`/`DiffPreview`.
- applications: `ApplicationsPipeline` (`KanbanBoard` + list view + filters),
  `ApplicationWorkspace` (`OverviewSection`, `CoverLetterSection`, `InterviewPrepSection`,
  `OutreachSection`, `LifecycleControl`, `DuplicateReuseActions`).
- settings: `SettingsTabs`. admin: `StatCard`, `UsersTable`, `UsageCharts`.

**Interfaces (mock/stub for later wiring):**
- `lib/api/auth.ts` — `login()`, `signup()`, `loginWithGoogle()`, `getSession()`,
  `logout()` (stubbed now; typed for real wiring).
- `lib/api/admin.ts` — `getAdminStats()`, `listUsers(query)`, `getUser(id)`,
  `setUserStatus(id, status)`, `getUsageSeries(range)` (return mock data now).
- `lib/api/history.ts` — `listVersions(resumeId)`, `getVersion(id)`, `restoreVersion(id)`,
  `restoreOriginal(resumeId)`, `undoLastAi(resumeId)` (available-data now, snapshots later).
- `lib/api/search.ts` — `search(query, scope)` over resume/application/JD nodes (client-side
  now, server-side later); reserves recent/favorites/pinned/saved.
- `lib/api/notifications.ts` — `list()`, `dismiss(id)` (transient/local now, server later).
- Existing `lib/api/*` (resumes, jobs, tailor, config, applications/tracker) — consumed as-is.

## Data Models

**Reused (from backend, via existing API — shapes unchanged):**
- `Resume` — `resume_id`, `content`, `processed_data` (structured resume JSON),
  `is_master`, `parent_id`, `processing_status`, `cover_letter`, `outreach_message`,
  `interview_prep`, `title`, timestamps.
- `Job` — `job_id`, `content` (JD), keywords/preview metadata.
- `Application` — `application_id`, `job_id`, `resume_id`, `status`, `company`, `role`,
  `position`, `notes`, timestamps. Status is a string; the UI defines the **lifecycle enum**
  (`Tailoring│Applied│Interviewing│Offer│Rejected│Accepted│Withdrawn│Archived`) mapping onto
  it. `original_markdown` on a Resume backs "restore original".
- `ApiKey` — provider + encrypted ciphertext (never exposed to UI in plaintext).

**Object-graph types (Req 34):** typed relationships in `lib/types` expressing
`MasterResume → TailoredResume → Application → {CoverLetter, InterviewPrep, Outreach}`, with
`JobDescription` on the Application and `Version[]` on a Resume — the single model all hooks
and routes use.

**New UI-only models (typed, mock now):**
- `AuthUser` — `id`, `name`, `email`, `avatarUrl`, `role` ("user" | "admin").
- `Session` — `user: AuthUser | null`, `status` ("authenticated" | "loading" | "guest").
- `Version` — `id`, `resumeId`, `label`, `createdAt`, `source` ("original" | "ai" | "manual")
  — restore/compare (snapshots future backend).
- `SearchResult` — `nodeType` ("resume" | "application" | "jd"), `id`, `title`, `snippet`.
- `Notification` — `id`, `kind` ("transient" | "persistent"), `type`, `nodeRef`, `message`,
  `read`, `createdAt` (persistent/scheduled future backend).
- `ChangeSummary` — counts + per-change `{ path, before, after, status }` for trust UX.
- `AdminStats` — `totalUsers`, `activeUsers`, `resumesTailored`, `coverLettersGenerated`,
  `signups` counters.
- `AdminUserRow` — `id`, `name`, `email`, `joinedAt`, `status`, `usageCount`.
- `UsageSeriesPoint` — `date`, `value` (per metric) for charts.

**Analytics/telemetry schema (for admin dashboards; mock now, real later):**
- `TelemetryEvent` — `type` ("signup" | "login" | "resume_tailored" | "cover_letter" |
  "interview_prep" | "export"), `userId`, `timestamp` (no resume content / PII in payload).
- Admin dashboards consume **aggregated** metrics derived from these events via
  `lib/api/admin.ts`; the same interface returns mock aggregates now and real ones later.

## Data & API Reuse

- **Keep** `lib/api/*` as the single integration point; new screens call existing methods.
- **Keep** `components/resume/*` (template renderers) and `/print/*` pages **unchanged** so
  PDF output is identical.
- **New** `lib/api/admin.ts` (mock) and `lib/api/auth.ts` (stub) with typed interfaces for
  later wiring.
- i18n: reuse `messages/*.json`; add new keys; maintain locale parity (Req 19, 22.4).

## Security & Access Control (designed now, enforced when wired)

Even though auth/admin are UI-only this phase, the security model is fixed now so it can't
be built insecurely later:

- **Sessions:** httpOnly + SameSite=Lax cookies set by the backend; the browser never holds
  a readable token. No auth tokens in `localStorage`.
- **Route guards:** `middleware.ts` protects `(app)` (redirect to `/login` if no session)
  and `(admin)` (redirect/403 if role ≠ admin). `SessionProvider` exposes the current user
  and role to the UI for conditional rendering only — never as the security boundary.
- **Admin RBAC:** every admin data call goes through `lib/api/admin.ts`; when wired, the
  server enforces the admin role on each endpoint. Hiding the UI is not access control.
- **BYO API key:** the key input is write-only/masked; the API never returns a decrypted
  key to the browser (matches the existing encrypted `api_keys` store). Per-user encryption
  when multi-user.
- **OAuth (Google):** authorization-code flow with a `state` param (CSRF), backend-handled
  callback, session cookie issued server-side.
- **CSRF:** state-changing requests protected via SameSite cookies + CSRF token where
  applicable.
- **Data handling:** account deletion cascades user data; admin views show minimized PII;
  Privacy/Terms pages state retention and data use. UI never echoes raw backend errors.

## Theming & Charts
- Class-strategy theme provider for light/dark (default light) with a pre-hydration inline
  script to prevent FOUC. Verify `next-themes` (or a small custom provider) against Next 16
  / React 19 before adopting.
- Charts (admin): a lightweight, tree-shakeable lib (evaluate Recharts vs a minimal
  alternative) — **lazy-loaded** and themed to tokens; decision gated by bundle impact.

## Dependency Compatibility (verify before adopting)
- shadcn/ui against **Tailwind v4** (shadcn historically targeted v3) — confirm generator +
  component compatibility, or pin working versions.
- Theme provider + charts lib against **Next 16 / React 19**.
- Any new dep must respect the ≤250KB per-route budget and support lazy-loading.

## Responsiveness & Accessibility
- Desktop (sidebar) and mobile (bottom nav) are **each intentionally designed** — see Mobile
  Design; the Resume Editor is desktop-first with a streamlined mobile editing/review mode
  (not a shrunk split view).
- Radix/shadcn provide focus management, ARIA, and keyboard nav; verify contrast in both
  themes; visible focus rings; semantic landmarks (header/nav/main); `prefers-reduced-motion`
  honored; skip-to-content link; ARIA live regions announce async AI results.

## Delivery Phasing

- **Phase 1 (local, single-user, shippable):** foundation (tokens + coexisting styles + UI
  kit + app shell + command palette) → Landing → Home → Resumes/Import + Resume Editor →
  **Tailor (AI-native core)** → Applications (pipeline + workspace) → Settings, all on the
  existing SQLite backend. Auth/admin are UI-only and isolated so Phase 1 ships without
  them. Future-backend items (JD-URL fetch, streaming, follow-up/interview scheduling) are
  designed but deferred.
- **Phase 2 (hosted, multi-user):** wire auth (httpOnly sessions, Google OAuth), the
  security model, PostgreSQL (SQLite for local dev → hosted Postgres, e.g. Neon free tier
  — see phase-2-roadmap ADR-13), and the admin backend + real telemetry.

## Migration Strategy (screen-by-screen, low risk)

1. **Foundation:** new coexisting tokens + theme provider (pre-hydration script) + shadcn
   kit + app shell (sidebar + bottom nav) + command palette + TanStack Query setup. Old
   tokens remain; the app still routes to existing screens until each is replaced.
2. **Replace screens in order:** Landing → Home → Resumes + Resume Editor (import/wizard) →
   Tailor (AI-native) → Applications (pipeline + workspace) → Settings → (Auth stub) →
   (Admin stub).
3. For each screen: build the new version under the same route, verify the **feature-parity
   checklist**, then retire that screen's superseded component. **No global CSS/token
   deletion** occurs during migration; the engine (resume/print) keeps its original tokens.
4. Keep `/print/*` + `components/resume/*` untouched, pinned to light mode, throughout.
5. Run frontend tests + locale parity + bundle-budget check after each screen; keep the
   branch green.
6. **Optional end-state cleanup (deferred, separate task):** only after everything is
   migrated, unused old tokens *may* be removed under an explicit, isolated cleanup.

### Feature parity checklist (must survive migration)
Upload (PDF/DOCX, drag-drop) · Wizard + live preview · Master concept · Tailor (JD, style,
language) · ATS score · Keyword highlight · Diff accept/discard · Truthfulness note · Cover
letter (gen/edit/export) · Outreach (gen/edit/copy) · Interview prep · AI enrichment
(analyze/enhance/regenerate item/skills) · Section editor (rich text, drag-drop, custom
sections) · 7 templates + customization · Live preview = PDF · PDF export (resume + cover
letter) · Kanban (drag, auto-card, manual add, bulk, detail) · Multi-provider + BYO key +
test connection + custom prompts · UI + content i18n.

## Correctness Properties

Invariants the revamp must uphold:

### Property 1: PDF output equivalent
`/print/*` and `components/resume/*` are untouched and pinned to light mode, so the exported
PDF is **visually and text-content equivalent** (not byte-identical) to pre-revamp output
for the same data and template, verified by a regression check.
**Validates: Requirements 11.5, 16.2, 16.4, 22.2**

### Property 2: No feature loss
Every item on the feature-parity checklist exists in the new UI before the old screen is
deleted.
**Validates: Requirements 22.3, 22.5**

### Property 3: Backend contract stability
No request/response shape consumed by the UI changes.
**Validates: Requirements 22.1**

### Property 4: Single master invariant
The UI never allows more than one master resume (enforced by backend; UI reflects it).
**Validates: Requirements 8.1**

### Property 5: Theme persistence
The selected theme survives reloads and applies before first paint (no flash of wrong
theme), defaulting to light when unset.
**Validates: Requirements 1.3, 1.4**

### Property 6: Locale parity
All user-facing strings are translation keys, and every locale has the same key set (parity
test stays green).
**Validates: Requirements 19.2, 22.4**

### Property 7: Truthfulness surfaced
The diff/result UI never presents AI additions as anything other than derived from the
master resume.
**Validates: Requirements 15.4**

### Property 8: Mock/real swap
Replacing admin/auth mock modules with real APIs requires no change to admin/auth screen
components.
**Validates: Requirements 20.6, 26.2**

### Property 9: Styles coexist, engine unaffected
The new token system is added without deleting old CSS; resume-template and `/print/*`
styling resolve the original tokens, so no un-migrated screen or the PDF output breaks
during migration.
**Validates: Requirements 1.6, 22.2**

### Property 10: Access is server-enforced
Authenticated/admin routes are gated by middleware and (when wired) server-side role
checks; hiding UI is never the access boundary, and sessions use httpOnly cookies.
**Validates: Requirements 23.1, 23.2, 23.3**

### Property 11: Secrets never exposed
The decrypted BYO API key is never returned to the browser; the key field is
write-only/masked and stored encrypted.
**Validates: Requirements 23.4**

### Property 12: No silent data loss
The Resume Editor autosaves or blocks navigation on unsaved changes; the tailor flow
preserves user input across errors/timeouts/cancellation.
**Validates: Requirements 10.5, 9.8**

### Property 13: Workflow object integrity
Tailoring creates/updates an Application; per-job deliverables (cover letter, interview
prep, outreach) belong to the Application, and resume-document editing belongs to the Resume
Editor — no feature is orphaned or duplicated across surfaces.
**Validates: Requirements 9.7, 10.1, 17.2, 17.4**

### Property 14: Cost-aware AI
No AI/provider call occurs without explicit user intent; analysis/keywords are cached and
reused to avoid duplicate spend; every AI action indicates it will call the provider.
**Validates: Requirements 27.5**

### Property 15: Universal object-graph consistency
Every screen, route, data hook, and type operates on the canonical graph
(MasterResume → TailoredResume → Application → deliverables); new capabilities attach to
graph nodes rather than parallel structures.
**Validates: Requirements 34.1, 34.2, 34.4**

### Property 16: Crash/refresh recovery
Unsaved Resume Editor / Tailor input is restorable after refresh or crash; autosave never
silently overwrites newer server state (conflict prompts the user).
**Validates: Requirements 30.2, 30.4**

### Property 17: Trust transparency
AI edits present a change summary with per-change accept/discard and a visual distinction
between AI-authored and user-authored content; restore/compare are non-destructive.
**Validates: Requirements 15.5, 15.6, 31.3**

## Error Handling

See **Resilience & Recovery (Req 30)** for the full failure/recovery strategy; the patterns
below are the baseline it builds on.

- **API/network errors:** show a friendly inline `ErrorState` or toast; never echo raw
  backend error text (may leak details); offer retry where safe.
- **Upload/parse failures:** explain the likely cause (unsupported/scanned file) with a
  retry action (matches the fixed MIME-or-extension validation).
- **Tailor/generation failures:** keep the user's input intact, show a clear message, allow
  re-run; long operations show progress and a bounded timeout message.
- **Not-configured AI key:** non-blocking prompt linking to Settings, rather than a hard
  error.
- **Auth/admin (stub phase):** stubbed handlers surface a clear "not yet available" state
  rather than failing silently.
- **Destructive actions:** always behind an explicit confirm dialog.

## Testing Strategy
- Component/unit tests (Vitest + Testing Library) for new interactive components and the
  tailor-flow state logic.
- Keep existing tests green; maintain i18n locale parity (resolve the pre-existing `fr.json`
  gap first — complete or drop French).
- **PDF/preview regression:** an automated visual/text-content check that the exported PDF
  matches pre-revamp output for a fixed sample (the repo's `e2e_monitor` render probe can
  seed this), plus a manual spot-check.
- **E2E (Playwright):** cover the core path (import → tailor → cover letter → export) and,
  once wired, auth guards + admin RBAC (unauthorized user cannot reach `(app)`/`(admin)`).
- Accessibility checks per screen: keyboard nav, visible focus, contrast (both themes),
  `prefers-reduced-motion`, skip link, and live-region announcements.
- Bundle-budget check per route (≤250KB First-Load JS).

## Tech Decisions Summary
- Stack unchanged: Next.js 16, React 19, TypeScript, Tailwind v4.
- Add (after compatibility verification): shadcn/ui, a class-based theme provider with
  pre-hydration script, **TanStack Query** (data layer), a lazy-loaded charts lib (admin).
- Reuse: TipTap, dnd-kit, `lib/api/*`, `components/resume/*`, `/print/*` (all unchanged).
- Styling: new tokens **coexist** with old (no deletion); engine styles isolated; preview/
  print pinned to light.
- Security: httpOnly-cookie sessions, `middleware.ts` guards, server-side admin RBAC,
  write-only API-key field — designed now, enforced when wired.
- Admin/auth backend + telemetry: deferred to Phase 2; UI built against typed mock/stub
  modules with a swappable interface.
- Performance: ≤250KB First-Load JS per route, no `lucide-react` barrel imports, lazy-load
  heavy modules.
