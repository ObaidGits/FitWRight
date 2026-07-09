"""Security sign-off suite (Task 10.2) — mapped to Correctness Properties.

A consolidated, adversarial pass over the P1 auth surface. Each test is annotated
with the design Property it defends (1 isolation, 3 session integrity, 4 no
enumeration, 5 OAuth authenticity/linking, 6 step-up). Everything here is
deterministic: Argon2 is dialed down by the ``auth_env`` fixture, the LLM/network
are never touched, and the OAuth flow runs against an injected mock IdP. The one
timing test is statistical-but-stable — the *authoritative* guarantee is the
structural spy assertion (the dummy-hash branch actually executes on the
unknown-email path); the timing test only corroborates it, comparing medians
against a two-sided band, so it never flakes.

Requirements: 1.2, 2.2, 4.4, 6.5, 9.1, 12.1, 12.2, 13.4
"""

from __future__ import annotations

import statistics
import time
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth import validate_next_path
from app.auth.accounts import create_user
from app.auth.oauth import OAUTH_TXN_COOKIE, registry as oauth_registry
from app.auth.passwords import get_password_service
from app.config import settings as app_settings
from app.main import app

from tests.integration.test_auth_api import (
    STRONG_PW,
    _cookie_str,
    _csrf,
    _login,
    _seed_active_user,
    _signup,
)
from tests.integration.test_auth_oauth_api import MockProvider

pytestmark = pytest.mark.integration

FRONTEND = app_settings.frontend_base_url.rstrip("/")


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://test")


@pytest.fixture
def hosted(monkeypatch):
    """Hosted mode: per-session CSRF + step-up gates are live."""
    monkeypatch.setattr(app_settings, "single_user_mode", False)


@pytest.fixture
def mock_provider():
    provider = MockProvider()
    oauth_registry.register("mock", lambda: provider)
    try:
        yield provider
    finally:
        oauth_registry._factories.pop("mock", None)
        oauth_registry.reset()


def _extract_state(resp) -> str:
    return parse_qs(urlparse(resp.headers["location"]).query)["state"][0]


def _is_cleared(cookie: str | None) -> bool:
    if cookie is None:
        return False
    low = cookie.lower()
    return "max-age=0" in low or "expires=thu, 01 jan 1970" in low


# ===========================================================================
# CSRF — Property 3 / R12.1, R12.2
# ===========================================================================


class TestCsrf:
    async def test_login_requires_presession_token(self, auth_env):
        """Login-CSRF: without the pre-session double-submit token, login 403s."""
        await _seed_active_user(auth_env, "csrf-login@example.com")
        async with _client() as client:
            resp = await client.post(
                "/api/v1/auth/login",
                json={"email": "csrf-login@example.com", "password": STRONG_PW},
            )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "csrf_failed"

    async def test_presession_cookie_header_mismatch_rejected(self, auth_env):
        """A forged header that doesn't match the issued cookie is rejected."""
        await _seed_active_user(auth_env, "csrf-mismatch@example.com")
        async with _client() as client:
            await _csrf(client)  # sets a valid csrf cookie
            resp = await client.post(
                "/api/v1/auth/login",
                json={"email": "csrf-mismatch@example.com", "password": STRONG_PW},
                headers={"X-CSRF-Token": "not-the-issued-token"},
            )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "csrf_failed"

    async def test_logout_requires_csrf(self, auth_env, hosted):
        """Logout is a mutation and MUST carry the per-session CSRF header (R12.2)."""
        await _seed_active_user(auth_env, "csrf-logout@example.com")
        async with _client() as client:
            await _login(client, "csrf-logout@example.com")
            no_header = await client.post("/api/v1/auth/logout")
            assert no_header.status_code == 403
            csrf = client.cookies.get("csrf")
            ok = await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
            assert ok.status_code == 200

    async def test_mutation_without_csrf_rejected(self, auth_env, hosted):
        """A state-changing owned-resource call without CSRF is blocked by the middleware."""
        user = await _seed_active_user(auth_env, "csrf-mut@example.com")
        await auth_env.create_resume(user.id, content="x")
        async with _client() as client:
            await _login(client, "csrf-mut@example.com")
            resp = await client.post(
                "/api/v1/jobs/upload", json={"job_descriptions": ["a role"]}
            )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "csrf_failed"


# ===========================================================================
# Session fixation — Property 3 / R2.1
# ===========================================================================


