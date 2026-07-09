# Implementation Plan — P4 Resilience & Advanced UX

## Overview

Streaming AI, offline support, conflict resolution, advanced autosave, crash
recovery, multi-tab coordination, and local-data safety. Depends on
`../auth-foundation/` (sessions, CSRF, user-scoping, KVStore); inherits
`../phase-2-roadmap.md`. Each area is flag-gated and independently shippable.
Verify each parent task: backend `pytest`, frontend build/test/lint, plus the
failure-matrix and a11y checks called out per task.

Build order rationale: **conflict resolution (version CAS) is the foundation** —
autosave, offline replay, and recovery all write through it, so it ships first.
Multi-tab coordination and local-data safety underpin autosave/offline, so they
land alongside the foundation before the durable write paths depend on them.

## Task Dependency Graph

```json
{
  "waves": [
    { "wave": 1, "tasks": ["0"], "depends_on": [] },
    { "wave": 2, "tasks": ["1", "2"], "depends_on": ["0"] },
    { "wave": 3, "tasks": ["3", "4"], "depends_on": ["1", "2"] },
    { "wave": 4, "tasks": ["5", "6"], "depends_on": ["3", "4"] },
    { "wave": 5, "tasks": ["7"], "depends_on": ["3", "4", "5", "6"] },
    { "wave": 6, "tasks": ["8"], "depends_on": ["1", "2", "3", "4", "5", "6", "7"] }
  ]
}
```

Task 1 = Conflict resolution (version CAS + UI) · 2 = Local-data safety &
multi-tab foundation · 3 = Advanced autosave · 4 = Streaming AI · 5 = Offline
support · 6 = Recovery · 7 = Service-worker update & deployment coordination ·
8 = Verification (security, failure-matrix, perf, a11y, E2E).

## Tasks

- [ ] 0. Scaffold & flags
  - [ ] 0.1 Alembic migration: add `resumes.version int not null default 1` (forward + reversible); backfill existing rows to 1
    - _Requirements: 3.1, 6.1_
  - [ ] 0.2 KVStore-backed per-user stream-task registry + concurrent-stream counter + idempotency-key cache helpers (cross-worker)
    - _Requirements: 1.5, 4.2_
  - [ ] 0.3 Feature flags `STREAMING_AI` / `OFFLINE_SUPPORT` / `ADVANCED_AUTOSAVE` wired to `/config/flags`
    - _Requirements: 6.4_

- [ ] 1. Conflict resolution (foundation)
  - [ ] 1.1 Atomic version CAS on `PATCH /api/v1/resumes/{id}` (If-Match/base_version; conditional single-row UPDATE bumping version); 409 payload `{your_base_version,current_version,current_data}`; CSRF + user-scope + 404 on mismatch
    - _Requirements: 3.1, 3.3, 3.4, 6.1_
  - [ ] 1.2 Conflict modal with readable mine-vs-latest diff and keep-mine (re-base + fresh write) / take-latest / field-merge (disjoint only); extend RecoveryBanner variant
    - _Requirements: 3.2, 3.5, 3.6, 6.5_

- [ ] 2. Local-data safety & multi-tab foundation
  - [ ] 2.1 Durable local record format `{schema_version, content_hash, savedAt, base_version, payload}` with integrity validation on read → quarantine store on failure; per-`user_id` namespacing; extend `useDraft`
    - _Requirements: 8.1, 8.3, 5.3_
  - [ ] 2.2 WebCrypto (AES-GCM) encryption-at-rest for draft/outbox payloads under a per-session key; clear draft/outbox/cache + drop key on logout; storage quota/unavailable detection → memory-only-with-warning
    - _Requirements: 8.2, 8.4, 9.9_
  - [ ] 2.3 `TabCoordinator`: Web Locks leader election + re-election, BroadcastChannel save fan-out, lock-guarded draft/outbox writes
    - _Requirements: 7.1, 7.2, 7.3, 7.5_

- [ ] 3. Advanced autosave
  - [ ] 3.1 `SaveController` state machine (debounce + single in-flight + trailing coalesce) → idempotent `PATCH` with base_version + Idempotency-Key; durable draft written before each network attempt; status chips (saved/dirty/saving/retrying/offline/conflict + last-saved)
    - _Requirements: 4.1, 4.2, 4.5_
  - [ ] 3.2 Retry with exponential backoff + full jitter, capped attempts, client circuit breaker (closed/open/half-open), honor `Retry-After`; leader-only via `TabCoordinator`
    - _Requirements: 4.4, 7.1_
  - [ ] 3.3 Route autosave 409 into the conflict flow (1.2); best-effort unload flush (`visibilitychange`/`pagehide`, keepalive)
    - _Requirements: 4.3, 4.6_

