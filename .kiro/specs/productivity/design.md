# Design — P3 Productivity Features

## Overview

Inherits `../phase-2-roadmap.md`; depends on P1 (user-scoping, sessions,
KVStore, audit, jobs). Delivers version history, notifications, global search,
JD-from-URL, reminders, interviews, an agenda, and avatar/profile — built on
**shared platform services** (event outbox + async consumers, a claim-based
scheduler, a single NotificationService) so features are decoupled, reliable,
and scalable. All tables carry `user_id`; AI is opt-in, cached, and bounded.

## Architecture

```
Write path (resume/app/job/reminder/interview) ─┬─► domain change (txn)
                                                 └─► outbox row (same txn)
                                                        │  at-least-once, idempotent by event_id
                        ┌───────────────────────────────┼───────────────────────────────┐
                        ▼                                ▼                                ▼
                 SearchIndexer                    NotificationService              (future consumers)
             (FTS search_documents)          (dedupe, prefs, priority,
                                              grouping, unread counter,
                                              in-app + email via pluggable
                                              EmailSender w/ DLQ; free-tier
                                              provider default, ADR-14)
Scheduler (worker, single-flighted; `SCHEDULER_MODE` — external_cron free / internal premium, ADR-15):
   scan due reminders/interviews → CLAIM (pending→firing)
   → emit event → NotificationService; recurring reminders materialize next occurrence.
Retention worker: prune read notifications / fired reminders / over-cap snapshots.
Object storage (`StorageProvider` interface, `STORAGE_PROVIDER`; Cloudinary free default / S3 premium / Local dev — ADR-10): avatars via direct browser→Cloudinary signed upload (bypasses the sleeping backend) + URL transforms for resize/format; still validated (signed-params + post-upload verify/callback → sniff → re-encode → strip → signed serve).
```

Feature routers are thin; shared services (`outbox`, `scheduler`, `notifications`,
`search`, `storage`) are the reusable core. Every feature is flag-gated.

## Data Models

_(new tables, all user-scoped; timestamps UTC ISO)_
- **resume_versions**: `id, user_id, resume_id, source(original|ai|manual),
  label?, content_hash, data_gz (blob, gzip JSON), size_bytes, created_at`.
  Index `(user_id, resume_id, created_at)`; unique guard skips equal
  consecutive `content_hash`.
- **outbox**: `id, user_id?, event_type, payload(json), created_at,
  processed_at?, attempts`. Index `(processed_at, id)` for the consumer cursor.
- **search_documents**: `user_id, node_type, node_id, title, body,
  updated_at`, PK `(node_type, node_id)`, index `(user_id)`; FTS5 external-content
  (or Postgres `tsvector` column + GIN). Node-type registry in code.
- **notifications**: `id, user_id, type, category, priority, title, body,
  node_type?, node_id?, group_key?, read, created_at, dedupe_key?`. Indexes
  `(user_id, read, created_at)`, unique `(user_id, dedupe_key)`.
- **notification_prefs**: `user_id, category, in_app(bool), email(bool)`,
  PK `(user_id, category)`; plus a per-user `digest` setting.
- **user_unread_counts**: `user_id PK, unread int` (denormalized O(1) badge).
- **reminders**: `id, user_id, application_id, due_at(UTC), tz, note?,
  recurrence?(rrule-lite), status(pending|firing|fired|snoozed|cancelled),
  next_occurrence_at?, created_at`. Indexes `(status, due_at)` (scanner),
  `(user_id, application_id)`.
- **interviews**: `id, user_id, application_id, starts_at(UTC), tz, duration_min,
  kind, location?, notes?, lead_times(json e.g. [1440,60] min),
  status(scheduled|cancelled), created_at`. Indexes `(status, starts_at)`,
  `(user_id, starts_at)`.
- **avatar bookkeeping**: `users.avatar_url` + `users.avatar_key`; an
  `orphan_avatars` reclaim handled by the retention worker.
- **profile**: extend `users` with `headline?, location?, links(json)`.

Retention: read notifications + fired non-recurring reminders older than N days
pruned; snapshots capped per Requirement 1.3; outbox rows pruned after processed.

## Components and Interfaces

_(endpoints + frontend wiring; all `/api/v1`, auth-guarded, cursor-paginated)_

