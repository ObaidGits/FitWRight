# Design — P4 Resilience & Advanced UX

## Overview

Inherits every ADR/standard in `../phase-2-roadmap.md`; depends on P1 (sessions,
CSRF, user-scoping, KVStore, multi-worker readiness, Alembic) and the ui-revamp
draft/RecoveryBanner/OfflineIndicator primitives. It delivers streaming AI,
offline support, optimistic-concurrency conflict resolution, advanced autosave,
crash recovery, multi-tab coordination, and local-data safety — without
weakening the preview-before-apply trust model.

The organizing principle is **three independent durability layers** for every
edit: volatile editor memory → durable local store (IndexedDB draft + outbox) →
authoritative server. No single-layer failure loses work. Every feature area is
flag-gated (`STREAMING_AI`, `OFFLINE_SUPPORT`, `ADVANCED_AUTOSAVE`) and
independently shippable, with a documented off/rollback path. These three flags
are this spec's slice of the ADR-14 free/premium toggle registry — moving between
profiles is a flag/config value change, never a separate code path.

**Free-tier cold-start alignment (ADR-15):** the Service Worker cache +
IndexedDB drafts specified here are exactly the mechanism that masks the free
backend's cold start — a Render dyno sleeps after ~15 min and takes 30–60 s to
wake, but returning users and offline reads render instantly from cache while the
slept backend wakes behind them. The `/api/v1/health` reachability probe
(§Offline) doubles as the keep-warm ping target the external cron hits.

## Architecture

```
                         ┌───────────────── Browser (per account, N tabs) ──────────────────┐
                         │                                                                    │
   Resume Editor ──► editor state (volatile)                                                  │
        │                    │                                                                │
        │              useAutosave ──debounce+coalesce──► SaveController ─► circuit breaker ──┼──► PATCH /resumes/{id}
        │                    │                                   │  (jittered backoff)        │      (version CAS)
        │              useDraft ──► IndexedDB DRAFT (durable, hashed, encrypted-at-rest)       │
        │                    │                                                                │
        └── useOfflineSync ──┴──► IndexedDB OUTBOX (ordered op-log) ──SyncController──────────┼──► replay (version CAS)
                             │                                                                │
        TabCoordinator (Web Locks + BroadcastChannel): leader election, save fan-out, draft/outbox mutex
                             │                                                                │
        Service Worker (Workbox): app-shell + safe-GET cache (versioned), reachability probe  │
                             │                                                                │
        useStream ──► EventSource / fetch-stream ◄──SSE── /resumes/{id}/*/stream               │
                         └────────────────────────────────────────────────────────────────────┘
                                                    │
  Server: streaming endpoints (SSE relay + per-user task registry in KVStore, cap+reaper)
          PATCH endpoints (atomic version CAS, idempotency-key dedupe)
          reachability/health endpoint · session recheck · CSRF · rate limits (ADR-8)
```

Layering rule (inherited): routers call services; services own transactions;
every read/write is user-scoped. Client controllers (`SaveController`,
`SyncController`, `TabCoordinator`, `StreamController`) are pure state machines
with injected transports so they are unit-testable without a browser network.

## Components and Interfaces

### Streaming AI
- **Transport:** SSE (`text/event-stream`) from new endpoints
  `POST /api/v1/resumes/{id}/cover-letter/stream` and the tailor-rationale /
  interview-prep stream, gated by `STREAMING_AI` and a provider capability probe.
  LiteLLM streaming → server relays chunks as SSE events: `token` (delta),
  `heartbeat` (keep-alive/liveness), `done` (final + usage for cost accounting),
  `error` (terminal, triggers fallback).
- **Task registry & cancellation:** the server holds the generating
  `asyncio.Task` keyed by `(user_id, request_id)` in the KVStore-backed registry
  (works across workers). The client cancels by closing the stream or calling
  `POST …/stream/{request_id}/cancel`; the server cancels the task, which aborts
  the provider call. A **reaper** cancels tasks with no client heartbeat past a
  TTL and enforces a **per-user concurrent-stream cap** and **max lifetime**, so
  abandoned streams never leak tasks or provider spend. The reaper runs under
  `SCHEDULER_MODE` (ADR-15) like the other specs' scheduled jobs —
  `external_cron` hitting an authenticated internal endpoint on free, `internal`
  (worker) on premium — with identical logic.
