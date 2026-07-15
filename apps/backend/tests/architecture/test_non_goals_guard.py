"""Architecture fitness function: the admin-panel Non-Goals are NOT violated
(admin-panel-upgrade Requirements 21.1, 21.2-adjacent, 21.3, 21.4, 21.5).

The new observability / product-analytics code lives under ``app/admin/*`` and
``app/analytics/*`` and is deliberately built on **daily aggregates + KV
snapshots**, never on host metrics, live object-storage queries, raw-log
explorers, or per-event storage. This is a static/import-based guard that reads
the module sources and asserts none of those Non-Goals sneak back in — a
regression here would otherwise be invisible until it hurt scale or leaked data.

What is asserted:

- **No host metrics (Req 21.3/21.4).** No new admin/analytics module imports a
  host-sampling library (``psutil``, ``resource`` for ``getrusage``, or
  ``os.getloadavg``). Host CPU/RAM/disk is a Non-Goal unless the backend already
  produces it — and it does not. (Plain ``os`` / ``platform`` for non-metric use
  is allowed; only the metric-sampling entry points are forbidden.)

- **No live object-storage query on the request path (Req 21.5).** The
  storage-panel read path (``StorageMetricsService.panel()`` and its helpers)
  reads only cached ``MetricStore`` series + KV snapshots; it never resolves the
  object-storage provider or enumerates objects. The provider walk lives only in
  the *rollup step*, off the request path.

- **No raw-log / trace explorer (Req 21.2-adjacent).** The errors summary is
  grouped buckets only — the ``ErrorsSummary`` schema has no raw-log / stack /
  trace / exception-message field.

- **No per-event storage (Req 21.1).** Every durable Metric_Key is a **daily
  aggregate** drawn from the closed, bounded Metric_Registry; there is no
  per-request / per-event key family.

The guard reuses the module list style of ``tests/architecture/test_admin_import_graph.py``
(read the file source, analyse statically). Missing modules are skipped, not
failed, so the guard tightens automatically as modules are added.
"""

from __future__ import annotations

import ast
from pathlib import Path

import app as app_pkg

APP_DIR = Path(app_pkg.__file__).parent
APP_ROOT = APP_DIR.parent

# The new admin observability + product-analytics modules the Non-Goals bind to.
# (Superset of the domain services in test_admin_import_graph.py plus the shared
# request-path primitives that back the reads.)
GUARDED_MODULES: tuple[str, ...] = (
    # Observability bounded context
    "app.admin.health_service",
    "app.admin.ai_metrics",
    "app.admin.security_metrics",
    "app.admin.storage_metrics",
    "app.admin.perf_metrics",
    "app.admin.errors_metrics",
    "app.admin.overview",
    "app.admin.config_diag",
    "app.admin.maintenance",
    "app.admin.jobs_panel",
    # Shared primitives on the request path
    "app.admin.metric_store",
    "app.admin.metric_registry",
    "app.admin.metrics_service",
    # Product Analytics bounded context
    "app.analytics.feature_usage",
    "app.analytics.resume_metrics",
)


def _module_file(dotted: str) -> Path:
    rel = Path(*dotted.split(".")).with_suffix(".py")
    return APP_ROOT / rel


def _existing_modules() -> dict[str, Path]:
    return {
        dotted: _module_file(dotted)
        for dotted in GUARDED_MODULES
        if _module_file(dotted).is_file()
    }


