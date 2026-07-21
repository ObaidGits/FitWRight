"""PDF appearance resolver - query override -> stored template -> default.

Guards the WYSIWYG export contract (audit Bug #1): a bare PDF request must
render the resume's PERSISTED template, an explicit query param overrides it,
and malformed values fall back to safe defaults.
"""

from app.routers.resumes import _resolve_pdf_settings

STORED = {
    "template": "latex",
    "pageSize": "LETTER",
    "margins": {"top": 12, "bottom": 12, "left": 8, "right": 8},
    "spacing": {"section": 4, "item": 3, "lineHeight": 4},
    "fontSize": {"base": 3, "headerScale": 4, "headerFont": "serif", "bodyFont": "serif"},
    "compactMode": True,
    "showContactIcons": True,
    "accentColor": "green",
}


def test_defaults_when_nothing_stored_or_overridden():
    r = _resolve_pdf_settings(None, {})
    assert r["template"] == "swiss-single"
    assert r["pageSize"] == "A4"
    assert r["marginTop"] == 10
    assert r["accentColor"] == "blue"
    assert r["compactMode"] is False


def test_stored_template_used_without_query_params():
    r = _resolve_pdf_settings(STORED, {})
    assert r["template"] == "latex"
    assert r["pageSize"] == "LETTER"
    assert r["marginTop"] == 12
    assert r["marginLeft"] == 8
    assert r["sectionSpacing"] == 4
    assert r["headerFont"] == "serif"
    assert r["compactMode"] is True
    assert r["showContactIcons"] is True
    assert r["accentColor"] == "green"


def test_query_override_beats_stored():
    r = _resolve_pdf_settings(STORED, {"template": "modern", "accentColor": "red"})
    assert r["template"] == "modern"
    assert r["accentColor"] == "red"
    # Untouched settings still come from stored.
    assert r["pageSize"] == "LETTER"


def test_malformed_stored_falls_back_and_clamps():
    r = _resolve_pdf_settings(
        {"template": "not-a-template", "margins": {"top": 999}, "accentColor": "purple"}, {}
    )
    assert r["template"] == "swiss-single"  # invalid enum -> default
    assert r["marginTop"] == 25  # clamped to max
    assert r["accentColor"] == "blue"  # invalid enum -> default


def test_non_dict_stored_is_safe():
    assert _resolve_pdf_settings("garbage", {})["template"] == "swiss-single"
    assert _resolve_pdf_settings(["x"], {})["pageSize"] == "A4"