### A. Version history
`GET /resumes/{id}/versions` (metadata list), `GET …/versions/{vid}` (decompressed
data on demand), `POST …/versions/{vid}/restore` (snapshot-current-then-apply,
version CAS), `POST …/undo-last-ai`, `GET …/versions/compare?a=&b=` (field diff).
Snapshot hooks fire via services (parse→original, confirm→ai, save→manual w/
debounce + content-hash dedupe). `VersionHistoryPanel` wires to these.

### B. Notifications
`GET /notifications?cursor=&unread=&category=`, `POST /{id}/read`,
`POST /read-all`, `DELETE /{id}`, `POST /dismiss-group`, `GET/PUT
/notifications/prefs`, `GET /notifications/unread-count`. `NotificationCenter`
wires to these; unread badge from `user_unread_counts`. Transport is an ADR-14
toggle `notification_transport` (admin setting): **polling on free** (client
polls unread-count/notifications every `polling_interval_seconds`, active-tab
only) / **sse on premium** (`SSE_NOTIFICATIONS` selects the sse transport);
data model + endpoints are identical, only delivery differs. WebSockets are
avoided on free tier (they hold the sleeping dyno open).

### C. Global search
`GET /search?q=&types=&status=&from=&to=&cursor=`. Indexer consumes outbox →
upserts `search_documents`; `POST /admin`-less internal `rebuild` command +
drift check. Command palette → server search + client fallback; recent searches
local.

### D. JD from URL
`POST /jobs/fetch-url {url, idempotency_key?}` → SSRF-guarded fetch (see
Security) → extract (readability) → optional bounded+cached LLM cleanup →
`{content, low_confidence, source_url}`. Cached by content hash; kill-switch.

### E/F. Reminders & Interviews
`POST/PATCH/DELETE /applications/{id}/reminders[/{rid}]` (+ snooze, recurrence,
presets), `POST/PATCH/DELETE /applications/{id}/interviews[/{iid}]`
(+ reschedule, lead-times), `GET /interviews/{iid}.ics`. Parent-ownership → 404.
Scheduler claims + fires; recurring reminders materialize next occurrence.

### G. Agenda
`GET /agenda?cursor=` — merged, time-ordered upcoming reminders + interviews
across applications (indexed union), with quick actions.

### H. Avatar & profile
`POST /users/me/avatar` (multipart) → sniff/validate/re-encode/strip/store →
set url; `PATCH /users/me` extended fields (validated).

## Security — threat model
| Threat | Mitigation |
|---|---|
| SSRF (JD-URL) incl. **DNS-rebinding** | scheme (http/https) + **port (80/443)** allow-list; block private/loopback/link-local/CGNAT/metadata; **resolve once, connect to pinned IP**; re-validate each redirect hop (≤3); no forwarded auth headers |
| SSRF resource exhaustion | streamed **byte cap** (ignore Content-Length), **decompression-bomb** cap, wall-clock timeout, per-user+global rate limit, concurrency cap, kill-switch |
| Stored XSS (JD/notification/search text) | React escaping; notifications/search store plain text (no HTML); resume HTML only via the vetted sanitizer |
| IDOR (versions/reminders/interviews/notifications/search) | user_id scoping + parent-ownership → 404; search scoped in SQL, `q` parameterized |
| Malicious avatar (polyglot/bomb/EXIF/SVG) | magic-byte sniff, no SVG, byte+**pixel** caps, canonical re-encode, EXIF/GPS strip, server path, signed serve, orphan GC |
| Notification/reminder spam | `dedupe_key` unique + single-flighted claim-based scheduler; per-user reminder/interview caps; idempotency-key on creates |
| ICS/CRLF injection | escape VEVENT fields; strip CRLF |
| Content leakage (email/notification) | title + deep link only; never resume/JD content or secrets |
| AI cost abuse | opt-in, bounded, cached by content hash; never auto-fire |
| Search index poisoning/drift | outbox-driven idempotent upserts; rebuild + drift detection |

## Reliability & concurrency
Outbox write is transactional with the change; consumers are at-least-once +
idempotent by event id (safe to reprocess). Scheduler **claims** rows
(pending→firing) so multi-worker never double-fires; recurring reminders
materialize atomically. Restore snapshots-current-first + version CAS. Search
indexer failure never fails the user write (async). Email send retries → DLQ.
Avatar: set url only after successful store; failed store leaves no dangling url.
Idempotency-keys collapse double-submits.

