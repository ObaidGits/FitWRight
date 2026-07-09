# Requirements Document

_P4 Resilience & Advanced UX — streaming AI, offline support, conflict
resolution, advanced autosave, and crash recovery. The goal: **no user ever
loses work**, and the app degrades gracefully under every realistic failure._

## Introduction

P4 hardens the live editing experience against the messy reality of browsers and
networks: refreshes, crashes, sleep, tab death, flaky connections, backend
restarts, provider outages, and multi-device / multi-tab use. It builds on P1
(sessions, user-scoping, CSRF, KVStore) and the ui-revamp
draft/RecoveryBanner/OfflineIndicator primitives. It inherits every ADR and
standard in `../phase-2-roadmap.md` (especially ADR-1/2 sessions+CSRF, ADR-6
KVStore/multi-worker, ADR-7 error envelope, ADR-8 rate limiting, §4.2
reliability, idempotency & optimistic concurrency).

Feature areas: **(A) Streaming AI**, **(B) Offline support**, **(C) Conflict
resolution**, **(D) Advanced autosave**, **(E) Recovery**, plus cross-cutting
**(F) resilience guarantees**, **(G) multi-tab coordination**, and **(H) local
data safety**.

The design principle is **defense in depth for durability**: three independent
layers protect edits — (1) the in-memory editor state, (2) a durable local store
(IndexedDB draft + outbox, survives refresh/crash), and (3) the authoritative
server copy (survives device loss). A failure in any one layer never causes data
loss because another layer still holds the work.

### Non-goals
- Full CRDT / real-time co-editing. Concurrency is resolved by **explicit
  conflict resolution**, not automatic merge of arbitrary overlapping edits.
- Offline **AI generation**. AI stays network-only; offline covers reading and
  editing only.
- Cross-device real-time sync / presence. Sync is on reconnect / on load, not a
  live socket.
- Server-side per-keystroke history (that is P3 Version History); P4 autosave
  produces coarse server saves + a local crash buffer.

## Glossary
- **SSE**: Server-Sent Events; the one-way `text/event-stream` transport used for
  streaming AI tokens.
- **Version CAS**: compare-and-set on a resource's integer `version` for
  optimistic concurrency; a write carries the base version it read.
- **Outbox**: a durable local (IndexedDB) ordered op-log of mutations made while
  offline or while a save was failing, replayed to the server on reconnect.
- **Draft**: the durable local snapshot of the current editor content (IndexedDB
  via the ui-revamp `useDraft`), written independently of network state; the
  crash safety net.
- **Idempotency key**: a client-generated unique id attached to a mutation so a
  server can dedupe retries (ADR §4.2).
- **Leader tab**: among multiple open tabs of the same account, the single tab
  elected (via BroadcastChannel + Web Locks) to own autosave and outbox flushing,
  preventing duplicate/racing writes.
- **Retry storm**: uncoordinated aggressive client retries that overwhelm a
  recovering backend; prevented by jittered backoff, retry caps, and a client
  circuit breaker.
- **Circuit breaker**: a client-side state machine (closed→open→half-open) that
  stops hammering an endpoint that is repeatedly failing, and probes for recovery.
- **Preview-before-apply**: streamed/generated AI output is a *preview* until the
  user explicitly accepts; nothing is persisted before acceptance (trust model,
  unchanged from ui-revamp).
- **Degradation level**: a named tier (Full / Degraded-AI / Offline-Read-Write /
  Read-Only / Safe-Mode) describing which capabilities are available under a
  given failure, so behavior is deterministic and communicable.
- **Quarantine**: isolating a draft/outbox entry that fails integrity validation
  so it never corrupts live state, while preserving it for manual recovery.

---

## Requirements

### Requirement 1: Streaming AI
**User Story:** As a user, I want AI output to stream in and be cancellable, so
that long generations feel fast, transparent, and under my control.

#### Acceptance Criteria

1. WHERE the active provider supports streaming, THE SYSTEM SHALL stream tokens
   for long generations (tailor rationale, cover letter, interview prep) with a
   first-token target of under 2 seconds, rendered progressively.
2. THE SYSTEM SHALL let the user cancel an in-flight generation at any time;
   cancellation SHALL abort the provider call server-side (propagating the abort
   to the LLM request) and leave all persisted state unchanged.
3. WHEN streaming is unsupported by the provider, is disabled by flag, or the
   stream errors mid-flight, THE SYSTEM SHALL fall back transparently to the
   existing single-response path without user action, and SHALL surface partial
   text (if any) as a discardable preview.