class TestSessionFixation:
    async def test_login_rotates_session_id(self, auth_env):
        """A fresh session id is minted on login; the pre-login token dies."""
        await _seed_active_user(auth_env, "fix-login@example.com")
        async with _client() as client:
            await _login(client, "fix-login@example.com")
            token1 = client.cookies.get("__Host-session")
            await _login(client, "fix-login@example.com")
            token2 = client.cookies.get("__Host-session")
        assert token1 and token2 and token1 != token2
        # The old token no longer authorizes (fixation defense).
        async with _client() as fresh:
            r = await fresh.get(
                "/api/v1/auth/session", headers={"Cookie": f"__Host-session={token1}"}
            )
        assert r.status_code == 401

    async def test_password_change_rotates_session_id(self, auth_env, hosted):
        """Changing the password rotates the current session id (design §Sessions)."""
        await _seed_active_user(auth_env, "fix-change@example.com")
        new_pw = "brand-new-passphrase-71"
        async with _client() as client:
            await _login(client, "fix-change@example.com")
            before = client.cookies.get("__Host-session")
            csrf = client.cookies.get("csrf")
            # Open a step-up window, then change the password.
            assert (
                await client.post(
                    "/api/v1/auth/step-up",
                    json={"password": STRONG_PW},
                    headers={"X-CSRF-Token": csrf},
                )
            ).status_code == 200
            csrf = client.cookies.get("csrf")
            changed = await client.post(
                "/api/v1/auth/password/change",
                json={"current_password": STRONG_PW, "new_password": new_pw},
                headers={"X-CSRF-Token": csrf},
            )
            assert changed.status_code == 200
            after = client.cookies.get("__Host-session")
        # The session id rotated, and the pre-change token is dead.
        assert before and after and before != after
        async with _client() as fresh:
            r = await fresh.get(
                "/api/v1/auth/session", headers={"Cookie": f"__Host-session={before}"}
            )
        assert r.status_code == 401

    async def test_password_reset_rotates_and_revokes_all(self, auth_env):
        """A reset revokes every prior session and issues a fresh one (Property 3)."""
        from app.auth.sessions import get_session_service
        from app.auth.tokens import get_token_service

        record = await _seed_active_user(auth_env, "fix-reset@example.com")
        async with _client() as old:
            await _login(old, "fix-reset@example.com")
            old_token = old.cookies.get("__Host-session")

        raw_token = await get_token_service().issue_reset(record.id)
        async with _client() as client:
            await _csrf(client)
            resp = await client.post(
                "/api/v1/auth/password/reset",
                json={"token": raw_token, "password": "totally-fresh-passphrase-9"},
                headers={"X-CSRF-Token": client.cookies.get("csrf")},
            )
        assert resp.status_code == 200
        # Every pre-reset session is gone; the old token no longer authorizes.
        async with _client() as fresh:
            r = await fresh.get(
                "/api/v1/auth/session", headers={"Cookie": f"__Host-session={old_token}"}
            )
        assert r.status_code == 401


# ===========================================================================
# No enumeration — Property 4 / R1.2, R2.2, R6.1, R13.4
# ===========================================================================


