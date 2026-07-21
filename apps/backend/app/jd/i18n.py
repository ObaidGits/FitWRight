"""Internationalization for JD extraction (§21 of enhancement plan).

Provides:
- Language detection (html lang -> Content-Language -> script/keyword heuristic)
- Multi-language section keywords (en/de/fr/es/ja/pt)
- Locale-aware salary parsing (currency + number-format normalization)

Zero new dependencies: language detection uses the html lang attribute first,
then a lightweight Unicode-script + keyword heuristic. This keeps latency at
< 1ms and avoids pulling a heavy CLD model into the hot path. When the detected
language is not in the keyword set, section classification is skipped entirely
(no false positives) - confidence is unaffected because sections are a bonus,
not a requirement.
"""

from __future__ import annotations

import re

__all__ = [
    "detect_language",
    "SECTION_KEYWORDS",
    "SUPPORTED_LANGUAGES",
    "section_keywords_for",
    "parse_salary",
]

SUPPORTED_LANGUAGES = ("en", "de", "fr", "es", "ja", "pt")

# Multi-language section keywords (§21). Lowercased for matching.
SECTION_KEYWORDS: dict[str, dict[str, list[str]]] = {
    "responsibilities": {
        "en": ["responsibilities", "what you'll do", "what you will do", "the role", "your role", "duties"],
        "de": ["aufgaben", "ihre aufgaben", "verantwortlichkeiten", "deine aufgaben"],
        "fr": ["responsabilités", "missions", "votre rôle", "vos missions"],
        "es": ["responsabilidades", "funciones", "tu rol", "tus funciones"],
        "ja": ["業務内容", "仕事内容", "担当業務", "職務内容"],
        "pt": ["responsabilidades", "atividades", "o que você fará", "suas atividades"],
    },
    "qualifications": {
        "en": ["qualifications", "requirements", "what we're looking for", "what you'll need", "who you are", "skills"],
        "de": ["qualifikationen", "anforderungen", "ihr profil", "dein profil", "was du mitbringst"],
        "fr": ["qualifications", "profil recherché", "compétences", "exigences", "votre profil"],
        "es": ["requisitos", "cualificaciones", "perfil", "lo que buscamos", "aptitudes"],
        "ja": ["応募資格", "必須要件", "求めるスキル", "歓迎要件", "必要なスキル"],
        "pt": ["requisitos", "qualificações", "o que buscamos", "perfil", "competências"],
    },
    "benefits": {
        "en": ["benefits", "perks", "what we offer", "what's in it for you", "compensation"],
        "de": ["benefits", "wir bieten", "was wir bieten", "vorteile", "leistungen"],
        "fr": ["avantages", "ce que nous offrons", "nos avantages", "rémunération"],
        "es": ["beneficios", "lo que ofrecemos", "ventajas", "qué ofrecemos"],
        "ja": ["待遇", "福利厚生", "給与", "手当"],
        "pt": ["benefícios", "o que oferecemos", "vantagens", "remuneração"],
    },
}

# Currency symbols/codes -> normalized currency code.
_CURRENCY_MAP = {
    "$": "USD", "US$": "USD", "usd": "USD",
    "€": "EUR", "eur": "EUR",
    "£": "GBP", "gbp": "GBP",
    "¥": "JPY", "jpy": "JPY", "円": "JPY",
    "r$": "BRL", "brl": "BRL",
    "chf": "CHF",
    "kr": "SEK",
    "₹": "INR", "inr": "INR", "rs": "INR",
    "c$": "CAD", "cad": "CAD",
    "a$": "AUD", "aud": "AUD",
}

# Period keywords -> normalized period.
_PERIOD_MAP = {
    "year": "YEAR", "yr": "YEAR", "annum": "YEAR", "annual": "YEAR", "pa": "YEAR",
    "p.a.": "YEAR", "jahr": "YEAR", "an": "YEAR", "año": "YEAR", "ano": "YEAR",
    "month": "MONTH", "mo": "MONTH", "monat": "MONTH", "mois": "MONTH", "mes": "MONTH", "mês": "MONTH",
    "hour": "HOUR", "hr": "HOUR", "stunde": "HOUR", "heure": "HOUR", "hora": "HOUR",
    "week": "WEEK", "wk": "WEEK",
    "day": "DAY",
}

# Scripts: quick detection for non-Latin languages.
_JA_RE = re.compile(r"[\u3040-\u30ff\u4e00-\u9faf]")  # hiragana/katakana/kanji

# Language keyword hints for Latin-script disambiguation (stopwords).
# Only DISTINCTIVE multi-character tokens - single-letter articles (a/e/o/y)
# collide across languages and with ordinary English text, so they are excluded.
_LANG_HINTS = {
    "de": (" und ", " der ", " die ", " für ", " mit ", " wir ", " sie ", " nicht ", " werden ", " eine "),
    "fr": (" et ", " les ", " pour ", " nous ", " vous ", " des ", " une ", " avec ", " votre "),
    "es": (" los ", " para ", " con ", " una ", " del ", " que ", " las ", " nuestro ", " buscamos "),
    "pt": (" para ", " com ", " uma ", " você ", " nossa ", " que ", " dos ", " das ", " atividades "),
    "en": (" the ", " and ", " for ", " with ", " you ", " your ", " we ", " are ", " our ", " will "),
}