- [ ] 4. Streaming AI
  - [ ] 4.1 SSE endpoints (cover letter, tailor rationale, interview prep) relaying LiteLLM chunks as `token`/`heartbeat`/`done`/`error`; per-user task registry + `POST …/cancel`; concurrent-stream cap + max lifetime + heartbeat reaper; 401-mid-stream close
    - _Requirements: 1.1, 1.2, 1.5, 1.6_
  - [ ] 4.2 Capability probe + transparent fallback to non-stream (partial text as discardable preview); token-usage on `done`/cancel into P3 cost-guard
    - _Requirements: 1.3, 1.7_
  - [ ] 4.3 `useStream` hook + progressive `aria-live` review UI + Cancel; preview-before-apply preserved (accept via existing confirm path)
    - _Requirements: 1.1, 1.4, 6.5_

- [ ] 5. Offline support
  - [ ] 5.1 Service worker (Workbox): precache app shell + SWR safe-GET caching scoped to `(app)`; never cache auth/OAuth/CSRF/api-key/mutation; AI network-only; versioned cache + activate-time prune
    - _Requirements: 2.1, 2.4, 8.5_
  - [ ] 5.2 Reachability probe (`GET /health`, short timeout) as source of truth for online state; `DegradationBanner` naming the current level; AI disabled offline with explanation
    - _Requirements: 2.3, 2.6, 6.4_
  - [ ] 5.3 IndexedDB outbox (ordered, base_version + idempotency key, bounded by entries/bytes/age with warn + block-on-cap); `SyncController` FIFO replay via version CAS through conflict flow; offline/syncing/synced/conflict status (leader-only)
    - _Requirements: 2.1, 2.2, 2.5, 3.1_

- [ ] 6. Recovery
  - [ ] 6.1 On-load reconcile local draft vs server version/updated_at; RecoveryBanner re-bases through conflict flow; deterministic non-destructive default with explanation
    - _Requirements: 5.1, 5.4_
  - [ ] 6.2 Coherent recovery surface for permanently-failed outbox entries (view/re-apply/discard) and quarantined records (export/discard); never drop silently
    - _Requirements: 5.2, 5.3, 5.5_

- [ ] 7. Service-worker update & deployment coordination
  - [ ] 7.1 Versioned SW safe-update (no destructive skipWaiting mid-edit) + "update available, reload" prompt at a safe point preserving draft across reload; shipped unregister/kill-switch path for flag-off
    - _Requirements: 9.8, 2.4_
  - [ ] 7.2 API/schema version-skew detection → Safe-Mode (read + local-draft-preserve, block writes) + reload prompt
    - _Requirements: 9.8, 9.12, 6.4_

- [ ] 8. Verification
  - [ ] 8.1 Security tests: SSE cross-user isolation + auth required; SW never caches auth/keys; outbox/draft tamper re-validation; stream cap/timeout/reaper; local encryption + logout clear; per-user namespacing
    - _Requirements: 1.5, 8.2, 8.5, 6.1_
  - [ ] 8.2 Failure-matrix tests: one per Requirement 9 scenario (refresh/close/crash/sleep/disconnect/backend-restart/AI-outage/deploy/storage-failure/corruption/multi-tab/stale-cache) asserting recoverable + informed + uncorrupted
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8, 9.9, 9.10, 9.11, 9.12_
  - [ ] 8.3 E2E: stream+cancel; offline edit → reconnect ordered sync; two-tab concurrent → conflict resolve (each option); leader close → follower takeover; crash-refresh recovery; deploy-mid-edit safe update
    - _Requirements: 1.1, 2.2, 3.2, 5.1, 7.2, 7.4_
  - [ ] 8.4 Perf: first-token latency; autosave write rate under rapid typing; retry-storm/brownout circuit-breaker behavior; SW hit ratio; outbox replay throughput. A11y: streaming live-region, status SR labels, keyboard conflict-diff, reduced-motion
    - _Requirements: 1.1, 4.2, 4.4, 6.5_

## Notes
- Streamed output is a preview until accept (trust model unchanged).
- No CRDT/real-time co-editing; conflicts resolved explicitly. Offline AI disabled.
- Every write goes through atomic version CAS; no silent overwrite.
- Three durability layers (memory → local draft/outbox → server); no single-layer
  failure loses work; crash loss bounded to sub-debounce keystrokes.
