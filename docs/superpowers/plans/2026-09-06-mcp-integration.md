# FitWright MCP Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose FitWright's mature features to external MCP clients (Claude Desktop, Cursor, any AI app) as a curated, per-user-authenticated tool surface — a thin adapter over existing business logic.

**Architecture:** FastMCP 4 (`fastmcp==4.0.3`) mounted inside the existing FastAPI app at `/api/v1/mcp`, behind a `MCP_ENABLED` kill-switch. Auth = new `mcp_tokens` table (sha256-hashed bearer tokens, revocable, expirable) validated by a custom `TokenVerifier` subclass; token lifecycle managed via browser-session-authenticated REST endpoints. MCP tools call existing route handlers / service functions directly with the token's `user_id`; AI tools wrap calls in the same billing primitives (`ai_spend`, `enforce_llm_rate_limit`) that `ai_metered` uses. Zero changes to cookie/session/CSRF auth.

**Tech Stack:** Python 3.13, FastAPI 0.128.4, SQLAlchemy 2 async, alembic, `fastmcp==4.0.3` (new dep), pytest + httpx TestClient, Next.js frontend (token management UI section).

**Spec:** User prompt of 2026-09-06 (verbatim requirements embedded per-task below).

## Global Constraints

- All work in worktree `.claude/worktrees/mcp-feature`, branch `worktree-mcp-feature`. Never touch `/home/obaid/Downloads/fitwright` main tree.
- Thin adapter: tools reuse `app/routers/*.py` handler functions and `app/database.py` service methods. No duplicated business logic. Allowed reuse primitives: `ai_spend`, `start_metering`/`stop_metering`, `enforce_llm_rate_limit`, `user_has_own_key`, `AuditEvent` audit service.
- Do NOT weaken browser auth: no changes to `AuthMiddleware` CSRF logic, cookie handling, or session resolution. Bearer auth lives entirely inside FastMCP's mount.
- Do NOT expose: auto-apply, `record_submission`, extension auto-fill, anything in `BrainDecision` flow, admin endpoints.
- MCP requests execute with the token owner's `user_id` only — every tool call scopes queries via that id (same guarantee as `get_effective_user_id`).
- Kill-switch: everything MCP (token endpoints + mount) returns 404 when `MCP_ENABLED=false`. Default false.
- Billing: AI tools charge the SAME feature names as REST (`cover_letter`, `interview_prep`) through `ai_spend` — no new feature names, no second billing path.
- Token security: sha256-at-rest, raw shown exactly once at creation, log only `fw_` + first 6 chars, revocation + optional expiry, `last_used_at` throttled writes (≤1/60s per token).
- No unrelated refactors. Match existing code style (docstring-heavy, `file:line` comments citing design rules).
- Commit per task, conventional commits, `Co-Authored-By: Claude <noreply@anthropic.com>`.

## Verified codebase facts (do not re-derive)