## Performance & scalability
FTS (no LIKE scans); outbox-fed async indexing keeps writes fast. Unread count is
O(1) via `user_unread_counts` (no COUNT-per-poll). Snapshots gzip-compressed +
content-hash-deduped + capped; version list is metadata-only, data on demand.
Scheduler scans are indexed `(status, due_at/starts_at)` bounded batches. All
lists cursor-paginated; agenda is an indexed union. Retention prunes growth.
Object storage + CDN for avatars. Targets: search p95 < 300ms; unread badge
< 50ms; scheduler backlog ≈ 0.

## Observability & operations
Metrics: search latency + **indexer lag/backlog**, scheduler backlog +
**double_fire_total=0**, notification create/send + **email DLQ depth**,
jd_fetch_total{result} (+ blocked-SSRF count as a probe signal), avatar upload
outcomes, unread-counter drift. Alerts: indexer lag, scheduler backlog, DLQ
growth, JD-fetch error/block spikes, retention job failure. Runbooks: rebuild
search index, drain/replay outbox, unstick a claim, replay DLQ, reclaim orphan
avatars, reconcile unread counter. Flags/kill-switches per feature (esp.
`JD_FROM_URL`, `NOTIFICATIONS_EMAIL`, `SSE_NOTIFICATIONS`).

## Deployment
Migrations: all new tables + FTS + outbox + counters. Workers: indexer,
scheduler, retention, avatar-GC, email-sender — all run under `SCHEDULER_MODE`
(ADR-15): free tier as `external_cron`-driven single-flighted batches invoked
via an authenticated internal endpoint; premium as `internal` always-on workers
— identical logic and single-flighted KVStore locks either way. Object storage
via `StorageProvider` (`STORAGE_PROVIDER`; Cloudinary free default / S3 premium /
Local dev, ADR-10) + lifecycle + CDN/URL-transforms + signed-URL TTL. Env: storage
creds, pluggable `EmailSender` (free-tier provider default e.g. Resend/Brevo,
selectable by config; premium swaps provider with no code change — ADR-14),
CAPTCHA/breach providers (reused from P1), JD limits (size/time/
concurrency), retention windows, reminder/interview caps. Rollback: flags off +
down-migrate; search index + counters are rebuildable.

## Correctness Properties

### Property 1: Strict user scoping

**Validates: Requirements 17.1, 7.2**

Every new resource is user-scoped and parent-ownership checked; a foreign/absent
id returns 404; no search result, notification, version, reminder, or interview
ever exposes another user's data (scope enforced in SQL, `q` parameterized).

### Property 2: Non-destructive, deduped versioning

**Validates: Requirements 1.2, 2.1**

Restore snapshots the current state first and is reversible; the `original` is
always retained; identical consecutive states are de-duplicated by content hash.

### Property 3: Exactly-once effect for scheduled notifications

**Validates: Requirements 5.2, 10.3, 16.2**

The claim-based single-flighted scheduler plus `dedupe_key` guarantee no
duplicate notification is delivered for the same event, even across workers or
retries.

### Property 4: SSRF containment

**Validates: Requirements 9.2**

JD-from-URL can never reach a private/metadata address or a non-allowed
scheme/port; the guard holds across redirects and DNS-rebinding (pinned IP), and
resource caps bound size/time.

### Property 5: Avatars are always sanitized

**Validates: Requirements 13.1**

Every stored/served avatar is magic-byte-validated, dimension-bounded,
re-encoded to a canonical format, and EXIF-stripped; SVG and polyglots are never
stored or served.

### Property 6: Decoupled, self-healing indexing

**Validates: Requirements 7.1, 16.1**

