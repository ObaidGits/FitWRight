# Requirements Document

_P3 Productivity Features — version history, notifications, global search,
JD-from-URL, reminders, interview scheduling, an agenda, avatar & profile, on a
shared events/scheduler/notification platform._

## Introduction

P3 turns FitWright from a set of tools into a productivity platform: users find
anything instantly, never lose a resume version, never miss a follow-up or
interview, and import job descriptions in one click. It delivers the backends +
wiring for the existing frontend stubs (`lib/api/{history,notifications,search}`,
`VersionHistoryPanel`, `NotificationCenter`, command-palette search) plus new
capabilities, built on **shared platform services** (a domain-event outbox, a
scheduler with claim semantics, and a notification service) so features stay
decoupled and reliable.

Every table and endpoint is **user-scoped** (P1 ADR-4) and auth-guarded. AI is
used sparingly, explicitly, and cost-aware — never auto-firing. Inherits
`../phase-2-roadmap.md`; depends on `../auth-foundation/`.

Feature areas: (A) Version history, (B) Notifications, (C) Global search,
(D) JD-from-URL, (E) Reminders, (F) Interviews, (G) Agenda, (H) Avatar & profile,
plus shared platform services and cross-cutting guarantees.

### Non-goals (deferred, extension points reserved)
- Real-time collaborative/branching version graphs (linear snapshots only).
- Two-way calendar sync (ICS export + one-way now; provider sync later).
- Web/native push (in-app + email now; push behind the same NotificationService).
- Semantic/embedding search (keyword FTS now, behind a `SearchIndex` port so
  embeddings drop in later).

## Glossary
- **Snapshot**: an immutable, compressed copy of a resume's processed data at a
  point in time; identical consecutive states are de-duplicated by content hash.
- **Domain event / outbox**: a write emits an event to an `outbox` table; async
  consumers (indexer, notifier) process it — decoupling producers from consumers.
- **SchedulerService**: scans due time-based rows, **claims** them (status
  transition) to prevent double-fire, and emits events.
- **NotificationService**: the single writer of notifications (in-app + optional
  email), enforcing dedupe, priority, category, grouping, and preferences.
- **Node ref**: a `(type,id)` pointer a notification/search result deep-links to;
  never embeds sensitive content.
- **SSRF**: server-side request forgery — the JD-from-URL threat class.
- **FTS**: full-text search index (SQLite FTS5 / Postgres tsvector).
- **Idempotency-key**: a client-supplied key that de-duplicates create requests.

---

## Requirements

### Requirement 1: Version snapshots — capture, dedupe & storage
**User Story:** As a user, I want an efficient history of my resume versions, so
I can roll back confidently without bloating storage.

#### Acceptance Criteria
1. THE SYSTEM SHALL capture immutable snapshots on meaningful changes: initial
   parse (`original`), each accepted AI generation (`ai`), and manual saves
   (`manual`) — each with `source`, optional label, `content_hash`, and the
   **gzip-compressed** processed_data, scoped to `(user_id, resume_id)`.
2. THE SYSTEM SHALL **skip** creating a snapshot when the new `content_hash`
   equals the latest snapshot's (no-op dedupe), and SHALL **debounce/coalesce**
   rapid manual saves (e.g. ≤1 manual snapshot per short window).
3. THE SYSTEM SHALL cap snapshots per resume (configurable, default 50), pruning
   the oldest non-`original` snapshots, and SHALL always retain the `original`.

### Requirement 2: Restore & undo — non-destructive
**User Story:** As a user, I want to restore or undo AI changes safely, so
experimenting never risks my work.

#### Acceptance Criteria
1. WHEN a user restores a snapshot, THE SYSTEM SHALL first snapshot the current
   state (restore is reversible), then apply the chosen snapshot atomically.
2. "Restore original" SHALL restore the retained `original`; "Undo last AI" SHALL
   restore the snapshot immediately preceding the last `ai` snapshot.
