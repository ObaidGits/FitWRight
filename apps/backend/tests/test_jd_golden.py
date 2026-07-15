"""Golden / contract tests for JD extraction (§35, §36 of enhancement plan).

These tests run adapters and extractors against COMMITTED, frozen fixtures
(``tests/fixtures/jd/``) — real-shaped platform API responses and multilingual
JSON-LD pages. They act as contract tests: if an adapter's parsing contract
regresses, or the i18n language/section detection drifts, these fail offline
with no network. Complements the synthetic-input unit tests in
``test_jd_phase3.py``.
"""

import json
from pathlib import Path

import pytest

_FIX = Path(__file__).parent / "fixtures" / "jd"


def _load_json(name: str) -> dict:
    return json.loads((_FIX / "adapters" / name).read_text())


def _load_html(name: str) -> str:
    return (_FIX / "jsonld" / name).read_text(encoding="utf-8")


# ============================================================
# Adapter contract tests (frozen API responses)
# ============================================================

class TestAdapterContracts:
    def test_ashby_contract(self):
        from app.jd.adapters.ashby import AshbyAdapter
        r = AshbyAdapter().parse_response(_load_json("ashby.json"), "https://jobs.ashbyhq.com/weave/x")
        assert r.confidence.level == "HIGH"
        assert r.source == "platform_api"
        assert "Senior Backend Engineer" in r.content
        assert "scalable APIs" in r.content
        assert r.company.value == "Weave"
        assert r.location.value == "Berlin, Germany"
        assert "<" not in r.content  # HTML stripped

    def test_greenhouse_contract(self):
        from app.jd.adapters.greenhouse import GreenhouseAdapter
        r = GreenhouseAdapter().parse_response(_load_json("greenhouse.json"), "https://boards.greenhouse.io/acme/jobs/1")
        assert r.confidence.level == "HIGH"
        assert "Staff Software Engineer" in r.content
        assert r.company.value == "Acme Corp"
        assert "San Francisco, CA" in (r.location.value or "")
        assert "<" not in r.content

    def test_lever_contract(self):
        from app.jd.adapters.lever import LeverAdapter
        r = LeverAdapter().parse_response(_load_json("lever.json"), "https://jobs.lever.co/co/x")
        assert r.confidence.level == "HIGH"
        assert "Product Designer" in r.content
        assert "user research" in r.content
        assert r.location.value == "New York, NY"
        assert r.employment_type.value == "Full-time"
        assert "<" not in r.content

    def test_smartrecruiters_contract(self):
        from app.jd.adapters.smartrecruiters import SmartRecruitersAdapter
        r = SmartRecruitersAdapter().parse_response(_load_json("smartrecruiters.json"), "https://jobs.smartrecruiters.com/DataCo/1")
        assert r.confidence.level == "HIGH"
        assert "Data Scientist" in r.content
        assert "production ML models" in r.content
        assert r.company.value == "DataCo"
        assert "London" in (r.location.value or "")
        assert r.employment_type.value == "Full-time"
        assert "<" not in r.content


# ============================================================
# Multilingual JSON-LD golden tests
# ============================================================

_LANGS = {
    "de.html": ("de", "Senior Backend Entwickler", "aufgaben"),
    "fr.html": ("fr", "Ingenieur Backend Senior", "missions"),
    "es.html": ("es", "Ingeniero Backend Senior", "responsabilidades"),
    "ja.html": ("ja", "シニアバックエンドエンジニア", "業務内容"),
    "pt.html": ("pt", "Engenheiro Backend Senior", "responsabilidades"),
}


class TestMultilingualJsonLd:
    @pytest.mark.parametrize("fixture", list(_LANGS.keys()))
    def test_jsonld_extraction(self, fixture):
        from app.jd.extractors.jsonld import extract_jsonld
        html = _load_html(fixture)
        result = extract_jsonld(html)
        assert result is not None, f"{fixture}: no JobPosting extracted"
        _lang, expected_title, _section = _LANGS[fixture]
        assert expected_title in result.content
        assert result.confidence.level in ("HIGH", "MEDIUM")
        assert len(result.content) >= 400

    @pytest.mark.parametrize("fixture", list(_LANGS.keys()))
    def test_language_detected_from_html_lang(self, fixture):
        from app.jd.i18n import detect_language
        html = _load_html(fixture)
        expected_lang = _LANGS[fixture][0]
        assert detect_language(html=html) == expected_lang

    @pytest.mark.parametrize("fixture", list(_LANGS.keys()))
    def test_section_keywords_available_for_language(self, fixture):
        from app.jd.i18n import section_keywords_for
        expected_lang, _title, expected_section_kw = _LANGS[fixture]
        kw = section_keywords_for(expected_lang)
        assert kw is not None, f"{expected_lang} must be a supported language"
        all_kw = [k for section in kw.values() for k in section]
        assert expected_section_kw in all_kw


# ============================================================
# End-to-end golden: fixture HTML through the full cascade (offline)
# ============================================================

class TestGoldenCascade:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("fixture", list(_LANGS.keys()))
    async def test_cascade_extracts_multilingual(self, fixture, monkeypatch):
        from app.jd import orchestrator
        from app.jd.cache import JdCache
        from app.jd.drift import DriftMonitor
        from app.auth.kvstore.local import LocalKVStore

        kv = LocalKVStore()
        monkeypatch.setattr(orchestrator, "_cache", JdCache(kv))
        monkeypatch.setattr(orchestrator, "_drift", DriftMonitor(kv))

        html = _load_html(fixture)

        async def mock_fetch(url, *a, **k):
            return html

        monkeypatch.setattr(orchestrator, "fetch_url_safely", mock_fetch)

        expected_lang, expected_title, _ = _LANGS[fixture]
        r = await orchestrator.orchestrate_v2("u", f"https://careers.example.com/{expected_lang}", force_refresh=True)
        assert r.source == "json_ld"
        assert expected_title in r.content
        assert r.language == expected_lang
        assert r.fingerprint != ""
