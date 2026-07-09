# Requirements Document

## Introduction

FitWright is an AI resume-tailoring app (upload a master resume → tailor it to a job
description → generate cover letter / interview prep → export PDF, with an application
tracker). The current UI is functional but hard to use: features are buried in a
monolithic builder, there is no clear global navigation, and there is no obvious "fast
path" to the core outcome (a tailored resume + cover letter).

This spec defines a **full UI revamp** with a **warm, friendly, professional design
language** (inspired by, not imitating, Notion/Tally/Cal.com) and **light (default) and
dark modes**. The goal is a professional, modern, **very easy-to-use**, **AI-native**
interface that gets users to their result fast.

**Design philosophy — workflow-first, not feature-first.** The IA is organized around the
user's job-search lifecycle, not around internal features. The domain has two first-class
objects:
- **Resume** — the user's material (one master + tailored variants).
- **Application** — a single job pursuit that bundles the job description, its tailored
  resume, cover letter, interview prep, outreach, ATS score, and pipeline status.

The user's mental model — *"I have a resume → I found a job → tailor it → apply → track it
→ interview → offer"* — is the navigation. Top-level destinations reduce to **Home ·
Resumes · Applications** (plus Settings); **"Tailor to a job"** is the central action (not
a destination); resume editing is a surface reached from a resume; per-job deliverables
live in the Application workspace.

**AI-native, not transactional.** The product surfaces AI understanding continuously
(instant JD analysis, keyword/skill-gap detection, staged/progressive generation, contextual
"ask AI" actions, a command palette) rather than a single "generate and wait" step.

Scope principle — **rebuild the UI, reuse the engine**:
- **Rebuild:** design system, global navigation/app shell, and all interactive screens.
- **Reuse untouched:** the backend API, the API client layer (`lib/api/*`), the resume
  template render components (`components/resume/*`), and the `/print/*` pages that drive
  PDF export.
- **Build UI-only now, wire later:** authentication screens and the entire `/admin`
  dashboard are built as UI (with mock/stub data) in this revamp; their backend arrives
  in a later phase.

This is a frontend-only effort. No backend feature behavior changes.

## Glossary

- **Engine:** The parts kept unchanged — backend API, API client (`lib/api/*`), resume
  template render components (`components/resume/*`), and the `/print/*` PDF pages.
- **App shell:** The persistent authenticated layout wrapping app screens (desktop sidebar
  / mobile bottom navigation + header).
- **Resume:** A resume document — one **master** plus **tailored** variants.
- **Application:** A first-class object = one job pursuit, bundling the job description, its
  tailored resume, cover letter, interview prep, outreach, ATS score, and pipeline status.
- **Home:** The proactive workspace (next actions, pipeline snapshot, resume health,
  activity) — replaces the passive "dashboard".
- **Resume Editor:** The surface for editing a resume document (content, template, preview,
  export, AI enrichment) — replaces the monolithic "builder"; reached from a resume, not a
  top-level nav item.
- **Application Workspace:** The per-application surface (tailored resume + cover letter +
  interview prep + outreach + score + status) — where per-job deliverables live.
- **Tailor flow:** The central AI-native action that turns a resume + job description into
  an Application; not a navigation destination.
- **Analysis (pre-generation):** Instant AI reading of a pasted JD (keywords, role/
  seniority, present vs missing skills, fit snapshot) shown before generating.
- **Command palette:** A ⌘K launcher combining navigation and contextual AI commands.
- **Contextual AI:** Inline "ask AI to change this" actions on bullets/sections (rewrite,
  shorten, quantify, adjust seniority) — not a global chatbot.
- **Diff preview:** The before/after view of AI changes the user accepts or discards.
- **Truthfulness guard:** The rule that AI output must derive from the master resume (no
  fabricated experience), surfaced to the user.
- **Design language:** FitWright's named design philosophy (see Requirement 30).
- **UI-only:** Screens built now with mock/stub data; backend wired in a later phase.
- **Future backend:** A frontend capability designed now but dependent on backend work not
  yet built; explicitly marked so it can be wired later.
- **Parity checklist:** The list of features a migrated screen must retain.

## Requirements

## Requirement 1 — Design System & Theming

**User Story:** As a user, I want a warm, modern, consistent look with light and dark
modes, so that the app feels professional and comfortable to use for long sessions.

#### Acceptance Criteria
1. THE system SHALL define a design-token set (colors, typography, spacing, radius,
   shadows) implementing a warm/friendly aesthetic (rounded corners, soft neutral
   palette, generous whitespace) inspired by Notion/Tally/Cal.com.