- **Fallback:** if the capability probe is negative, the flag is off, or an
  `error`/timeout occurs mid-stream, the client transparently calls the existing
  non-stream endpoint. Any partial streamed text is shown as a discardable
  preview. Preview-before-apply is unchanged: streamed text persists only via the
  existing accept/confirm path.
- **Cost:** the `done` event carries provider token usage; cancelled streams
  report tokens produced so far. Both feed the P3 AI cost-guard.
- **Frontend:** `useStream()` accumulates tokens into an `aria-live="polite"`
  review region, exposes a Cancel button, and degrades to fallback on error.
- **Free-tier topology (ADR-15):** SSE is compatible with the keep-warm setup —
  the `heartbeat` events keep the connection and dyno alive during generation. On
  the strictest free tier where SSE is unavailable, `STREAMING_AI=off` makes the
  client use the existing non-stream path (the same fallback above), consistent
  with the ADR-14 toggle; only the flag value changes.

### Offline support
- **Service worker (Workbox):** precache the app shell; runtime-cache safe GET
  API responses (stale-while-revalidate). Scope limited to `(app)` routes.
  **Never** cache auth/OAuth/CSRF/api-key responses. AI and mutation endpoints
  are network-only. The SW is **versioned**; see §Service-worker update flow.
- **Reachability probe:** `navigator.onLine` is advisory only. A lightweight
  `GET /api/v1/health` probe (short timeout) is the source of truth for
  "backend reachable", so captive portals / backend-down are not reported as
  "synced" (R2.6).
- **Outbox:** offline resume edits append to the IndexedDB `outbox` op-log
  (ordered, each with `base_version` + idempotency key). `OfflineIndicator` shows
  offline/syncing/synced/conflict. On reconnect the `SyncController` (leader tab
  only) replays entries **in FIFO order** through the version-CAS path; a 409
  pauses that resource's replay and raises the conflict flow.
- **Offline scope (R2.4):** fully offline = read cached resumes/apps + edit
  resume text + queue edits; read-only = data never loaded while online;
  unavailable = AI, JD-from-URL, server-rendered export, auth. The
  `DegradationBanner` names the current level.
- **Bounds (R2.5):** outbox is capped (max entries / bytes / age). Approaching
  the cap warns the user; at the cap, new offline edits are blocked with an
  explanation — queued work is never silently dropped.

### Conflict resolution
- Add `version: int` to editable resources (`resumes` first; pattern extends).
  Every write sends `If-Match: <version>` (or `base_version` in body). The server
  performs an **atomic** CAS (`UPDATE … SET …, version = version + 1 WHERE id = ?
  AND user_id = ? AND version = :base` — the row is changed only if the guard
  matches; zero rows affected ⇒ conflict). Match → apply + bump + return new
  version; mismatch → **409** with `{ your_base_version, current_version,
  current_data }`.
- **Resolution UI** (extends RecoveryBanner conflict variant): **keep mine**
  (re-base local edit onto current server version, then write with the new base —
  a normal versioned write, R3.5), **take latest** (discard local, adopt server),
  **field-merge** (offered only when changed field sets are disjoint; otherwise
  keep/latest). A readable **diff** (mine vs latest) is shown so the choice is
  informed (R3.6). Never a silent overwrite.

### Advanced autosave
- **`SaveController` state machine:** `idle → dirty → saving → (saved | retrying
  | conflict | offline)`. Debounce ~1.2s idle → `PATCH /resumes/{id}` with
  `base_version` + `Idempotency-Key`. Coalescing: at most one in-flight request +
  one trailing pending save (latest content wins locally). Idempotent: identical
  content+version is a no-op; a retried request with the same idempotency key is
  deduped server-side.