3. Restore SHALL return 404 on snapshots not owned by the user, and be safe
   under concurrent restores (last-writer via resource version; no corruption).

### Requirement 3: Version listing & compare
**User Story:** As a user, I want to browse and compare versions, so I can see
exactly what changed.

#### Acceptance Criteria
1. THE SYSTEM SHALL list snapshots **as metadata only** (id, source, label, size,
   created_at), paginated; the full (decompressed) data SHALL be fetched
   on-demand for view/compare (never loading all snapshots' data at once).
2. THE SYSTEM SHALL expose a field-level diff between any two owned snapshots
   (reusing the existing tailoring diff logic; no AI required).

### Requirement 4: Notification model
**User Story:** As a user, I want notifications organized and never overwhelming,
so I act on what matters.

#### Acceptance Criteria
1. THE SYSTEM SHALL persist user-scoped notifications with: `type`, `category`
   (system/reminder/interview/ai/security), `priority` (low/normal/high),
   `title`, `body` (content-safe — no resume/JD content or secrets), `node_ref`,
   `group_key?`, `read`, `created_at`, `dedupe_key?`.
2. THE SYSTEM SHALL maintain a **denormalized unread counter** per user
   (incremented on create, decremented on read) so unread count is O(1), never a
   COUNT scan.
3. THE SYSTEM SHALL support list (cursor, unread-first, filter by category), mark
   one/all read, dismiss one, and dismiss-group; grouped notifications collapse
   under `group_key`.

### Requirement 5: Notification generation via the event platform
**User Story:** As a user, I want to be reliably notified about time-sensitive
events, so I never miss anything.

#### Acceptance Criteria
1. Server events (parsing done/failed, AI done/failed, export ready, reminder
   due, interview upcoming, key invalid) SHALL be produced through the **event
   outbox**; the **NotificationService** is the sole writer of notifications.
2. Scheduled/derived notifications SHALL be idempotent per `dedupe_key`
   (`{type}:{node}:{bucket}`) and produced by the single-flighted scheduler +
   async consumers; duplicate delivery SHALL be impossible even across workers.
3. Notification creation SHALL respect per-user **preferences** (Requirement 6);
   failed email sends SHALL retry with backoff via a dead-letter path (in-app
   delivery always succeeds locally).

### Requirement 6: Notification preferences & delivery
**User Story:** As a user, I want to control which notifications I get and how,
so I'm not spammed.

#### Acceptance Criteria
1. THE SYSTEM SHALL let a user set per-category preferences (in-app on/off,
   email on/off) and an optional **daily/weekly email digest** that batches
   low/normal items.
2. Email notifications SHALL contain no resume/JD content — only a title + a
   deep link (content-safe), honoring preferences and digest batching.
3. The frontend SHALL show the unread count via the counter (poll 30–60s, or SSE
   behind a flag) without fetching content.

### Requirement 7: Global search — indexed via the outbox
**User Story:** As a user, I want to instantly search everything I own, so I
find any resume, application, or job in one place.

#### Acceptance Criteria
1. THE SYSTEM SHALL maintain a user-scoped `search_documents` store (title +
   content-safe body + node ref) populated **asynchronously from the event
   outbox** (never inline in the write path), with a full **rebuild** command
   and drift detection.
2. `GET /search` SHALL be scoped, ranked, cursor-paginated, and FTS-backed
   (SQLite FTS5 / Postgres FTS behind a `SearchIndex` port; embeddings are a
   future drop-in). Queries SHALL be parameterized and scoped **in SQL** (not via
   query text) so no crafted `q` can cross users.
3. The **node-type registry** SHALL be extensible (resume/application/jd now;
   cover letters/notes later) without API change.

### Requirement 8: Search UX
**User Story:** As a user, I want fast, keyboard-first search that remembers my
recent queries, so finding things is effortless.

#### Acceptance Criteria
1. Search SHALL support filters (node type, status, date) + sort, be debounced,
   keyboard-navigable (↑/↓/Enter), grouped by type, and deep-link results to the
   exact node/tab.
2. THE UI SHALL keep **recent searches** (local) and offer them; empty/no-match/
   loading/error states SHALL be explicit; offline SHALL fall back to the
   existing client-side `searchLocal`.

### Requirement 9: JD from URL — SSRF-hardened & cached
**User Story:** As a user, I want to paste a job link and get the description, so
I skip manual copy-paste.

#### Acceptance Criteria
1. WHEN a user submits a job URL, THE SYSTEM SHALL fetch it server-side, extract
   the JD text, and return `{ content, low_confidence, source_url }` for the
   tailor flow; low-confidence extraction SHALL be flagged for user verification
   before tailoring.
2. THE SYSTEM SHALL enforce SSRF protection: **http/https only**, **port
   allow-list (80/443)**, block private/loopback/link-local/CGNAT/metadata IPs,
   **resolve DNS once and connect to the pinned IP** (anti DNS-rebinding),
   **re-validate every redirect hop** (max 3), a streamed **byte cap** enforced
   during read (never trust Content-Length) with **decompression-bomb** limits,
   a wall-clock timeout, no auth headers forwarded, and no internal error/header
   leakage.
3. THE SYSTEM SHALL **cache** results by normalized-URL/content-hash to avoid
   re-fetch/re-bill, rate-limit per user + globally, cap concurrency, and be
   behind a `JD_FROM_URL` **kill-switch**. Optional LLM cleanup SHALL be bounded
   (token/time cap) and cached (Requirement 15).

### Requirement 10: Follow-up reminders
**User Story:** As a user, I want follow-up reminders with snooze and recurrence,
so I stay on top of applications.

#### Acceptance Criteria
1. THE SYSTEM SHALL let a user set/edit/cancel a reminder on an application
   (due datetime in **UTC** + IANA tz for display + note), user-scoped, with
   quick presets ("in 3 days", "next week").
2. THE SYSTEM SHALL support **snooze** (reschedule due) and **bounded recurrence**
   (a small RRULE subset — daily/weekly/custom-interval with an end); recurring
   reminders **materialize the next occurrence on fire** (no infinite rows).
3. THE scheduler SHALL scan due reminders, **claim** them (pending→firing) to
   prevent double-fire, emit an event → notification (idempotent), and set fired/
   next-occurrence; the count of reminders per user SHALL be bounded (abuse).
4. Home "Needs attention" + the Agenda (Requirement 12) SHALL surface due/overdue
   reminders; the workspace shows/edits them.

### Requirement 11: Interview scheduling
**User Story:** As a user, I want to schedule interviews with timezone-correct
reminders, rescheduling, and prep, so I'm always prepared.

#### Acceptance Criteria
1. THE SYSTEM SHALL record interviews on an application (start in **UTC** +
   IANA tz, duration, type, location/link, notes, configurable **lead-time**
   reminders e.g. 1 day + 1 hour), user-scoped, DST-correct.
2. THE SYSTEM SHALL support **reschedule/cancel** (re-arming reminders) and
   detect **overlapping** interviews (soft warning).
3. THE SYSTEM SHALL produce idempotent lead-time "upcoming" notifications, expose
   an **ICS** download (escaped, tz-correct VEVENT), and link to **interview-prep
   generation** (existing feature) as a preparation workflow.
4. Home + Agenda surface upcoming interviews, timezone-correct.

### Requirement 12: Upcoming / Agenda surface
**User Story:** As a user, I want one place that shows everything coming up, so I
don't hunt through applications.

#### Acceptance Criteria
1. THE SYSTEM SHALL provide an aggregated, time-ordered **Agenda** of upcoming
   reminders + interviews across all applications (paginated, indexed), with
   quick actions (open, snooze, reschedule, mark done).
2. The Agenda SHALL be reachable from Home and be keyboard/mobile friendly.

### Requirement 13: Avatar upload — hardened
**User Story:** As a user, I want a profile photo, so my account feels personal —
without introducing security risk.

#### Acceptance Criteria
1. THE SYSTEM SHALL accept an avatar, **sniff magic bytes** (not trust
   extension/MIME), allow only jpeg/png/webp (**no SVG**), enforce **byte and
   pixel-dimension caps** (image-bomb guard), **re-encode to a canonical format**
   server-side, **strip EXIF/GPS** metadata, store to S3-compatible storage with
   a **server-generated** path, and set `users.avatar_url` only after success.
2. Avatars SHALL be served via a signed/CDN URL with a bounded TTL; replaced/
   old avatars SHALL be **garbage-collected** (orphan cleanup job).

### Requirement 14: Extended profile
**User Story:** As a user, I want reusable profile details, so new resumes
prefill quickly.

#### Acceptance Criteria
1. THE SYSTEM SHALL persist optional, validated profile fields (headline,
   location, links) used to prefill resumes, user-scoped; URLs validated
   (scheme/host) and length-bounded.

### Requirement 15: AI productivity helpers — cost-guarded
**User Story:** As a user, I want AI to save me effort without surprise cost or
noise, so I trust it.

#### Acceptance Criteria
1. Any AI in P3 (e.g. JD-URL cleanup, optional version diff summary) SHALL be
   **explicit/opt-in**, **never auto-fired**, **bounded** (token/time caps),
   **cached** by content hash to avoid re-billing, and cost-aware (shows it uses
   the provider), consistent with the ui-revamp AI principles.

### Requirement 16: Shared platform services
**User Story:** As an engineer, I want reliable shared primitives, so features
stay decoupled and correct.

#### Acceptance Criteria
1. THE SYSTEM SHALL provide an **outbox** (transactional event write with the
   originating change) + at-least-once async consumers (indexer, notifier) that
   are **idempotent** by event id.
2. THE **SchedulerService** SHALL scan due rows in bounded batches, **claim**
   them atomically (prevent double-fire across workers), be single-flighted,
   resumable, and expose backlog metrics.
3. THE **NotificationService** SHALL be the single notification writer,
   enforcing dedupe, preferences, priority, grouping, and the unread counter.

### Requirement 17: Cross-cutting — scope, retention, idempotency, ops
**User Story:** As a user/operator, I want these features private, durable, and
operable at scale.

#### Acceptance Criteria
1. All endpoints user-scoped + auth-guarded; foreign/absent ids → 404; parent-
   ownership checked for reminders/interviews.
2. All lists cursor-paginated + indexed; inputs validated; outputs content-safe.
3. User-initiated creates (reminder/interview) SHALL accept an **idempotency-key**
   to prevent double-submit duplicates.
4. **Retention/archival** jobs SHALL prune read notifications and fired reminders
   older than N days and enforce snapshot caps; retention windows configurable.
5. Metrics/alerts SHALL cover search latency + indexer lag, scheduler backlog +
   double-fire=0, notification create/send + email DLQ, jd_fetch outcomes (SSRF
   probe signal), avatar upload outcomes; each feature is flag-gated with
   kill-switches for JD-fetch and email.

### Requirement 18: Frontend UX, accessibility & mobile
**User Story:** As a user, I want every productivity surface fast, accessible,
and great on mobile.

#### Acceptance Criteria
1. Every surface SHALL have explicit loading/empty/error/edge states; lists
   virtualized where long (notifications, search, agenda, versions).
2. Optimistic updates with rollback for mark-read/dismiss/snooze; deep links
   carry context (node + tab).
3. Keyboard-first: search + notifications + quick-reminder shortcuts; focus
   management; `aria-live` for async results; reduced-motion.
4. Mobile: notification center + agenda as sheets, search full-screen, touch-
   friendly date/tz pickers.

---

## Traceability
R1–R3 → Design §A. R4–R6 → §B. R7–R8 → §C. R9 → §D. R10 → §E. R11 → §F.
R12 → §G. R13–R14 → §H. R15 → §AI. R16 → §Platform. R17 → §Cross-cutting/Ops.
R18 → §Frontend. All → `tasks.md` waves.
