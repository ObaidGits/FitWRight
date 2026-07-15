"""PDF extraction pipeline (§20 of enhancement plan).

Detects and extracts job descriptions from PDF sources. Native-text PDFs are
parsed with pdfminer.six (already a backend dependency). Image-only/scanned PDFs
optionally fall back to Tesseract OCR when ``JD_OCR_ENABLED`` is set AND the
optional deps (pytesseract + pdf2image + a system tesseract binary) are present;
otherwise a scanned PDF returns a classified LOW-confidence failure with a clear
"upload the PDF directly" suggestion (honest failure over garbage).

Unsupported sources are detected and rejected with specific error codes:
- Google Docs (docs.google.com/document/) → UNSUPPORTED_PLATFORM
- Notion (notion.so / *.notion.site)       → UNSUPPORTED_PLATFORM
- DOCX / office open XML                    → UNSUPPORTED_FORMAT

Limits (§20): 10 MB, 20 pages, 30s timeout.
"""

from __future__ import annotations

import io
import logging
import re
from urllib.parse import urlparse

from app.jd.models import (
    ConfidenceResult,
    ExtractionExplanation,
    ExtractionResult,
    FieldProvenance,
)

logger = logging.getLogger(__name__)

__all__ = [
    "is_pdf_url",
    "looks_like_pdf",
    "detect_unsupported_source",
    "extract_pdf",
    "VERSION",
]

VERSION = "1.0.0"

MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_PAGES = 20

# A PDF is "native text" if the extracted text passes a quality bar; otherwise
# we treat it as image-only and (optionally) OCR it.
_MIN_NATIVE_CHARS = 300
# Garbage detection: ratio of printable-ish chars must be high.
_MIN_PRINTABLE_RATIO = 0.85


def is_pdf_url(url: str) -> bool:
    """True if the URL path ends in .pdf."""
    path = urlparse(url).path.lower()
    return path.endswith(".pdf")


def looks_like_pdf(data: bytes, content_type: str = "") -> bool:
    """True if the bytes/content-type indicate a PDF."""
    if content_type == "application/pdf":
        return True
    return data[:5] == b"%PDF-"


def detect_unsupported_source(url: str, content_type: str = "") -> str | None:
    """Return an error code if the source is a known-unsupported document type.

    Returns None if the source is not one of the recognized unsupported types.
    """
    host = (urlparse(url).hostname or "").lower()
    path = urlparse(url).path.lower()

    if host == "docs.google.com" and "/document/" in path:
        return "UNSUPPORTED_PLATFORM"
    if host == "notion.so" or host.endswith(".notion.site") or host == "www.notion.so":
        return "UNSUPPORTED_PLATFORM"
    # Office Open XML (docx/xlsx/pptx) and legacy office.
    if content_type in (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
        "application/vnd.oasis.opendocument.text",
    ):
        return "UNSUPPORTED_FORMAT"
    if path.endswith((".docx", ".doc", ".odt", ".pptx", ".xlsx")):
        return "UNSUPPORTED_FORMAT"
    return None


def _printable_ratio(text: str) -> float:
    if not text:
        return 0.0
    printable = sum(1 for c in text if c.isprintable() or c in "\n\r\t")
    return printable / len(text)


def _clean_text(text: str) -> str:
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _unsupported_result(url: str, code: str) -> ExtractionResult:
    messages = {
        "UNSUPPORTED_PLATFORM": (
            "This document type isn't directly supported.",
            "Export the document to PDF or paste the job description text directly.",
        ),
        "UNSUPPORTED_FORMAT": (
            "This file format isn't supported for automatic extraction.",
            "Paste the job description text directly, or upload a PDF.",
        ),
        "AUTH_REQUIRED": (
            "This PDF is password-protected.",
            "Remove the password or paste the job description text directly.",
        ),
    }
    summary, suggestion = messages.get(code, ("Could not process this document.", "Paste the text directly."))
    return ExtractionResult(
        content="",
        confidence=ConfidenceResult(level="LOW", score=0, reasons=[code]),
        explanation=ExtractionExplanation(summary=summary, warnings=[code], suggestions=[suggestion]),
        source="pdf_ocr",
        submitted_url=url,
        error_code=code,
    )


def _extract_native(data: bytes) -> tuple[str, int]:
    """Extract text + page count using pdfminer.six. Returns (text, n_pages)."""
    from pdfminer.high_level import extract_text
    from pdfminer.pdfpage import PDFPage

    bio = io.BytesIO(data)
    try:
        n_pages = sum(1 for _ in PDFPage.get_pages(bio, maxpages=MAX_PAGES + 1))
    except Exception:
        n_pages = 0
    bio.seek(0)
    text = extract_text(bio, maxpages=MAX_PAGES) or ""
    return text, n_pages


def _ocr_available() -> bool:
    try:
        import pytesseract  # noqa: F401
        import pdf2image  # noqa: F401
        return True
    except Exception:
        return False