- **Retry & circuit breaker (R4.4):** transient errors (network/5xx/429) retry
  with **exponential backoff + full jitter**, capped attempts. Repeated failures
  **open** the circuit breaker (stop hammering); it moves to **half-open** after a
  cooldown to probe, then **closed** on success. While open, edits still land in
  the durable draft and the UI shows "saved locally, will retry". A `Retry-After`
  from a 429 is honored.
- **409 handling:** never blind-retried; routed into the conflict flow.
- **Durability invariant (R4.5):** the durable draft is written on every debounce
  tick *before* the network attempt, so at all times either the server has the
  latest accepted content or the local draft/outbox does.
- **Unload flush (R4.6):** `visibilitychange`/`pagehide` triggers a best-effort
  synchronous flush (`fetch(..., { keepalive: true })`); correctness never
  depends on it because the draft is already durable.

### Recovery
- **On load reconcile:** compare local draft (`savedAt`, content hash, base
  version) against server `updated_at`/`version`. Local newer or divergent →
  RecoveryBanner offers restore; restore re-bases onto the current server version
  through the conflict flow if the server moved (R5.1). Decisions are explained
  and default to non-destructive (R5.4).
- **Failed-outbox recovery (R5.2):** permanently-failed entries (retries
  exhausted or deferred conflict) are retained durably and surfaced with
  view / re-apply / discard.
- **Quarantine (R5.3):** any draft/outbox record failing integrity (hash mismatch,
  schema version mismatch, decrypt failure) is moved to a `quarantine` store,
  never loaded into the editor, and surfaced with export/discard. Multiple
  recovery sources are presented coherently in one place (R5.5).

### Multi-tab & multi-instance coordination
- **`TabCoordinator`:** uses the **Web Locks API** (`navigator.locks`) to elect a
  single **leader** per account that owns autosave + outbox flush for a resource;
  **BroadcastChannel** fans out events between tabs. Follower tabs edit locally
  and delegate persistence to the leader (or, simpler and equally safe, only the
  leader runs the `SaveController`/`SyncController`).
- **Re-election (R7.2):** the Web Lock releases automatically when the leader tab
  closes/crashes; a waiting tab acquires it and resumes flush within a bounded
  time.
- **Save fan-out (R7.3):** after a successful save the leader broadcasts
  `{resource_id, new_version, content_hash}`; other tabs update their base
  version/content to avoid a self-inflicted 409.
- **Same-resource concurrent edit (R7.4):** if two tabs diverge, reconciliation
  goes through the version-CAS conflict path; the UI warns "open in another tab".
- **Draft/outbox mutex (R7.5):** writes to IndexedDB draft/outbox are guarded by
  a Web Lock so concurrent tabs cannot interleave into a corrupt record.

### Local data safety & integrity
- **Integrity:** each durable record stores `{schema_version, content_hash,
  savedAt, base_version, payload}`; reads validate the hash and schema version →
  mismatch triggers quarantine (R8.1).
- **Encryption at rest (R8.2):** sensitive draft payloads are encrypted with
  WebCrypto (AES-GCM) under a per-session key held in memory (and optionally
  wrapped in the session); on logout the key is dropped and drafts/outbox/SW
  cache are cleared.
- **Namespacing (R8.3):** all IndexedDB stores and cache keys are prefixed by
  `user_id`; a different logged-in user never sees another's local data.
- **Storage failures (R8.4):** quota-exceeded / storage-unavailable (private
  mode) is caught → warn + degrade to memory-only-with-warning; never a silent
  loss. Eviction of the draft is detected (missing record) and never fabricated
  as a false "restored" (R9.9).

### Service-worker update flow & deployment coordination
- The SW ships with a **build version**. On a new deploy the new SW installs but
  **waits** (no destructive `skipWaiting` mid-edit). When the editor is at a safe
  point (no unsaved dirty state, or user confirms), the app prompts "Update
  available — reload"; on accept it `skipWaiting` + reloads, preserving the
  durable draft across the reload (R9.8).