Search indexing and notifications run asynchronously from the outbox (a failure
never fails the user's write) and are idempotent + rebuildable, so drift is
always recoverable.

## Error Handling
Standard envelope. 404 (foreign/absent id), 422 (validation), 409 (version
conflict on restore), 429 (rate limited), `fetch_failed` (JD-URL, no internal
detail), `invalid_file` (avatar). Search/notification read failures degrade to
empty/error states with retry. Async consumer failures retry (outbox attempts) →
alert; email failures → DLQ. All user strings sanitized before persist/log.

## Testing Strategy
- **Unit:** snapshot dedupe/compress/cap/debounce; restore non-destructive +
  version CAS; SSRF validator (private/rebind/redirect/port/size/bomb); ICS
  escaping; avatar sniff/dimension/re-encode/EXIF; FTS query builder + scope;
  recurrence materialization + snooze; tz/DST conversion; unread-counter math;
  outbox idempotency; scheduler claim.
- **Integration:** versions CRUD + ownership 404 + compare; notifications
  list/read/dismiss/group + prefs + counter + dedupe idempotency; search scoping
  (no cross-user) + ranking + pagination stability; JD-fetch SSRF matrix (blocked
  IPs/ports/redirects/rebind/bomb) + cache; reminders (snooze/recurrence/claim/
  fire→notify) + interviews (reschedule/lead-times/ICS/overlap); agenda union;
  avatar happy + malicious reject + orphan GC; retention prune.
- **Concurrency:** multi-worker scheduler → **no double-fire**; concurrent
  restore; concurrent mark-read vs create (counter correctness); outbox replay.
- **Security:** SSRF suite (incl. rebind + bomb + port), malicious-upload suite,
  IDOR across users, search injection/scope, ICS injection, email/notification
  content-leak, AI cost-guard (no auto-fire).
- **Perf/scale:** search + agenda + notifications at millions of rows; indexer
  throughput/lag; scheduler backlog; unread badge latency.
- **E2E:** restore version + compare; notification receive→group→dismiss + prefs;
  search jump; JD-URL→verify→tailor; reminder w/ snooze+recurrence→due; interview
  reschedule→ICS→prep; avatar upload; agenda quick actions.
- **A11y/Mobile:** keyboard search/notifications/agenda; SR + `aria-live`;
  reduced-motion; mobile sheets + touch date/tz pickers.
- **Failure/recovery:** indexer down (search stale, rebuild); scheduler down
  (backlog alert, catch-up no double-fire); email down (DLQ replay); storage down
  (avatar fails cleanly, no dangling url).

## Self-critique loop

**Round 1**
- *Architect:* inline search triggers couple indexing to the write path (a search
  failure fails the save). **Fix:** transactional **outbox + async idempotent
  indexer** (Property 6, §Architecture).
- *Architect:* reminders/interviews reinvent scheduling + notifications. **Fix:**
  shared **SchedulerService (claim) + NotificationService + outbox** (R16).
- *Security:* JD-URL SSRF incomplete. **Fix:** DNS-rebind IP pin, port allow-list,
  decompression-bomb + streamed byte cap, per-hop revalidation (Property 4).

**Round 2**
- *SRE/Backend:* multi-worker scheduler double-fires despite single-flight.
  **Fix:** atomic **claim** (pending→firing) + dedupe_key (Property 3).
- *Backend:* unread count = COUNT scan per poll. **Fix:** denormalized
  `user_unread_counts` (R4.2).
- *DB:* snapshots store full JSON × 50 × millions. **Fix:** gzip + content-hash
  dedupe + save debounce + metadata-only list (R1, R3.1).
- *Security:* avatar trusts MIME/extension; no dimension cap. **Fix:** magic-byte
  sniff + pixel cap + canonical re-encode + EXIF strip + orphan GC (Property 5).

**Round 3**
- *Productivity UX:* notification fatigue (only read/unread). **Fix:** category,
  priority, grouping, **preferences + digest**, content-safe email (R4, R6).
- *UX:* reminders lack snooze/recurrence; interviews lack reschedule/lead-time/
  tz/prep. **Fix:** snooze + bounded recurrence + presets; reschedule + lead-times
  + DST + prep link (R10, R11).
- *UX:* no single "what's next". **Fix:** **Agenda** surface (R12).
- *AI Architect:* AI cost/creep unbounded. **Fix:** opt-in, cached, bounded,
  never auto-fire principle (R15).

**Round 4**
- *DB/SRE:* everything grows forever. **Fix:** **retention/archival** jobs +
  outbox pruning (R17.4).
- *Backend:* double-submit dupes on create. **Fix:** **idempotency-key** (R17.3).
- *Backend:* semantic-search pressure. **Fix:** keyword FTS now behind a
  `SearchIndex` port; embeddings future drop-in (R7.2).
- *Frontend:* long lists + deep-link context. **Fix:** virtualization + context-
  carrying deep links + optimistic + a11y (R18).

**Round 5 (final — "hundreds of thousands of daily users, what emerges?")**
Residuals, explicitly accepted: (a) push notifications, two-way calendar sync,
and semantic search are deferred behind existing ports/services (no rework);
(b) recurrence is a bounded RRULE subset (full RFC-5545 later); (c) outbox gives
eventual consistency for search/notifications (bounded lag, monitored). No open
critical/high/medium architectural, security, UX, or scalability issue remains.
