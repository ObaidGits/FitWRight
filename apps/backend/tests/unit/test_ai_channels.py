"""Channel selection and error classification.

The failures these guard against: failing over on an error that will fail
everywhere (wasting time and, later, money), serving JSON features from a channel
that cannot produce JSON, and hammering a provider that has not recovered.
"""

from datetime import datetime, timedelta, timezone

from app.ai_channels import (
    FEATURES_REQUIRING_STRUCTURED_OUTPUT,
    classify_error,
    is_retryable,
    select_channels,
)

NOW = datetime(2026, 8, 14, 12, 0, 0, tzinfo=timezone.utc)


def ch(cid, *, priority=100, state="active", verdict="reliable", name=None):
    return {
        "id": cid,
        "name": name or cid,
        "provider": "openai",
        "model": "gpt-5-nano",
        "api_base": None,
        "priority": priority,
        "state": state,
        "structured_verdict": verdict,
        "created_at": "2026-01-01",
    }


def cooling(seconds_from_now):
    return {"cooling_until": (NOW + timedelta(seconds=seconds_from_now)).isoformat()}


class TestOrdering:
    def test_orders_by_priority(self):
        got = select_channels(
            [ch("c", priority=30), ch("a", priority=10), ch("b", priority=20)],
            {},
            feature="cover_letter",
            now=NOW,
        )
        assert [c.id for c in got] == ["a", "b", "c"]

    def test_excludes_disabled_and_draining(self):
        got = select_channels(
            [ch("ok"), ch("off", state="disabled"), ch("drain", state="draining")],
            {},
            feature="cover_letter",
            now=NOW,
        )
        assert [c.id for c in got] == ["ok"]

    def test_returns_empty_when_nothing_is_usable(self):
        """The caller must be able to tell 'all channels down' from 'not configured'
        and from 'out of credits' - three distinct user messages."""
        got = select_channels(
            [ch("off", state="disabled")], {}, feature="cover_letter", now=NOW
        )
        assert got == []


class TestHealth:
    def test_skips_a_channel_still_cooling(self):
        got = select_channels(
            [ch("hot", priority=1), ch("cool", priority=2)],
            {"hot": cooling(+60)},
            feature="cover_letter",
            now=NOW,
        )
        assert [c.id for c in got] == ["cool"]

    def test_a_lapsed_cooldown_returns_as_a_probe_ranked_last(self):
        """Worth one request to see if the provider recovered - but behind every
        channel that is simply healthy."""
        got = select_channels(
            [ch("recovered", priority=1), ch("healthy", priority=50)],
            {"recovered": cooling(-60)},
            feature="cover_letter",
            now=NOW,
        )
        assert [c.id for c in got] == ["healthy", "recovered"]
        assert got[0].probe is False
        assert got[1].probe is True, "a post-cooldown channel must be marked a probe"

    def test_unparseable_cooldown_does_not_bench_a_channel_forever(self):
        got = select_channels(
            [ch("a")], {"a": {"cooling_until": "not-a-date"}}, feature="cover_letter", now=NOW
        )
        assert [c.id for c in got] == ["a"]

    def test_missing_health_row_is_treated_as_healthy(self):
        got = select_channels([ch("a")], {}, feature="cover_letter", now=NOW)
        assert [c.id for c in got] == ["a"]


class TestStructuredGating:
    def test_unsupported_channel_is_barred_from_a_json_feature(self):
        """A fallback that keeps the app up while returning unusable JSON is worse
        than an honest error: the user only finds out after reading the result."""
        got = select_channels(
            [ch("bad", priority=1, verdict="unsupported"), ch("good", priority=2)],
            {},
            feature="resume_tailor",
            now=NOW,
        )
        assert [c.id for c in got] == ["good"]

    def test_unsupported_channel_may_still_serve_free_text(self):
        got = select_channels(
            [ch("bad", verdict="unsupported")], {}, feature="cover_letter", now=NOW
        )
        assert [c.id for c in got] == ["bad"]

    def test_flaky_is_allowed_for_json_because_callers_retry(self):
        got = select_channels(
            [ch("flaky", verdict="flaky")], {}, feature="resume_tailor", now=NOW
        )
        assert [c.id for c in got] == ["flaky"]

    def test_the_gated_feature_list_covers_the_json_producing_features(self):
        for feature in ("resume_parse", "resume_tailor", "resume_wizard"):
            assert feature in FEATURES_REQUIRING_STRUCTURED_OUTPUT


class TestPinning:
    def test_pin_restricts_to_one_channel(self):
        got = select_channels(
            [ch("a", priority=1), ch("b", priority=2)],
            {},
            feature="cover_letter",
            now=NOW,
            pinned_channel_id="b",
        )
        assert [c.id for c in got] == ["b"]

    def test_pin_still_respects_structured_gating(self):
        """Pinning an unsuitable channel into a JSON feature would produce a
        confusing bug report rather than a useful one."""
        got = select_channels(
            [ch("bad", verdict="unsupported")],
            {},
            feature="resume_tailor",
            now=NOW,
            pinned_channel_id="bad",
        )
        assert got == []


class TestErrorClassification:
    def test_retryable_classes(self):
        for cls in ("timeout", "rate_limit", "server", "connection"):
            assert is_retryable(cls), cls

    def test_non_retryable_classes(self):
        """These fail identically on every provider - failing over only multiplies
        latency and cost."""
        for cls in ("auth", "bad_request", "content_policy"):
            assert not is_retryable(cls), cls

    def test_unknown_is_not_retryable(self):
        """Failing over on an unclassifiable error risks burning every channel on a
        request that was never going to succeed."""
        assert not is_retryable("unknown")

    def test_classifies_common_provider_failures(self):
        cases = [
            (TimeoutError("request timed out"), "timeout"),
            (Exception("429 Too Many Requests"), "rate_limit"),
            (Exception("Error code: 401 - invalid api key"), "auth"),
            (Exception("HTTP 503 Service Unavailable"), "server"),
            (ConnectionError("failed to connect"), "connection"),
            (Exception("400 unprocessable entity"), "bad_request"),
        ]
        for exc, expected in cases:
            assert classify_error(exc) == expected, f"{exc!r} -> {classify_error(exc)}"

    def test_an_auth_error_mentioning_timeout_is_still_auth(self):
        """Ordering guard: a misfiled auth error would fan a doomed request across
        every channel."""
        assert classify_error(Exception("401 unauthorized (connection timed out)")) == "auth"