- **API version skew:** responses carry an API/schema version header; a client
  detecting incompatibility enters Safe-Mode (read + local-draft-preserve only)
  and prompts reload rather than sending writes the server may misinterpret.
- Cache is versioned; old caches are pruned on activate. A reachability probe
  guards against serving a stale shell's cached data as if it were live (R9.12).

## Data Models

- `resumes.version: int NOT NULL DEFAULT 1` — optimistic-concurrency token,
  bumped by every write via atomic CAS. (Alembic migration, forward + reversible.)
- **Server, KVStore (ephemeral, cross-worker):** per-user stream-task registry
  `stream:{user_id}:{request_id}` → task handle/metadata; per-user
  concurrent-stream counter; idempotency-key cache `idem:{user_id}:{key}` →
  result/version (short TTL) for autosave dedupe.
- **Client, IndexedDB (untrusted; server re-validates everything on replay):**
  - `draft:{user_id}:{resume_id}` → `{schema_version, content_hash, savedAt,
    base_version, payload(enc)}` (the crash safety net).
  - `outbox` → ordered entries `{id(seq), user_id, resume_id, base_version,
    patch(enc), idempotency_key, created_at, attempts, last_error}`.
  - `quarantine` → records that failed integrity, with reason + timestamp.
- No other server tables; drafts reuse the ui-revamp `useDraft` primitive
  (extended with hash + encryption).

## Security

Threat model & mitigations:
| Threat | Mitigation |
|---|---|
| SSE auth bypass / cross-user chunk leak | streams require a valid session; tasks keyed by `(user_id, request_id)`; a stream only ever emits its owner's data; session re-checked, 401 mid-stream closes it |
| Sensitive data cached by SW | never cache auth/OAuth/CSRF/api-key/mutation responses; cache scoped per origin; cleared on logout and on different-user detection |
| Local drafts readable on shared machine | encrypt draft/outbox at rest (WebCrypto); clear on logout; namespaced per user |
| Outbox / draft tampering (IndexedDB is user-writable) | server re-validates ownership + version + CSRF on every replay; client store is untrusted; integrity hash detects corruption/tamper → quarantine |
| Cross-account local data mixing | all local stores + cache keys namespaced by `user_id`; cleared on user switch |
| Cancel abuse / task exhaustion (DoS) | per-user concurrent-stream cap + max lifetime + heartbeat reaper; rate-limited stream starts (ADR-8) |
| Retry storm from many clients hammering a recovering backend | jittered backoff + capped attempts + client circuit breaker; server rate limits + `Retry-After` |
| Idempotency-key replay/collision | keys are client-random, namespaced per user, short TTL, and only dedupe identical operations; server still enforces version CAS |
| Conflict-payload data leak | 409 body contains only the user's own resource |
| Forged BroadcastChannel messages from another origin | BroadcastChannel is same-origin only; leader authority is backed by the same-origin Web Lock, not by trust in messages |
| Stale SW serving old/foreign data after logout or deploy | versioned cache + activate-time prune + logout clear + reachability probe + user-scoped keys |

## Reliability & concurrency

- **Streaming:** bounded lifetime, heartbeats, auto-close on disconnect;
  cancellation idempotent; tasks reaped.
- **Autosave:** single in-flight + trailing coalesce; idempotent PATCH
  (idempotency key + version CAS); jittered backoff; circuit breaker prevents
  storms; durable draft written before each network attempt.
- **Offline sync:** ordered, idempotent op-log replay via version CAS; a 409
  pauses that resource and raises conflict; partial failures retained; outbox
  bounded.
- **Concurrency:** version CAS is atomic (single-row conditional UPDATE) so
  concurrent writers can't both win; multi-tab coordination (Web Locks) prevents
  duplicate/interleaved local writes; save fan-out keeps tabs' base versions
  fresh.
- **Idempotency:** every retriable mutation carries an idempotency key (§4.2);
  replays are deduped; applying an op twice cannot corrupt state.

## Performance & scalability

