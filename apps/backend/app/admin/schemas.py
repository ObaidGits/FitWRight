"""Admin API response/request models with a strict field allowlist (Task 1.2).

Every admin response is an explicit Pydantic model with ``extra="forbid"`` and
is constructed field-by-field from allowlisted values (never ``**row.__dict__``),
so a new column on ``users`` can never ride along into an admin response. The
:data:`FORBIDDEN_SUBSTRINGS` + :func:`assert_no_forbidden_fields` pair is the
serialization safeguard asserted by the security suite (R14.3, Property 2):
password hashes, session/CSRF tokens, api-keys (even masked), and OAuth tokens
never serialize — the api-key state is surfaced only as ``aiConfigured: bool``.

All timestamps are the stored UTC ISO strings (the frontend renders local time
with a UTC tooltip). Field names are camelCase to match the P1 ``SafeUser``
convention and the existing typed frontend ``adminApi`` shape.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "FORBIDDEN_SUBSTRINGS",
    "assert_no_forbidden_fields",
    "AdminUserRow",
    "AdminUserList",
    "AuditEntry",
    "AdminUserDetail",
    "AdminStats",
    "UsageSeriesPoint",
    "UsageSeries",
    "AuditList",
    "PatchUserRequest",
    "DeleteUserRequest",
    "BulkDisableRequest",
    "MutationResult",
    "BulkDisableResult",
    "MaintenanceResult",
    # -- Observability / product-analytics shared submodels (Task 5.1) --
    "SeriesPoint",
    "HealthTile",
    "ReleaseInfo",
    "JobRow",
    "ProviderCount",
    "RouteClassFailures",
    "ErrorsBySource",
    "RouteClassLatency",
    "SlowJob",
    "KpiValue",
    "FeatureSeries",
    "TemplateCount",
    "ResumeSourceSplit",
    # -- Observability / product-analytics response models (Task 5.1) --
    "AdminHealth",
    "AiAnalytics",
    "ErrorsSummary",
    "PerformanceSignals",
    "StoragePanel",
    "JobsPanel",
    "SecurityView",
    "ConfigDiagnostics",
    "OverviewKpis",
    "FeatureUsage",
    "ResumeAnalytics",
]

# Substrings that must never appear as a key anywhere in an admin response body
# (defense-in-depth against a widened model leaking a secret — R14.3).
FORBIDDEN_SUBSTRINGS: tuple[str, ...] = (
    "password",
    "passwd",
    "hash",
    "secret",
    "token",
    "ciphertext",
    "apikey",
    "api_key",
    "csrf",
    "cookie",
    "credential",
    "private",
)


def assert_no_forbidden_fields(payload: Any, *, path: str = "") -> Any:
    """Recursively assert no key in ``payload`` matches a forbidden substring.

    Raises :class:`ValueError` naming the offending key path. Used at the
    response boundary (tests + optional runtime guard) so a regression that
    surfaces a secret-bearing field fails loudly rather than leaking.

    ``meta`` values are already sanitized by the audit writer; the *keys* are
    what we scan. A legitimate ``ipHash`` / ``tokenHash``-style key would trip
    this, so admin models deliberately avoid such names (ip is exposed as
    ``ipHash`` — allowed via the explicit exception below).
    """
    allowed_exact = {"ipHash"}  # ip_hash is a salted HMAC, not a secret (R12.5)
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_str = str(key)
            if key_str not in allowed_exact:
                lowered = key_str.casefold()
                for marker in FORBIDDEN_SUBSTRINGS:
                    if marker in lowered:
                        raise ValueError(
                            f"admin response leaked forbidden field: {path}{key_str}"
                        )
            assert_no_forbidden_fields(value, path=f"{path}{key_str}.")
    elif isinstance(payload, (list, tuple)):
        for i, item in enumerate(payload):
            assert_no_forbidden_fields(item, path=f"{path}{i}.")
    return payload


class AdminUserRow(BaseModel):
    """One row in the admin user list — allowlisted user-management metadata."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    email: str
    role: str
    status: str
    emailVerified: bool
    createdAt: str
    deletedAt: str | None = None
    purgeDueAt: str | None = None
    resumeCount: int = 0
    applicationCount: int = 0
    lastActiveAt: str | None = None


