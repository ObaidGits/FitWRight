# FitWright MCP Server

FitWright exposes an [MCP](https://modelcontextprotocol.io) (Model Context
Protocol) server so AI clients such as Claude Desktop and Cursor can work with
your FitWright account: list resumes, track job applications, schedule
follow-up reminders, run job searches, and generate cover letters and interview
prep — with the same billing and the same per-user data isolation as the web
app.

## Architecture

The MCP server is a thin layer over the app's existing business logic, not a
parallel implementation. Tools call the same database service functions and the
same REST handler functions the web app uses, so behavior can never drift:

```
External MCP client (Claude Desktop, Cursor, ...)
        |
        | HTTPS + JSON-RPC, "Authorization: Bearer fw_..."
        v
FastMCP streamable-HTTP transport  (stateless, one JSON-RPC round-trip
        |                          per POST, mounted at /api/v1/mcp/)
        v
FitWrightTokenVerifier             (app/mcp/auth_verifier.py)
  - validates the bearer token against the mcp_tokens table
  - publishes the token OWNER as the caller; every tool scopes
    its queries to that user
        |
        v
MCP tool layer (app/mcp/tools/*)   - thin wrappers, no business logic
        |
        v
Existing services / REST handlers (app/database.py, app/routers/*,
app/applications/*, app/scheduling/*, app/ai_metered.py)
```

Key property: the tool layer contains **no business rules of its own**. Read
tools call `db.*` service methods scoped to the caller's user id; write tools
call the exact seams the REST handlers use (`create_manual_application`,
`db.update_application`, the scheduling service); AI tools call the REST
endpoint functions themselves under the same rate-limit and billing guards
their routes declare.

## Enabling the server

The whole MCP surface ships **off** (`MCP_ENABLED=false`). Set:

```
MCP_ENABLED=true
# Optional: tokens expire after N days (0 = no expiry, the default)
MCP_TOKEN_TTL_DAYS=0
```

When the flag is off, neither the token-management API nor the server mount
exists — requests to `/api/v1/mcp` return 404, so a disabled deployment leaks
nothing about the feature.

The server is part of the main FastAPI app (no separate process, no extra
port): it is available at

```
https://<your-deployment>/api/v1/mcp/
```

Job-search tools additionally follow the `JOB_DISCOVERY` kill-switch: with
`JOB_DISCOVERY=false` the two search tools refuse with a `job_discovery_disabled`
error, exactly like the REST discovery routes 404.

## Token lifecycle

MCP access is by **personal bearer token**, created in the web app:

1. **Create** — Settings → *MCP / API access* → enter a client name (e.g.
   "Claude Desktop") → *Create*. The raw token (it starts with `fw_`) is shown
   **exactly once**. Copy it now; it is never displayed again.
2. **Store** — paste it into your MCP client's config (examples below).
   Tokens are long-lived, revocable, and (if `MCP_TOKEN_TTL_DAYS` is set on the
   server, or the deployment's default applies) may expire.
3. **Revoke** — the same Settings page lists your tokens with their last-used
   time. *Revoke* immediately invalidates the token; clients using it lose
   access on their next request.

Notes:

- Only sha256 hashes of tokens are stored server-side. Losing the raw value
  means creating a new token — there is no recovery path, on purpose.
- Token management (`/api/v1/mcp/tokens`) is browser-authenticated (session +
  CSRF), not bearer-authenticated. A bearer token cannot mint more tokens.
- Token creation and revocation are written to the admin audit log
  (`mcp_token.created` / `mcp_token.revoked`).

## Client configuration

The endpoint is remote HTTP with bearer auth, so local clients need a bridge.
Both examples below use [`mcp-remote`](https://www.npmjs.com/package/mcp-remote).

### Claude Desktop

`claude_desktop_config.json` (Claude → Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "fitwright": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://your-fitwright-deployment.example.com/api/v1/mcp/",
        "--header",
        "Authorization: Bearer fw_YOUR_TOKEN_HERE"
      ]
    }
  }
}
```

### Cursor

`~/.cursor/mcp.json` (or `.cursor/mcp.json` in a project):

```json
{
  "mcpServers": {
    "fitwright": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://your-fitwright-deployment.example.com/api/v1/mcp/",
        "--header",
        "Authorization: Bearer fw_YOUR_TOKEN_HERE"
      ]
    }
  }
}
```

Replace the URL with your deployment's origin and the token with the `fw_...`
value from Settings. For a local dev server the URL is
`http://localhost:8000/api/v1/mcp/`.

