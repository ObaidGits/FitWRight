# MCP Reliability Hardening — Report

**Date:** 2026-09-06
**Scope:** The MCP surface's failure behavior under real-deployment infrastructure failures: database outage (mid-session and at auth time), LLM provider outage/hang, client disconnect mid-generation, background search worker death, runtime settings flips, timeout discipline, and log hygiene under failure.
**Artifacts:**
- New suite: `apps/backend/tests/integration/test_mcp_reliability.py` (16 tests, all green, run 3x for timing stability)
- Surgical fixes: `apps/backend/app/mcp/tools/_context.py` (new `db_fail_closed` decorator), `apps/backend/app/mcp/tools/reminders.py` (SchedulingError classification), plus `@db_fail_closed` applied to all 14 tools in `resumes.py`, `applications.py`, `reminders.py`, `ai.py`, `search.py`
- No REST behavior was weakened; the decorator is MCP-layer only.

---

## Method

Every injection follows the same spine, taken from `test_mcp_data_integrity.py`:
drive the real mounted MCP app (`mcp_app(True)`) over `httpx.AsyncClient` + ASGI transport with a real bearer token, inject the failure at the **infrastructure boundary** (the db layer, the token service, the provider call — never FitWright logic), then assert **both** the response shape (tool error over HTTP 200, never a 500, never a traceback, never partial data) **and** the post-recovery behavior (next call succeeds; state not poisoned).

Only externals are mocked: the LLM provider (`resumes_router.generate_cover_letter` — the same seam `test_mcp_tools_ai.py` uses), the job-board scrape (`discovery._execute_manual_search` — the seam `test_background_search.py` uses), and the db/token layers via proxies (`BlackoutDb`, `GatedDb`) that raise/park at the exact call sites a real outage would.

The DB outage is simulated with a `BlackoutDb` proxy installed at every `db` binding the tools reach: `app.database.db` (per-call body resolution), `app.applications.submissions.db` (queue/duplicate tools), and `app.routers.resumes.db` (import-time binding used by the billed AI endpoints). Recovery is modeled in the strongest form: the **same proxy object** is healed (`active = False`), so the tests prove nothing was memoized.

## Injection 1 — DB outage mid-session

**Setup:** Valid token, seeded resume/job/application. `BlackoutDb` raises `OperationalError("server closed the connection unexpectedly")` on every db method once activated.

**Observed (before fix):** unhandled `OperationalError` reached FastMCP's generic handler and the client received the full SQLAlchemy driver internals: `(builtins.Exception) server closed the connection unexpectedly [SQL: SELECT resumes.id FROM resumes] ...`. `list_reminders` was worse — its `getattr(exc, "code", None)` duck-typing matched `SQLAlchemyError.code`, so a DB outage rendered as `invalid_reminder: <sqlalchemy internals>`.

**Observed (after fix):**
- Reads (7 tools: `list_applications`, `get_application`, `list_resumes`, `get_resume`, `get_apply_queue`, `check_duplicate`, `list_reminders`): coherent tool error `storage_unavailable: The database is temporarily unavailable. Please retry in a moment.` over HTTP 200; no `SQL:` fragments, no traceback; `proxy.failed_calls >= 7` proves the outage was exercised.
- Writes (`add_application`, `update_application_status`, `create_reminder`, `generate_cover_letter`): fail closed with the same coherent code; afterwards no new cards, no orphan jobs, no phantom reminders, the refused status move did not mutate the row, ledger empty, reserved 0 — **never charged for the operator's outage**.
- Recovery: healing the same proxy object → the very next `list_applications` succeeds and a subsequent `add_application` lands (proves per-call db resolution, no poisoned binding).
- The full traceback is logged exactly once server-side via `logger.exception` in `db_fail_closed`; the client never sees it.

**Verdict: PASS** (after two surgical fixes, below).

## Injection 2 — DB outage at auth time, mid-session

**Setup:** Token verifies successfully twice, then `verify_token` starts raising `OperationalError`.

**Observed:** HTTP **401** while the DB is down — fail closed, never a bypass, never a 500. After healing, the **same token** authenticates immediately (no stuck verifier state). Server log records `"MCP token verification failed"` once.

**Verdict: PASS** (no fix needed; the verifier was already fail-closed — this pins it against regression).

## Injection 3 — LLM provider outage

**Setup:** Wallet funded (`price + 100`); provider raises `TimeoutError` (timeout path) or `litellm.APIConnectionError` (connection path) inside `generate_cover_letter` / `generate_interview_prep`; rate limits left at their real values.