- SSE avoids polling; first-token target < 2s. Autosave debounced + coalesced to
  minimize write volume; circuit breaker sheds load during incidents. SW cache
  improves repeat loads and enables offline reads. Version CAS is an indexed
  single-row conditional update. Outbox replay is batched and ordered. Local
  integrity hashing is O(content) and off the typing path (runs on debounce, in a
  worker where beneficial).

## Accessibility

- Streaming output in an `aria-live="polite"` region so screen readers announce
  progress. All status chips (offline, saving, retrying, conflict, degraded)
  carry text + SR labels, not color alone. The conflict diff is keyboard
  navigable with clear focus order and labeled choices. RecoveryBanner is
  focus-managed. All motion honors `prefers-reduced-motion`.

## Frontend

Hooks/controllers: `useStream`, `useAutosave`(`SaveController`),
`useOfflineSync`(`SyncController`), `TabCoordinator`, SW registration + update
prompt, conflict modal with diff, enhanced RecoveryBanner, `DegradationBanner`,
status chips. Controllers are transport-injected pure state machines for
testability. Mobile: status/interactions adapted; unload-flush + reachability on
mobile lifecycle events.

## Observability

- Metrics: `stream_first_token_ms`, `stream_cancel_total`, `stream_reaped_total`,
  `stream_active_gauge`, `autosave_conflict_total`, `autosave_retry_total`,
  `autosave_breaker_open_total`, `offline_sync_total{result}`,
  `outbox_depth_gauge`, `sw_cache_hit_ratio`, `quarantine_total{reason}`,
  `recovery_offered_total`/`recovery_accepted_total`.
- Alerts: conflict/sync-error spikes, breaker-open spikes (backend incident
  signal), rising outbox depth (sync stuck), quarantine spikes (client
  corruption/bug), reaper backlog. Structured logs carry `request_id`+`user_id`
  (never content/secrets, ADR-11).

## Deployment

- Flags: `STREAMING_AI`, `OFFLINE_SUPPORT`, `ADVANCED_AUTOSAVE` (independent) —
  this spec's ADR-14 free/premium toggles; profile changes are config values, not
  code paths.
- Migration: `resumes.version` (forward + reversible; default 1 backfilled).
- SW: versioned, safe-update (no destructive `skipWaiting` mid-edit), a shipped
  **unregister/kill-switch** path so `OFFLINE_SUPPORT=off` cleanly removes the SW
  and caches.
- Rollout: migrate → deploy flags-off → enable autosave → enable streaming →
  enable offline (canary each). Rollback: flags off (SW unregisters, autosave
  falls back to local-draft-only, streaming falls back to non-stream); the
  `version` column is harmless if unused.

## Disaster recovery

- Server data is covered by the P1/ADR backup + RPO schedule; `resumes.version`
  is part of normal backups. Client-local durability (draft/outbox) is the
  first-line recovery for in-flight edits and is intentionally device-local.
- Runbook: force-close a stuck stream registry entry; drain/inspect a user's
  server-visible conflict backlog via metrics; guide a user through
  quarantine export; disable a misbehaving SW fleet via the kill-switch flag;
  re-run migration rollback. RTO/RPO inherit the program defaults; no P4-specific
  server datastore is added beyond one column.

## Correctness Properties

### Property 1: No write ever silently overwrites a newer version

**Validates: Requirements 3.1, 3.3, 3.4**

Every write is an atomic version CAS; a stale base yields 409 with the current
version+data, and the only path by which local content replaces a newer server
version is an explicit user "keep mine" that re-bases and writes a fresh version.
Two concurrent same-base writes cannot both succeed.

### Property 2: No data loss under refresh, crash, close, sleep, or offline

**Validates: Requirements 2.1, 4.5, 5.1, 6.2, 9.1, 9.2, 9.3, 9.4, 9.5**

Every edit is written to the durable local draft (and, offline, the outbox)
before/independently of any network attempt, so at all times either the server
holds the latest accepted content or the durable local store does; on reload the
draft/outbox is reconciled to the server through the conflict path. Loss is
bounded to sub-debounce keystrokes on an uncoordinated crash.

### Property 3: Cancellation aborts the provider call and never mutates state