2. THE system SHALL support a light theme (default) and a dark theme.
3. WHEN a user toggles the theme THEN the system SHALL persist the choice and apply it
   across all pages without a full reload.
4. WHEN no explicit choice exists THEN the system SHALL default to light.
5. THE system SHALL use a single accent color for primary actions and neutral grays
   elsewhere, with at most one primary action emphasized per screen region.
6. THE system SHALL introduce a new design-token set that **coexists** with the existing
   styles and SHALL NOT delete existing CSS/tokens; resume-template (`components/resume/*`)
   and `/print/*` styling SHALL be scoped so it remains visually unchanged.
7. THE system SHALL meet WCAG 2.2 AA contrast (≥4.5:1 for text) in both themes.
8. THE token set SHALL define **explicit values** for colors, a spacing scale, a type
   scale, radii, elevation/shadows, and motion (durations + easing).
9. THE system SHALL apply the persisted theme **before first paint** (no flash of incorrect
   theme / no hydration mismatch).

## Requirement 2 — Component Library & UI Kit

**User Story:** As a developer, I want a reusable component kit, so that all screens are
consistent, accessible, and fast to build.

#### Acceptance Criteria
1. THE system SHALL adopt shadcn/ui (Radix + Tailwind) as the base component library,
   themed to the warm design tokens.
2. THE UI kit SHALL provide at minimum: Button, Input, Textarea, Select, Dialog/Modal,
   Dropdown, Tabs, Card, Toast/Notification, Tooltip, Badge, Avatar, Skeleton, Progress,
   Switch/Toggle, Table, and Sidebar/Nav primitives.
3. ALL interactive components SHALL be keyboard-navigable with visible focus states.
4. THE system SHALL provide standard loading (skeleton), empty, and error states as
   reusable patterns.

## Requirement 3 — Navigation & Information Architecture (workflow-first)

**User Story:** As a user, I want navigation organized around my job-search workflow, so
that I always know what to do next without learning the app's internal structure.

#### Acceptance Criteria
1. THE authenticated app SHALL present exactly **three primary destinations** — **Home**,
   **Resumes**, **Applications** — plus Settings and an account menu (Settings is secondary,
   not a primary peer).
2. "Tailor to a job" SHALL be presented as the **central primary action** (not a
   navigation destination), reachable from anywhere (app shell + command palette).
3. Resume editing SHALL be reached from a specific resume (Resume Editor), and per-job
   deliverables SHALL be reached from a specific Application (Application Workspace) — NEITHER
   SHALL be a top-level navigation item.
4. THE navigation SHALL indicate the active destination and the user's place in a workflow.
5. THE public (marketing/auth) pages SHALL use a simple top bar; the authenticated app
   SHALL use a left sidebar on desktop and **bottom navigation on mobile** (see Req 28).
6. THE app MAY provide a **command palette (⌘K)** as a **power-user enhancement** for
   navigation, global search (Req 32), and contextual AI commands. It SHALL NOT be a
   required interaction model — **every action SHALL be reachable through the visible UI**
   without it.
7. THE IA SHALL NOT require the user to understand internal feature boundaries (no separate
   "Tailor" vs "Builder" vs "Tracker" mental model); the flow SHALL read as resume → tailor
   → apply → track.

## Requirement 4 — Landing Page

**User Story:** As a visitor, I want a single informative landing page, so that I
understand what FitWright does and can start quickly.

#### Acceptance Criteria
1. THE landing page SHALL be a single scrollable page containing: hero (name + tagline
   "Built to fit" + primary CTA), what-it-does summary, key features, a how-it-works
   (3–4 steps) section, a small "about the developer" strip, and a footer.
2. THE hero SHALL present a clear primary CTA ("Get Started" / "Launch App") and a
   secondary link (GitHub).
3. THE landing page SHALL be fully responsive (mobile → desktop).
4. THE landing page SHALL support light and dark themes.
5. THE footer SHALL link to Privacy, Terms, GitHub, and the developer's site/LinkedIn.

## Requirement 5 — Authentication UI (UI-only in this phase)

**User Story:** As a user, I want simple login/signup screens, so that I can access my
account (backend wired in a later phase).

#### Acceptance Criteria
1. THE system SHALL provide Login and Signup pages using a centered-card layout.
2. EACH auth page SHALL offer "Continue with Google" and an email field, with a link to
   switch between login and signup.
3. THE auth pages SHALL show validation and error states.
4. IN this phase the auth actions MAY use stubbed handlers; the UI SHALL be complete and
   theme-aware.
