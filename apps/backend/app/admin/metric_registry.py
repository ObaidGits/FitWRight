"""Central, static Metric_Registry — the single source of truth for keys (Req 20).

Every durable observability / product-analytics signal is stored in
``metrics_daily`` under a **Metric_Key**. This module is the *only* place those
keys are defined, and it defines each one as a compile-time ``str`` **literal
constant** with an owning category and a one-line description (Req 20.1).

Design guarantees enforced structurally here (Req 20.2–20.5, Property 8):

- **No runtime-composed keys.** Every key is a literal assigned to a module-level
  constant. Nothing in this module concatenates, formats, or otherwise derives a
  key from a runtime or user-supplied value — a static-analysis/lint test
  (Task 1.5) asserts this.
- **Closed, enumerated dimensions.** Where a metric is dimensioned (AI provider,
  downsamplable audit event), each dimension value is its *own* pre-registered
  static key drawn from a closed set (the 5 per-provider ``AI_CALLS_*`` keys and
  the single ``AUDIT_DOWNSAMPLED_*`` key). The provider/event → key mappings are
  fixed dictionaries over closed :class:`enum.Enum` inputs, so a lookup can only
  ever return an already-registered constant (never a new key). Adding a provider
  or event is a one-line edit here.
- **Bounded cardinality.** The total number of distinct keys changes only by an
  explicit edit to this module — never by runtime data. :func:`all_keys` and
  :data:`METRIC_REGISTRY` let the cardinality test enumerate the full, fixed set.
- **Single source of truth.** Every Domain_Metrics_Service and every Rollup_Step
  references these constants; no key string is written inline elsewhere.

Categories (each with an owning component — see the design's Metric_Registry
table): AI, errors, security, storage, resume, feature-usage, audit-downsample.
The ``in_usage_series`` flag marks the subset the usage-series chart exposes
(``AI_CALLS``, ``REQUEST_5XX``, ``SEC_LOGIN_FAILED``, all ``RESUMES_*`` and all
``FEAT_*``).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = [
    "MetricCategory",
    "AiProvider",
    "DownsamplableEvent",
    "MetricKeySpec",
    "METRIC_REGISTRY",
    "AI_CALLS_BY_PROVIDER",
    "AUDIT_DOWNSAMPLE_BY_EVENT",
    "all_keys",
    "usage_series_keys",
    "keys_for_category",
    "category_of",
    "is_registered",
    "ai_calls_key",
    "audit_downsample_key",
    # AI category
    "AI_CALLS",
    "AI_SUCCESS",
    "AI_FAILURE",
    "AI_TIMEOUTS",
    "AI_RETRIES",
    "AI_TOKENS_SUM",
    "AI_LATENCY_MS_SUM",
    "AI_CALLS_OPENAI",
    "AI_CALLS_GEMINI",
    "AI_CALLS_ANTHROPIC",
    "AI_CALLS_OLLAMA",
    "AI_CALLS_OPENAI_COMPAT",
    # Errors category
    "REQUEST_2XX",
    "REQUEST_4XX",
    "REQUEST_5XX",
    # Security category
    "SEC_LOGIN_FAILED",
    "SEC_ADMIN_LOGIN",
    "SEC_AUTHZ_DENIED",
    "SEC_RATE_LIMITED",
    "SEC_SUSPICIOUS",
    # Storage category
    "DB_SIZE_BYTES",
    # Resume category
    "RESUMES_GENERATED",
    "RESUMES_IMPORTED",
    "RESUMES_TAILORED",
    "RESUMES_DELETED",
    # Feature-usage category
    "FEAT_BUILDER",
    "FEAT_TAILOR",
    "FEAT_PARSER",
    "FEAT_IMPORT",
    "FEAT_COVER_LETTER",
    "FEAT_PROFILE_GEN",
    "FEAT_PORTFOLIO",
    "FEAT_JD_PARSE",
    # Audit-downsample category
    "AUDIT_DOWNSAMPLED_USER_VIEWED",
]


class MetricCategory(str, Enum):
    """Owning category for every Metric_Key (bounded, closed set — Req 20.1)."""

    AI = "ai"
    ERRORS = "errors"
    SECURITY = "security"
    STORAGE = "storage"
    RESUME = "resume"
    FEATURE_USAGE = "feature_usage"
    AUDIT_DOWNSAMPLE = "audit_downsample"


class AiProvider(str, Enum):
    """Closed set of supported AI providers (the AI-call key dimension — Req 20.3).

    A new provider is a one-line addition here plus its static ``AI_CALLS_*``
    key + one :data:`AI_CALLS_BY_PROVIDER` entry — never a runtime-composed key.
    """

    OPENAI = "openai"
    GEMINI = "gemini"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"
    OPENAI_COMPAT = "openai_compat"


class DownsamplableEvent(str, Enum):
    """Closed set of downsamplable audit events (the downsample key dimension)."""

    USER_VIEWED = "admin.user_viewed"


# ---------------------------------------------------------------------------
# Static Metric_Key constants — one documented, category-owned literal each.
# These are the ONLY key definitions in the codebase (Req 20.1/20.5).
# ---------------------------------------------------------------------------

# -- AI (owner: AiMetricsService) -------------------------------------------
AI_CALLS = "ai_calls"  # Total AI provider calls.
AI_SUCCESS = "ai_success"  # Successful AI provider calls.
AI_FAILURE = "ai_failure"  # Failed AI provider calls.
AI_TIMEOUTS = "ai_timeouts"  # AI calls that timed out.
AI_RETRIES = "ai_retries"  # AI call retry attempts.
AI_TOKENS_SUM = "ai_tokens_sum"  # Summed tokens across AI calls (for averages).
AI_LATENCY_MS_SUM = "ai_latency_ms_sum"  # Summed AI latency in ms (for averages).

# Per-provider AI call counts — a closed enumeration (5 static keys), never
# ``ai_calls:<provider>`` composed at runtime (Req 20.2/20.3).
AI_CALLS_OPENAI = "ai_calls_openai"  # AI calls to OpenAI.
AI_CALLS_GEMINI = "ai_calls_gemini"  # AI calls to Gemini.
AI_CALLS_ANTHROPIC = "ai_calls_anthropic"  # AI calls to Anthropic.
AI_CALLS_OLLAMA = "ai_calls_ollama"  # AI calls to Ollama.
AI_CALLS_OPENAI_COMPAT = "ai_calls_openai_compat"  # AI calls to an OpenAI-compatible provider.

# -- Errors (owner: ErrorsMetricsService) -----------------------------------
REQUEST_2XX = "request_2xx"  # Requests answered with a 2xx status.
REQUEST_4XX = "request_4xx"  # Requests answered with a 4xx status.
REQUEST_5XX = "request_5xx"  # Requests answered with a 5xx status.

# -- Security (owner: SecurityMetricsService) -------------------------------
SEC_LOGIN_FAILED = "sec_login_failed"  # Failed login attempts (daily aggregate).
SEC_ADMIN_LOGIN = "sec_admin_login"  # Admin logins (daily aggregate).
SEC_AUTHZ_DENIED = "sec_authz_denied"  # Authorization denials (daily aggregate).
SEC_RATE_LIMITED = "sec_rate_limited"  # Rate-limit hits (daily aggregate).
SEC_SUSPICIOUS = "sec_suspicious"  # Suspicious/blocked requests (daily aggregate).

# -- Storage (owner: StorageMetricsService) ---------------------------------
DB_SIZE_BYTES = "db_size_bytes"  # Sampled database size in bytes (≤ hourly).

# -- Resume (owner: ResumeMetricsService, Product Analytics) ----------------
RESUMES_GENERATED = "resumes_generated"  # Resumes generated (daily aggregate).
RESUMES_IMPORTED = "resumes_imported"  # Resumes imported (daily aggregate).
RESUMES_TAILORED = "resumes_tailored"  # Resumes tailored (daily aggregate).
RESUMES_DELETED = "resumes_deleted"  # Resumes deleted (daily aggregate).

# -- Feature usage (owner: FeatureUsageService, Product Analytics) ----------
FEAT_BUILDER = "feat_builder"  # Resume builder invocations (daily total).
FEAT_TAILOR = "feat_tailor"  # Tailor invocations (daily total).
FEAT_PARSER = "feat_parser"  # Resume parser invocations (daily total).
FEAT_IMPORT = "feat_import"  # Import invocations (daily total).
FEAT_COVER_LETTER = "feat_cover_letter"  # Cover-letter generator invocations (daily total).
FEAT_PROFILE_GEN = "feat_profile_gen"  # Profile generator invocations (daily total).
FEAT_PORTFOLIO = "feat_portfolio"  # Portfolio generator invocations (daily total).
FEAT_JD_PARSE = "feat_jd_parse"  # Job-description parse invocations (daily total).

# -- Audit downsample (owner: Audit_Retention_Job) --------------------------
# A closed enumeration of downsamplable events (initially one static key).
AUDIT_DOWNSAMPLED_USER_VIEWED = "audit_downsampled_user_viewed"  # Aggregated admin.user_viewed rows.


@dataclass(frozen=True, slots=True)
class MetricKeySpec:
    """One registered Metric_Key: its literal, owning category, and description."""

    key: str
    category: MetricCategory
    description: str
    in_usage_series: bool = False


# ---------------------------------------------------------------------------
# The registry — the full, fixed enumeration of every Metric_Key. Ordering is
# by category. This tuple is the single thing the cardinality/lint test walks.
# ---------------------------------------------------------------------------
METRIC_REGISTRY: tuple[MetricKeySpec, ...] = (
    # AI
    MetricKeySpec(AI_CALLS, MetricCategory.AI, "Total AI provider calls.", in_usage_series=True),
    MetricKeySpec(AI_SUCCESS, MetricCategory.AI, "Successful AI provider calls."),
    MetricKeySpec(AI_FAILURE, MetricCategory.AI, "Failed AI provider calls."),
    MetricKeySpec(AI_TIMEOUTS, MetricCategory.AI, "AI calls that timed out."),
    MetricKeySpec(AI_RETRIES, MetricCategory.AI, "AI call retry attempts."),
    MetricKeySpec(AI_TOKENS_SUM, MetricCategory.AI, "Summed tokens across AI calls."),
    MetricKeySpec(AI_LATENCY_MS_SUM, MetricCategory.AI, "Summed AI latency in milliseconds."),
    MetricKeySpec(AI_CALLS_OPENAI, MetricCategory.AI, "AI calls to OpenAI."),
    MetricKeySpec(AI_CALLS_GEMINI, MetricCategory.AI, "AI calls to Gemini."),
    MetricKeySpec(AI_CALLS_ANTHROPIC, MetricCategory.AI, "AI calls to Anthropic."),
    MetricKeySpec(AI_CALLS_OLLAMA, MetricCategory.AI, "AI calls to Ollama."),
    MetricKeySpec(AI_CALLS_OPENAI_COMPAT, MetricCategory.AI, "AI calls to an OpenAI-compatible provider."),
    # Errors
    MetricKeySpec(REQUEST_2XX, MetricCategory.ERRORS, "Requests answered with a 2xx status."),
    MetricKeySpec(REQUEST_4XX, MetricCategory.ERRORS, "Requests answered with a 4xx status."),
    MetricKeySpec(REQUEST_5XX, MetricCategory.ERRORS, "Requests answered with a 5xx status.", in_usage_series=True),
    # Security
    MetricKeySpec(SEC_LOGIN_FAILED, MetricCategory.SECURITY, "Failed login attempts.", in_usage_series=True),
    MetricKeySpec(SEC_ADMIN_LOGIN, MetricCategory.SECURITY, "Admin logins."),
    MetricKeySpec(SEC_AUTHZ_DENIED, MetricCategory.SECURITY, "Authorization denials."),
    MetricKeySpec(SEC_RATE_LIMITED, MetricCategory.SECURITY, "Rate-limit hits."),
    MetricKeySpec(SEC_SUSPICIOUS, MetricCategory.SECURITY, "Suspicious/blocked requests."),
    # Storage
    MetricKeySpec(DB_SIZE_BYTES, MetricCategory.STORAGE, "Sampled database size in bytes."),
    # Resume (Product Analytics)
    MetricKeySpec(RESUMES_GENERATED, MetricCategory.RESUME, "Resumes generated.", in_usage_series=True),
    MetricKeySpec(RESUMES_IMPORTED, MetricCategory.RESUME, "Resumes imported.", in_usage_series=True),
    MetricKeySpec(RESUMES_TAILORED, MetricCategory.RESUME, "Resumes tailored.", in_usage_series=True),
    MetricKeySpec(RESUMES_DELETED, MetricCategory.RESUME, "Resumes deleted.", in_usage_series=True),
    # Feature usage (Product Analytics)
    MetricKeySpec(FEAT_BUILDER, MetricCategory.FEATURE_USAGE, "Resume builder invocations.", in_usage_series=True),
    MetricKeySpec(FEAT_TAILOR, MetricCategory.FEATURE_USAGE, "Tailor invocations.", in_usage_series=True),
    MetricKeySpec(FEAT_PARSER, MetricCategory.FEATURE_USAGE, "Resume parser invocations.", in_usage_series=True),
    MetricKeySpec(FEAT_IMPORT, MetricCategory.FEATURE_USAGE, "Import invocations.", in_usage_series=True),
    MetricKeySpec(FEAT_COVER_LETTER, MetricCategory.FEATURE_USAGE, "Cover-letter generator invocations.", in_usage_series=True),
    MetricKeySpec(FEAT_PROFILE_GEN, MetricCategory.FEATURE_USAGE, "Profile generator invocations.", in_usage_series=True),
    MetricKeySpec(FEAT_PORTFOLIO, MetricCategory.FEATURE_USAGE, "Portfolio generator invocations.", in_usage_series=True),
    MetricKeySpec(FEAT_JD_PARSE, MetricCategory.FEATURE_USAGE, "Job-description parse invocations.", in_usage_series=True),
    # Audit downsample
    MetricKeySpec(AUDIT_DOWNSAMPLED_USER_VIEWED, MetricCategory.AUDIT_DOWNSAMPLE, "Aggregated admin.user_viewed rows."),
)


# ---------------------------------------------------------------------------
# Closed dimension → static-key maps. A lookup can only ever return an
# already-registered constant, so no key is composed at runtime (Req 20.2/20.3).
# ---------------------------------------------------------------------------
AI_CALLS_BY_PROVIDER: dict[AiProvider, str] = {
    AiProvider.OPENAI: AI_CALLS_OPENAI,
    AiProvider.GEMINI: AI_CALLS_GEMINI,
    AiProvider.ANTHROPIC: AI_CALLS_ANTHROPIC,
    AiProvider.OLLAMA: AI_CALLS_OLLAMA,
    AiProvider.OPENAI_COMPAT: AI_CALLS_OPENAI_COMPAT,
}

AUDIT_DOWNSAMPLE_BY_EVENT: dict[DownsamplableEvent, str] = {
    DownsamplableEvent.USER_VIEWED: AUDIT_DOWNSAMPLED_USER_VIEWED,
}


# ---------------------------------------------------------------------------
# Enumeration / lookup helpers (used by the cardinality + lint tests, Task 1.5,
# and by every Domain_Metrics_Service / Rollup_Step needing the key set).
# ---------------------------------------------------------------------------

# Fast lookups derived once from the static registry (still fully static).
_SPEC_BY_KEY: dict[str, MetricKeySpec] = {spec.key: spec for spec in METRIC_REGISTRY}


def all_keys() -> frozenset[str]:
    """Return the complete, fixed set of registered Metric_Keys (Req 20.4)."""
    return frozenset(_SPEC_BY_KEY)


def usage_series_keys() -> frozenset[str]:
    """Return the subset of keys exposed by the usage-series chart."""
    return frozenset(spec.key for spec in METRIC_REGISTRY if spec.in_usage_series)


def keys_for_category(category: MetricCategory) -> frozenset[str]:
    """Return every registered key owned by ``category``."""
    return frozenset(spec.key for spec in METRIC_REGISTRY if spec.category is category)


def category_of(key: str) -> MetricCategory:
    """Return the owning category for ``key`` (raises ``KeyError`` if unknown)."""
    return _SPEC_BY_KEY[key].category


def is_registered(key: str) -> bool:
    """Return whether ``key`` is a registered static Metric_Key."""
    return key in _SPEC_BY_KEY


def ai_calls_key(provider: AiProvider) -> str:
    """Return the static per-provider AI-call key for ``provider`` (closed map)."""
    return AI_CALLS_BY_PROVIDER[provider]


def audit_downsample_key(event: DownsamplableEvent) -> str:
    """Return the static downsample key for ``event`` (closed map)."""
    return AUDIT_DOWNSAMPLE_BY_EVENT[event]


# Fail fast at import time if the registry ever drifts into an inconsistent
# state (duplicate keys, or a dimension key missing from the registry). This is
# a static invariant on constants, not runtime data.
assert len(_SPEC_BY_KEY) == len(METRIC_REGISTRY), "duplicate Metric_Key in registry"
assert set(AI_CALLS_BY_PROVIDER) == set(AiProvider), "AI provider map is not exhaustive"
assert set(AUDIT_DOWNSAMPLE_BY_EVENT) == set(DownsamplableEvent), "downsample map is not exhaustive"
assert all(v in _SPEC_BY_KEY for v in AI_CALLS_BY_PROVIDER.values()), "unregistered per-provider key"
assert all(v in _SPEC_BY_KEY for v in AUDIT_DOWNSAMPLE_BY_EVENT.values()), "unregistered downsample key"