## Tool reference

All tools act **only on the token owner's data**. Tools that can fail on
unknown ids always say so in the error and point at the listing tool that
produces valid ids.

### Resumes

| Tool | Params | Cost |
|---|---|---|
| `list_resumes` | — | free |
| `get_resume` | `resume_id` (required) | free |

`list_resumes` returns lightweight summaries (id, filename, title, ATS score,
status, updated date) — never resume content. `get_resume` returns one
resume's full content, parsed data, and any generated deliverables.

### Job applications

| Tool | Params | Cost |
|---|---|---|
| `list_applications` | — | free |
| `get_application` | `application_id` (required) | free |
| `get_apply_queue` | — | free |
| `check_duplicate` | `company`, `role` (both required) | free |
| `add_application` | `job_description` (required), `company`, `role`, `resume_id` | free |
| `update_application_status` | `application_id`, `status` (both required) | free |

- `list_applications` groups cards into the seven status columns (saved,
  applied, no_response, response, interview, accepted, rejected); every column
  is always present.
- `check_duplicate` is **advisory**: it reports whether a live application to
  the same company AND role exists (case-insensitive, within the cool-off
  window). `add_application` never blocks on it — same as the web app.
- `add_application` creates a tracker card from a pasted job description in the
  "applied" column. `resume_id` is required; `company`/`role` fall back to a
  best-effort extraction from the description when omitted.
- `update_application_status` accepts any of the seven statuses; any transition
  is allowed.

### Reminders

| Tool | Params | Cost |
|---|---|---|
| `list_reminders` | `application_id` (required) | free |
| `create_reminder` | `application_id`, `remind_at` (both required), `note` | free |

`remind_at` is an ISO-8601 datetime (no timezone = UTC). `note` is optional,
max 1000 characters. Per-application reminder limits match the web app. If the
reminders feature is disabled on the deployment, both tools refuse with
`reminders_disabled`.

### AI generation (charges credits)

| Tool | Params | Cost |
|---|---|---|
| `generate_cover_letter` | `resume_id` (required), `regenerate` | credits — same price as in-app |
| `generate_interview_prep` | `resume_id` (required), `regenerate` | credits — same price as in-app |

- Both require a **tailored** resume (one produced by tailoring to a job
  description); pass an id from `list_resumes`.
- A previously generated deliverable is returned as-is unless `regenerate` is
  true — reuse costs nothing.
- Billing parity: an MCP call runs the same per-user LLM rate limit
  (`LLM_RATE_PER_MIN_USER`) and the same `metered_ai_call` ledger entry as the
  web app — one feature, one price, one ledger row, whether the call came from
  the browser or an MCP client. Users on their own provider key are charged
  nothing, same as in-app. A zero balance is refused (`insufficient_credits`)
  before any work runs.

### Job search

| Tool | Params | Cost |
|---|---|---|
| `start_job_search` | `query` (required), `sites` | free (subject to the daily plan ceiling) |
| `get_job_search_status` | `search_id` (required) | free |

- `start_job_search` returns in milliseconds with a `search_id`; the scrape
  (15–35s) continues in the background. Poll `get_job_search_status` until
  `status` is `done` or `failed`.
- Rate/cap guards are the REST ones, in the REST order: the 10-second cooldown
  between searches (`http_429`), one search per user at a time (a second start
  returns the running search's id with `already_running: true` and does not use
  up a daily search), and the daily plan ceiling (`search_limit_reached`,
  resets at midnight UTC — not credit-purchasable).
- Status `expired` means the server no longer knows the search — an unknown id,
  a restart, or someone else's id. It leaks nothing.
- Searches never cost credits.

## Security model

- **Bearer-only on the mount.** `/api/v1/mcp` authenticates exclusively via
  `Authorization: Bearer fw_...`. Browser session cookies are not accepted
  there, and the browser CSRF machinery does not apply (no cookies are read).
