# Implementation Plan — P3 Productivity Features

## Overview

Version history, notifications, global search, JD-from-URL, reminders,
interviews, an agenda, and avatar/profile — built on shared platform services
(event outbox + async idempotent consumers, a claim-based scheduler, a single
NotificationService). Depends on `../auth-foundation/` (user-scoping, sessions,
KVStore, jobs); inherits `../phase-2-roadmap.md`. Each feature is independently
shippable behind its flag. Verify each parent task: backend `uv run pytest`
(incl. scoping/security/concurrency), frontend `npm run build`/`test`/lint.

## Task Dependency Graph

```json
{
  "waves": [
    { "wave": 1, "tasks": ["0"], "depends_on": [] },
    { "wave": 2, "tasks": ["1", "2", "3"], "depends_on": ["0"] },
    { "wave": 3, "tasks": ["4", "5", "6", "7", "8"], "depends_on": ["0", "3"] },
    { "wave": 4, "tasks": ["9"], "depends_on": ["2", "3", "4", "5", "6", "8"] },
    { "wave": 5, "tasks": ["10"], "depends_on": ["1", "2", "3", "4", "5", "6", "7", "8", "9"] }
  ]
}
```

Task 1 = Version history · 2 = Search · 3 = Notifications · 4 = JD-from-URL ·
5 = Reminders · 6 = Interviews · 7 = Agenda · 8 = Avatar/profile ·
9 = Retention/observability/AI-guard · 10 = Verification.

## Tasks

- [ ] 0. Shared platform scaffold
  - [ ] 0.1 Migrations: `resume_versions`(+content_hash/data_gz), `outbox`, `search_documents`(FTS), `notifications`(+category/priority/group), `notification_prefs`, `user_unread_counts`, `reminders`(+recurrence/status), `interviews`(+lead_times/status), profile/avatar fields; all `user_id` + indexes
    - _Requirements: 1, 4, 7, 10, 11, 13, 14_
  - [ ] 0.2 Outbox writer (txn-coupled) + at-least-once idempotent consumer framework; **SchedulerService** (bounded scan + atomic **claim** + single-flight + resumable); **NotificationService** (sole writer: dedupe/prefs/priority/group/unread-counter); object-storage client; per-feature flags + kill-switches
    - _Requirements: 16.1, 16.2, 16.3, 17.5_

- [ ] 1. Version history
  - [ ] 1.1 Snapshot hooks (parse→original, confirm→ai, save→manual) with content-hash dedupe, save debounce, gzip storage, per-resume cap/prune (keep original)
    - _Requirements: 1.1, 1.2, 1.3_
  - [ ] 1.2 Endpoints: metadata list, data-on-demand, restore (snapshot-current-first + version CAS), undo-last-ai, compare(diff); ownership 404
    - _Requirements: 2.1, 2.2, 2.3, 3.1, 3.2_
  - [ ] 1.3 Wire `VersionHistoryPanel` (list/restore/undo/compare + diff viewer)
    - _Requirements: 2, 3, 18.1_

- [ ] 2. Global search (outbox-indexed)
  - [ ] 2.1 SearchIndexer consuming outbox → `search_documents` (node-type registry, content-safe) + rebuild command + drift detection
    - _Requirements: 7.1, 7.3, 16.1_
  - [ ] 2.2 `GET /search` (scoped-in-SQL, parameterized, FTS ranked, filters, cursor); command palette → server + client fallback; recent searches, keyboard nav, deep-link
    - _Requirements: 7.2, 8.1, 8.2_

- [ ] 3. Notifications
  - [ ] 3.1 Endpoints (list/read/read-all/dismiss/dismiss-group, prefs get/put, unread-count) + `user_unread_counts` maintenance
    - _Requirements: 4.1, 4.2, 4.3, 6.1_
  - [ ] 3.2 Event-driven creation via NotificationService (dedupe, priority, category, grouping); email delivery honoring prefs + **digest** + DLQ (content-safe)
    - _Requirements: 5.1, 5.2, 5.3, 6.2_
  - [ ] 3.3 Wire `NotificationCenter` (grouping, filters, prefs panel, unread badge from counter, poll/SSE flag)
    - _Requirements: 4.3, 6.3, 18.1, 18.2_

