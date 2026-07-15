"""Unit tests for the observability + product-analytics response models (Task 5.1).

Property 3 (Req 15.7 / 10.4 / 17.3): *No content or secret ever leaves the new
surface.* Every one of the eleven new response models — plus their shared
submodels — must serialize to a body that passes ``assert_no_forbidden_fields``
(zero forbidden substrings) and must be strict (``extra="forbid"``) so a widened
model can never silently ride a new column/secret into an admin response.

The strategy: build a fully-populated representative instance of every model
(every optional field set to a non-None value so the recursive guard actually
visits it), dump via ``model_dump(by_alias=True)``, and assert the guard does
not raise. We then assert each model rejects unknown fields and declares
``extra="forbid"`` at the config level.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from app.admin.schemas import (
    AdminHealth,
    AiAnalytics,
    ConfigDiagnostics,
    ErrorsBySource,
    ErrorsSummary,
    FeatureSeries,
    FeatureUsage,
    HealthTile,
    JobRow,
    JobsPanel,
    KpiValue,
    OverviewKpis,
    PerformanceSignals,
    ProviderCount,
    ReleaseInfo,
    ResumeAnalytics,
    ResumeSourceSplit,
    RouteClassFailures,
    RouteClassLatency,
    SecurityView,
    SeriesPoint,
    SlowJob,
    StoragePanel,
    TemplateCount,
    assert_no_forbidden_fields,
)

pytestmark = pytest.mark.unit


_TS = "2026-01-01T00:00:00+00:00"


def _series() -> list[SeriesPoint]:
    return [SeriesPoint(date="2026-01-01", value=3), SeriesPoint(date="2026-01-02", value=5)]


def _job_row() -> JobRow:
    """A JobRow with every optional field populated so the guard visits them all."""
    return JobRow(
        name="rollup",
        lastRun=_TS,
        lastOutcome="success",
        lagSeconds=12,
        nextRun=_TS,
        lastSuccess=_TS,
        runningSince=_TS,
        currentDurationSeconds=4,
        expectedDurationSeconds=10,
        potentiallyStuck=False,
        lockState="free",
    )


def _admin_health() -> AdminHealth:
    return AdminHealth(
        tiles=[
            HealthTile(name="Backend", status="ok", detail="up"),
            HealthTile(name="Database", status="ok"),
            HealthTile(name="KVStore/Queue", status="degraded", detail="slow"),
            HealthTile(name="AI provider", status="ok"),
            HealthTile(name="Storage provider", status="ok"),
            HealthTile(name="Migrations", status="ok", detail="0021"),
        ],
        release=ReleaseInfo(
            version="1.2.3",
            build="build-42",
            commit="abcdef0",
            migrationApplied="0021",
            migrationHead="0021",
            env="production",
        ),
        backendUptimeSeconds=3600,
        jobs=[_job_row()],
        computedAt=_TS,
        stale=False,
    )


def _ai_analytics() -> AiAnalytics:
    return AiAnalytics(
        window=30,
        totalCalls=100,
        successRate=0.9800,
        failureRate=0.0200,
        avgLatencyMs=812.5,
        avgUnitsPerCall=1234.0,
        timeouts=1,
        retries=3,
        estimatedCostDollars=7,
        providers=[ProviderCount(provider="openai", calls=80), ProviderCount(provider="gemini", calls=20)],
        daily=_series(),
        computedAt=_TS,
    )


def _errors_summary() -> ErrorsSummary:
    return ErrorsSummary(
        window=30,
        counts4xx=42,
        counts5xx=3,
        topRouteClasses=[RouteClassFailures(routeClass="POST /resumes", failures=2)],
        bySource=ErrorsBySource(api=3, job=1, storage=0, ai=2),
        trend=_series(),
        computedAt=_TS,
    )


def _performance_signals() -> PerformanceSignals:
    return PerformanceSignals(
        routeClasses=[RouteClassLatency(routeClass="GET /health", avgMs=5.0, p95Ms=9.0)],
        topSlowRoutes=[RouteClassLatency(routeClass="POST /tailor", avgMs=900.0, p95Ms=1500.0)],
        topSlowJobs=[SlowJob(name="rollup", avgMs=1200.0)],
        dbQueryTimeMs=3.2,
        cacheHitRatio=0.95,
        memoryBytes=1024,
        cpuPercent=12.5,
        diskBytes=2048,
        unavailable=["cpuPercent"],
        computedAt=_TS,
    )


def _storage_panel() -> StoragePanel:
    return StoragePanel(
        dbSizeBytes=10_000_000,
        dbSizeStale=False,
        objectStorageBytes=50_000_000,
        objectStorageStale=True,
        avatarCount=12,
        resumeCount=340,
        resumeVersionCount=900,
        retentionStatus="ok",
        growthBytesPerDay=1234.5,
        growthUnavailable=False,
        growthUnavailableReason="insufficient samples",
        computedAt=_TS,
    )


def _jobs_panel() -> JobsPanel:
    return JobsPanel(
        jobs=[_job_row()],
        queueLength=5,
        queueLengthUnavailable=False,
        purgeBacklog=2,
        purgeBacklogUnavailable=False,
        computedAt=_TS,
        stale=False,
    )


def _security_view() -> SecurityView:
    return SecurityView(
        windowHours=24,
        loginFailed=4,
        adminLogin=1,
        authzDenied=0,
        rateLimited=2,
        suspicious=0,
        computedAt=_TS,
    )


def _config_diagnostics() -> ConfigDiagnostics:
    return ConfigDiagnostics(
        env="production",
        activeAiProviders=["openai", "gemini"],
        storageProvider="cloudinary",
        emailProvider="smtp",
        featureFlags={"portfolio": True, "coverLetter": False},
        maintenanceMode=False,
        schedulerMode="internal",
        gracePeriodDays=30,
        killSwitches={"ai": False, "signups": False},
        versions={"backend": "1.2.3", "alembic": "0021"},
        # Presence booleans ONLY — keys deliberately avoid forbidden substrings.
        configured={"aiConfigured": True, "smtpConfigured": True, "oauthConfigured": False},
        computedAt=_TS,
    )


def _overview_kpis() -> OverviewKpis:
    return OverviewKpis(
        totalUsers=KpiValue(value=1500, unavailable=False),
        newUsersToday=KpiValue(value=12, unavailable=False),
        aiCallsToday=KpiValue(value=340, unavailable=False),
        errorRate24h=KpiValue(value=1.25, unavailable=False),
        purgeBacklog=KpiValue(value=None, unavailable=True),
        computedAt=_TS,
        stale=False,
    )


def _feature_usage() -> FeatureUsage:
    return FeatureUsage(
        window=30,
        series=[
            FeatureSeries(feature="feat_builder", points=_series(), total=8),
            FeatureSeries(feature="feat_tailor", points=_series(), total=8),
        ],
        computedAt=_TS,
    )


def _resume_analytics() -> ResumeAnalytics:
    return ResumeAnalytics(
        window=30,
        sourceSplit=ResumeSourceSplit(
            generated=10,
            imported=5,
            tailored=20,
            deleted=2,
            generatedPct=27.03,
            importedPct=13.51,
            tailoredPct=54.05,
            deletedPct=5.41,
        ),
        topTemplates=[TemplateCount(name="Modern", count=15), TemplateCount(name="Classic", count=9)],
        growth=_series(),
        computedAt=_TS,
    )


# The eleven task-5.1 response models, each with a fully-populated builder.
_MODEL_BUILDERS = {
    "AdminHealth": _admin_health,
    "AiAnalytics": _ai_analytics,
    "ErrorsSummary": _errors_summary,
    "PerformanceSignals": _performance_signals,
    "StoragePanel": _storage_panel,
    "JobsPanel": _jobs_panel,
    "SecurityView": _security_view,
    "ConfigDiagnostics": _config_diagnostics,
    "OverviewKpis": _overview_kpis,
    "FeatureUsage": _feature_usage,
    "ResumeAnalytics": _resume_analytics,
}


class TestObservabilityModelsAreSecretFree:
    """Property 3 / Req 15.7, 10.4, 17.3."""

    @pytest.mark.parametrize("name", sorted(_MODEL_BUILDERS))
    def test_model_dump_passes_forbidden_field_guard(self, name):
        instance = _MODEL_BUILDERS[name]()
        payload = instance.model_dump(by_alias=True)
        # Must not raise — a raise means a forbidden (secret) key leaked.
        assert_no_forbidden_fields(payload)

    def test_all_eleven_models_covered(self):
        assert len(_MODEL_BUILDERS) == 11


class TestObservabilityModelsForbidExtra:
    """Each model + its config declares ``extra="forbid"`` (strict allowlist)."""

    @pytest.mark.parametrize("name", sorted(_MODEL_BUILDERS))
    def test_config_declares_extra_forbid(self, name):
        instance = _MODEL_BUILDERS[name]()
        assert type(instance).model_config.get("extra") == "forbid"

    @pytest.mark.parametrize("name", sorted(_MODEL_BUILDERS))
    def test_unknown_field_rejected(self, name):
        model_cls = type(_MODEL_BUILDERS[name]())
        with pytest.raises(ValidationError):
            model_cls(**{"totallyUnknownField": "x"})


class TestSharedSubmodelsForbidExtra:
    """Shared submodels are strict too (they nest inside the response bodies)."""

    _SUBMODELS = [
        SeriesPoint,
        HealthTile,
        ReleaseInfo,
        JobRow,
        ProviderCount,
        RouteClassFailures,
        ErrorsBySource,
        RouteClassLatency,
        SlowJob,
        KpiValue,
        FeatureSeries,
        TemplateCount,
        ResumeSourceSplit,
    ]

    @pytest.mark.parametrize("model_cls", _SUBMODELS)
    def test_submodel_extra_forbid(self, model_cls: type[BaseModel]):
        assert model_cls.model_config.get("extra") == "forbid"
