"""Unit tests for auth metrics + structured JSON logging (Task 9.2, R16.1).

Covers the in-process :class:`AuthMetrics` counters (incl. the derived session-
cache hit ratio and the labelled oauth-failure-by-reason map) and the
:class:`JsonLogFormatter` - specifically that it emits one JSON object with the
request correlation fields and that it scrubs secrets / log-injection newlines so
no token or PII lands in a log line.
"""

from __future__ import annotations

import json
import logging

from app.auth.metrics import AuthMetrics
from app.observability import (
    JsonLogFormatter,
    request_id_var,
    user_id_var,
)


class TestAuthMetrics:
    def test_counters_increment(self):
        m = AuthMetrics()
        m.record_login_success()
        m.record_login_success()
        m.record_login_failure()
        m.record_signup()
        m.record_lockout()
        m.record_rate_limited()
        m.record_step_up(success=True)
        m.record_step_up(success=False)
        snap = m.snapshot()
        assert snap["login_success"] == 2
        assert snap["login_failure"] == 1
        assert snap["signup"] == 1
        assert snap["lockout"] == 1
        assert snap["rate_limited"] == 1
        assert snap["step_up_success"] == 1
        assert snap["step_up_failure"] == 1

    def test_session_cache_hit_ratio(self):
        m = AuthMetrics()
        # No lookups yet -> 0.0 (no division by zero).
        assert m.session_cache_hit_ratio == 0.0
        m.record_session_cache(hit=True)
        m.record_session_cache(hit=True)
        m.record_session_cache(hit=True)
        m.record_session_cache(hit=False)
        assert m.session_cache_hit_ratio == 0.75
        assert m.snapshot()["session_cache_hit_ratio"] == 0.75

    def test_oauth_failure_by_reason_is_labelled(self):
        m = AuthMetrics()
        m.record_oauth_failure("state_mismatch")
        m.record_oauth_failure("state_mismatch")
        m.record_oauth_failure("email_unverified")
        m.record_oauth_success()
        snap = m.snapshot()
        assert snap["oauth_failure"] == 3
        assert snap["oauth_success"] == 1
        assert snap["oauth_failure_by_reason"] == {
            "state_mismatch": 2,
            "email_unverified": 1,
        }

    def test_counters_never_go_negative(self):
        m = AuthMetrics()
        m.incr("x", -5)
        assert m.snapshot()["x"] == 0


class TestJsonLogFormatter:
    def _record(self, msg: str, **extra) -> logging.LogRecord:
        record = logging.LogRecord(
            name="app.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg=msg,
            args=(),
            exc_info=None,
        )
        for key, value in extra.items():
            setattr(record, key, value)
        return record

    def test_emits_json_with_correlation_fields(self):
        rid = request_id_var.set("req-123")
        uid = user_id_var.set("user-abc")
        try:
            out = JsonLogFormatter().format(self._record("hello"))
        finally:
            request_id_var.reset(rid)
            user_id_var.reset(uid)
        payload = json.loads(out)
        assert payload["msg"] == "hello"
        assert payload["level"] == "INFO"
        assert payload["logger"] == "app.test"
        assert payload["request_id"] == "req-123"
        assert payload["user_id"] == "user-abc"
        assert "ts" in payload

    def test_omits_correlation_fields_when_unset(self):
        # Ensure a clean context (no request in flight).
        rid = request_id_var.set("")
        uid = user_id_var.set("")
        try:
            payload = json.loads(JsonLogFormatter().format(self._record("startup")))
        finally:
            request_id_var.reset(rid)
            user_id_var.reset(uid)
        assert "request_id" not in payload
        assert "user_id" not in payload

    def test_scrubs_secret_bearing_extra_fields(self):
        # A field whose name marks it as secret must be dropped entirely.
        payload = json.loads(
            JsonLogFormatter().format(
                self._record("auth", password="hunter2", csrf_token="abc", safe="ok")
            )
        )
        assert "password" not in payload
        assert "csrf_token" not in payload
        assert payload["safe"] == "ok"

    def test_neutralizes_log_injection_newlines(self):
        # A crafted message with CR/LF must not forge a second log line.
        payload = json.loads(
            JsonLogFormatter().format(self._record("line1\nINJECTED level=CRITICAL"))
        )
        assert "\n" not in payload["msg"]
        assert "INJECTED" in payload["msg"]  # still present, just collapsed inline