5. THE system SHALL provide Privacy Policy and Terms pages (static content).

## Requirement 6 — First-Run Onboarding

**User Story:** As a new user, I want to know what to do first, so that I reach a result
without confusion.

#### Acceptance Criteria
1. WHEN a user has no resumes THEN Home SHALL show a guided empty state with two
   clear choices: "Upload a resume" and "Build with the wizard".
2. WHEN the AI provider/API key is not configured THEN the system SHALL surface a
   non-blocking prompt to configure it in Settings before tailoring.
3. THE onboarding guidance SHALL be dismissible and not reappear once a resume exists.

## Requirement 7 — Home (Lightweight Launchpad)

**User Story:** As a user, I want Home to be a fast launchpad — not a dense dashboard — so
that I can immediately continue my work or start the next task.

Home is a **launchpad**, not a metrics dashboard (like the entry surface of Notion/Linear/
Cursor). It presents a short, prioritized hierarchy and defers everything else.

#### Acceptance Criteria
1. THE Home SHALL present, in priority order and nothing more by default:
   (a) the primary **"Tailor to a job"** action, (b) **"Continue where you left off"**
   (the most recent in-progress resume/application/tailoring), (c) **"Needs attention"**
   (a short list: failed processing, missing AI config, and — future backend — follow-ups
   due / interviews soon), (d) **recent resumes & applications**.
2. Home SHALL NOT duplicate destination content: full pipeline analytics live in
   Applications and resume health lives contextually in the Resume Editor; Home only links
   to them.
3. Secondary information (full activity feed, deeper stats) SHALL be hidden by default and
   revealed only on demand (progressive disclosure).
4. WHEN the user has no resumes THEN Home SHALL show the guided first-run state (Req 6).
5. Home SHALL NOT make any AI call the user did not initiate; lightweight health hints must
   be computed locally or shown only from already-available data.

## Requirement 8 — Resume Library & Import

**User Story:** As a user, I want one place to manage my resumes and add new ones, so that
I control my material before tailoring.

#### Acceptance Criteria
1. THE **Resumes** destination SHALL list the user's resumes (master + tailored) with title,
   status (processing/ready/failed), and created date, and SHALL clearly mark the **master**.
2. EACH resume SHALL offer actions: open in Resume Editor, tailor, export, delete; WHEN a
   resume is processing/failed THEN status SHALL be shown with a retry action for failures.
3. THE system SHALL provide an upload control accepting PDF/DOC/DOCX via click and
   drag-and-drop, validating by MIME type OR file extension, with a clear inline error for
   unsupported files.
4. WHILE a file is uploading/parsing THEN the system SHALL show progress and status; WHEN
   parsing fails THEN the system SHALL explain the likely cause (e.g., scanned/image PDF)
   and offer retry.
5. THE system SHALL provide the guided resume wizard (question-by-question) with a live
   preview of the resume being built.

## Requirement 9 — Tailor Flow (AI-Native Core)

**User Story:** As a user, I want the app to understand the job and guide me before it
generates, so that tailoring feels intelligent and I trust the result — and I reach a
tailored resume + cover letter fast.

The flow is driven by an **internal** state machine
(`start → analyzing → ready → generating → reviewing → saved`), but this is an
implementation detail. THE **visible experience SHALL feel like one continuous surface**,
not a multi-step wizard exposing execution stages (no "Step 1 of 5"): the source + JD sit at
the top, analysis and results appear inline in place as they become ready.

#### Acceptance Criteria
1. THE flow SHALL start by letting the user pick/confirm a source resume and provide a job
   description (paste; **JD-fetch-by-URL** is a future backend capability).
2. WHEN a job description is provided THEN the system SHALL run an **instant analysis pass**
   (reusing keyword extraction) and present a **summary first** — e.g. "Backend Engineer ·
   18 keywords · 7 missing skills · looks good" with a single **Generate** action.
3. THE analysis detail (full keyword list, skill gaps, fit breakdown) SHALL be **collapsed
   by default behind "Expand details"** (progressive disclosure) so the default experience
   stays simple while experts can drill in.
4. WHEN the user generates THEN the system SHALL present results **progressively/in stages**
   (e.g., keyword coverage → section rewrites → ATS score) using the natural pipeline stages,
   so useful work is continuously visible; **token-level streaming** is a future backend
   enhancement.
5. THE result SHALL show the ATS/match score with a readable breakdown, keyword highlighting
   (matched/missing), and a diff the user can accept or discard before saving.
