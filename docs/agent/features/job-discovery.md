# Job Discovery & Recommendations

AI-assisted job discovery: turn a user's resume into a ranked list of live job
listings, sourced from fixed boards (via JobSpy) and user-defined custom sites
(via LLM-guided extraction), then hand a chosen listing off to the resume
tailoring flow.

The whole feature ships **OFF** behind the `JOB_DISCOVERY` kill-switch and its
scraper/browser dependencies are an **optional** install group. A default
deployment neither exposes the endpoints nor requires the extra packages.

## Installation (optional dependencies)

The connectors that scrape boards and drive a headless browser are pinned in an
optional `job-discovery` extra in `apps/backend/pyproject.toml`. The base app
imports and boots **without** this group installed — connectors lazy-import
their scraper deps, so nothing is loaded until a recommend call actually runs a
connector.

Install the extra only on deployments that enable the feature:

```bash
uv sync --extra job-discovery
# or:
pip install '.[job-discovery]'
```

The extra pins:

| Package | Purpose |
|---|---|
| `python-jobspy` | Fixed-board scraping (Indeed / Naukri / LinkedIn) |
| `crawl4ai` | LLM-guided extraction for custom site recipes |
| `patchright` | Patched Playwright driver for the stealth fetch lane |
| `camoufox` | Optional hardened Firefox fetcher for the stealth lane |

## Kill-switch & settings

All settings live in `app/config.py` and are documented in
`apps/backend/.env.example`. Defaults are off / low.

| Setting | Default | Meaning |
|---|---|---|
| `JOB_DISCOVERY` | `false` | Master kill-switch. While off, **every** discovery route returns `404` (indistinguishable from a deployment where the surface does not exist — no capability leak) and the orchestrator refuses to run. |
| `JOB_DISCOVERY_JOBSPY_SITES` | `indeed` | Comma-separated JobSpy board slugs queried on the fast lane. Kept small to limit the outbound scraping surface. |
| `JOB_DISCOVERY_CACHE_TTL_SECONDS` | `3600` | TTL for the content-addressed search-result cache. |
| `JOB_DISCOVERY_MAX_RESULTS` | `50` | Max listings returned from a single recommend call. |
| `JOB_DISCOVERY_MAX_RECIPES` | `20` | Max site recipes a single user may own. |
| `JOB_DISCOVERY_STEALTH_MAX_CONCURRENCY` | `1` | Concurrency cap for the stealth (headless-browser) fetch lane. |

To enable the feature: install the extra (above), set `JOB_DISCOVERY=true`, and
restart the backend.

## API surface

Mounted under `/api/v1/discovery` (see `app/routers/discovery.py`). Every route
is gated by the kill-switch.

| Method & path | Purpose | Auth |
|---|---|---|
| `POST /discovery/recommend` | Run discovery → ranked recommendations | verified user |
| `GET  /discovery/recommend/{id}` | Last cached recommendations if fresh | effective user |
| `POST /discovery/tailor` | Hand a listing off → create a job | verified user |
| `GET  /discovery/recipes` | List the user's site recipes | effective user |
| `POST /discovery/recipes` | Create a recipe (validated) | verified user |
| `PUT  /discovery/recipes/{slug}` | Update a recipe | verified user |
| `DELETE /discovery/recipes/{slug}` | Delete a recipe | verified user |

Endpoints that trigger an LLM call (`recommend`, `tailor`) also carry the LLM
rate limiter.

## Pipeline

The orchestrator (`app/job_discovery/service.py`) runs a recommend request as:
kill-switch gate → resume ownership check → LLM query generation (deterministic
fallback if the LLM is unavailable) → content-addressed cache lookup →
connector fan-out with partial success (a single failing source is *collected,
never raised*) → normalize + dedup → rank against the resume → cache store.

Results carry a per-source `sources` report and a `degraded` flag (true when
the query fell back or any source failed) so the UI can show a partial-results
banner.

Design reference: `.kiro/specs/job-discovery/design.md`. Requirements: 10.5.
