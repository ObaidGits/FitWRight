"""Unit tests for the rendered-PDF cache and meaningful PDF filenames."""

from __future__ import annotations


from app.pdf_cache import PdfRenderCache, make_pdf_cache_key
from app.routers.resumes import _pdf_content_disposition, _slug_filename_part


class TestMakePdfCacheKey:
    def test_stable_for_identical_inputs(self):
        a = make_pdf_cache_key(kind="resume", resume_id="r1", params="template=modern", content={"x": 1})
        b = make_pdf_cache_key(kind="resume", resume_id="r1", params="template=modern", content={"x": 1})
        assert a == b

    def test_changes_when_content_changes(self):
        a = make_pdf_cache_key(kind="resume", resume_id="r1", params="p", content={"x": 1})
        b = make_pdf_cache_key(kind="resume", resume_id="r1", params="p", content={"x": 2})
        assert a != b

    def test_changes_when_settings_change(self):
        a = make_pdf_cache_key(kind="resume", resume_id="r1", params="template=modern", content={})
        b = make_pdf_cache_key(kind="resume", resume_id="r1", params="template=latex", content={})
        assert a != b


class TestPdfRenderCache:
    async def test_set_then_get_round_trips(self):
        cache = PdfRenderCache(max_entries=4, ttl_seconds=60)
        await cache.set("k", b"pdf-bytes")
        assert await cache.get("k") == b"pdf-bytes"

    async def test_miss_returns_none(self):
        cache = PdfRenderCache(max_entries=4, ttl_seconds=60)
        assert await cache.get("absent") is None

    async def test_empty_value_is_not_stored(self):
        cache = PdfRenderCache(max_entries=4, ttl_seconds=60)
        await cache.set("k", b"")
        assert await cache.get("k") is None

    async def test_lru_eviction_drops_oldest(self):
        cache = PdfRenderCache(max_entries=2, ttl_seconds=60)
        await cache.set("a", b"1")
        await cache.set("b", b"2")
        await cache.get("a")  # touch 'a' so 'b' is now least-recently-used
        await cache.set("c", b"3")  # evicts 'b'
        assert await cache.get("a") == b"1"
        assert await cache.get("c") == b"3"
        assert await cache.get("b") is None

    async def test_expired_entry_misses(self, monkeypatch):
        import app.pdf_cache as mod

        now = {"t": 1000.0}
        monkeypatch.setattr(mod.time, "monotonic", lambda: now["t"])
        cache = PdfRenderCache(max_entries=4, ttl_seconds=10)
        await cache.set("k", b"v")
        now["t"] = 1005.0
        assert await cache.get("k") == b"v"  # within TTL
        now["t"] = 1011.0
        assert await cache.get("k") is None  # expired


class TestSlugFilenamePart:
    def test_strips_accents_and_symbols(self):
        assert _slug_filename_part("Obaïd  Zeeshan!") == "Obaid_Zeeshan"

    def test_empty_for_none(self):
        assert _slug_filename_part(None) == ""

    def test_caps_length(self):
        assert _slug_filename_part("a" * 100, 10) == "a" * 10


class TestPdfContentDisposition:
    def test_uses_name_and_title(self):
        resume = {"processed_data": {"personalInfo": {"name": "Obaid Zeeshan", "title": "Full Stack Dev"}}}
        cd = _pdf_content_disposition(resume, "r1", "resume")["Content-Disposition"]
        assert 'filename="Obaid_Zeeshan_Full_Stack_Dev_Resume.pdf"' in cd
        assert "filename*=UTF-8''" in cd

    def test_name_only(self):
        resume = {"processed_data": {"personalInfo": {"name": "Obaid Zeeshan"}}}
        cd = _pdf_content_disposition(resume, "r1", "resume")["Content-Disposition"]
        assert 'filename="Obaid_Zeeshan_Resume.pdf"' in cd

    def test_cover_letter_kind(self):
        resume = {"processed_data": {"personalInfo": {"name": "Obaid Zeeshan"}}}
        cd = _pdf_content_disposition(resume, "r1", "cover-letter")["Content-Disposition"]
        assert 'filename="Obaid_Zeeshan_Cover_Letter.pdf"' in cd

    def test_falls_back_to_id_when_no_name(self):
        resume = {"processed_data": {}}
        cd = _pdf_content_disposition(resume, "f800600f-63dc-4651", "resume")["Content-Disposition"]
        assert 'filename="resume-f800600f.pdf"' in cd
