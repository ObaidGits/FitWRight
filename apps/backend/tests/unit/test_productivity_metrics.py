"""Unit tests for productivity metrics counters (P3 §Observability, R17.5)."""

from __future__ import annotations

from app.productivity.metrics import get_productivity_metrics, reset_productivity_metrics


def test_counters_increment():
    reset_productivity_metrics()
    m = get_productivity_metrics()
    m.jd_fetch("ok")
    m.jd_fetch("ok")
    m.jd_fetch("failed")
    m.jd_blocked_ssrf()
    m.avatar_upload("rejected")
    m.notification_created()
    m.notification_deduped()
    m.reminders_fired(3)
    m.interview_leads_fired(2)
    m.ai_cleanup("ok")
    snap = m.snapshot()
    assert snap["jd_fetch_total.ok"] == 2
    assert snap["jd_fetch_total.failed"] == 1
    assert snap["jd_blocked_ssrf_total"] == 1
    assert snap["avatar_upload_total.rejected"] == 1
    assert snap["notification_created_total"] == 1
    assert snap["notification_deduped_total"] == 1
    assert snap["scheduler_reminders_fired_total"] == 3
    assert snap["scheduler_interview_leads_fired_total"] == 2
    assert snap["ai_cleanup_total.ok"] == 1


def test_reset_clears():
    m = get_productivity_metrics()
    m.jd_fetch("ok")
    reset_productivity_metrics()
    assert get_productivity_metrics().snapshot() == {}