- `AuthMiddleware` (`app/auth/principal.py:399`) resolves only the session cookie; no `Authorization` header handling exists anywhere. Anonymous requests pass through to the router.
- Route deps: `get_effective_user_id` (`app/auth/principal.py:156`), `require_verified_user_id` (`app/auth/principal.py:193`). Both read `request.state.principal` set by middleware.
- `ai_metered` (`app/ai_metered.py:82`) = DI sugar over `async with ai_spend(user_id, feature=..., has_own_key=...)` + `start_metering`/`stop_metering`. `llm_rate_limit_dep` (`app/llm_ratelimit.py:57`) = sugar over `await enforce_llm_rate_limit(user_id)`.
- Metered feature names in use: `resume_parse`, `resume_tailor`, `cover_letter`, `interview_prep`, `outreach`, `enrichment`, `discovery_recommend`, `resume_wizard`, `match_score`, `extension_draft`.
- Handlers take `user_id` as an explicit parameter — directly callable, e.g. `generate_interview_prep_endpoint(resume_id, regenerate, user_id)` (`app/routers/resumes.py:3369`).
- Reusable service calls: `db.list_applications(user_id)`, `db.get_application_detail(user_id, application_id)`, `db.update_application(user_id, application_id, updates)`, `db.list_resume_summaries(...)`, `db.get_resume(user_id, resume_id)`, `submissions.list_queue(user_id)` / `find_duplicate(user_id, company=..., role=...)` / `reorder_queue(...)` (`app/applications/submissions.py:106,131,123`).
- Async search: `search_jobs.start(user_id, query, sites, _work)` + progress via `search/progress/{search_id}` (`app/routers/discovery.py:716-770`); `_check_search_rate` (1/10s) and `_enforce_daily_search_cap` guard it.
- DB pattern: SQLAlchemy models in `app/models.py`, `create_all` locally, alembic migrations `alembic/versions/00NN_name.py` (next: `0043`). Timestamps = zero-padded UTC ISO strings. Sessions model (`app/models.py:630`) is the template for token rows.
- Audit: `get_audit_service().record(AuditEvent.<CONST>, actor_user_id=..., request_id=..., meta={...})` (`app/auth/audit.py`).
- Settings: `class Settings(BaseSettings)` (`app/config.py:445`); kill-switch pattern = `require_extension_enabled` (`app/routers/extension.py:75`) 404ing a whole router.
- Test harness: `tests/integration/conftest.py` `auth_env` + `isolated_db` fixtures; FastAPI `TestClient` used directly; per-service `reset_*` functions must be called in fixtures (see `auth_env`, lines 24-43).
- FastMCP 4 (verified via docs): `mcp.http_app(path="/")` → `api.mount("/api/v1/mcp", mcp_app)`; MUST pass `mcp_app.lifespan` (use `combine_lifespans` from `fastmcp.utilities.lifespan` with the app's existing lifespan, `app/main.py` `lifespan`); custom bearer auth = subclass `fastmcp.server.auth.TokenVerifier`, implement `async def verify_token(self, token: str) -> AccessToken | None`; tools get the token via `token: AccessToken = CurrentAccessToken()` (`fastmcp.dependencies.CurrentAccessToken`), claims dict is ours.
- MCP protocol revision 2026-07-28: streamable HTTP, sessionless. Bearer in `Authorization` header per OAuth 2.1 resource-server pattern.

---

### Task 1: Dependency, settings, package skeleton, mount behind kill-switch

**Files:**
- Modify: `apps/backend/pyproject.toml` (add `fastmcp==4.0.3` to `[project] dependencies`)
- Modify: `apps/backend/app/config.py:642` area (add MCP settings fields)
- Create: `apps/backend/app/mcp/__init__.py` (empty)
- Create: `apps/backend/app/mcp/server.py`
- Modify: `apps/backend/app/main.py` (mount + lifespan combine)
- Test: `apps/backend/tests/integration/test_mcp_mount.py`

**Interfaces:**
- Produces: `app.mcp.server.build_mcp_app() -> Starlette` (the FastMCP ASGI app); settings fields `settings.mcp_enabled: bool` (default `False`), `settings.mcp_token_ttl_days: int` (default `0` = no expiry). Mounted at `/api/v1/mcp` only when enabled.

- [ ] **Step 1: Add dependency**

In `apps/backend/pyproject.toml` `[project] dependencies`, add:

```toml
    # MCP server (Model Context Protocol, spec rev 2026-07-28). Streamable HTTP,
    # sessionless; mounted inside the FastAPI app behind MCP_ENABLED.
    "fastmcp==4.0.3",
```

Run: `cd apps/backend && uv sync 2>/dev/null || pip install -e ".[dev]" && pip install fastmcp==4.0.3`
Verify: `python -c "import fastmcp; print(fastmcp.__version__)"` → `4.0.3`

- [ ] **Step 2: Read installed fastmcp source to pin exact APIs**

Before writing code, read (this is required — docs may lag the installed version):
```bash
python - <<'EOF'
import inspect, fastmcp
from fastmcp.server.auth import TokenVerifier
from fastmcp.dependencies import CurrentAccessToken
from fastmcp.server.auth import AccessToken
print(inspect.signature(TokenVerifier.__init__))
print(inspect.getsource(TokenVerifier.verify_token))
print([f for f in dir(AccessToken) if not f.startswith('_')])
EOF
```
Note the exact `TokenVerifier.__init__` params (there may be required `base_url`/`resource_server_url` args) and `AccessToken` fields. Use these in Task 4; record them in a comment in `app/mcp/server.py`.

- [ ] **Step 3: Add settings fields**

In `app/config.py` near `single_user_mode` (`app/config.py:642`), add:

```python
    # MCP (Model Context Protocol) integration. Kill-switch pattern like
    # JOB_DISCOVERY: when False the whole MCP surface (token management and the
    # mounted server) 404s, so a disabled deployment leaks nothing about it.
    mcp_enabled: bool = False
    # Bearer-token lifetime in days for MCP access tokens. 0 = no expiry
    # (revocation is then the only kill path, same trust model as the session
    # ``remember_me`` cap absent). Positive values set ``expires_at`` at issue.
    mcp_token_ttl_days: int = 0
```

- [ ] **Step 4: Write failing mount smoke test**

`apps/backend/tests/integration/test_mcp_mount.py`:

```python
"""MCP mount availability follows the MCP_ENABLED kill-switch.

Disabled -> the mount does not exist (404, no protocol trace). Enabled -> a
POST to the streamable-HTTP endpoint speaks MCP JSON-RPC (tools/list after
initialize is the cheapest full round-trip).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings as app_settings


def _tools_list(client: TestClient, token: str):
    return client.post(
        "/api/v1/mcp/",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.asyncio
async def test_mcp_mount_absent_when_disabled(auth_env, monkeypatch):
    monkeypatch.setattr(app_settings, "mcp_enabled", False)
    from app.main import app

    with TestClient(app) as client:
        res = client.post("/api/v1/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert res.status_code == 404


@pytest.mark.asyncio
async def test_mcp_mount_speaks_protocol_when_enabled(auth_env, monkeypatch, mcp_token):
    monkeypatch.setattr(app_settings, "mcp_enabled", True)
    from app.main import app

    with TestClient(app) as client:
        res = _tools_list(client, mcp_token["raw"])
        assert res.status_code == 200
        body = res.json()
        assert body.get("result", {}).get("tools") is not None
```

(`mcp_token` fixture arrives in Task 4; until then import it as a skip — write the fixture stub now and fill it in Task 4. For this task's red run only the disabled-path test needs to pass later; expect `mcp_token` fixture to fail → that's the failing state.)

- [ ] **Step 5: Create `app/mcp/server.py` minimal version**

```python
"""The FitWright MCP server: a thin tool layer over existing business logic.

Architecture (user spec 2026-09-06):

    FitWright core/services (app/database.py, app/routers/*)
            ↓
    MCP tool layer (app/mcp/tools/*)  ← reuses handlers/services verbatim
            ↓
    FastMCP streamable-HTTP transport, mounted at /api/v1/mcp
            ↓
    External MCP client (Claude Desktop, Cursor, ...)

Auth is bearer-only inside this mount (FastMCP TokenVerifier); the browser
session/CSRF machinery is untouched and does not apply here because
``AuthMiddleware`` only resolves cookie sessions and passes anonymous requests
through.
"""

from __future__ import annotations

from starlette.types import ASGIApp


def build_mcp_app() -> ASGIApp:
    """Build the mounted MCP ASGI app. Raises if MCP is disabled."""
    from fastmcp import FastMCP

    from app.config import settings

    if not settings.mcp_enabled:
        raise RuntimeError("MCP_ENABLED is false; build_mcp_app must not be mounted")

    from app.mcp.auth_verifier import FitWrightTokenVerifier

    mcp = FastMCP(
        "FitWright",
        instructions=(
            "Tools for the user's FitWright account: resume management, "
            "job-application tracking, reminders, cover letters, and interview "
            "prep. All data belongs to the authenticated token owner."
        ),
        auth=FitWrightTokenVerifier(),
    )

    from app.mcp import tools  # noqa: F401  (registers tools via import)

    return mcp.http_app(path="/")
```

- [ ] **Step 6: Wire the mount + combined lifespan in `app/main.py`**

In `app/main.py`, after `install_error_handlers(app)` (`app/main.py:403`) and before the `include_router` block:

```python
# MCP mount (kill-switched). The FastMCP app needs its lifespan (session
# manager) merged with ours, or the streamable-HTTP transport never boots.
if settings.mcp_enabled:
    from app.mcp.server import build_mcp_app

    _mcp_app = build_mcp_app()
    app.mount("/api/v1/mcp", _mcp_app)
```

For the lifespan: read the existing `lifespan` context manager in `app/main.py` (near `app = FastAPI(`, `app/main.py:308`). Replace its use with:

```python
from contextlib import asynccontextmanager
from fastmcp.utilities.lifespan import combine_lifespans

# (keep the existing body as _core_lifespan, renamed)
@asynccontextmanager
async def _core_lifespan(app):
    ...  # existing body unchanged, verbatim

def _final_lifespan():
    if settings.mcp_enabled:
        from app.mcp.server import build_mcp_app
        return combine_lifespans(_core_lifespan, build_mcp_app().lifespan)
    return _core_lifespan

app = FastAPI(..., lifespan=_final_lifespan())
```

Careful: `build_mcp_app()` creates a second FastMCP instance here. Refactor so the module builds the FastMCP instance ONCE (`app/mcp/server.py` gets `get_mcp_instance()` memoized) and both the mount and the lifespan share it. Keep `mcp.http_app(path="/")` also memoized (calling `http_app` twice is not supported).

- [ ] **Step 7: Stub the auth verifier so imports resolve**

Create `apps/backend/app/mcp/auth_verifier.py`:

```python
"""Bearer-token verification for the MCP mount (filled in Task 4)."""

from __future__ import annotations


class FitWrightTokenVerifier:  # TODO(task-4): subclass fastmcp TokenVerifier
    pass
```

Create `apps/backend/app/mcp/tools/__init__.py` (empty — registered in Task 5).

- [ ] **Step 8: Run tests**

Run: `cd apps/backend && python -m pytest tests/integration/test_mcp_mount.py -v`
Expected: disabled-path test PASSES once mount wiring correct (404); enabled-path FAILS on the `mcp_token` fixture (not yet implemented — that's Task 4). Also run `python -m pytest tests/unit -x -q` expecting no import regressions.

- [ ] **Step 9: Commit**

```bash
git add apps/backend/pyproject.toml apps/backend/app/config.py apps/backend/app/main.py apps/backend/app/mcp/ apps/backend/tests/integration/test_mcp_mount.py
git commit -m "feat(mcp): mount FastMCP server behind MCP_ENABLED kill-switch"
```

---

### Task 2: `mcp_tokens` model, migration, token service

**Files:**
- Modify: `apps/backend/app/models.py` (add `McpToken` after `EmailChangeToken`, ~line 778)
- Create: `apps/backend/alembic/versions/0043_mcp_tokens.py`
- Create: `apps/backend/app/auth/mcp_tokens.py`
- Test: `apps/backend/tests/unit/test_mcp_token_service.py`

**Interfaces:**
- Produces: `McpTokenService` with `async issue(user_id: str, label: str, ttl_days: int | None) -> tuple[dict, str]` (record without hash + raw token), `async verify(raw_token: str) -> dict | None` (row with `id`, `user_id`, `label` — active only), `async revoke(user_id: str, token_id: str) -> bool`, `async list_for_user(user_id: str) -> list[dict]` (masked), `async touch(token_id: str) -> None` (throttled). Module fns `get_mcp_token_service()` / `reset_mcp_token_service()` (pattern of `app/auth/tokens.py:313-324`). Token format `fw_` + `secrets.token_urlsafe(32)`; hash = `hashlib.sha256(raw.encode()).hexdigest()`.

- [ ] **Step 1: Write failing unit tests**

`apps/backend/tests/unit/test_mcp_token_service.py` — cover: issue returns raw starting `fw_` and stores only sha256; verify accepts issued token; verify rejects unknown/revoked/expired; revoke is scoped to owner (other user's revoke → False, token still valid); list_for_user never contains `token_hash`; touch updates `last_used_at` at most once per 60s. Use the `isolated_db` fixture + `reset_mcp_token_service()` teardown, mirroring `tests/unit/test_llm.py` fixture usage. Test code sketch (write all six cases):

```python
import pytest
from app.auth.mcp_tokens import get_mcp_token_service, reset_mcp_token_service


@pytest.fixture
async def svc(isolated_db):
    s = get_mcp_token_service()
    yield s
    reset_mcp_token_service()


async def test_issue_stores_only_hash(svc, isolated_db):
    rec, raw = await svc.issue("user-1", "claude-desktop", ttl_days=0)
    assert raw.startswith("fw_") and len(raw) > 30
    assert rec["user_id"] == "user-1"
    assert "token_hash" not in rec
    assert await svc.verify(raw) is not None


async def test_verify_rejects_revoked(svc, isolated_db):
    rec, raw = await svc.issue("user-1", "x", ttl_days=0)
    assert await svc.revoke("user-1", rec["id"]) is True
    assert await svc.verify(raw) is None


async def test_revoke_scoped_to_owner(svc, isolated_db):
    rec, raw = await svc.issue("user-1", "x", ttl_days=0)
    assert await svc.revoke("user-2", rec["id"]) is False
    assert await svc.verify(raw) is not None


async def test_verify_rejects_expired(svc, isolated_db):
    rec, raw = await svc.issue("user-1", "x", ttl_days=-1)  # negative = already expired
    assert await svc.verify(raw) is None


async def test_list_masks_hash(svc, isolated_db):
    await svc.issue("user-1", "x", ttl_days=0)
    listing = await svc.list_for_user("user-1")
    assert listing and all("token_hash" not in r for r in listing)
```

- [ ] **Step 2: Run — expect import failure** (`app.auth.mcp_tokens` missing)

- [ ] **Step 3: Add the `McpToken` model**

In `app/models.py` after `EmailChangeToken` (line ~778), following the `Session` model's style (`app/models.py:630-673`):

```python
class McpToken(Base):
    """A bearer token for the MCP (Model Context Protocol) mount.

    Same trust model as :class:`Session` but for non-browser callers: only
    ``sha256(raw)`` is stored (``token_hash``), revocation is a non-null
    ``revoked_at``, and ``expires_at`` is optional (0-day TTL = no expiry, the
    documented default). ``label`` is the user-chosen name of the client
    ("Claude Desktop"). ``last_used_at`` is written at most once a minute so a
    busy agent does not turn every tool call into a DB write.
    """

    __tablename__ = "mcp_tokens"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    token_hash: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    last_used_at: Mapped[str | None] = mapped_column(String, nullable=True)
    expires_at: Mapped[str | None] = mapped_column(String, nullable=True)
    revoked_at: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        Index("ux_mcp_tokens_token_hash", "token_hash", unique=True),
        Index("ix_mcp_tokens_user_revoked", "user_id", "revoked_at"),
    )
```

- [ ] **Step 4: Alembic migration `0043_mcp_tokens.py`**

Copy the header pattern from `alembic/versions/0042_brain_decisions.py` (revision chain: `down_revision = "<0042's revision id>"`). Body:

```python
def upgrade() -> None:
    op.create_table(
        "mcp_tokens",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("last_used_at", sa.String(), nullable=True),
        sa.Column("expires_at", sa.String(), nullable=True),
        sa.Column("revoked_at", sa.String(), nullable=True),
    )
    op.create_index("ux_mcp_tokens_token_hash", "mcp_tokens", ["token_hash"], unique=True)
    op.create_index("ix_mcp_tokens_user_id", "mcp_tokens", ["user_id"])
    op.create_index("ix_mcp_tokens_user_revoked", "mcp_tokens", ["user_id", "revoked_at"])


def downgrade() -> None:
    op.drop_table("mcp_tokens")
```

- [ ] **Step 5: Implement `app/auth/mcp_tokens.py`**

Follow `app/auth/tokens.py` structure (service class + module-level singleton getters, `app/auth/tokens.py:105-324`). Key logic:

```python
"""Issue/verify/revoke bearer tokens for the MCP mount.

Deliberately mirrors the Session trust model rather than the single-use email
tokens: MCP tokens are long-lived, revocable, and per-user. Raw tokens exist
only in the client's config; the DB keeps sha256 only.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.database import get_session_factory  # verify exact import via existing usage in app/auth/sessions.py
from app.models import McpToken


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class McpTokenService:
    def __init__(self, session_factory) -> None:
        self._sf = session_factory
        self._last_touch: dict[str, str] = {}  # token_id -> iso; throttles last_used_at writes

    async def issue(self, user_id: str, label: str, *, ttl_days: int = 0) -> tuple[dict, str]:
        raw = f"fw_{secrets.token_urlsafe(32)}"
        expires = None
        if ttl_days:
            expires = (datetime.now(timezone.utc) + timedelta(days=ttl_days)).isoformat()
        async with self._sf() as s:
            row = McpToken(token_hash=_hash(raw), user_id=user_id, label=label,
                           created_at=_now_iso(), expires_at=expires)
            s.add(row)
            await s.commit()
            return self._public(row), raw

    async def verify(self, raw: str) -> dict | None:
        """Active token row ({id, user_id, label}) or None. Also stamps last_used_at."""
        if not raw.startswith("fw_"):
            return None
        async with self._sf() as s:
            row = (await s.execute(
                select(McpToken).where(McpToken.token_hash == _hash(raw))
            )).scalar_one_or_none()
            if row is None or row.revoked_at is not None:
                return None
            if row.expires_at and row.expires_at <= _now_iso():
                return None
        await self.touch(row.id)
        return {"id": row.id, "user_id": row.user_id, "label": row.label}

    async def revoke(self, user_id: str, token_id: str) -> bool:
        """Owner-scoped revoke: another user's id returns False and revokes nothing."""
        async with self._sf() as s:
            res = await s.execute(
                update(McpToken)
                .where(McpToken.id == token_id, McpToken.user_id == user_id,
                       McpToken.revoked_at.is_(None))
                .values(revoked_at=_now_iso())
            )
            await s.commit()
            return res.rowcount > 0

    async def list_for_user(self, user_id: str) -> list[dict]:
        async with self._sf() as s:
            rows = (await s.execute(
                select(McpToken).where(McpToken.user_id == user_id)
                .order_by(McpToken.created_at.desc())
            )).scalars().all()
        return [self._public(r) for r in rows]

    async def touch(self, token_id: str) -> None:
        """Best-effort last_used_at, throttled to one write/minute per token."""
        now = _now_iso()
        if self._last_touch.get(token_id, "") > now[:16]:  # same minute
            return
        self._last_touch[token_id] = now
        async with self._sf() as s:
            await s.execute(
                update(McpToken).where(McpToken.id == token_id).values(last_used_at=now)
            )
            await s.commit()

    @staticmethod
    def _public(row: McpToken) -> dict:
        return {"id": row.id, "label": row.label, "created_at": row.created_at,
                "last_used_at": row.last_used_at, "expires_at": row.expires_at,
                "revoked_at": row.revoked_at}
```

Add singleton getters copying `get_token_service`/`reset_token_service` (`app/auth/tokens.py:313-324`), sourcing the session factory the same way `SessionService` does (read `app/auth/sessions.py:707-722` for the exact pattern and copy it).

- [ ] **Step 6: Run tests**

Run: `cd apps/backend && python -m pytest tests/unit/test_mcp_token_service.py -v`
Expected: all PASS. Then `python -m pytest tests/unit -q` — no regressions.

- [ ] **Step 7: Commit**

```bash
git add apps/backend/app/models.py apps/backend/alembic/versions/0043_mcp_tokens.py apps/backend/app/auth/mcp_tokens.py apps/backend/tests/unit/test_mcp_token_service.py
git commit -m "feat(mcp): mcp_tokens table + revocable bearer-token service"
```

---

### Task 3: Token-management REST endpoints (browser-authenticated)

**Files:**
- Create: `apps/backend/app/routers/mcp_tokens.py`
- Modify: `apps/backend/app/main.py` (include router) and `apps/backend/app/routers/__init__.py` (export)
- Test: `apps/backend/tests/integration/test_mcp_tokens_api.py`

**Interfaces:**
- Produces: `POST /api/v1/mcp/tokens` body `{"label": str, "ttl_days": int|None}` → 201 `{"token": "<raw shown once>", "id", "label", "created_at", "expires_at"}`; `GET /api/v1/mcp/tokens` → `{"items": [masked...]}`; `DELETE /api/v1/mcp/tokens/{token_id}` → `{"revoked": true}`. All require a verified browser session (`require_verified_user_id`), inherit CSRF via existing middleware, 404 whole router when `MCP_ENABLED=false` (pattern: `require_extension_enabled`, `app/routers/extension.py:75-94`).

- [ ] **Step 1: Write failing integration tests**

`apps/backend/tests/integration/test_mcp_tokens_api.py`. Follow an existing session-authenticated integration test for the login/CSRF dance — read `tests/integration/test_admin_authz_matrix.py` or `tests/integration/test_credit_accounting.py` first and copy its client-auth fixture usage. Cover:

1. disabled (`mcp_enabled=False`) → all three endpoints 404
2. unauthenticated POST → 401
3. authenticated create → 201, raw starts `fw_`, appears exactly once; listing shows `label` + masked fields, NO `token`/`token_hash`
4. revoke by owner → subsequent `verify` (service) returns None; listing shows `revoked_at`
5. user B cannot revoke/list user A's tokens (empty list / `revoked: false`)
6. label validation: >100 chars → 422; empty → 422
7. audit entries recorded (if `AuditEvent` supports it — read `app/auth/audit.py` first; add `MCP_TOKEN_CREATED = "mcp_token.created"` / `MCP_TOKEN_REVOKED = "mcp_token.revoked"` constants there if no fit exists)

- [ ] **Step 2: Run — expect 404/failures**

- [ ] **Step 3: Implement router**

`apps/backend/app/routers/mcp_tokens.py`, modeled on the extension router's kill-switch + deps:

```python
"""MCP access-token management (browser-authenticated).

Creating a token is the act of granting a non-browser client full access to
the user's FitWright data, so these routes require a verified session and sit
behind the same MCP_ENABLED kill-switch as the mount itself. The raw token is
returned exactly once at creation - the DB keeps only sha256.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import require_verified_user_id
from app.auth.audit import AuditEvent, get_audit_service
from app.auth.mcp_tokens import get_mcp_token_service
from app.config import Settings, settings
from app.errors import ApiError


class TokenCreateRequest(BaseModel):
    label: str = Field(min_length=1, max_length=100,
                       description="Client name, e.g. 'Claude Desktop'")
    ttl_days: int | None = Field(default=None, ge=1, le=3650)


def _require_mcp_enabled(config: Settings = Depends(lambda: settings)) -> None:
    if not config.mcp_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")


router = APIRouter(
    prefix="/mcp/tokens",
    tags=["MCP"],
    dependencies=[Depends(_require_mcp_enabled)],
)


@router.post("", status_code=201)
async def create_token(
    body: TokenCreateRequest,
    user_id: str = Depends(require_verified_user_id),
) -> dict:
    svc = get_mcp_token_service()
    ttl = body.ttl_days if body.ttl_days is not None else settings.mcp_token_ttl_days
    rec, raw = await svc.issue(user_id, body.label, ttl_days=ttl)
    await get_audit_service().record(
        AuditEvent.MCP_TOKEN_CREATED, actor_user_id=user_id,
        meta={"token_id": rec["id"], "label": body.label, "prefix": raw[:9]},
    )
    return {"token": raw, **rec}


@router.get("")
async def list_tokens(user_id: str = Depends(require_verified_user_id)) -> dict:
    return {"items": await get_mcp_token_service().list_for_user(user_id)}


@router.delete("/{token_id}")
async def revoke_token(token_id: str, user_id: str = Depends(require_verified_user_id)) -> dict:
    revoked = await get_mcp_token_service().revoke(user_id, token_id)
    if not revoked:
        raise ApiError(404, "not_found", "No such active token.")
    await get_audit_service().record(
        AuditEvent.MCP_TOKEN_REVOKED, actor_user_id=user_id, meta={"token_id": token_id}
    )
    return {"revoked": True}
```

Check `app/auth/audit.py` for the actual `AuditEvent` enum shape and `record` signature first; adjust to match. Check `app/errors.py` for `ApiError` constructor. Wire into `app/routers/__init__.py` exports and `app/main.py` include (`prefix="/api/v1"`, next to the others, `app/main.py:406-438`).

- [ ] **Step 4: Run tests** — `python -m pytest tests/integration/test_mcp_tokens_api.py -v` → all PASS; then full `tests/integration/test_auth*` unchanged.

- [ ] **Step 5: Commit** — `git commit -m "feat(mcp): session-authenticated token lifecycle endpoints"`

---

### Task 4: FastMCP auth wiring — TokenVerifier + `mcp_token` test fixture

**Files:**
- Modify: `apps/backend/app/mcp/auth_verifier.py` (real implementation)
- Modify: `apps/backend/app/mcp/server.py` (attach verifier, memoize instance)
- Modify: `apps/backend/tests/integration/conftest.py` or new `tests/integration/test_mcp_conftest.py` (add `mcp_token` fixture)
- Test: `apps/backend/tests/integration/test_mcp_auth.py`

**Interfaces:**
- Produces: `FitWrightTokenVerifier` (subclass of `fastmcp.server.auth.TokenVerifier`); test fixture `mcp_token` → `{"raw": str, "id": str, "user_id": str}` minted for the fixture's primary user; every tool call resolves `user_id` via `token: AccessToken = CurrentAccessToken()` reading `token.claims["sub"]`.

- [ ] **Step 1: Write failing auth tests**

`apps/backend/tests/integration/test_mcp_auth.py` — with `mcp_enabled=True`:

```python
@pytest.mark.asyncio
async def test_missing_token_401(auth_env, monkeypatch):
    # POST /api/v1/mcp/ tools/list without Authorization -> 401
    ...

@pytest.mark.asyncio
async def test_garbage_token_401(auth_env, monkeypatch):
    ...  # Bearer fw_garbage

@pytest.mark.asyncio
async def test_revoked_token_401(auth_env, monkeypatch):
    ...  # mint, revoke via service, call -> 401

@pytest.mark.asyncio
async def test_valid_token_tools_list(auth_env, monkeypatch, mcp_token):
    ...  # 200, result.tools is a list, tool names present

@pytest.mark.asyncio
async def test_bearer_token_cannot_call_rest_api(auth_env, monkeypatch, mcp_token):
    # A bearer token must NOT authenticate against ordinary REST routes:
    # GET /api/v1/resumes/list with only Authorization header (no cookies) -> 401.
    # This pins the boundary: tokens live inside the MCP mount and nowhere else.
    ...
```

- [ ] **Step 2: Implement `FitWrightTokenVerifier`**

Using the exact `TokenVerifier.__init__` signature discovered in Task 1 Step 2:

```python
"""Bearer-token verification bridging FastMCP onto FitWright's mcp_tokens."""

from __future__ import annotations

from fastmcp.server.auth import AccessToken, TokenVerifier

from app.auth.mcp_tokens import get_mcp_token_service


class FitWrightTokenVerifier(TokenVerifier):
    """Validates ``Authorization: Bearer fw_...`` against the mcp_tokens table.

    Returns an AccessToken whose claims carry the token OWNER as ``sub`` - every
    tool then scopes its queries to that user, the same guarantee
    ``get_effective_user_id`` gives REST routes.
    """

    async def verify_token(self, token: str) -> AccessToken | None:
        row = await get_mcp_token_service().verify(token)
        if row is None:
            return None
        return AccessToken(
            token=token,
            client_id=row["id"],
            claims={"sub": row["user_id"], "token_id": row["id"], "label": row["label"]},
        )
```

(Adjust constructor args to whatever Task 1 Step 2 revealed — e.g. a `base_url` may be required; pass `settings.public_base_url` if the app defines one, else a placeholder like `http://localhost/` and note it in a comment: no OAuth discovery metadata is served, discovery is irrelevant to bearer-only clients.)

- [ ] **Step 3: Add the `mcp_token` fixture**

In `tests/integration/conftest.py` (alongside existing fixtures), using the same primary-user helper the other integration tests use (read how `test_credit_accounting.py` obtains a user id first):

```python
@pytest.fixture
async def mcp_token(auth_env, isolated_db):
    """Mint an MCP bearer token for the test's primary user."""
    from app.auth.mcp_tokens import get_mcp_token_service

    rec, raw = await get_mcp_token_service().issue("<primary-test-user-id>", "test-client")
    yield {"raw": raw, "id": rec["id"], "user_id": "<primary-test-user-id>"}
```

- [ ] **Step 4: Fix `test_mcp_mount.py` enabled-path** — should now pass (Task 1 Step 4's `mcp_token` dependency resolves).

- [ ] **Step 5: Run** — `python -m pytest tests/integration/test_mcp_auth.py tests/integration/test_mcp_mount.py -v` → PASS. `python -m pytest tests/integration/test_auth* -q` → unchanged.

- [ ] **Step 6: Commit** — `git commit -m "feat(mcp): bearer-token TokenVerifier wired into FastMCP mount"`

---

### Task 5: Core read tools (resumes, applications, queue, duplicates)

**Files:**
- Create: `apps/backend/app/mcp/tools/__init__.py` (import submodules)
- Create: `apps/backend/app/mcp/tools/_context.py`
- Create: `apps/backend/app/mcp/tools/resumes.py`
- Create: `apps/backend/app/mcp/tools/applications.py`
- Test: `apps/backend/tests/integration/test_mcp_tools_read.py`

**Interfaces:**
- Produces: `_context.py` with `def current_user_id(token: AccessToken) -> str` (reads `token.claims["sub"]`, raises `ToolError`-shaped ValueError if absent) and `async def call_tool(user_id, coro_fn)` helper NOT needed — tools call services directly.
- Tools registered on the memoized FastMCP instance: `list_resumes()`, `get_resume(resume_id: str)`, `list_applications()`, `get_application(application_id: str)`, `get_apply_queue()`, `check_duplicate(company: str, role: str)`.

- [ ] **Step 1: Write failing tool tests**

`tests/integration/test_mcp_tools_read.py`. Seed data via the same fixtures used by existing integration tests (read `tests/integration/test_credit_accounting.py` + `tests/conftest.py` resume fixtures). Each test = one `tools/call` JSON-RPC POST:

```python
def _call(client, token, name, arguments):
    return client.post(
        "/api/v1/mcp/",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": name, "arguments": arguments}},
        headers={"Authorization": f"Bearer {token}"},
    ).json()
```

Cover:
1. `list_resumes` returns seeded resumes (name, id, updated_at only — no bloated `processed_data`)
2. `get_resume` happy path; unknown id → tool error with 404-style message (isError true, no stack trace)
3. `list_applications` groups by status
4. `get_application` unknown id → error
5. `check_duplicate` finds seeded duplicate (`company`+`role` match) and returns `is_duplicate: true`
6. **cross-user isolation**: user B's token calling `get_resume(user A's resume_id)` → error/not found; B's `list_resumes` → empty
7. tool schemas: `tools/list` response includes inputSchema for each tool with required params marked

- [ ] **Step 2: Implement `_context.py` + tools**

`app/mcp/tools/_context.py`:

```python
"""Shared helpers for MCP tool bodies."""

from __future__ import annotations

from fastmcp.server.auth import AccessToken


def current_user_id(token: AccessToken) -> str:
    """The token owner - the ONLY user id any tool may query (spec: MCP requests
    execute strictly within the authenticated user's permissions)."""
    sub = token.claims.get("sub")
    if not sub:
        raise ValueError("token_missing_subject")
    return sub
```

`app/mcp/tools/resumes.py`:

```python
"""Resume read tools - thin wrappers over db service calls."""

from __future__ import annotations

from fastmcp import Context
from fastmcp.dependencies import CurrentAccessToken
from fastmcp.server.auth import AccessToken

from app.database import db
from app.mcp.server import get_mcp_instance
from app.mcp.tools._context import current_user_id

mcp = get_mcp_instance()


@mcp.tool
async def list_resumes(token: AccessToken = CurrentAccessToken()) -> dict:
    """List the user's resumes with name, id, master flag, and updated date."""
    user_id = current_user_id(token)
    summaries = await db.list_resume_summaries(user_id)
    return {"resumes": summaries}


@mcp.tool
async def get_resume(resume_id: str, token: AccessToken = CurrentAccessToken()) -> dict:
    """Get one resume's full content by id (from list_resumes)."""
    user_id = current_user_id(token)
    resume = await db.get_resume(user_id, resume_id)
    if resume is None:
        raise ValueError(f"resume_not_found: {resume_id}")
    return resume
```

(Check `db.list_resume_summaries` signature at `app/database.py:716` — it may take extra args like limit; adapt. If `get_mcp_instance()` risks circular import with `server.py` importing `tools`, invert: tools modules receive the `mcp` instance via a registration function `register(mcp)` called from `server.py` after instance creation — choose whichever avoids the cycle and note it.)

`app/mcp/tools/applications.py` — same pattern, tools:

```python
@mcp.tool
async def list_applications(token=CurrentAccessToken()) -> dict:
    """List all the user's job applications, grouped by status column."""
    user_id = current_user_id(token)
    apps = await db.list_applications(user_id)
    columns: dict[str, list] = {}
    for app in apps:
        columns.setdefault(app.get("status", "unknown"), []).append(app)
    return {"columns": columns, "total": len(apps)}


@mcp.tool
async def get_application(application_id: str, token=CurrentAccessToken()) -> dict:
    """Get one application with its embedded job description and applied resume."""
    ...  # db.get_application_detail(user_id, application_id); None -> ValueError

@mcp.tool
async def get_apply_queue(token=CurrentAccessToken()) -> dict:
    """The user's apply queue: saved applications in the order to work through them."""
    ...  # submissions.list_queue(user_id)  (app/applications/submissions.py:106)

@mcp.tool
async def check_duplicate(company: str, role: str, token=CurrentAccessToken()) -> dict:
    """Check whether the user already applied to this company/role (advisory)."""
    ...  # submissions.find_duplicate(user_id, company=company, role=role)
```

`app/mcp/tools/__init__.py` imports both modules (or `server.py` calls `register(mcp)` on each).

- [ ] **Step 3: Run** — `python -m pytest tests/integration/test_mcp_tools_read.py -v` → PASS. Rerun mount/auth tests.

- [ ] **Step 4: Commit** — `git commit -m "feat(mcp): read tools for resumes, applications, queue, duplicates"`

---

### Task 6: Core write tools (add application, update status, reminders)

**Files:**
- Modify: `apps/backend/app/mcp/tools/applications.py`
- Create: `apps/backend/app/mcp/tools/reminders.py`
- Test: `apps/backend/tests/integration/test_mcp_tools_write.py`

**Interfaces:**
- Produces: `add_application(job_description: str, company: str | None, role: str | None, resume_id: str | None)`, `update_application_status(application_id: str, status: str)`, `list_reminders(application_id: str)`, `create_reminder(application_id: str, remind_at: str, note: str | None)`.
- Status values: read `ApplicationStatus` enum in `app/schemas/models.py` and mirror allowed values in the tool docstring + validate before calling `db.update_application` (the REST layer validates via `ApplicationUpdate`; the MCP layer validates the same enum — import it, don't re-declare).

- [ ] **Step 1: Failing tests** — happy path per tool; invalid status string → validation error; cross-user `update_application_status` on A's application → not found; `create_reminder` for another user's application → not found; `add_application` with a 10k-char job description → accepted (check the REST schema `ManualApplicationCreate` bounds in `app/schemas/models.py` and mirror — if it bounds, mirror the bound).

- [ ] **Step 2: Implement** — call the same service functions the REST handlers call (read `app/routers/applications.py:60-100, 313-333` and `app/routers/reminders.py:40-110` bodies; reuse `db.create_job` + application-create sequence or the handler's helper if importable). Write tools follow the Task 5 pattern. No auto-apply, no `record_submission` — those are excluded by spec.

- [ ] **Step 3: Run + Commit** — `git commit -m "feat(mcp): write tools for applications and reminders"`

---

### Task 7: AI tools with identical billing (cover letter, interview prep)

**Files:**
- Create: `apps/backend/app/mcp/tools/ai.py`
- Test: `apps/backend/tests/integration/test_mcp_tools_ai.py`

**Interfaces:**
- Produces: `generate_cover_letter(resume_id: str, job_description: str | None, tone: str | None)` and `generate_interview_prep(resume_id: str, regenerate: bool = False)`. Both charge the existing feature names `cover_letter` / `interview_prep` through `ai_spend`, guarded by `enforce_llm_rate_limit`.

- [ ] **Step 1: Failing tests** — with the `credits_on` fixture (read `tests/conftest.py` for it):
  1. successful generation deducts credits exactly as the REST endpoint does (compare ledger rows/feature name `cover_letter` / `interview_prep` — reuse assertions from `tests/integration/test_credit_accounting.py`)
  2. zero-balance user → tool error `insufficient` (402-equivalent), no partial work
  3. rate-limited user → error
  4. LLM mocked (see how `tests/unit/test_llm.py` fakes the provider) — no real calls in CI
  5. user with own key → metered but zero-charged (`user_has_own_key` path)

- [ ] **Step 2: Implement**

Pattern — the exact body of `ai_metered`'s dependency (`app/ai_metered.py:105-141`) inlined as a shared async context manager so REST and MCP literally share one function. Refactor `ai_metered` to use it (small, justified change — prevents drift):

```python
# app/ai_metered.py — extract
@asynccontextmanager
async def metered_ai_call(user_id: str, feature: str, *, blocking: bool = True):
    """The billing context behind Depends(ai_metered) - shared with MCP tools."""
    ...  # existing dependency body, verbatim


def ai_metered(feature: str, *, blocking: bool = True):
    async def dependency(user_id: str = Depends(get_effective_user_id)) -> AsyncIterator[None]:
        async with metered_ai_call(user_id, feature, blocking=blocking):
            yield
    ...
```

`app/mcp/tools/ai.py`:

```python
@mcp.tool
async def generate_interview_prep(resume_id: str, regenerate: bool = False,
                                  token: AccessToken = CurrentAccessToken()) -> dict:
    """Generate interview preparation for a tailored resume (charges credits)."""
    from app.ai_metered import metered_ai_call
    from app.llm_ratelimit import enforce_llm_rate_limit
    from app.routers.resumes import generate_interview_prep_endpoint

    user_id = current_user_id(token)
    await enforce_llm_rate_limit(user_id)   # same guard as llm_rate_limit_dep
    async with metered_ai_call(user_id, "interview_prep"):
        return await generate_interview_prep_endpoint(resume_id, regenerate, user_id)
```

`generate_cover_letter` mirrors this with the cover-letter handler (`app/routers/resumes.py:3179` — read its exact signature/params first and pass through). If the handler's response model is a Pydantic object, return `response.model_dump()`.

- [ ] **Step 3: Run + regression** — `python -m pytest tests/integration/test_mcp_tools_ai.py tests/integration/test_credit_accounting.py tests/unit/test_llm*.py -v` (the refactor must not change REST billing behavior).

- [ ] **Step 4: Commit** — `git commit -m "feat(mcp): AI tools billed through shared metered_ai_call primitive"`

---

### Task 8: Async job-search tools (start/status pattern)

**Files:**
- Modify: `apps/backend/app/mcp/tools/applications.py` or new `apps/backend/app/mcp/tools/search.py`
- Test: `apps/backend/tests/integration/test_mcp_tools_search.py`

**Interfaces:**
- Produces: `start_job_search(query: str, sites: list[str] | None)` → `{"search_id", "status", "already_running"}` (never blocks; the scrape takes 15-35s — spec requires no long blocking calls); `get_job_search_status(search_id: str)` → progress/result rows.

- [ ] **Step 1: Failing tests** — start returns immediately with a `search_id` (mock `app.job_discovery.search_jobs` the way `tests/test_discovery_router.py` / `test_e2e_discovery_smoke.py` do); status reports running→done; second concurrent start returns `already_running: true`; cross-user status check → not found; search-rate limit (1/10s) respected.

- [ ] **Step 2: Implement** — reuse the exact logic of `start_manual_search` / `manual_search_progress` (`app/routers/discovery.py:716-770`): call `search_jobs.start(...)`, `_check_search_rate(user_id)`, `_enforce_daily_search_cap(user_id, db)`. Import the private helpers if module-level; if they're route-local, call the handlers directly where their signature allows, or promote the helper to module scope (smallest justified change, mirror of Task 7's refactor rule).

- [ ] **Step 3: Run + Commit** — `git commit -m "feat(mcp): async start/status job-search tools"`

---

### Task 9: Frontend — token management in Settings

**Files:**
- Modify: `apps/frontend/app/(app)/settings/page.tsx` (new "MCP / API access" section)
- Modify: `apps/frontend/features/settings/hooks.ts` (token CRUD hooks)
- Modify: `apps/frontend/messages/{en,es,fr,ja,pt-BR,zh}.json` (keys under `settings.mcp*`)
- Test: `apps/frontend/tests/settings-mcp-tokens.test.tsx`

**Interfaces:**
- Produces: UI section listing tokens (label, created, last used, revoked badge), create dialog (label input → shows raw token ONCE with copy button), revoke button with confirm. Section hidden when API reports MCP disabled (extend the existing settings/config fetch pattern — read `apps/frontend/features/settings/hooks.ts` first).

- [ ] **Step 1: Failing frontend test** — follow `tests/settings-ai-source-toggle.test.tsx` for the render/mocks pattern. Cover: renders section when enabled; create flow shows raw token once and never after refetch; revoke calls DELETE; revoked badge shown; section hidden when disabled.
- [ ] **Step 2: Implement** — minimal section, reuse existing UI components from the settings page; add the ~10 i18n keys to ALL six locale files (translate properly — es/fr/ja/pt-BR/zh).
- [ ] **Step 3: Run** — `cd apps/frontend && npm test -- settings-mcp-tokens` + typecheck + lint.
- [ ] **Step 4: Commit** — `git commit -m "feat(mcp): settings UI for MCP token management"`

---

### Task 10: Documentation, red-team review, full regression, final commit

**Files:**
- Create: `docs/mcp.md`
- Test: `apps/backend/tests/integration/test_mcp_redteam.py`

**Interfaces:** none (docs + adversarial tests + verification).

- [ ] **Step 1: Red-team test file** — explicit attacks:
  1. cross-user: every tool called with the other user's resource ids → not found / empty
  2. token misuse: expired, revoked, malformed (`Bearer fw_` empty, `Bearer` without scheme, non-`fw_` tokens, SQL/JSON injection in token string)
  3. privilege: user token hitting `/api/v1/admin/*` REST routes via bearer → 401 (tokens are MCP-mount-only); admin capability checks never consult MCP tokens
  4. parameter abuse: 1MB arguments, null/nested-enum status strings, resume_id as array → 422/tool error, never 500
  5. business-rule bypass: `add_application` on a cooldown-locked company → still advisory-duplicate behavior; rate limits (search 1/10s, LLM limits) enforced on the MCP path
  6. leakage: tool outputs and `tools/list` schemas contain no token hashes, no other-user fields, no stack traces; server logs (caplog) record only `fw_` + 6-char prefixes
  7. CSRF boundary: MCP endpoints ignore cookie-based sessions (a valid session cookie without bearer → still 401 on the MCP mount); REST endpoints ignore bearer tokens

- [ ] **Step 2: Fix whatever the red-team run surfaces; re-run until clean.**

- [ ] **Step 3: Write `docs/mcp.md`** — sections: overview + architecture diagram (text), enable (`MCP_ENABLED=true`), token lifecycle (create in Settings → copy once → store in client config → revoke), client config examples (Claude Desktop JSON with `mcp-remote` bridge, Cursor), full tool reference (name, params, what it costs in credits), security model (bearer-only in mount, sha256 storage, isolation, billing parity), limitations (no auto-apply, no scopes, single deployment URL), how to add a tool (the `_context` + register pattern). Document ONLY what exists.

- [ ] **Step 4: Full regression** — `cd apps/backend && python -m pytest -q` (full suite), `cd apps/frontend && npm test` + `npx tsc --noEmit`. Both must pass with zero changes to pre-existing tests (if a pre-existing test fails, that's a regression — fix before proceeding, do not skip).

- [ ] **Step 5: Diff review** — `git diff main...HEAD --stat` and read the full diff: confirm no file outside `app/mcp/`, `app/auth/mcp_tokens.py`, `app/models.py` (one class), `alembic/versions/0043_*`, `app/config.py` (two fields), `app/main.py` (mount+router), `app/routers/{mcp_tokens,__init__}.py`, `app/ai_metered.py` (extract refactor), `app/routers/discovery.py` (helper promotion if done), frontend files, docs, and tests was touched.

- [ ] **Step 6: Verify main tree untouched** — from the worktree, `git -C /home/obaid/Downloads/fitwright status --short` must show the SAME uncommitted bug-fix files as before this work started (nothing staged/committed/modified by us).

- [ ] **Step 7: Final commit + tag summary** — `git commit -m "docs(mcp): MCP integration documentation and red-team tests"` (if not already committed per-file), then produce the final report (implemented, architecture, tools, auth model, security decisions, tests+results, files changed, limitations/follow-ups, branch+worktree+commits).

---

## Self-Review (completed)

- Spec coverage: isolation (worktree ✓), thin-layer (Tasks 5-8 call handlers/services ✓), auth model (Tasks 2-4 ✓), no-CSRF-weakening (bearer confined to mount, tested Task 4 + Task 10 red-team #7 ✓), tool surface limited to mature features (no auto-apply/submission anywhere ✓), async pattern (Task 8 ✓), billing parity (Task 7 ✓, same feature names), tests incl. red-team (Tasks 2-8, 10 ✓), docs (Task 10 ✓), commits (every task ✓), main-tree safety (Task 10 Step 6 ✓).
- Gaps accepted: scopes deferred (existing architecture has no token-scope concept — documented as limitation, per spec "if the existing architecture supports scopes"); OAuth discovery metadata not served (bearer-only, documented).
- Placeholder scan: none — every code step carries real code or an exact "read X at file:line, mirror it" instruction with the mirror target named.
- Type consistency: `current_user_id`, `metered_ai_call`, `mcp_token` fixture, `get_mcp_instance` used consistently across tasks.