- **Mount-only tokens.** The reverse holds everywhere else: MCP tokens are
  ignored by all REST routes (including `/api/v1/admin/*` and token management
  itself). `AuthMiddleware` resolves cookie sessions only, never bearer
  headers, so a leaked MCP token grants exactly the MCP tool surface and
  nothing more.
- **Sha256 at rest.** The database stores only `sha256(token)`; the raw value
  exists solely in the client's config and the one-time creation response.
- **Per-user isolation.** The verifier resolves the token to its owner and
  every tool scopes its queries to that user id — the same guarantee REST gets
  from `get_effective_user_id`. Cross-user ids read as not-found/empty and
  never confirm or deny another user's data.
- **Billing parity.** AI tools publish the caller on the request-scoped user-id
  context before running, so an own-key user's call resolves *their* provider
  key — one user's key never serves another's calls — and charges post to the
  caller's ledger under the same feature name as in-app generation.
- **Fail closed.** If token verification cannot reach the database, requests
  are rejected (401), never waved through, and the raw token is never logged.
- **Hardened tool surface.** Red-team integration tests
  (`tests/integration/test_mcp_redteam.py`) continuously attack the live
  mount: cross-user ids, malformed/injection bearer strings, privilege
  escalation via admin routes, oversized/mistyped arguments, business-rule
  bypass attempts, response/log leakage, and the cookie/bearer boundary.

## Limitations

- **No auto-apply or form submission.** The tool surface deliberately excludes
  auto-apply, employer-site login, and answer submission. The MCP tools read
  and organize; humans apply.
- **No token scopes.** A token grants the owner's full MCP tool surface.
  Granularity is per-user, per-token revocation. (If finer-grained access is
  ever needed, revoke and re-issue rather than share tokens.)
- **No OAuth discovery metadata.** Bearer-only clients never fetch OAuth
  discovery documents, so none is served. Clients that require a full OAuth
  flow are not supported; use a bridge like `mcp-remote` with a header token.
- **Single deployment URL.** One server per deployment; the client config
  points at one origin.
- **Sessionless transport.** Each POST is a complete JSON-RPC round-trip —
  no `Mcp-Session-Id` sessions, so any state lives in the user's data, not the
  connection.

## Adding a tool

Tools live in `app/mcp/tools/`, one module per area, following the existing
pattern:

1. **Resolve the caller** — take the access token and read the owner:

   ```python
   from fastmcp.dependencies import CurrentAccessToken
   from fastmcp.server.auth import AccessToken
   from app.mcp.server import get_mcp_instance
   from app.mcp.tools._context import current_user_id

   mcp = get_mcp_instance()

   @mcp.tool
   async def my_tool(some_id: str, token: AccessToken = CurrentAccessToken()) -> dict:
       user_id = current_user_id(token)
       ...
   ```

   `current_user_id` (in `app/mcp/tools/_context.py`) is the only way a tool
   learns its caller — never accept a user id as a tool argument.

2. **Call the existing seam, don't copy it.** Read tools call `db.*` service
   methods with `user_id`; write tools call the same service function the REST
   handler uses; anything with guards (rate limits, billing, kill-switches)
   calls the REST handler itself under the same guards in the same order — see
   `_billed_generation` in `app/mcp/tools/ai.py` and `_require_job_discovery_enabled`
   in `app/mcp/tools/search.py` for the pattern. If the logic you need is
   inline in a REST handler, extract it to a shared seam first (as
   `app/applications/manual.py` was for manual add).

3. **Fail with actionable one-line errors.** Raise `ValueError` with a stable
   machine-readable code prefix (`resume_not_found: ... Call list_resumes to
   get valid resume ids.`). Never let a stack trace or a 500 reach the client.

4. **Import the module for registration.** `app/mcp/server.py` imports
   `app.mcp.tools`, which pulls in every tool module; a new module under
   `app/mcp/tools/` must be added to that package's `__init__.py` imports if it
   is not imported elsewhere.

5. **Test it as a real client.** Mirror `tests/integration/test_mcp_tools_*.py`:
   one JSON-RPC POST per test through the mounted app, plus a cross-user case
   (another user's token + your ids → not found) and a schema check in
   `tools/list`. Add hostile-input cases to
   `tests/integration/test_mcp_redteam.py` if the tool parses anything.