class TestNoEnumeration:
    async def test_login_unknown_vs_wrong_password_uniform_shape(self, auth_env):
        await _seed_active_user(auth_env, "enum-known@example.com")
        async with _client() as c1:
            wrong = await _login(c1, "enum-known@example.com", password="wrong-password-here-1")
        async with _client() as c2:
            unknown = await _login(c2, "enum-nobody@example.com", password="wrong-password-here-1")
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json() == unknown.json()  # identical envelope → no disclosure

    async def test_signup_uniform_when_verification_on(self, auth_env, monkeypatch):
        monkeypatch.setattr(app_settings, "single_user_mode", False)
        await _seed_active_user(auth_env, "enum-existing@example.com")
        async with _client() as c1:
            new = await _signup(c1, "enum-brand-new@example.com")
        async with _client() as c2:
            existing = await _signup(c2, "enum-existing@example.com")
        assert new.status_code == existing.status_code == 200
        assert new.json() == existing.json() == {"status": "pending_verification"}
        assert _cookie_str(new, "__Host-session") is None
        assert _cookie_str(existing, "__Host-session") is None

    async def test_forgot_password_uniform_regardless_of_existence(self, auth_env):
        await _seed_active_user(auth_env, "enum-forgot@example.com")
        async with _client() as c1:
            known = await c1.post(
                "/api/v1/auth/password/forgot", json={"email": "enum-forgot@example.com"}
            )
        async with _client() as c2:
            unknown = await c2.post(
                "/api/v1/auth/password/forgot", json={"email": "enum-ghost@example.com"}
            )
        assert known.status_code == unknown.status_code == 200
        assert known.json() == unknown.json()

    async def test_verify_and_resend_uniform(self, auth_env):
        await _seed_active_user(auth_env, "enum-verify@example.com")
        async with _client() as c1:
            known = await c1.post(
                "/api/v1/auth/verify/request", json={"email": "enum-verify@example.com"}
            )
        async with _client() as c2:
            unknown = await c2.post(
                "/api/v1/auth/verify/request", json={"email": "enum-none@example.com"}
            )
        assert known.status_code == unknown.status_code == 200
        assert known.json() == unknown.json()

    async def test_dummy_hash_path_executes_for_unknown_email(self, auth_env, monkeypatch):
        """The unknown-email login branch MUST still run an Argon2 verify.

        We spy on the password service: an unknown email must call
        ``verify_password`` with ``stored_hash=None`` (the dummy-hash branch), so
        it is not measurably faster than a real wrong-password attempt (R2.2).
        """
        svc = get_password_service()
        calls: list[str | None] = []
        original = svc.verify_password

        def _spy(stored_hash, password):
            calls.append(stored_hash)
            return original(stored_hash, password)

        monkeypatch.setattr(svc, "verify_password", _spy)
        async with _client() as client:
            await _login(client, "enum-absent@example.com", password="whatever-wrong-1")
        # Exactly the login verify ran, and it used the dummy hash (None).
        assert calls == [None]

    async def test_enumeration_timing_statistical_bound(self, auth_env):
        """Corroborating timing check for the no-enumeration guarantee (R2.2).

        The *authoritative* guarantee lives in
        ``test_dummy_hash_path_executes_for_unknown_email``: a spy proves the
        unknown-email branch runs a real Argon2 verify against the dummy hash, so
        the code cannot short-circuit and leak "no such user" via a fast path.
        That structural assertion is deterministic and cannot flake.

        This test is *corroborating*: since both the unknown-email and
        known-wrong-password paths run exactly one Argon2 verify, their wall-clock
        cost should be of the same order. We compare **medians** (robust to the
        occasional GC pause / scheduler hiccup that makes a mean useless) with a
        **two-sided** band rather than the old one-sided ``>= 0.4x`` floor: the
        unknown median must be neither dramatically faster (a skipped hash) nor
        dramatically slower (which would itself be a timing oracle) than the
        known median. The band is deliberately wide because Argon2 is dialed to a
        test-fast cost by ``auth_env`` (so per-call time is tiny and dominated by
        ASGI/JSON overhead + noise); a tighter bound at this cost would flake,
        which is precisely why the spy — not this test — is the source of truth.
        """
        await _seed_active_user(auth_env, "enum-timing@example.com")

        async def _time_login(email: str) -> float:
            async with _client() as client:
                token = await _csrf(client)
                start = time.perf_counter()
                await client.post(
                    "/api/v1/auth/login",
                    json={"email": email, "password": "definitely-wrong-xyz-1"},
                    headers={"X-CSRF-Token": token},
                )
                return time.perf_counter() - start

        # A few warm-up calls so import/JIT/connection costs don't skew the first
        # samples of whichever branch happens to run first.
        for _ in range(3):
            await _time_login("enum-timing@example.com")
            await _time_login("enum-absent-timing@example.com")

        known, unknown = [], []
        for _ in range(40):  # modestly more samples → a more stable median
            known.append(await _time_login("enum-timing@example.com"))
            unknown.append(await _time_login("enum-absent-timing@example.com"))

        m_known = statistics.median(known)
        m_unknown = statistics.median(unknown)
        # Two-sided, defensible band: the unknown-email median must sit within
        # [0.5x, 2.0x] of the known-email median. Below 0.5x would suggest the
        # dummy-hash verify was skipped (enumeration via a fast path); above 2.0x
        # would be a timing oracle in the other direction. The band is symmetric
        # in log-space (0.5 = 1/2, 2.0 = 2/1) and wide enough to absorb the noise
        # inherent to fast-Argon2 + ASGI overhead without flaking.
        ratio = m_unknown / m_known if m_known > 0 else float("inf")
        assert 0.5 <= ratio <= 2.0, (
            f"unknown-email login timing diverges from known-wrong-password: "
            f"unknown median {m_unknown:.4f}s vs known {m_known:.4f}s "
            f"(ratio {ratio:.2f}, expected within [0.5, 2.0]). The authoritative "
            f"check is the dummy-hash spy test; this bound only corroborates it."
        )


# ===========================================================================
# Open redirect — Property (R11.4) — next validation matrix
# ===========================================================================