def _extract_ocr(data: bytes) -> str:
    """OCR an image-only PDF. Requires optional deps + system tesseract."""
    import pdf2image
    import pytesseract

    images = pdf2image.convert_from_bytes(data, fmt="png", first_page=1, last_page=MAX_PAGES)
    parts: list[str] = []
    for img in images:
        parts.append(pytesseract.image_to_string(img) or "")
    return "\n\n".join(parts)


def extract_pdf(data: bytes, url: str = "", *, ocr_enabled: bool = False) -> ExtractionResult:
    """Extract a JD from raw PDF bytes.

    Returns an ExtractionResult; on failure the result has empty content, an
    ``error_code``, and an actionable suggestion (never fabricated content).
    """
    if len(data) > MAX_PDF_BYTES:
        return ExtractionResult(
            content="",
            confidence=ConfidenceResult(level="LOW", score=0, reasons=["PDF too large"]),
            explanation=ExtractionExplanation(
                summary="This PDF exceeds the 10 MB size limit.",
                warnings=["pdf_too_large"],
                suggestions=["Paste the job description text directly."],
            ),
            source="pdf_ocr", submitted_url=url, error_code="PDF_TOO_LARGE",
        )

    if not looks_like_pdf(data):
        return _unsupported_result(url, "UNSUPPORTED_FORMAT")

    # Password-protected detection (pdfminer raises on encrypted docs).
    try:
        native_text, n_pages = _extract_native(data)
    except Exception as exc:  # pragma: no cover - depends on pdf internals
        name = type(exc).__name__.lower()
        if "password" in str(exc).lower() or "encrypt" in name or "pdfpassword" in name:
            return _unsupported_result(url, "AUTH_REQUIRED")
        logger.debug("PDF native extraction failed: %s", exc)
        native_text, n_pages = "", 0

    if n_pages > MAX_PAGES:
        return ExtractionResult(
            content="",
            confidence=ConfidenceResult(level="LOW", score=0, reasons=["PDF exceeds page limit"]),
            explanation=ExtractionExplanation(
                summary=f"This PDF has more than {MAX_PAGES} pages.",
                warnings=["pdf_too_many_pages"],
                suggestions=["Paste the relevant job description text directly."],
            ),
            source="pdf_ocr", submitted_url=url, error_code="PDF_TOO_MANY_PAGES",
        )

    cleaned = _clean_text(native_text)
    quality = _printable_ratio(cleaned)

    # Native text is good enough → return with MEDIUM-HIGH confidence.
    if len(cleaned) >= _MIN_NATIVE_CHARS and quality >= _MIN_PRINTABLE_RATIO:
        score = 78 if len(cleaned) >= 800 else 65
        return ExtractionResult(
            content=cleaned,
            confidence=ConfidenceResult(
                level="HIGH" if score >= 70 else "MEDIUM",
                score=score,
                reasons=["Native PDF text", f"{len(cleaned)} chars extracted"],
            ),
            explanation=ExtractionExplanation(
                summary="Extracted text directly from the PDF.",
                suggestions=["Verify the extracted text — PDF layout can affect ordering."]
                if score < 70 else [],
            ),
            source="pdf_ocr",
            title=FieldProvenance(
                value=cleaned.split("\n", 1)[0][:200], source="pdf_ocr",
                confidence=60, extractor_version=VERSION, raw_location="pdf:first_line",
            ),
            submitted_url=url,
        )

    # Image-only / low-quality → OCR fallback (optional).
    if ocr_enabled and _ocr_available():
        try:
            ocr_text = _clean_text(_extract_ocr(data))
        except Exception as exc:
            logger.debug("PDF OCR failed: %s", exc)
            ocr_text = ""
        if len(ocr_text) >= _MIN_NATIVE_CHARS:
            return ExtractionResult(
                content=ocr_text,
                confidence=ConfidenceResult(
                    level="LOW", score=40,
                    reasons=["OCR extraction (scanned PDF)", "May contain recognition errors"],
                ),
                explanation=ExtractionExplanation(
                    summary="Extracted text via OCR from a scanned PDF.",
                    warnings=["OCR output may contain recognition errors."],
                    suggestions=["Upload the PDF directly for higher confidence, or paste the text."],
                ),
                source="pdf_ocr", submitted_url=url,
            )

    # Nothing usable — honest failure.
    reason = "scanned_pdf_no_ocr" if not ocr_enabled else "pdf_no_extractable_text"
    return ExtractionResult(
        content="",
        confidence=ConfidenceResult(level="LOW", score=0, reasons=["No extractable text in PDF"]),
        explanation=ExtractionExplanation(
            summary="This looks like a scanned/image-only PDF with no extractable text.",
            warnings=[reason],
            suggestions=["Paste the job description text directly.", "Upload the PDF directly if supported."],
        ),
        source="pdf_ocr", submitted_url=url, error_code="PDF_NO_TEXT",
    )
