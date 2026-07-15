"""PDF render concurrency-gate tests (backpressure / DoS protection).

The gate wraps render_resume_pdf with a semaphore sized by
settings.pdf_max_concurrency; excess concurrent renders fail fast with
PDFRenderError (→ 503) instead of piling up. These tests drive the gate with a
fake impl (no real Chromium) so they are fast and deterministic.
"""

from __future__ import annotations

import asyncio

import pytest

import app.pdf as pdfmod
from app import config as cfg
from app.pdf import PDFRenderError, render_resume_pdf


async def test_excess_concurrent_render_fails_fast(monkeypatch):
    monkeypatch.setattr(cfg.settings, "pdf_max_concurrency", 1)
    monkeypatch.setattr(cfg.settings, "pdf_render_queue_timeout_seconds", 1)
    pdfmod._render_semaphore = None  # force rebuild at the new size

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_impl(url, page_size="A4", selector=".resume-print", margins=None):
        started.set()
        await release.wait()
        return b"PDF"

    monkeypatch.setattr(pdfmod, "_render_resume_pdf_impl", slow_impl)

    first = asyncio.create_task(render_resume_pdf("http://x/1"))
    await asyncio.wait_for(started.wait(), timeout=2)  # first holds the only slot

    # Second render can't get a slot within the 1s queue timeout → fails fast.
    with pytest.raises(PDFRenderError, match="busy"):
        await render_resume_pdf("http://x/2")

    release.set()
    assert await first == b"PDF"


async def test_concurrency_up_to_limit_succeeds(monkeypatch):
    monkeypatch.setattr(cfg.settings, "pdf_max_concurrency", 2)
    monkeypatch.setattr(cfg.settings, "pdf_render_queue_timeout_seconds", 2)
    pdfmod._render_semaphore = None

    async def impl(url, page_size="A4", selector=".resume-print", margins=None):
        await asyncio.sleep(0.05)
        return b"PDF"

    monkeypatch.setattr(pdfmod, "_render_resume_pdf_impl", impl)

    results = await asyncio.gather(
        render_resume_pdf("http://x/a"), render_resume_pdf("http://x/b")
    )
    assert results == [b"PDF", b"PDF"]


async def test_slot_released_on_impl_error(monkeypatch):
    """A failing render must release its slot (no permanent slot leak)."""
    monkeypatch.setattr(cfg.settings, "pdf_max_concurrency", 1)
    monkeypatch.setattr(cfg.settings, "pdf_render_queue_timeout_seconds", 2)
    pdfmod._render_semaphore = None

    calls = {"n": 0}

    async def failing(url, page_size="A4", selector=".resume-print", margins=None):
        calls["n"] += 1
        raise PDFRenderError("boom")

    monkeypatch.setattr(pdfmod, "_render_resume_pdf_impl", failing)

    for _ in range(3):
        with pytest.raises(PDFRenderError, match="boom"):
            await render_resume_pdf("http://x")
    # If the slot leaked, the 2nd/3rd calls would time out with "busy" instead.
    assert calls["n"] == 3