class TestOpenRedirect:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("/home", "/home"),
            ("/settings/account", "/settings/account"),
            ("/a?b=c#d", "/a?b=c#d"),
            ("//evil.example/phish", None),
            ("https://evil.example", None),
            ("http://evil.example", None),
            ("\\\\evil.example", None),
            ("/\\evil.example", None),
            ("javascript:alert(1)", None),
            ("", None),
            ("relative/no-slash", None),
            ("/", "/"),
        ],
    )
    def test_next_validation_matrix(self, value, expected):
        assert validate_next_path(value) == expected

    async def test_oauth_next_open_redirect_dropped(self, auth_env, mock_provider):
        """An unsafe ``next`` through the real OAuth flow falls back to /home."""
        async with _client() as client:
            start = await client.get(
                "/api/v1/auth/oauth/mock/start",
                params={"next": "//evil.example/x"},
                follow_redirects=False,
            )
            state = _extract_state(start)
            cb = await client.get(
                "/api/v1/auth/oauth/mock/callback",
                params={"code": "c1", "state": state},
                follow_redirects=False,
            )
        assert cb.headers["location"] == f"{FRONTEND}/home"


# ===========================================================================
# OAuth authenticity + safe linking — Property 5 / R4.2, R4.4, R4.6
# ===========================================================================


class TestOAuthSecurity:
    async def test_state_mismatch_no_session(self, auth_env, mock_provider):
        async with _client() as client:
            await client.get("/api/v1/auth/oauth/mock/start", follow_redirects=False)
            cb = await client.get(
                "/api/v1/auth/oauth/mock/callback",
                params={"code": "c1", "state": "forged-state"},
                follow_redirects=False,
            )
        assert cb.headers["location"] == f"{FRONTEND}/login?error=oauth_failed"
        assert _cookie_str(cb, "__Host-session") is None

    async def test_replay_after_success_rejected(self, auth_env, mock_provider):
        async with _client() as client:
            start = await client.get("/api/v1/auth/oauth/mock/start", follow_redirects=False)
            state = _extract_state(start)
            first = await client.get(
                "/api/v1/auth/oauth/mock/callback",
                params={"code": "c1", "state": state},
                follow_redirects=False,
            )
            assert first.headers["location"] == f"{FRONTEND}/home"
            # Transient cookie was cleared → replay of the same state fails closed.
            replay = await client.get(
                "/api/v1/auth/oauth/mock/callback",
                params={"code": "c2", "state": state},
                follow_redirects=False,
            )
        assert replay.headers["location"] == f"{FRONTEND}/login?error=oauth_failed"
        assert _cookie_str(replay, "__Host-session") is None
        assert _is_cleared(_cookie_str(first, OAUTH_TXN_COOKIE))

    async def test_linking_hijack_of_unverified_password_account_refused(
        self, auth_env, mock_provider
    ):
        """An unverified password account is never silently hijacked by OAuth (R4.4)."""
        await create_user(
            email="oauth-user@example.com",
            name="Pending",
            password_hash=get_password_service().hash_password("pw-abcdef-123456"),
            status="pending_verification",
            email_verified_at=None,
            db=auth_env,
        )
        async with _client() as client:
            start = await client.get("/api/v1/auth/oauth/mock/start", follow_redirects=False)
            state = _extract_state(start)
            cb = await client.get(
                "/api/v1/auth/oauth/mock/callback",
                params={"code": "c1", "state": state},
                follow_redirects=False,
            )
        assert cb.headers["location"] == f"{FRONTEND}/login?error=oauth_failed"
        assert _cookie_str(cb, "__Host-session") is None

    async def test_unverified_provider_email_rejected(self, auth_env, mock_provider):
        mock_provider.email_verified = False
        async with _client() as client:
            start = await client.get("/api/v1/auth/oauth/mock/start", follow_redirects=False)
            state = _extract_state(start)
            cb = await client.get(
                "/api/v1/auth/oauth/mock/callback",
                params={"code": "c1", "state": state},
                follow_redirects=False,
            )
        assert cb.headers["location"] == f"{FRONTEND}/login?error=oauth_failed"
        assert _cookie_str(cb, "__Host-session") is None


# ===========================================================================
# Step-up bypass — Property 6 / R9.1
# ===========================================================================