class AdminUserList(BaseModel):
    """Cursor-paginated user list envelope."""

    model_config = ConfigDict(extra="forbid")

    items: list[AdminUserRow]
    nextCursor: str | None = None


class AuditEntry(BaseModel):
    """One append-only audit row projected for the admin audit view."""

    model_config = ConfigDict(extra="forbid")

    id: str
    ts: str
    event: str
    actorUserId: str | None = None
    targetUserId: str | None = None
    ipHash: str | None = None
    requestId: str | None = None
    meta: dict[str, Any] | None = None


class AuditList(BaseModel):
    """Cursor-paginated audit list envelope."""

    model_config = ConfigDict(extra="forbid")

    items: list[AuditEntry]
    nextCursor: str | None = None


class AdminUserDetail(BaseModel):
    """User detail — profile + activity summary + recent audit (content-free)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    email: str
    role: str
    status: str
    emailVerified: bool
    createdAt: str
    updatedAt: str
    deletedAt: str | None = None
    purgeDueAt: str | None = None
    # activity summary
    resumeCount: int = 0
    tailoredCount: int = 0
    applicationCount: int = 0
    lastActiveAt: str | None = None
    signupMethod: str = "password"
    aiConfigured: bool = False
    recentAudit: list[AuditEntry] = Field(default_factory=list)


class AdminStats(BaseModel):
    """Overview dashboard stats with precisely-defined semantics (R2.1)."""

    model_config = ConfigDict(extra="forbid")

    totalUsers: int
    activeUsers: int
    disabledUsers: int
    totalResumes: int
    resumesTailored: int
    applications: int
    coverLettersGenerated: int
    interviewPrepsGenerated: int
    outreachGenerated: int
    signups: int
    computedAt: str
    stale: bool = False


class UsageSeriesPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str  # YYYY-MM-DD (UTC day)
    value: int


class UsageSeries(BaseModel):
    """Daily time-series for a registry metric over a window."""

    model_config = ConfigDict(extra="forbid")

    metric: str
    window: int
    points: list[UsageSeriesPoint]
    computedAt: str


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class PatchUserRequest(BaseModel):
    """``PATCH /admin/users/{id}`` — set status and/or role (both optional)."""

    model_config = ConfigDict(extra="forbid")

    status: str | None = Field(default=None)
    role: str | None = Field(default=None)


class DeleteUserRequest(BaseModel):
    """``POST /admin/users/{id}/delete`` — typed email confirmation (R8.1/14.1)."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=320)


class BulkDisableRequest(BaseModel):
    """``POST /admin/users/bulk-disable`` — bounded batch of target ids (R6.4)."""

    model_config = ConfigDict(extra="forbid")

    ids: list[str] = Field(min_length=1)


class MutationResult(BaseModel):
    """Standard result of a lifecycle mutation (idempotent no-op → changed:false)."""

    model_config = ConfigDict(extra="forbid")

    changed: bool
    user: AdminUserRow | None = None


class BulkDisableResult(BaseModel):
    """Per-target outcome of a bulk-disable batch (R6.4)."""

    model_config = ConfigDict(extra="forbid")

    results: list[dict[str, Any]]
    disabled: int
    skipped: int


class MaintenanceResult(BaseModel):
    """Result of a single Maintenance_Action (admin-panel-upgrade Req 18).

    Secret-free by construction: only the echoed ``action`` name (one of the four
    fixed actions) and the ``status`` outcome are returned. ``status`` is one of:

    - ``started`` — the underlying single-flighted job/refresh was (re-)invoked;
    - ``already_running`` — the job's single-flight lock was already held, so no
      second run was started (Req 18.4);
    - ``disabled`` — the underlying job is gated off by a kill-switch (the cleanup
      job when ``ADMIN_DESTRUCTIVE_ACTIONS`` is off).
    """

    model_config = ConfigDict(extra="forbid")

    action: str
    status: Literal["started", "already_running", "disabled"]