**Observed:**
- `TimeoutError` → tool error containing `llm_timeout` / "did not respond in time"; `APIConnectionError` → `llm_provider_unavailable`. No traceback in either.
- Wallet intact, `reserved == 0` (hold released), no half-written deliverable stored, and exactly **one** zero-charge ledger row `[("cover_letter", 0, "failed")]` — the exact contract `test_mcp_data_integrity.py` #5 pinned.
- Rate-limit state not poisoned: the failure does not render as `rate_limited`, and after the provider heals the next call generates and bills exactly once (`wallet0 - price`, single `(price, "ok")` row).

**Verdict: PASS** (no fix needed; the shared REST/MCP billing context already did the right thing — this pins it).

## Injection 4 — Slow LLM + client disconnect

**Setup:** Provider parks on an `asyncio.Event` until the test releases it (a scaled stand-in for a 30s provider). Two tests:

(a) *Slow provider blocks (documented):* the tool call blocks until the provider answers — there is deliberately no internal deadline that would silently abort a paid generation — then settles exactly once (one `(price, "ok")` row, hold released, wallet `wallet0 - price`).

(b) *Client disconnects mid-generation:* after the hold is confirmed taken, the request task is cancelled. Test runs the app's lifespan **on the calling loop** (`_lifespan_on_this_loop` — the production shape where uvicorn runs one loop; `TestClient`'s portal-thread lifespan is a cross-loop artifact that wedges teardown).

**Observed (b):** The disconnect does **not** abort the in-flight generation: the MCP stateless transport runs the tool on the session-manager's task group, not the request task, so the tool completes detached. Billing settles **exactly once at the published price**: `reserved == 0`, one ledger row `[("cover_letter", price, "ok")]`, `wallet == wallet0 - price`, the deliverable is stored, and a follow-up call (with the stored copy cleared) generates and charges exactly once more. Shutdown completes within 15s — no wedge.

**Verdict: PASS on billing coherence.** Two findings:
- **FLAGGED (upstream, mcp SDK):** on request cancellation, `mcp/server/streamable_http_manager.py::_handle_stateless_request` skips `http_transport.terminate()` (no try/finally around the yield at line ~246), leaking one transport + server task per cancelled request until session-manager shutdown. Billing never corrupts; it is a bounded per-disconnect resource leak. Recommended: pin SDK version and/or upstream fix; a FitWright-side workaround would mean patching the vendored SDK (too invasive for this pass).
- **Documented behavior:** a disconnecting client still pays the full price for provider work that ran (settled-once, no refund path). This is REST-equivalent and defensible, but worth stating in operator docs.

## Injection 5 — Search worker dies mid-scrape

**Setup:** `discovery._execute_manual_search` raises `RuntimeError("SUPER-SECRET-SCRAPE-FRAGMENT ...")` mid-run (fragment includes another user's email as a leak canary); cooldown set to 0 (the real 10s rule, compressed).

**Observed:** `get_job_search_status` reports `failed` with `error == "RuntimeError"` — class name only, **no exception text** (matches the contract `test_background_search.py:48` pins for REST), no traceback, canary absent. The dead search does not pin the single-flight slot: a new search starts immediately with a fresh `search_id`, completes, and reports `saved == 2`. Daily-cap parity: exactly **2** `job_search` counter rows burned for 2 started searches — MCP calls the same `start_manual_search` handler as REST, so burn-at-start semantics are identical (a failed search is not free — same as REST).

**Verdict: PASS** (no fix needed).

## Injection 6 — Settings flipped at runtime

**Setup (a):** An in-flight `list_applications` is parked inside its db call (via `GatedDb`); then `mcp_app(False)` builds a new app — the same reload that a real `MCP_ENABLED` flip triggers.
**Setup (b):** `JOB_DISCOVERY` flips true→false while a search is mid-scrape (worker parked on a gate).

**Observed (a):** On the new app the MCP mount 404s (no protocol trace) and the REST token-management surface (`GET /api/v1/mcp/tokens`) 404s with it. The request the old app had already accepted completes coherently (`total == 1`) — the flip does not tear the app out from under a running request.

**Observed (b):** New `start_job_search` calls are refused with the actionable code `job_discovery_disabled`; status polls are gated the same way while the flag is off (kill-switch gates the whole tool surface — no progress data leaks from a flipped deployment). The already-running search completes (`done`, `saved == 4`). After re-enabling, the finished search reads normally through the tool.

**Verdict: PASS** (no fix needed).

## Injection 7 — Timeout discipline sweep

Source-level sweep (pinned as tests so the sweep reruns forever):

| Slow path | File | Bound | Can it hang forever? |
|---|---|---|---|
| LLM provider calls (4 `acompletion` sites) | `app/llm.py` (lines ~932, 1396, 1596, 2306) | explicit `timeout=` on every call site (adaptive, provider-aware) | No — pinned by `test_every_llm_provider_call_carries_a_timeout` |
| SSE streaming relay + tailoring flow | `app/routers/resumes.py` (`asyncio.wait_for` sites ~1529, 1661, 1706) | explicit `timeout=` on every `wait_for` | No — pinned by `test_streaming_and_improve_waits_are_bounded` |
| Wedged search worker | `app/job_discovery/search_jobs.py` `_MAX_RUNTIME_SECONDS` | hard 300s runtime cap → job abandoned | No — pinned by `test_wedged_search_is_abandoned_not_eternal` |
| MCP tool layer itself | `app/mcp/tools/*.py` | no internal `asyncio.sleep` / `wait_for` at all — slowness only from bounded layers below | No — pinned by `test_mcp_tools_add_no_unbounded_waits` |
| SQLite lock contention | `app/db_engine.py` `busy_timeout` PRAGMA | bounded (5000ms) retry window | No — pinned by `test_sqlite_busy_contention_is_bounded` |
| Tool call end-to-end | (by design) | **none** — a paid generation blocks until the provider's own timeout fires | By design; the provider timeout is the bound. See Injection 4. |

**Verdict: PASS — no unbounded hang found.** One deliberate unbounded-at-the-MCP-layer path (the tool call itself) is bounded by the provider timeout one layer down; that is the correct altitude (aborting a paid generation internally would double-charge or orphan holds).

## Injection 8 — Log hygiene under failure

**Setup:** `caplog` captured across injections 1–3; canaries: the raw MCP bearer token (every outage test), a scrape fragment containing another user's email (injection 5's canary also checked client-side), and size bounds (`MAX_LOG_TEXT = 200_000` chars total, `MAX_RECORD_MESSAGE = 100_000` per record — a multi-MB traceback dump is a bug).