class TestStepUpBypass:
    async def test_password_change_without_step_up_blocked(self, auth_env, hosted):
        await _seed_active_user(auth_env, "su-bypass@example.com")
        async with _client() as client:
            await _login(client, "su-bypass@example.com")
            csrf = client.cookies.get("csrf")
            resp = await client.post(
                "/api/v1/auth/password/change",
                json={"current_password": STRONG_PW, "new_password": "another-strong-pass-8"},
                headers={"X-CSRF-Token": csrf},
            )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "step_up_required"

    async def test_logout_all_without_step_up_blocked(self, auth_env, hosted):
        await _seed_active_user(auth_env, "su-bypass-all@example.com")
        async with _client() as client:
            await _login(client, "su-bypass-all@example.com")
            csrf = client.cookies.get("csrf")
            resp = await client.post(
                "/api/v1/auth/logout-all", headers={"X-CSRF-Token": csrf}
            )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "step_up_required"

    async def test_email_change_without_step_up_blocked(self, auth_env, hosted):
        await _seed_active_user(auth_env, "su-bypass-email@example.com")
        async with _client() as client:
            await _login(client, "su-bypass-email@example.com")
            csrf = client.cookies.get("csrf")
            resp = await client.post(
                "/api/v1/users/me/email",
                json={"email": "new-address@example.com"},
                headers={"X-CSRF-Token": csrf},
            )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "step_up_required"

    async def test_expired_step_up_window_blocks(self, auth_env, hosted, monkeypatch):
        await _seed_active_user(auth_env, "su-expired@example.com")
        async with _client() as client:
            await _login(client, "su-expired@example.com")
            csrf = client.cookies.get("csrf")
            assert (
                await client.post(
                    "/api/v1/auth/step-up",
                    json={"password": STRONG_PW},
                    headers={"X-CSRF-Token": csrf},
                )
            ).status_code == 200
            # Force the sudo window to have lapsed.
            monkeypatch.setattr(app_settings, "step_up_window", -1)
            csrf = client.cookies.get("csrf")
            resp = await client.post(
                "/api/v1/auth/password/change",
                json={"current_password": STRONG_PW, "new_password": "another-strong-pass-8"},
                headers={"X-CSRF-Token": csrf},
            )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "step_up_required"


# ===========================================================================
# Breached password — R13.3
# ===========================================================================


class TestBreachedPassword:
    async def test_breached_password_rejected_at_signup(self, auth_env, monkeypatch):
        from app.auth.breach import BreachResult

        class _Breach:
            async def check(self, password: str) -> BreachResult:
                return BreachResult(breached=True, count=1234)

        monkeypatch.setattr(get_password_service(), "_breach_check", _Breach())
        async with _client() as client:
            resp = await _signup(client, "breach@example.com")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "breached_password"


# ===========================================================================
# Cookie attribute assertions — R12.1
# ===========================================================================


class TestCookieHardening:
    async def test_host_session_cookie_attributes(self, auth_env):
        await _seed_active_user(auth_env, "cookie@example.com")
        async with _client() as client:
            resp = await _login(client, "cookie@example.com")
        session_cookie = _cookie_str(resp, "__Host-session")
        assert session_cookie is not None
        assert "HttpOnly" in session_cookie
        assert "Secure" in session_cookie
        assert "Path=/" in session_cookie
        assert "samesite=lax" in session_cookie.lower()
        assert "Domain=" not in session_cookie  # __Host- forbids Domain
        # The CSRF cookie must be JS-readable (double-submit) but still hardened.
        csrf_cookie = _cookie_str(resp, "csrf")
        assert csrf_cookie is not None
        assert "HttpOnly" not in csrf_cookie
        assert "Secure" in csrf_cookie
        assert "samesite=lax" in csrf_cookie.lower()


# ===========================================================================
# IDOR cross-user — Property 1 / R10.2, R10.3
# ===========================================================================


class TestIdorCrossUser:
    async def test_cross_user_application_is_404(self, auth_env, hosted):
        user_a = await _seed_active_user(auth_env, "idor-a@example.com")
        await _seed_active_user(auth_env, "idor-b@example.com")
        card = await auth_env.create_application(user_a.id, job_id="j1", resume_id="r1")
        aid = card["application_id"]

        async with _client() as client:
            await _login(client, "idor-b@example.com")
            csrf = client.cookies.get("csrf")
            assert (await client.get(f"/api/v1/applications/{aid}")).status_code == 404
            assert (
                await client.patch(
                    f"/api/v1/applications/{aid}",
                    json={"notes": "x"},
                    headers={"X-CSRF-Token": csrf},
                )
            ).status_code == 404
            assert (
                await client.delete(
                    f"/api/v1/applications/{aid}", headers={"X-CSRF-Token": csrf}
                )
            ).status_code == 404
        # A's card is intact.
        assert (await auth_env.get_application(user_a.id, aid))["status"] == "applied"