def _top_level_imports(path: Path) -> set[str]:
    """Collect module dotted-paths imported anywhere in ``path`` (via ``ast``).

    Records both ``import x``/``import x.y`` and ``from a.b import c`` (adding
    ``a.b`` and ``a.b.c``). This catches an import regardless of where it appears
    (module top level or inside a function), so a lazily-imported host-metric
    sampler is still caught.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and not (node.level and node.level > 0):
                modules.add(node.module)
                for alias in node.names:
                    modules.add(f"{node.module}.{alias.name}")
    return modules


# ---------------------------------------------------------------------------
# Req 21.3 / 21.4 — no host-metric sampling libraries
# ---------------------------------------------------------------------------

# Import roots that only exist to sample host CPU/RAM/disk/load. Plain ``os`` and
# ``platform`` are intentionally NOT here (they have legitimate non-metric uses —
# ``os.walk`` for the rollup disk sample, ``platform``/version strings, etc.); we
# forbid the specific host-sampling entry points instead.
_FORBIDDEN_HOST_METRIC_IMPORTS = frozenset({"psutil", "resource"})
# Forbidden host-sampling call/attribute patterns in the source text.
_FORBIDDEN_HOST_METRIC_PATTERNS = (
    "getloadavg",       # os.getloadavg — 1/5/15-min load average
    "getrusage",        # resource.getrusage — process CPU/RSS
    "psutil.",          # any psutil sampler
    "virtual_memory",   # psutil.virtual_memory
    "cpu_percent",      # psutil.cpu_percent
    "disk_usage",       # psutil.disk_usage / shutil.disk_usage host sampling
)


def test_no_admin_module_imports_a_host_metric_library():
    """Req 21.3/21.4: the new admin/analytics code samples no host metrics."""
    violations: list[str] = []
    for dotted, path in sorted(_existing_modules().items()):
        imported = _top_level_imports(path)
        for forbidden in sorted(_FORBIDDEN_HOST_METRIC_IMPORTS):
            if forbidden in imported or any(
                m == forbidden or m.startswith(f"{forbidden}.") for m in imported
            ):
                violations.append(f"{dotted} imports host-metric library '{forbidden}'")
    assert not violations, (
        "Host-metric sampling is a Non-Goal (Req 21.3/21.4) — the dashboard uses "
        "only signals the backend already produces:\n" + "\n".join(violations)
    )


def test_no_admin_module_uses_a_host_metric_sampling_pattern():
    """Req 21.3/21.4: no host-sampling call patterns in the source text."""
    violations: list[str] = []
    for dotted, path in sorted(_existing_modules().items()):
        source = path.read_text(encoding="utf-8")
        for pattern in _FORBIDDEN_HOST_METRIC_PATTERNS:
            if pattern in source:
                violations.append(f"{dotted} contains host-sampling pattern '{pattern}'")
    assert not violations, (
        "Host CPU/RAM/disk/load sampling is a Non-Goal (Req 21.3/21.4):\n"
        + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# Req 21.5 — no live object-storage query / enumeration on the request path
# ---------------------------------------------------------------------------


def test_storage_read_path_never_touches_the_object_storage_provider():
    """Req 21.5: ``StorageMetricsService`` request path is cached-only.

    The panel read model and its private helpers must not resolve the storage
    provider or enumerate objects — object-storage usage comes only from the KV
    ``"storage"`` snapshot the *rollup step* wrote. We read the source of every
    method on the read-model class and assert none of them reference the
    provider-resolution / object-walk entry points (which live, correctly, in the
    module's rollup ``*Step`` classes and its module-level helper — off the
    request path).
    """
    import inspect

    from app.admin import storage_metrics
    from app.admin.storage_metrics import StorageMetricsService

    # Source of the read-model class ONLY (its methods) — not the rollup steps or
    # the module-level ``_object_storage_usage`` helper, which are job-time code.
    read_model_source = inspect.getsource(StorageMetricsService)

    forbidden_on_request_path = (
        "get_storage_provider",   # resolving the live provider
        "LocalStorageProvider",   # provider type / disk walk
        "os.walk",                # object enumeration
        "cloudinary",             # any live remote call
        "_object_storage_usage",  # the job-time sampler helper
    )
    found = [p for p in forbidden_on_request_path if p in read_model_source]
    assert not found, (
        "StorageMetricsService (the request-path read model) must read only "
        "cached MetricStore series + KV snapshots (Req 21.5); it references live "
        f"object-storage entry points: {found}"
    )

    # The panel read path must actually go through the cached snapshot primitives.
    panel_source = inspect.getsource(StorageMetricsService.panel)
    assert "series" in panel_source and "snapshot" in panel_source, (
        "StorageMetricsService.panel() should assemble the panel from the cached "
        "MetricStore series + named KV snapshot (Req 7.4/21.5)."
    )

    # Belt-and-braces: the provider import in the module is confined to the
    # job-time helper (lazy import), never a module-level request-path import.
    module_source = inspect.getsource(storage_metrics)
    assert "from app.storage.provider import" in module_source, (
        "expected the object-storage provider to be imported lazily inside the "
        "rollup helper (sanity anchor for this guard)"
    )


def test_no_request_path_service_imports_the_storage_provider_at_module_top():
    """Req 21.5: no new read service imports the object-storage provider eagerly.

    A module-level ``from app.storage.provider import ...`` in a request-path read
    service would mean the provider is wired into the read path. The only allowed
    reference is the *lazy* import inside ``storage_metrics``'s job-time helper
    (asserted above), so it must NOT appear as a module-top import here.
    """
    violations: list[str] = []
    for dotted, path in sorted(_existing_modules().items()):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:  # module-level statements only
            if isinstance(node, ast.ImportFrom) and node.module == "app.storage.provider":
                violations.append(f"{dotted} imports app.storage.provider at module top")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "app.storage.provider":
                        violations.append(f"{dotted} imports app.storage.provider at module top")
    assert not violations, (
        "Object-storage must never be wired into a request-path read (Req 21.5); "
        "the only allowed use is the lazy import in the rollup helper:\n"
        + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# Req 21.2-adjacent — the errors summary is grouped buckets only (no log/trace)
# ---------------------------------------------------------------------------


def test_errors_summary_schema_is_grouped_only_no_raw_log_fields():
    """Req 21.2-adjacent: ``ErrorsSummary`` exposes grouped aggregates only.

    The errors panel is an operational summary, not a raw-log / stack / trace /
    exception-message explorer. We pin the schema's field set to the grouped-only
    contract and assert no raw-log-shaped field name appears.
    """
    from app.admin.schemas import ErrorsSummary

    fields = set(ErrorsSummary.model_fields)
    expected = {
        "window",
        "counts4xx",
        "counts5xx",
        "topRouteClasses",
        "bySource",
        "trend",
        # Availability metadata: lists the grouped fields that have no durable
        # source so the UI can show "Not instrumented" instead of a misleading
        # zero/empty. Not a raw-log/trace field — the panel stays grouped-only.
        "notInstrumented",
        "computedAt",
    }
    assert fields == expected, (
        "ErrorsSummary must stay grouped-buckets-only (Req 21.2-adjacent); its "
        f"field set drifted: unexpected={fields - expected}, missing={expected - fields}"
    )

    forbidden_substrings = ("log", "stack", "trace", "message", "exception", "raw", "detail")
    leaking = {
        name
        for name in fields
        for bad in forbidden_substrings
        if bad in name.lower()
    }
    assert not leaking, (
        "ErrorsSummary must not carry raw-log/stack/trace/exception fields "
        f"(Req 21.2-adjacent, no log/trace explorer): {leaking}"
    )


# ---------------------------------------------------------------------------
# Req 21.1 — no per-event storage: every durable key is a bounded daily aggregate
# ---------------------------------------------------------------------------


def test_all_metric_keys_are_a_bounded_static_daily_aggregate_set():
    """Req 21.1: the durable key set is closed, bounded, and per-day (no per-event).

    Per-event/per-request storage is a Non-Goal — durable signals live in
    ``metrics_daily`` under a **fixed** set of statically-registered keys. We
    assert the registry is a small bounded set and that its lookup helpers can
    only ever return an already-registered key (so nothing is composed per event).
    """
    from app.admin import metric_registry as reg

    keys = reg.all_keys()
    # Closed + bounded: a fixed, small enumeration (a per-event key family would
    # be unbounded). The registry currently defines a few dozen keys; a generous
    # ceiling catches an accidental unbounded/per-event family without being
    # brittle to legitimate one-line additions.
    assert 0 < len(keys) <= 100, (
        f"metrics_daily key set must stay bounded (Req 21.1/20.4); got {len(keys)} "
        "keys — a per-event key family would make this unbounded."
    )
    # Every registry spec key is registered (no runtime-derived stragglers), and
    # the closed dimension maps only ever resolve to already-registered keys.
    assert all(reg.is_registered(spec.key) for spec in reg.METRIC_REGISTRY)
    assert all(v in keys for v in reg.AI_CALLS_BY_PROVIDER.values()), (
        "per-provider AI keys must be pre-registered constants, never composed"
    )
    assert all(v in keys for v in reg.AUDIT_DOWNSAMPLE_BY_EVENT.values()), (
        "downsample keys must be pre-registered constants, never composed"
    )