**Validates: Requirements 1.2, 1.4**

Cancelling a stream aborts the server-side task (propagating to the provider) and
leaves persisted state unchanged; streamed output is a preview and is persisted
only via explicit acceptance.

### Property 4: Retries and replays are storm-safe and idempotent

**Validates: Requirements 4.2, 4.4, 6.3**

Transient failures retry with jittered backoff under a capped count and a circuit
breaker that stops hammering a failing backend; every retriable mutation carries
an idempotency key + version, so a replayed or duplicated operation is deduped
and cannot corrupt state.

### Property 5: The service worker never leaks or serves unsafe data

**Validates: Requirements 8.5, 9.12**

The SW never caches auth/OAuth/CSRF/api-key/mutation responses; caches are
per-origin, versioned, user-namespaced, cleared on logout and different-user
detection; a reachability probe prevents a stale cached shell from presenting old
data as live. SSE streams never emit another user's chunks.

### Property 6: Multiple tabs cooperate and cannot corrupt local state

**Validates: Requirements 7.1, 7.2, 7.3, 7.5**

A single leader tab (Web Lock) owns autosave/flush and re-elects on close/crash;
draft/outbox writes are lock-guarded so tabs cannot interleave into a corrupt
record; successful saves fan out so followers refresh their base version and
avoid self-inflicted conflicts.

### Property 7: Corrupt or evicted local data never poisons live state

**Validates: Requirements 5.3, 8.1, 8.4, 9.9, 9.10**

Every durable record is integrity-checked (hash + schema version) on read; a
failure quarantines the record instead of loading it; storage
quota/unavailable/eviction is detected and degraded gracefully, and a missing
draft is never fabricated into a false "restored" state.

## Error Handling

- Standard envelope (ADR-7). Streaming `error` event → transparent fallback to
  the non-stream path; 401 mid-stream → close + route to login. Autosave:
  network/5xx/429 → jittered backoff (never blocks typing), breaker opens on
  repeated failure; 409 → conflict modal. Offline: failed replays retained with a
  recovery path; ordered replay pauses on conflict. Storage errors → warn +
  degrade. Corruption → quarantine. All user-supplied strings are validated;
  no content/secrets in logs.

## Testing Strategy

- **Unit:** stream token accumulation + cancel + fallback; `SaveController`
  debounce/coalesce/idempotency/backoff-jitter/circuit-breaker transitions;
  version-CAS logic; outbox ordered replay + idempotency; conflict merge (disjoint
  vs overlapping); integrity hash + quarantine; `TabCoordinator` leader
  election/re-election; encryption round-trip; reachability probe logic.
- **Integration:** streaming happy/cancel/fallback + task cap + reaper; PATCH
  version match/mismatch (409) + idempotency-key dedupe; SSE requires session +
  cross-user isolation; ownership 404; unload-flush endpoint.
- **E2E:** stream a cover letter + cancel; edit offline → reconnect → ordered
  sync; two-tab concurrent edit → conflict modal → each resolution; leader tab
  close → follower takes over; crash/refresh → recovery; deploy-mid-edit → safe
  SW update + draft preserved; corrupted draft → quarantine banner.
- **Security:** SSE cross-user isolation; SW never caches auth; outbox/draft
  tamper re-validation; stream cap/timeout; local encryption + logout clear;
  per-user namespacing.
- **Performance/scale:** first-token latency; autosave write rate under rapid
  typing; SW hit ratio; outbox replay throughput; breaker behavior under a
  simulated backend brownout (retry-storm prevention).
- **Failure-matrix:** an explicit test per Requirement 9 scenario asserting
  recoverability + user-informed + no corruption.
- **A11y:** streaming live-region announcement; status SR labels; keyboard
  conflict-diff nav; reduced-motion. **Mobile:** lifecycle flush + reachability +
  status UI.

## Self-critique loop

**Round 1**
- *Architect:* Full offline + CRDT is over-scope and high-risk. **Fix:** scope to
  read-offline + queued edits + explicit conflict resolution; three-layer
  durability model instead of CRDT (§Overview, §Offline, §Conflict).