4. Streamed output SHALL remain a preview until the user accepts; THE SYSTEM
   SHALL NOT persist streamed content before explicit acceptance.
5. THE SYSTEM SHALL cap concurrent streams per user and impose a maximum stream
   lifetime; exceeding the cap SHALL return a clear, retryable error, and an
   abandoned stream (client disconnect, no heartbeat) SHALL be reaped server-side
   within a bounded time so tasks and provider spend are not leaked.
6. WHEN the session expires mid-stream, THE SYSTEM SHALL close the stream, emit a
   terminal error event, and route the user to re-authenticate without a crash or
   a hung UI.
7. THE SYSTEM SHALL account for AI spend on streamed generations under the same
   cost-guard as non-streaming (inherited from P3), including cancelled streams
   (charged for tokens actually produced).

### Requirement 2: Offline support
**User Story:** As a user, I want to keep reading and editing when my connection
drops, so that a flaky or absent network never blocks me or loses my work.

#### Acceptance Criteria

1. WHEN offline, THE SYSTEM SHALL keep the app usable for reading previously
   loaded data (app shell + cached GET responses) and for editing resumes,
   queuing every edit durably in the local outbox.
2. WHEN connectivity returns, THE SYSTEM SHALL replay queued edits **in order**
   through the conflict path (Requirement 3), transitioning a visible status
   through offline → syncing → synced, or → conflict when resolution is needed.
3. THE SYSTEM SHALL clearly indicate offline mode and SHALL explicitly disable
   AI actions offline with a message explaining they require a connection, rather
   than letting them fail opaquely.
4. THE SYSTEM SHALL define and communicate the **offline scope**: what works
   fully offline (read cached resumes/apps, edit resume text, queue edits), what
   is read-only (data never loaded while online), and what is unavailable (AI,
   URL import, PDF export if server-rendered, auth flows).
5. THE SYSTEM SHALL bound outbox growth (max entries / max bytes / max age); on
   approaching the bound THE SYSTEM SHALL warn the user and SHALL NOT silently
   drop queued work — it SHALL block new offline edits with an explanation before
   it would overflow.
6. WHEN the browser signals `online` but the backend is actually unreachable
   (captive portal, backend down), THE SYSTEM SHALL detect the failed
   reachability probe and remain in a syncing/degraded state rather than
   reporting a false "synced".

### Requirement 3: Conflict resolution
**User Story:** As a user editing on multiple devices or tabs, I want conflicting
saves detected and resolved explicitly, so that my newer work is never silently
lost or silently overwritten.

#### Acceptance Criteria

1. THE SYSTEM SHALL use optimistic concurrency: every editable resource carries
   an integer `version`; every write sends the base `version` it was derived
   from (via `If-Match` header or `base_version` body field).
2. WHEN a write's base version is stale (server moved on), THE SYSTEM SHALL
   reject it with HTTP 409 carrying `{ your_base_version, current_version,
   current_data }`, and THE UI SHALL present explicit resolution: **keep mine**,
   **take latest**, or **field-level merge** (offered only when the changed
   fields are disjoint; otherwise keep/latest).
3. THE SYSTEM SHALL never silently overwrite a newer version; the only way local
   changes replace a newer server version is an explicit user "keep mine".
4. THE SYSTEM SHALL apply version CAS atomically server-side so two concurrent
   writes with the same base version cannot both succeed (exactly one wins; the
   other gets 409).
5. WHEN the user chooses "keep mine", THE SYSTEM SHALL re-base the local edit
   onto the current server version and write with the new base, producing a fresh
   version (a normal write, not a forced overwrite of history).
6. THE conflict UI SHALL present a readable diff (mine vs latest) so the user can
   make an informed choice, not a blind pick.

### Requirement 4: Advanced autosave
**User Story:** As a user, I want my edits saved automatically and safely, so
that I never have to remember to save and never lose changes, without autosave
ever interrupting my typing or hammering the server.

#### Acceptance Criteria

1. THE Resume Editor SHALL autosave edits (debounced on idle) to the server in
   addition to the durable local draft, exposing a clear status:
   saved / dirty / saving / retrying / offline / conflict, plus a last-saved
   relative time.
2. Autosave SHALL be idempotent (carry an idempotency key + base version so a
   retried save is deduped and identical content is a no-op), cancel-safe,
   coalesce rapid edits (single in-flight request + at most one trailing save),
   and SHALL NEVER block or lag typing.