- [ ] 4. JD from URL (SSRF-hardened + cached)
  - [ ] 4.1 `POST /jobs/fetch-url`: scheme+port allow-list, private/metadata block, **DNS-resolve-once + pinned-IP connect**, per-hop redirect revalidation (≤3), streamed byte cap + decompression-bomb guard + timeout, no forwarded headers, rate-limit + concurrency cap + kill-switch; extract + bounded/cached LLM cleanup; `low_confidence`
    - _Requirements: 9.1, 9.2, 9.3, 15.1_
  - [ ] 4.2 Tailor UI: URL input + loading/error + verify-before-generate
    - _Requirements: 9.1, 18.1_

- [ ] 5. Follow-up reminders
  - [ ] 5.1 CRUD (+presets, snooze, bounded recurrence) under `/applications/{id}/reminders` (ownership 404, idempotency-key, per-user cap); scheduler claim→fire→notify + materialize next occurrence
    - _Requirements: 10.1, 10.2, 10.3, 17.1, 17.3_
  - [ ] 5.2 Workspace reminders UI + Home "Needs attention"
    - _Requirements: 10.4, 18.1_

- [ ] 6. Interview scheduling
  - [ ] 6.1 CRUD (+reschedule/cancel, configurable lead-times, UTC+IANA tz DST-correct, overlap warning) + idempotency-key; scheduler lead-time notifications; `GET /interviews/{id}.ics` (escaped, tz-correct); prep-generation link
    - _Requirements: 11.1, 11.2, 11.3, 17.3_
  - [ ] 6.2 Workspace interview UI + Home upcoming (timezone-correct)
    - _Requirements: 11.4, 18.1_

- [ ] 7. Agenda
  - [ ] 7.1 `GET /agenda` (indexed union of upcoming reminders + interviews, cursor) + UI (quick actions: open/snooze/reschedule/done), keyboard + mobile sheet
    - _Requirements: 12.1, 12.2, 18.4_

- [ ] 8. Avatar & extended profile
  - [ ] 8.1 `POST /users/me/avatar`: magic-byte sniff, no-SVG, byte+pixel caps, canonical re-encode, EXIF/GPS strip, server path, signed serve, set-url-after-success; orphan-avatar GC (retention worker)
    - _Requirements: 13.1, 13.2_
  - [ ] 8.2 `PATCH /users/me` extended fields (validated URLs/length); Settings/Profile UI (avatar + fields)
    - _Requirements: 14.1, 18.1_

- [ ] 9. Retention, observability & AI cost-guard
  - [ ] 9.1 Retention/archival workers (read notifications + fired reminders + over-cap snapshots + processed outbox); configurable windows
    - _Requirements: 17.4_
  - [ ] 9.2 Metrics + alerts (search latency + indexer lag, scheduler backlog + double_fire=0, notification create/send + email DLQ, jd_fetch outcomes + blocked-SSRF, avatar outcomes, unread-counter drift); runbooks; enforce AI cost-guard (opt-in/bounded/cached/never-auto-fire)
    - _Requirements: 15.1, 17.5_

- [ ] 10. Verification (cross-feature)
  - [ ] 10.1 Security: SSRF matrix (private/rebind/port/redirect/bomb), malicious-upload, IDOR across users, search injection/scope, ICS injection, email/notification content-leak, AI no-auto-fire
    - _Requirements: 9.2, 13.1, 17.1, 15.1_
  - [ ] 10.2 Concurrency: multi-worker scheduler → **no double-fire**; concurrent restore; unread-counter under concurrent read/create; outbox replay idempotency
    - _Requirements: 5.2, 10.3, 16.2, 4.2_
  - [ ] 10.3 Perf/scale (search/agenda/notifications at volume; indexer/scheduler backlog), E2E (restore/compare, notify/group/prefs, search-jump, JD→verify→tailor, reminder snooze/recurrence, interview reschedule/ICS/prep, avatar, agenda), a11y+mobile, failure/recovery (indexer/scheduler/email/storage down)
    - _Requirements: 18.*, 17.4, 17.5_

## Notes
- Producers write to the **outbox** in-txn; the **indexer** and
  **NotificationService** consume async + idempotently (a search failure never
  fails a user write).
- The scheduler **claims** due rows to guarantee no double-fire across workers;
  every scheduled notification is `dedupe_key`-idempotent.
- All AI is opt-in, bounded, cached, and never auto-fires; all endpoints are
  user-scoped (foreign id → 404); everything is flag-gated with retention.