6. THE flow SHALL let the user select the tailoring style/prompt and output language
   (progressive disclosure — collapsed by default).
7. SAVING the result SHALL create (or update) an **Application** and route the user to its
   Application Workspace; the user SHALL be able to reach a tailored resume **and** a
   generated cover letter in one continuous path without leaving the flow.
8. THE flow SHALL be **cancellable and recoverable**: cancel/abort a running generation,
   preserve the user's input across errors/timeouts/cold-starts, show a clear timeout
   message, and offer retry (no fake progress bars — progress reflects real stages).

## Requirement 10 — Resume Editor (resume-document concerns only)

**User Story:** As a user, I want to edit my resume's content and appearance in one focused
place, so that editing is not cluttered with unrelated per-job deliverables.

The Resume Editor replaces the monolithic "builder" and is scoped to the **resume document
only**. Per-job deliverables (cover letter, interview prep, outreach) move to the
Application Workspace (Req 17); this resolves the god-page problem while keeping editing
smooth.

#### Acceptance Criteria
1. THE Resume Editor SHALL be a **content-first single surface** (not a set of builder
   tabs): content editing with an **always-visible live preview**. It SHALL NOT host cover
   letter, interview prep, or outreach.
2. THE Content area SHALL allow editing all sections with rich-text editing, adding/removing
   custom sections, and drag-and-drop reordering of sections and list items.
3. Template & appearance controls (template choice, font, accent, contact icons, compact)
   SHALL live in a **contextual inspector panel** (a side/floating panel toggled on demand),
   not a dedicated tab; **Export** SHALL be an **action** (button), not a section.
4. AI SHALL be **contextual/inline**, not a separate section: enrichment (analyze, enhance a
   bullet, regenerate an item or skills) and **"ask AI"** instructions appear on the
   relevant bullet/section, each with preview-before-apply (Req 27).
5. CHANGES SHALL be saved with clear saved/dirty status feedback and autosave and/or an
   unsaved-changes navigation guard to prevent data loss.