3. Autosave SHALL respect conflict resolution (Requirement 3) on every write; a
   409 routes into the conflict flow rather than being retried blindly.
4. WHEN a save fails with a transient error (network / 5xx / 429), THE SYSTEM
   SHALL retry with exponential backoff **plus jitter**, up to a bounded number
   of attempts, and SHALL trip a circuit breaker after repeated failures to avoid
   a retry storm; while the breaker is open the local draft still protects the
   work and the UI shows "changes saved locally, will retry".
5. THE SYSTEM SHALL guarantee that at all times either the server has the latest
   accepted content OR the durable local draft/outbox does; there is no window
   where an edit exists only in volatile memory beyond the debounce interval.
6. WHEN the user navigates away or closes the tab with unsaved changes, THE
   SYSTEM SHALL attempt a best-effort flush (e.g. `visibilitychange`/`pagehide`)
   and the durable local draft SHALL already hold the content regardless of
   whether the flush completes.

### Requirement 5: Recovery
**User Story:** As a user, I want my unsynced work restored after a crash,
refresh, tab close, or power loss, so that nothing is lost when something goes
wrong.

#### Acceptance Criteria

1. WHEN the app reloads after a crash / refresh / reopen, THE SYSTEM SHALL detect
   a durable local draft that is newer than or divergent from the server copy and
   SHALL offer non-destructive restore, reconciled against the current server
   version (through the conflict flow if the server has since moved).
2. WHEN a queued outbox edit fails permanently (max retries exhausted, or an
   unrecoverable conflict the user deferred), THE SYSTEM SHALL preserve it
   durably and surface a non-destructive recovery path (view / re-apply /
   discard), never dropping it silently.
3. WHEN a local draft or outbox entry fails integrity validation (corruption,
   schema/version mismatch, decryption failure), THE SYSTEM SHALL quarantine it —
   never load corrupt data into the live editor — and SHALL inform the user with
   a recovery/export option rather than crashing or overwriting good data.
4. THE SYSTEM SHALL make recovery decisions deterministic and explained: the
   RecoveryBanner SHALL state what was found, its age, and what each choice does,
   and SHALL default to the non-destructive option.
5. WHEN multiple recovery sources exist (local draft, failed outbox entries),
   THE SYSTEM SHALL present them coherently rather than as conflicting prompts.

### Requirement 6: Cross-cutting resilience guarantees
**User Story:** As a user, I want reliability to be a property of the whole app,
consistent and accessible everywhere, so I can trust it under any condition.

#### Acceptance Criteria

1. All new endpoints SHALL be user-scoped and auth-guarded (ADR-4/§4.1);
   mutations SHALL require the P1 CSRF token; ownership mismatch SHALL return 404.
2. THE SYSTEM SHALL guarantee no data loss under refresh, crash, tab close, power
   loss, sleep/resume, offline, backend restart, deployment, or concurrent edits,
   per the failure-scenario matrix in Requirement 9.
3. Cancellation, retries, and replays SHALL all be safe and idempotent; no
   operation applied twice SHALL corrupt state.
4. THE SYSTEM SHALL define explicit **degradation levels** (Full, Degraded-AI,
   Offline-Read-Write, Read-Only, Safe-Mode) with deterministic capability sets,
   and SHALL always communicate the current level to the user.
5. Accessibility: streaming output SHALL be announced via an `aria-live` region;
   all status indicators (offline, saving, conflict, retrying) SHALL have text /
   SR labels; the conflict diff SHALL be keyboard-navigable; all animation SHALL
   honor reduced-motion.

### Requirement 7: Multi-tab & multi-instance coordination
**User Story:** As a user with the app open in several tabs, I want them to
cooperate rather than fight, so that autosave and sync don't duplicate work,
race, or corrupt my draft.

#### Acceptance Criteria

1. WHEN the same account has multiple tabs open, THE SYSTEM SHALL elect a single
   **leader tab** (via Web Locks / BroadcastChannel) that owns autosave and
   outbox flushing for a given resource; follower tabs SHALL defer writes to the
   leader to prevent duplicate or racing saves.
2. WHEN the leader tab closes or crashes, THE SYSTEM SHALL re-elect a leader
   within a bounded time so autosave/flush resumes automatically.