_LANG_ATTR_RE = re.compile(r"<html[^>]*\blang\s*=\s*[\"']?([a-zA-Z-]{2,})", re.I)


def detect_language(
    html: str | None = None,
    content_language_header: str | None = None,
    text: str | None = None,
) -> str:
    """Detect the primary language, returning a 2-letter code or "" if unknown.

    Priority: <html lang> -> Content-Language header -> text heuristic.
    """
    # 1. html lang attribute (most authoritative, ~0 cost).
    if html:
        m = _LANG_ATTR_RE.search(html)
        if m:
            code = m.group(1).split("-")[0].lower()
            if code:
                return code

    # 2. HTTP Content-Language header.
    if content_language_header:
        code = content_language_header.split(",")[0].split("-")[0].strip().lower()
        if code:
            return code

    # 3. Text heuristic.
    sample = (text or html or "")[:4000]
    if not sample:
        return ""
    if _JA_RE.search(sample):
        return "ja"
    lowered = " " + sample.lower() + " "
    scores: dict[str, int] = {}
    for lang, hints in _LANG_HINTS.items():
        scores[lang] = sum(lowered.count(h) for h in hints)
    best = max(scores, key=lambda k: scores[k]) if scores else ""
    return best if scores.get(best, 0) > 0 else ""


def section_keywords_for(language: str) -> dict[str, list[str]] | None:
    """Return {section: [keywords]} for a language, or None if unsupported.

    Callers that receive None should SKIP section classification (§21).
    """
    lang = (language or "").split("-")[0].lower()
    if lang not in SUPPORTED_LANGUAGES:
        return None
    return {section: langs.get(lang, []) for section, langs in SECTION_KEYWORDS.items()}


# --- Salary parsing ------------------------------------------------------

_NUM_RE = re.compile(r"\d[\d.,\s]*\d|\d")


def _normalize_number(raw: str) -> float | None:
    """Normalize a locale-formatted number string to a float.

    Handles: 120,000 (en) / 120.000 (de,pt) / 120 000 (fr) / 120k / 1,20,000 (in).
    """
    s = raw.strip().replace(" ", "")
    if not s:
        return None

    mult = 1.0
    if s and s[-1] in "kK":
        mult = 1000.0
        s = s[:-1]
    elif s and s[-1] in "mM":
        mult = 1_000_000.0
        s = s[:-1]

    has_comma = "," in s
    has_dot = "." in s
    if has_comma and has_dot:
        # The rightmost separator is the decimal separator.
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")  # de/pt: 120.000,50
        else:
            s = s.replace(",", "")                     # en: 120,000.50
    elif has_comma:
        # Comma alone: decimal if exactly 2 trailing digits, else thousands.
        if re.search(r",\d{2}$", s) and not re.search(r",\d{3}$", s):
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    elif has_dot:
        # Dot alone: thousands separator if it looks like 120.000 (3 trailing).
        if re.search(r"\.\d{3}(\.\d{3})*$", s):
            s = s.replace(".", "")
        # else keep as decimal
    try:
        return float(s) * mult
    except ValueError:
        return None


def _detect_currency(text: str) -> str:
    lowered = text.lower()
    # Multi-char codes / symbols first (longest match wins).
    for token in ("us$", "r$", "c$", "a$", "chf", "usd", "eur", "gbp", "jpy",
                  "brl", "inr", "cad", "aud", "sek", "円"):
        if token in lowered:
            return _CURRENCY_MAP.get(token, token.upper())
    for sym in ("$", "€", "£", "¥", "₹"):
        if sym in text:
            return _CURRENCY_MAP.get(sym, "")
    if re.search(r"\bkr\b", lowered):
        return "SEK"
    return ""


def _detect_period(text: str) -> str:
    lowered = text.lower()
    for token, period in _PERIOD_MAP.items():
        if re.search(r"\b" + re.escape(token) + r"\b", lowered):
            return period
    return ""


def parse_salary(text: str) -> dict | None:
    """Parse a salary string into {min, max, currency, period}.

    Locale-aware: handles $, €, £, ¥, R$, CHF, kr and en/de/in number formats.
    Returns None if no numbers found. All keys may be null when undetectable.
    """
    if not text:
        return None
    nums = _NUM_RE.findall(text)
    values = [v for v in (_normalize_number(n) for n in nums) if v is not None and v >= 100]
    if not values:
        return None
    values.sort()
    result = {
        "min": values[0],
        "max": values[-1] if len(values) > 1 else values[0],
        "currency": _detect_currency(text) or None,
        "period": _detect_period(text) or None,
    }
    return result