6. THE Resume Editor SHALL be reachable from a resume (in the Resumes library) and from an
   Application (to edit that application's tailored resume), returning the user to where they
   came from.

## Requirement 11 — Templates & Customization

**User Story:** As a user, I want to choose and customize a resume template, so that it
matches my style.

#### Acceptance Criteria
1. THE system SHALL present all resume templates in a visual picker with previews
   (classic/modern single & two column, LaTeX, clean, vivid).
2. THE system SHALL allow customizing font, accent color, contact-icon visibility, and
   compact mode, with the live preview updating on change.
3. THE selected template and settings SHALL persist per resume.
4. THE preview SHALL visually match the exported PDF.
5. THE resume preview and PDF export SHALL always render in **light mode regardless of the
   app theme**, so the preview matches the printed output.

## Requirement 12 — Cover Letter (Application deliverable)

**User Story:** As a user, I want to generate and edit a cover letter for a specific job, so
that I can send a tailored application.

Cover letters are **per-application** and live in the Application Workspace (Req 17), not in
the Resume Editor.

#### Acceptance Criteria
1. THE system SHALL generate a cover letter grounded in the resume and job description.
2. THE system SHALL provide a rich-text editor with a live preview.
3. THE system SHALL allow choosing output language and (where supported) tone/style.
4. THE system SHALL export the cover letter to PDF.

## Requirement 13 — Interview Preparation (Application deliverable)

**User Story:** As a user, I want resume-grounded interview prep for a specific job, so that
I can prepare for that interview.

Interview prep is **per-application** and lives in the Application Workspace (Req 17).

#### Acceptance Criteria
1. THE system SHALL generate structured interview prep (role fit, resume questions,
   project follow-ups, skill gaps, talking points) for a tailored resume.
2. THE system SHALL let the user generate prep on demand and, where configured, enable
   automatic generation.
3. THE prep SHALL be displayed in a readable, scannable layout.

## Requirement 14 — Outreach / Recruiter Message (Application deliverable)

**User Story:** As a user, I want a cold outreach message for a specific job, so that I can
contact recruiters.

Outreach is **per-application** and lives in the Application Workspace (Req 17).

#### Acceptance Criteria
1. THE system SHALL generate an outreach/recruiter message grounded in the resume and job.
2. THE system SHALL provide an editor and a preview, with copy-to-clipboard.
3. THE system SHALL allow output-language selection.

## Requirement 15 — Scoring, Keywords & Diff

**User Story:** As a user, I want to see how well my resume fits and what changed, so that
I trust the tailoring.

#### Acceptance Criteria
1. THE system SHALL display an ATS/match score with a readable breakdown.
2. THE system SHALL highlight job-description keywords within the resume and list missing
   keywords.
3. THE system SHALL present a clear before/after diff of AI changes and let the user
   accept or discard before saving.
4. THE diff SHALL never claim to add content not derived from the master resume
   (truthfulness guard surfaced to the user).
5. THE system SHALL present a **change summary** of an AI edit (e.g., "14 bullets modified —
   accept individually or all"), with per-change accept/discard, so the user always knows
   exactly what changed.
6. THE UI SHALL provide a **visual distinction between AI-generated/AI-modified content and
   user-authored content** (a consistent, subtle marker), removable once the user accepts/
   edits. Per-change **confidence indicators** are a **future backend** signal, shown only
   when available.

## Requirement 16 — Export

**User Story:** As a user, I want to export polished PDFs, so that I can submit them.

#### Acceptance Criteria
1. THE system SHALL export the tailored resume and cover letter to PDF.
2. THE export SHALL reuse the existing `/print/*` render pipeline unchanged.
3. WHILE exporting THEN the system SHALL show progress; ON failure it SHALL show a clear
   error.
4. A visual/text-content regression check SHALL confirm the exported PDF matches the
   pre-revamp output for the same data and template (equivalence, not byte-identity).

## Requirement 17 — Applications: Pipeline & Workspace (workflow spine)

**User Story:** As a user, I want my applications to be the continuation of tailoring — not a
separate island — so that resume → tailor → apply → track → interview → offer is one
connected workflow.

The **Application** is a first-class object bundling the job, its tailored resume, cover
letter, interview prep, outreach, ATS score, and status. This integrates the former
"tracker" into the core workflow.

#### Acceptance Criteria
1. THE **Applications** destination SHALL present the pipeline as a Kanban board with
   drag-and-drop across the **full lifecycle**: Tailoring → Applied → Interviewing → Offer,
   plus terminal states **Rejected**, **Accepted**, **Withdrawn**, and **Archived**, with an
   alternative list view.
2. WHEN a resume is tailored (Req 9) THEN the system SHALL create the corresponding
   Application and place it in the pipeline automatically.
3. THE pipeline SHALL support manual add, bulk actions, per-card info (company, role,
   status, dates), and **archive** to keep the active board uncluttered while preserving a
   historical archive.
4. OPENING an application SHALL open its **Application Workspace** as an **overview plus
   resource sections** (not one long page): an Overview (tailored resume preview + ATS score
   + status + notes) and separate sections for **Cover Letter (Req 12)**, **Interview Prep
   (Req 13)**, and **Outreach (Req 14)** — reached by in-workspace tabs/segments to keep
   each surface focused without heavy navigation.
5. THE workspace SHALL let the user move the application through stages, edit the tailored
   resume (→ Resume Editor, returning here), and generate any missing deliverable on demand.
6. THE workspace SHALL support **Duplicate application** (reuse this pursuit for a similar
   role) and **Reuse resume** (start a new application from this tailored resume), so users
   don't redo work.
7. Follow-up reminders and interview scheduling SHALL be designed in the workspace but are
   **future backend** (shown when the data exists).

## Requirement 18 — Settings

**User Story:** As a user, I want organized settings, so that I can configure AI, profile,
and preferences.

#### Acceptance Criteria
1. THE Settings area SHALL be organized into sections: Profile, AI Providers & API Key,
   Preferences, and Account.
2. THE AI Providers section SHALL support multiple providers, entering a bring-your-own
   API key (stored encrypted), and a test-connection check with clear result feedback.
3. THE Preferences section SHALL include UI language, content language, theme, and feature
   toggles, plus custom prompts (tailoring style, cover letter, outreach).
4. THE Profile section SHALL show name and avatar (avatar upload wired in a later phase).
5. THE Account section SHALL include logout and destructive actions (reset/delete) behind
   explicit confirmation.

## Requirement 19 — Internationalization

**User Story:** As a global user, I want the UI and generated content in my language, so
that the app is usable for me.

#### Acceptance Criteria
1. THE UI SHALL remain fully internationalized using the existing message-catalog system.
2. ALL new screens SHALL use translation keys (no hardcoded user-facing strings) and
   maintain locale parity across supported languages.
3. THE system SHALL allow selecting the content-generation language.
4. THE system SHALL scope translation surfaces: **core app screens SHALL be fully
   translated**; admin and legal pages MAY ship English-first. Locale parity SHALL stay
   green for the translated surfaces.
5. THE system SHALL resolve the existing `fr.json` locale-parity gap (complete the missing
   keys or drop French) so the parity check passes.

## Requirement 20 — Admin Dashboard (UI-only in this phase)

**User Story:** As the admin, I want a full-control dashboard at `/admin`, so that I can
monitor users and usage (backend wired in a later phase).

#### Acceptance Criteria
1. THE system SHALL provide an `/admin` area with its own layout, separate from the user
   app.
2. THE admin overview SHALL show stat cards (total users, active users, resumes tailored,
   sign-ups over time) and usage charts, using mock/stub data in this phase.
3. THE admin SHALL provide a Users view: a searchable table listing users with detail view
   and enable/disable/delete actions (stubbed).
4. THE admin SHALL provide an Analytics view with time-series charts (stubbed).
5. ALL admin screens SHALL be theme-aware and built with the same UI kit.
6. THE admin data layer SHALL be abstracted so real APIs can replace mock data without UI
   changes.
7. WHEN the admin backend is wired THEN access SHALL be enforced **server-side by role on
   every admin endpoint**; hiding the admin UI SHALL NOT be treated as access control.

## Requirement 21 — Responsiveness & Accessibility

**User Story:** As any user, I want the app to work on my device and with assistive tech,
so that it is usable and inclusive.

#### Acceptance Criteria
1. ALL pages SHALL be responsive across mobile, tablet, and desktop breakpoints, with
   mobile designed as its own intentional experience (Req 28), not an auto-stacked desktop.
2. THE app SHALL support full keyboard navigation with visible focus indicators.
3. INTERACTIVE elements SHALL have appropriate ARIA roles/labels and semantic landmarks.
4. THE app SHALL target WCAG 2.2 AA (contrast, focus, labels) in both themes.
5. THE app SHALL honor `prefers-reduced-motion` and provide a skip-to-content link.
6. THE app SHALL announce async results (e.g., "tailoring complete", errors) via ARIA
   live regions.

## Requirement 22 — Migration Safety (Preserve the Engine)

**User Story:** As the owner, I want the revamp to not break existing functionality, so
that the app keeps working throughout.

#### Acceptance Criteria
1. THE revamp SHALL NOT change the backend API or its contracts.
2. THE revamp SHALL reuse the existing API client layer, resume-render components, and
   `/print/*` pages without altering the exported PDF output.
3. THE migration SHALL proceed screen-by-screen; each migrated screen SHALL retain full
   feature parity with the screen it replaces (verified against a parity checklist).
4. EXISTING frontend tests SHALL continue to pass, and locale parity SHALL be maintained.
5. NO user-facing feature from the current app SHALL be dropped without explicit approval.

## Requirement 23 — Security & Access Control (designed now, enforced when wired)

**User Story:** As the owner, I want the security model designed up front, so that auth and
admin can't be built insecurely.

#### Acceptance Criteria
1. THE system SHALL define session handling using **httpOnly, SameSite cookies** (not
   `localStorage`) so tokens are not exposible via XSS.
2. THE system SHALL define **route protection** (middleware/guards) for the authenticated
   app and `/admin`, so unauthenticated/unauthorized users are redirected.
3. THE `/admin` area SHALL require an **admin role**, enforced server-side when wired;
   client-side hiding SHALL NOT be the security boundary.
4. THE BYO API key field SHALL be **write-only/masked**; the decrypted key SHALL never be
   returned to the browser, and keys SHALL be stored encrypted per user.
5. THE system SHALL define the Google OAuth flow (redirect + callback + state param) and
   CSRF protection for state-changing requests.
6. THE system SHALL define account-data handling: account-deletion cascade, data
   minimization for admin views, and the content for Privacy/Terms pages.
7. THE UI SHALL never echo raw backend error text to users (avoid leaking internals).

## Requirement 24 — Frontend Architecture, Data Layer & Performance

**User Story:** As a developer, I want a clear, production-grade code structure and data
layer, so that the app is maintainable and fast.

#### Acceptance Criteria
1. THE project SHALL adopt a documented **folder/code architecture** (feature-based
   organization, naming conventions, where components/hooks/types/api live) and all new
   code SHALL follow it.
2. THE authenticated route group and its middleware boundary SHALL be explicitly named and
   documented (no ambiguous route grouping).
3. THE app SHALL use a **single data-fetching layer** (one library, e.g. TanStack Query)
   for loading/error/caching/refetch, applied consistently across screens.
4. THE app SHALL define shared state via typed providers/contexts (theme, session, toasts,
   and tailor-flow state) — no ad-hoc prop drilling for cross-screen state.
5. THE app SHALL preserve performance discipline: a per-route **First-Load JS budget
   (≤250KB)**, no barrel imports from `lucide-react`, and **lazy-loading** of heavy
   components (TipTap, dnd-kit, resume preview, charts).
6. NEW dependencies SHALL be verified for compatibility with Next 16 / React 19 / Tailwind
   v4 before adoption (shadcn/ui, theme lib, charts lib).

## Requirement 25 — Delivery Phasing (MVP cut line)

**User Story:** As the owner, I want the work phased, so that a usable app ships before the
big multi-user build.

#### Acceptance Criteria
1. THE plan SHALL define **Phase 1 (local, single-user)**: foundation + core screens
   (Landing, Home, Resumes + Resume Editor, Tailor, Applications, Settings) working on the
   existing SQLite backend — no auth required.
2. THE plan SHALL define **Phase 2 (hosted, multi-user)**: auth, admin, and the security
   model wired to a real backend.
3. Auth and admin screens built in Phase 1 SHALL be **UI-only** and clearly isolated so
   Phase 1 remains shippable without them.
4. THE core Tailor flow SHALL be prioritized and shippable before admin/auth work.

## Requirement 26 — Analytics & Telemetry (for the admin dashboard)

**User Story:** As the admin, I want real usage data to display, so that the admin
dashboard is meaningful once wired.

#### Acceptance Criteria
1. THE system SHALL define an **event/metric schema** (e.g., signups, active users,
   resumes tailored, cover letters generated) that the admin dashboards consume.
2. THE admin dashboards SHALL read from a typed data interface that returns mock data now
   and real telemetry later, with no UI change on swap.
3. Telemetry capture SHALL be privacy-respecting (no sensitive resume content in events).

## Requirement 27 — AI-Native Interactions

**User Story:** As a user of an AI product, I want AI woven throughout the interface, so
that guidance and edits feel intelligent and low-friction — without a gimmicky chatbot.

#### Acceptance Criteria
1. THE system SHALL provide **contextual "ask AI" actions** on a bullet or section
   (rewrite, shorten, quantify, adjust seniority, fix tone), reusing the existing
   regenerate-with-instruction capability, with a preview before applying.
2. THE system SHALL provide an **"explain" affordance** for AI outputs (e.g., "explain this
   ATS score", "why was this changed") to build trust.
3. THE system SHALL provide a **command palette (⌘K)** combining navigation and
   natural-language AI commands (e.g., "tailor for <company>", "shorten summary").
4. THE system SHALL **not** implement a global free-form chatbot; conversational input is
   scoped to targeted, context-bound actions that reduce friction.
5. BECAUSE users bring their own API key and each AI action has cost, THE system SHALL be
   **cost-aware**: never trigger an AI call the user did not initiate, indicate when an
   action will call the AI, and avoid redundant duplicate calls (reuse cached analysis).
6. AI suggestions SHALL respect the truthfulness guard (Req 15.4) — never fabricating
   experience.

## Requirement 28 — Multi-Device Experience (desktop / tablet / mobile)

**User Story:** As a user on any device, I want an interface fitted to that device's
capabilities, so that each experience is intentional — not a squeezed desktop.

#### Acceptance Criteria
1. **Desktop** SHALL use the sidebar shell with multi-pane surfaces (e.g., editor + preview,
   pipeline board) and hover/keyboard affordances.
2. **Mobile** SHALL use **bottom navigation** (Home · Resumes · **Tailor** center ·
   Applications, thumb-reachable), touch-first targets, **gestures** (e.g., swipe an
   application between stages), and sheets/drawers instead of hover menus; the mobile tailor
   flow is a focused single-column experience; deep resume WYSIWYG editing is
   **desktop-first**, with a streamlined mobile section-edit + review mode.
3. **Tablet** SHALL adapt the desktop layout with touch affordances (larger targets, sheets)
   and may collapse multi-pane surfaces to one pane with a toggle — a defined adaptation, not
   an afterthought and not a full third redesign.
4. EACH device SHALL be **intentionally laid out** (not one auto-stacked layout); the same
   capabilities SHALL remain reachable on all three (deep editing may recommend desktop).

## Requirement 29 — Design Language (FitWright's own philosophy)

**User Story:** As the owner, I want a defined design language, so that the product has a
consistent, distinctive identity rather than an imitation of other apps.

#### Acceptance Criteria
1. THE system SHALL define and document a **named design language** covering: visual
   personality, interaction philosophy, motion/animation principles, spacing philosophy,
   information density, empty-state philosophy, loading philosophy, AI-interaction
   philosophy, accessibility philosophy, consistency rules, and responsive principles.
2. ALL screens and components SHALL adhere to the documented design language.
3. THE design language SHALL be **inspired by but distinct from** Notion/Tally/Cal.com — a
   recognizable FitWright identity, not a clone.
4. THE design language SHALL define how AI presence is expressed visually and behaviorally
   (a consistent "AI voice" for suggestions, generation, and explanations).

## Requirement 30 — Resilience & Recovery

**User Story:** As a user of productivity software, I want the app to survive interruptions
without losing my work, so that I can trust it with real applications.

#### Acceptance Criteria
1. WHEN a network/API error occurs THEN the system SHALL show a friendly, actionable state
   (retry where safe) and SHALL NOT lose the user's in-progress input.
2. WHEN the browser is refreshed or crashes mid-edit THEN the system SHALL **restore
   unsaved drafts** from local persistence for the Resume Editor and the Tailor flow.
3. WHEN an AI generation is cancelled or fails THEN the system SHALL return to the prior
   stable state with input intact and offer retry (no partial corrupt save).
4. WHEN autosave conflicts (local draft vs newer server state) THEN the system SHALL detect
   the conflict and let the user choose (keep mine / take latest) rather than silently
   overwriting.
5. WHEN offline THEN the system SHALL clearly indicate offline status and disable actions
   that require the network, while preserving local edits; **full offline editing (service
   worker/PWA) is future backend/optional**.

## Requirement 31 — Version History (design now, backend later)

**User Story:** As a user whose resume is edited by AI, I want to see and restore previous
versions, so that I can experiment without fear of losing good content.

#### Acceptance Criteria
1. THE UI SHALL support **restoring the original parsed resume** and **undoing the last AI
   generation** using data available today (original markdown + diff).
2. THE UI SHALL be designed to **list versions, compare two versions, and restore a
   version**; persistent per-edit **snapshots and branching are future backend** (the UI
   reads from a typed history interface that returns available data now, full history later).
3. RESTORING or comparing SHALL never fabricate content and SHALL be clearly non-destructive
   (restore creates a new current state; it does not erase history).

## Requirement 32 — Search & Filtering

**User Story:** As my resumes and applications accumulate, I want to find things fast, so
that the product stays usable at scale.

#### Acceptance Criteria
1. EACH list (Resumes, Applications) SHALL provide **quick filters** (e.g., status/stage)
   and sort, working on already-loaded data.
2. THE app SHALL provide a **global search** entry (surfaced via the command palette and a
   visible search affordance) across resumes, applications, and job descriptions; it SHALL
   read from a typed search interface (client-side now; **server-side search is future
   backend** for scale).
3. THE search architecture SHALL reserve support for **recent** and, as **future backend**,
   favorites / pinned / saved searches — designed as a typed interface, not built now.

## Requirement 33 — Notifications

**User Story:** As a user, I want to be informed of important events, so that I don't miss
time-sensitive actions.

#### Acceptance Criteria
1. THE system SHALL distinguish **transient** notifications (toasts: export finished, AI
   generation failed, resume parsing complete) — deliverable now client-side — from
   **persistent/scheduled** notifications (interview tomorrow, API key expired, follow-up
   due) which are **future backend**.
2. THE system SHALL provide a **notification-center surface** (a typed list read from a
   notifications interface returning transient/local items now and server items later).
3. NOTIFICATIONS SHALL be non-intrusive, dismissible, and never leak sensitive resume
   content.

## Requirement 34 — Universal Object Model

**User Story:** As the owner, I want a single formal object graph, so that every screen,
route, and (future) API operates consistently and the product scales coherently.

#### Acceptance Criteria
1. THE system SHALL define and document a canonical object graph:
   `Master Resume → Tailored Resume → Application → { Cover Letter, Interview Prep,
   Outreach }`, with Job Description attached to the Application and versions attached to a
   Resume.
2. ALL routes, navigation, data hooks, and types SHALL be organized around this graph (no
   ad-hoc objects that don't fit it).
3. THE graph SHALL be expressed as **typed models/relationships** in the frontend so the
   future backend can map onto it without UI restructuring.
4. NEW capabilities (search, notifications, version history, lifecycle states) SHALL attach
   to nodes of this graph rather than introducing parallel structures.