3. WHEN one tab saves a resource, THE SYSTEM SHALL notify other tabs (via
   BroadcastChannel) so they update their base version and displayed content,
   avoiding an immediate self-inflicted conflict.
4. WHEN two tabs edit the *same* resource concurrently, THE SYSTEM SHALL reconcile
   through the version-CAS conflict path (Requirement 3) rather than losing one
   tab's edits, and SHALL warn the user that the resource is open elsewhere.
5. THE local draft and outbox SHALL be written under coordination (leader-owned
   or lock-guarded) so concurrent tabs cannot interleave writes into a corrupt
   state.

### Requirement 8: Local data safety & integrity
**User Story:** As a user, I want the work stored in my browser to be safe and
trustworthy, so that local storage is a reliable safety net, not a new risk.

#### Acceptance Criteria

1. THE SYSTEM SHALL store each durable local record (draft, outbox entry) with an
   integrity check (schema version + content hash) and SHALL validate it on read;
   a failed check triggers quarantine (Requirement 5.3), not a crash.
2. WHERE local drafts may contain sensitive resume content, THE SYSTEM SHALL
   support encrypting local data at rest in the browser (e.g. WebCrypto with a
   key scoped to the session), and SHALL clear local drafts/outbox and caches on
   logout so a shared or public machine does not retain personal data.
3. THE SYSTEM SHALL namespace all local storage by user id so switching accounts
   never mixes or leaks one user's drafts/outbox into another's.
4. THE SYSTEM SHALL handle storage-quota-exceeded and storage-unavailable
   (private mode / disabled storage) gracefully: warn, degrade to
   memory-only-with-warning, and never fail silently in a way that risks loss.
5. THE service worker cache SHALL never store auth, OAuth, CSRF, or API-key
   responses, SHALL be scoped to the current origin, and SHALL be cleared on
   logout and on detecting a different logged-in user.

### Requirement 9: Failure-scenario matrix (explicit, testable)
**User Story:** As a user, I want the app to behave predictably in every failure,
so that I always know my work is safe and what to do next.

#### Acceptance Criteria

For each scenario below, THE SYSTEM SHALL guarantee the stated outcome: the work
is recoverable, the user is informed, and no state is corrupted.

1. **Browser refresh / navigation** mid-edit: in-flight autosave may abort; the
   durable local draft holds content; on reload the editor restores from draft
   reconciled to server. No loss.
2. **Tab close / browser close** with unsaved edits: `pagehide` best-effort
   flush attempted; draft already durable; recovered on next open. No loss.
3. **Tab / browser crash**: no flush runs; draft (last debounce) is durable;
   recovered on next open, reconciled to server. Loss bounded to sub-debounce
   keystrokes only.
4. **OS sleep / resume**: on resume, timers/connections may be stale; the SYSTEM
   re-probes reachability, resumes autosave/outbox flush, and re-validates the
   session before writing.
5. **Network disconnect while editing**: edits queue to outbox; status shows
   offline; on reconnect, ordered replay through conflict path.
6. **Backend restart / 5xx burst**: autosave retries with jittered backoff; the
   circuit breaker prevents a storm; local draft/outbox hold work; recovers when
   backend returns.
7. **AI provider outage / timeout**: streaming falls back to non-stream, then to
   a clear error; no editor data affected; the user can retry later.
8. **Deployment / server version change during editing**: a changed SW /
   incompatible API version SHALL be detected; the SW update SHALL not activate
   destructively mid-edit; the user is prompted to reload at a safe point;
   in-flight work is preserved.
9. **Storage failure (quota / disabled / eviction)**: warn and degrade per
   Requirement 8.4; if IndexedDB is evicted, the SYSTEM detects the missing draft
   and does not fabricate a false "restored" state.
10. **Corrupted draft / outbox**: quarantined per Requirement 5.3; live state
    untouched; recovery path offered.
11. **Multiple tabs**: coordinated per Requirement 7; no duplicate saves, no
    interleaved corruption.
12. **Stale service-worker cache**: cache is versioned; a stale shell is replaced
    on safe update; API reachability probe prevents serving stale data as live.

---

## Traceability
Requirement 1 → Design §Streaming. 2 → §Offline. 3 → §Conflict. 4 → §Autosave.
5 → §Recovery. 6 → §Degradation/§Security/§Accessibility. 7 → §Multi-tab.
8 → §Local-data-safety/§Security. 9 → §Failure-matrix + all of the above.
All requirements map to Correctness Properties and to `tasks.md` waves.