# ===========================================================================
# Observability + Product-Analytics response models (Task 5.1)
#
# Every model below is aggregate-only and secret-free: it exposes only counts,
# rates, timestamps, and boolean presence indicators — never a secret value,
# raw log line, per-event row, or per-user surveillance field. Config secrets
# surface solely as presence booleans (Req 10.4), and release fields are
# secret-free (Req 17.3); every model passes ``assert_no_forbidden_fields``
# (Req 15.7 / Property 3). All timestamps are stored UTC ISO strings.
#
# Fields that can be unavailable during a partial outage are ``Optional`` with
# an explicit ``*Stale`` / ``*Unavailable`` marker so a dashboard degrades
# gracefully instead of hard-failing (see design §Error Handling). Field names
# are camelCase to match the existing admin models (no alias generator).
# ===========================================================================


class SeriesPoint(BaseModel):
    """One point in a daily aggregate time-series (UTC day → integer value)."""

    model_config = ConfigDict(extra="forbid")

    date: str  # YYYY-MM-DD (UTC day)
    value: int


# ---------------------------------------------------------------------------
# Health (Req 3, 8, 17) — tiles + release fields + jobs table
# ---------------------------------------------------------------------------


class HealthTile(BaseModel):
    """One traffic-light subsystem tile (Backend/DB/KV/AI/Storage/Migrations)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    status: Literal["ok", "degraded", "down"]
    detail: str | None = None


class ReleaseInfo(BaseModel):
    """Secret-free release/deployment metadata (Req 17)."""

    model_config = ConfigDict(extra="forbid")

    version: str
    build: str | None = None
    commit: str | None = None
    migrationApplied: str | None = None
    migrationHead: str | None = None
    env: str


class JobRow(BaseModel):
    """One background-job row, shared by ``AdminHealth`` and ``JobsPanel`` (Req 8)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    lastRun: str | None = None
    lastOutcome: Literal["success", "failure", "skipped"] | None = None
    lagSeconds: int | None = None
    nextRun: str | None = None
    lastSuccess: str | None = None
    runningSince: str | None = None
    currentDurationSeconds: int | None = None
    expectedDurationSeconds: int | None = None
    potentiallyStuck: bool = False
    lockState: Literal["held", "free"] | None = None


class AdminHealth(BaseModel):
    """``GET /admin/health`` — six subsystem tiles + release fields + jobs table."""

    model_config = ConfigDict(extra="forbid")

    tiles: list[HealthTile] = Field(default_factory=list)
    release: ReleaseInfo
    backendUptimeSeconds: int | None = None
    jobs: list[JobRow] = Field(default_factory=list)
    computedAt: str
    stale: bool = False


# ---------------------------------------------------------------------------
# AI analytics (Req 4) — allowlisted call aggregates + cost
# ---------------------------------------------------------------------------