- *Security:* SW could cache tokens/keys; local drafts readable on shared
  machines. **Fix:** never cache auth/OAuth/CSRF/keys; encrypt drafts at rest;
  clear on logout; user-namespaced stores (§Security, R8).
- *SRE:* Unbounded streams exhaust tasks/spend. **Fix:** per-user cap + max
  lifetime + heartbeat reaper (§Streaming, R1.5).

**Round 2**
- *Backend:* Autosave without concurrency control silently overwrites other
  devices. **Fix:** atomic version CAS + typed 409 conflict flow (§Conflict, R3).
- *Frontend:* Rapid autosave hammers the server. **Fix:** debounce + single
  in-flight + trailing coalesce (§Autosave, R4.2).
- *SRE (High):* On a backend brownout, every client retrying in lock-step causes
  a **retry storm** that prevents recovery. **Fix:** exponential backoff **with
  jitter** + capped attempts + **client circuit breaker** + honor `Retry-After`
  (§Autosave R4.4, Property 4).
- *Backend (High):* Retries without idempotency can double-apply. **Fix:**
  idempotency key on every retriable mutation + server dedupe (§Data Models, §4.2).

**Round 3**
- *QA (High):* Two tabs both autosaving the same resource cause self-inflicted
  409 storms and can interleave IndexedDB writes into corruption. **Fix:**
  `TabCoordinator` leader election (Web Locks) + save fan-out + draft/outbox
  mutex (§Multi-tab, R7, Property 6).
- *Frontend (High):* `navigator.onLine` lies (captive portal / backend down) →
  false "synced". **Fix:** a real reachability probe against `/health` as the
  source of truth (§Offline R2.6).
- *QA:* A corrupted or half-written draft could be loaded and overwrite good
  work, or crash the editor. **Fix:** integrity hash + schema version → quarantine;
  never load corrupt data (§Local-data-safety, R5.3/R8.1, Property 7).

**Round 4**
- *SRE (High):* A deployment while a user is mid-edit can activate a new SW and an
  incompatible API, corrupting or dropping in-flight work. **Fix:** versioned SW
  with safe-update (no destructive `skipWaiting` mid-edit) + reload prompt at a
  safe point + API-version-skew Safe-Mode + draft preserved across reload
  (§SW update flow, R9.8/R9.12).
- *QA:* IndexedDB can be evicted or unavailable (private mode / quota). **Fix:**
  detect eviction (don't fabricate "restored"); catch quota/unavailable → degrade
  to memory-only-with-warning (§Local-data-safety, R8.4/R9.9, Property 7).
- *Product:* Users won't know what actually works offline. **Fix:** explicit
  offline scope + named degradation levels + `DegradationBanner` (§Offline R2.4,
  R6.4).

**Round 5**
- *AI/Cost:* Streamed and cancelled generations could escape cost accounting.
  **Fix:** `done`/cancel report token usage into the P3 cost-guard (§Streaming,
  R1.7).
- *Backend:* Session could expire mid-stream and hang the UI. **Fix:** 401
  mid-stream closes the stream, emits terminal error, routes to login (R1.6).
- *QA:* Recovery could show conflicting prompts (draft + failed outbox + quarantine
  at once). **Fix:** a single coherent recovery surface; deterministic,
  non-destructive default (R5.4/R5.5).

**Round 6 (final): "Shipping to hundreds of thousands — what still bites?"**
Residuals, all explicitly accepted:
(a) No CRDT/real-time co-editing — concurrent edits resolve via explicit conflict
UI, and field-merge falls back to keep/latest when edits overlap.
(b) Offline AI is intentionally disabled (network-only).
(c) On an uncoordinated tab/browser crash, loss is bounded to keystrokes since
the last debounce tick (sub-second to ~1.2s) — accepted as the durability floor.
(d) Client-local durability (draft/outbox) is device-local by design; a
destroyed device relies on the last server-synced version (that's the point of
autosave-to-server). No open critical/high/medium issues.