**Observed:** No token material in any log; the raw bearer token appears nowhere. Errors are logged once per failure via `logger.exception` (`app.mcp.tools._context` for tool-layer failures, `app.mcp.tools.applications` for the refused status move, `app.auth.*` for verify failures) — not per-retry spam. Record sizes stay within bounds. Exception text from the dead scraper never reaches the client (class name only) — cross-user leak canary absent.

**Verdict: PASS.**

---

## Findings

### Fixed (surgical, MCP layer only)

1. **DB outage leaked SQLAlchemy internals to clients** (`app/mcp/tools/_context.py`): new `db_fail_closed` decorator on all 14 tools renders any `SQLAlchemyError` as one coherent, actionable tool error — `storage_unavailable: The database is temporarily unavailable. Please retry in a moment.` — while the full traceback still lands in the server log once. Clients now get a retryable error code instead of driver internals.
2. **`list_reminders` / `create_reminder` misclassified a DB outage as `invalid_reminder`** (`app/mcp/tools/reminders.py`): the error mapping duck-typed on `exc.code`, which `SQLAlchemyError` also carries. Now `_as_scheduling_error()` checks `isinstance(..., SchedulingError)`; only real scheduling refusals (not_found/limit/invalid) map to their specific codes, everything else propagates to the fail-closed handler.

### Flagged (not fixed — too invasive for this pass)

1. **Upstream mcp SDK leak on client disconnect** (`mcp/server/streamable_http_manager.py::_handle_stateless_request`): cancelled requests skip `http_transport.terminate()`, leaking one transport + server task per disconnect until shutdown. Billing stays coherent (settled exactly once — proven), so this is a bounded resource leak, not a correctness bug. Fix belongs upstream (or in a vendored-SDK patch); recommend tracking and pinning the SDK version.
2. **Disconnect during a paid generation still charges full price** (settled-once, no refund): REST-equivalent and defensible — the provider work ran — but it should be stated in operator/user docs for the MCP surface.

## Test summary

- New suite `tests/integration/test_mcp_reliability.py`: **16 passed**, run 3 consecutive times (8.5–14s each) for timing stability — no flakes.
- Full MCP batch (`test_mcp_auth`, `test_mcp_data_integrity`, `test_mcp_e2e_workflows`, `test_mcp_mount`, `test_mcp_redteam`, `test_mcp_reliability`, `test_mcp_tokens_api`, `test_mcp_tools_ai`, `test_mcp_tools_read`, `test_mcp_tools_search`, `test_mcp_tools_write`, `tests/unit/test_mcp_token_service.py`, `test_background_search.py`): **227 passed, 1 skipped** — the two product fixes regress nothing.
- Full backend suite (`pytest tests -q`): **3976 passed, 33 skipped, 1 deselected** in 232s — baseline 3960 passed plus exactly the 16 new reliability tests; no regressions from the two product fixes.

## Concerns

- The upstream SDK disconnect leak (finding F1) is the only open reliability gap; it needs no immediate action but deserves an upstream issue and a pinned dependency version.
- `app/routers/resumes.py` binds `db` at import time (line 24) rather than per-call like the rest of the codebase. Not a bug (a real outage still raises through that binding — proven by this suite), but a consistency wart worth cleaning up someday.