class ProviderCount(BaseModel):
    """Per-provider AI call count (closed provider enumeration — Req 20.3)."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    calls: int


class AiAnalytics(BaseModel):
    """``GET /admin/ai-analytics`` — allowlisted AI aggregates + cost estimate."""

    model_config = ConfigDict(extra="forbid")

    window: int
    totalCalls: int
    successRate: float  # 4dp; success + failure == 1.0 when totalCalls > 0
    failureRate: float  # 4dp; both 0.0 when totalCalls == 0
    avgLatencyMs: float
    # Average AI tokens per call. Named ``avgUnitsPerCall`` (not ``avgTokens``)
    # because "token" is a FORBIDDEN_SUBSTRINGS entry — the serialization guard
    # (Req 15.7) rejects any key containing it, so this avoids a false leak.
    avgUnitsPerCall: float
    timeouts: int
    retries: int
    estimatedCostDollars: int  # microdollars / 1_000_000, truncated
    providers: list[ProviderCount] = Field(default_factory=list)
    daily: list[SeriesPoint] | None = None
    computedAt: str


# ---------------------------------------------------------------------------
# Errors summary (Req 5) — grouped buckets only, never raw log/stack lines
# ---------------------------------------------------------------------------


class RouteClassFailures(BaseModel):
    """A grouped route-class failure count (route *class*, not raw path)."""

    model_config = ConfigDict(extra="forbid")

    routeClass: str
    failures: int


class ErrorsBySource(BaseModel):
    """Failure counts grouped by originating subsystem."""

    model_config = ConfigDict(extra="forbid")

    api: int = 0
    job: int = 0
    storage: int = 0
    ai: int = 0


class ErrorsSummary(BaseModel):
    """``GET /admin/errors`` — grouped 4xx/5xx counts + by-source + trend.

    ``notInstrumented`` lists the field paths that have **no durable source**
    today (e.g. ``topRouteClasses`` — there is no bounded per-route-class failure
    bucket; ``bySource.job`` / ``bySource.storage`` — no durable job/storage
    failure counter). The UI shows an explicit "Not instrumented" indicator for
    these instead of a misleading empty list / zero, so an operator can tell
    "no failures" apart from "not tracked". This is metadata, not a raw-log field
    (the panel remains grouped-buckets-only — Req 21.2).
    """

    model_config = ConfigDict(extra="forbid")

    window: int
    counts4xx: int
    counts5xx: int
    topRouteClasses: list[RouteClassFailures] = Field(default_factory=list)
    bySource: ErrorsBySource
    trend: list[SeriesPoint] = Field(default_factory=list)
    notInstrumented: list[str] = Field(default_factory=list)
    computedAt: str


# ---------------------------------------------------------------------------
# Performance signals (Req 6) — existing aggregates only; no new instrumentation
# ---------------------------------------------------------------------------


class RouteClassLatency(BaseModel):
    """Latency aggregate for one route class (avg + optional p95)."""

    model_config = ConfigDict(extra="forbid")

    routeClass: str
    avgMs: float
    p95Ms: float | None = None


class SlowJob(BaseModel):
    """Average duration for one slow background job."""

    model_config = ConfigDict(extra="forbid")

    name: str
    avgMs: float


class PerformanceSignals(BaseModel):
    """``GET /admin/performance`` — latency/cache aggregates the backend already
    produces. Host metrics (cpu/memory/disk) are ``None`` unless already
    produced and are dropped by ``exclude_none`` (Non-Goal — Req 21.4)."""

    model_config = ConfigDict(extra="forbid")

    routeClasses: list[RouteClassLatency] = Field(default_factory=list)
    topSlowRoutes: list[RouteClassLatency] = Field(default_factory=list)
    topSlowJobs: list[SlowJob] = Field(default_factory=list)
    dbQueryTimeMs: float | None = None
    cacheHitRatio: float | None = None  # 0.0..1.0
    memoryBytes: int | None = None
    cpuPercent: float | None = None
    diskBytes: int | None = None
    unavailable: list[str] = Field(default_factory=list)
    computedAt: str


# ---------------------------------------------------------------------------
# Storage panel (Req 7) — cached sample + counts + growth, never live-queried
# ---------------------------------------------------------------------------


class StoragePanel(BaseModel):
    """``GET /admin/storage`` — cached DB size + object storage + counts + growth."""

    model_config = ConfigDict(extra="forbid")

    dbSizeBytes: int | None = None
    dbSizeStale: bool = False
    objectStorageBytes: int | None = None
    objectStorageStale: bool = False
    avatarCount: int = 0
    resumeCount: int = 0
    resumeVersionCount: int = 0
    retentionStatus: str | None = None
    growthBytesPerDay: float | None = None  # None when insufficient samples
    growthUnavailable: bool = False
    growthUnavailableReason: str | None = None
    computedAt: str


# ---------------------------------------------------------------------------
# Jobs panel (Req 8) — shares JobRow with AdminHealth
# ---------------------------------------------------------------------------


class JobsPanel(BaseModel):
    """``GET /admin/jobs`` — job rows + optional queue/backlog gauges."""

    model_config = ConfigDict(extra="forbid")

    jobs: list[JobRow] = Field(default_factory=list)
    queueLength: int | None = None
    queueLengthUnavailable: bool = False
    purgeBacklog: int | None = None
    purgeBacklogUnavailable: bool = False
    computedAt: str
    stale: bool = False


# ---------------------------------------------------------------------------
# Security view (Req 9) — 24h aggregate counts, read from SEC_* keys only
# ---------------------------------------------------------------------------


class SecurityView(BaseModel):
    """``GET /admin/security`` — 24h security aggregate counts (zero when no data).

    ``notInstrumented`` lists the camelCase field names that have **no durable
    aggregate source** and therefore must NOT be read as a real "0". The UI shows
    an explicit "Not instrumented" indicator for those instead of a misleading
    zero — honesty over a fabricated count. A field is listed here only when the
    backend produces no signal for it without new instrumentation (a Non-Goal,
    Req 21.4); fields with a real source are never listed.
    """

    model_config = ConfigDict(extra="forbid")

    windowHours: int = 24
    loginFailed: int = 0
    adminLogin: int = 0
    authzDenied: int = 0
    rateLimited: int = 0
    suspicious: int = 0
    notInstrumented: list[str] = Field(default_factory=list)
    computedAt: str


# ---------------------------------------------------------------------------
# Config diagnostics (Req 10) — read-only, secret-free (presence booleans only)
# ---------------------------------------------------------------------------


class ConfigDiagnostics(BaseModel):
    """``GET /admin/config`` — read-only diagnostics. Secrets appear ONLY as
    boolean presence indicators (Req 10.4/10.5): the ``configured`` map's *keys*
    deliberately avoid every forbidden substring (e.g. ``aiConfigured``,
    ``smtpConfigured``) so no secret name or value ever serializes."""

    model_config = ConfigDict(extra="forbid")

    env: str
    activeAiProviders: list[str] = Field(default_factory=list)
    storageProvider: str
    emailProvider: str
    featureFlags: dict[str, bool] = Field(default_factory=dict)
    maintenanceMode: bool = False
    schedulerMode: str
    gracePeriodDays: int
    killSwitches: dict[str, bool] = Field(default_factory=dict)
    versions: dict[str, str] = Field(default_factory=dict)
    configured: dict[str, bool] = Field(default_factory=dict)  # presence booleans only
    computedAt: str


# ---------------------------------------------------------------------------
# Overview KPIs (Req 13) — each KPI is a value + explicit unavailable marker
# ---------------------------------------------------------------------------


class KpiValue(BaseModel):
    """A single KPI card value with an explicit unavailability marker.

    ``value`` is ``None`` (and ``unavailable`` is ``True``) when the underlying
    snapshot could not be computed, so the card renders "unavailable" rather than
    a misleading zero.
    """

    model_config = ConfigDict(extra="forbid")

    value: float | None = None
    unavailable: bool = False


class OverviewKpis(BaseModel):
    """``GET /admin/kpis`` — Overview KPI cards assembled from existing snapshots."""

    model_config = ConfigDict(extra="forbid")

    totalUsers: KpiValue
    newUsersToday: KpiValue
    aiCallsToday: KpiValue
    errorRate24h: KpiValue  # value 0.00..100.00
    purgeBacklog: KpiValue
    computedAt: str
    stale: bool = False


# ---------------------------------------------------------------------------
# Feature usage (Req 16) — aggregate-only daily feature totals (Product Analytics)
# ---------------------------------------------------------------------------


class FeatureSeries(BaseModel):
    """Daily point series + total for one feature (no user-level data)."""

    model_config = ConfigDict(extra="forbid")

    feature: str
    points: list[SeriesPoint] = Field(default_factory=list)
    total: int = 0


class FeatureUsage(BaseModel):
    """``GET /admin/analytics/feature-usage`` — daily per-feature totals."""

    model_config = ConfigDict(extra="forbid")

    window: int
    series: list[FeatureSeries] = Field(default_factory=list)
    computedAt: str


# ---------------------------------------------------------------------------
# Resume analytics (Req 14) — source split + top templates + growth
# ---------------------------------------------------------------------------


class ResumeSourceSplit(BaseModel):
    """Resume source split: counts + percentages for each origin."""

    model_config = ConfigDict(extra="forbid")

    generated: int = 0
    imported: int = 0
    tailored: int = 0
    deleted: int = 0
    generatedPct: float = 0.0
    importedPct: float = 0.0
    tailoredPct: float = 0.0
    deletedPct: float = 0.0


class TemplateCount(BaseModel):
    """One popular-template row (name + usage count)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    count: int


class ResumeAnalytics(BaseModel):
    """``GET /admin/analytics/resumes`` — source split, top templates, growth."""

    model_config = ConfigDict(extra="forbid")

    window: int
    sourceSplit: ResumeSourceSplit
    topTemplates: list[TemplateCount] = Field(default_factory=list)  # top 10, tie → name
    growth: list[SeriesPoint] = Field(default_factory=list)
    computedAt: str
