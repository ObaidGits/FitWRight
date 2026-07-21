"""Architecture fitness function: Domain_Metrics_Service import isolation
(admin-panel-upgrade Requirement 19.2, 19.3, 19.5; design Property 9).

Each Domain_Metrics_Service owns exactly one domain and may depend ONLY on the
shared primitives (Metric_Store, Metric_Registry, AdminRepo, schemas, config,
KVStore, models). It MUST NOT import another Domain_Metrics_Service. Because no
domain service may import any other, no cross-domain cycle can form (the
pairwise no-import rule strictly precludes a cycle).

This test performs a deterministic, static import-graph analysis with ``ast``:
for every domain-service module that currently EXISTS on disk it collects the
set of module dotted-paths it imports (resolving relative imports to absolute
``app.*`` paths) and asserts none of those refer to another domain service.

Modules that do not yet exist are skipped, not failed. As each service is added
in later tasks, this guard automatically begins enforcing the rule on it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import app as app_pkg

APP_DIR = Path(app_pkg.__file__).parent
# app/  -> its parent is the backend root that holds the ``app`` package.
APP_ROOT = APP_DIR.parent

# Every Domain_Metrics_Service, mapped to its dotted module path. MANY of these
# do not exist yet - they are created in later tasks. The test tolerates that
# (see ``_module_file``); the rule is enforced for each as it appears.
DOMAIN_SERVICES: dict[str, str] = {
    # Observability bounded context
    "app.admin.health_service": "HealthService",
    "app.admin.ai_metrics": "AiMetricsService",
    "app.admin.security_metrics": "SecurityMetricsService",
    "app.admin.storage_metrics": "StorageMetricsService",
    "app.admin.perf_metrics": "PerformanceMetricsService",
    "app.admin.errors_metrics": "ErrorsMetricsService",
    "app.admin.overview": "OverviewService",
    "app.admin.config_diag": "ConfigService",
    "app.admin.maintenance": "MaintenanceService",
    # Product Analytics bounded context
    "app.analytics.feature_usage": "FeatureUsageService",
    "app.analytics.resume_metrics": "ResumeMetricsService",
}

DOMAIN_SERVICE_MODULES = frozenset(DOMAIN_SERVICES)


def _module_file(dotted: str) -> Path:
    """Return the on-disk path for a dotted ``app.*`` module path."""
    rel = Path(*dotted.split(".")).with_suffix(".py")
    return APP_ROOT / rel


def _package_of(dotted_module: str) -> str:
    """The package that contains ``dotted_module`` (its dotted-path parent)."""
    return dotted_module.rsplit(".", 1)[0] if "." in dotted_module else ""


def _resolve_relative(package: str, level: int, module: str | None) -> str | None:
    """Resolve a relative import to an absolute dotted path.

    ``package`` is the dotted package of the importing module (e.g.
    ``app.admin`` for ``app/admin/overview.py``). ``level`` is the number of
    leading dots; ``module`` is the text after the dots (may be ``None`` for
    ``from . import x``).
    """
    parts = package.split(".") if package else []
    # level==1 -> current package; level==2 -> parent package; etc.
    if level - 1 > len(parts):
        return None  # escapes beyond the top package; not an app.* module
    base_parts = parts[: len(parts) - (level - 1)]
    base = ".".join(base_parts)
    if module:
        return f"{base}.{module}" if base else module
    return base or None


def _imported_module_paths(path: Path, self_dotted: str) -> set[str]:
    """Collect the set of absolute dotted module paths a file imports.

    Handles ``import x``/``import x.y``, ``from a.b import c`` (adding both
    ``a.b`` and ``a.b.c`` so ``from app.admin import ai_metrics`` is caught),
    and relative imports resolved against the importing module's package.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = _package_of(self_dotted)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                base = _resolve_relative(package, node.level, node.module)
                if base:
                    modules.add(base)
                    for alias in node.names:
                        modules.add(f"{base}.{alias.name}")
            elif node.module:
                modules.add(node.module)
                for alias in node.names:
                    modules.add(f"{node.module}.{alias.name}")
    return modules


def _existing_services() -> dict[str, Path]:
    """The subset of domain services whose module file exists on disk."""
    return {
        dotted: _module_file(dotted)
        for dotted in DOMAIN_SERVICE_MODULES
        if _module_file(dotted).is_file()
    }


def test_no_domain_service_imports_another_domain_service():
    """Req 19.2/19.3/19.5: a domain service must not import another one."""
    existing = _existing_services()
    violations: list[str] = []

    for dotted, path in sorted(existing.items()):
        forbidden = DOMAIN_SERVICE_MODULES - {dotted}
        imported = _imported_module_paths(path, dotted)
        for imported_module in sorted(imported):
            if imported_module in forbidden:
                violations.append(
                    f"{dotted} ({DOMAIN_SERVICES[dotted]}) imports another "
                    f"Domain_Metrics_Service {imported_module} "
                    f"({DOMAIN_SERVICES[imported_module]}); domain services may "
                    f"depend only on shared primitives (Metric_Store, "
                    f"Metric_Registry, AdminRepo, schemas, config, KVStore, models)."
                )

    assert not violations, (
        "Cross-domain Domain_Metrics_Service imports violate the bounded-context "
        "separation (Requirement 19.2/19.3/19.5):\n" + "\n".join(violations)
    )


def test_no_cross_domain_import_cycle_among_existing_services():
    """Nice-to-have: confirm no cycle exists among the domain-service modules.

    The pairwise no-import rule already precludes any cycle, so this must hold
    whenever the test above holds. It is kept as an explicit, independent guard.
    """
    existing = _existing_services()

    # Build the directed edge set restricted to domain-service -> domain-service.
    edges: dict[str, set[str]] = {}
    for dotted, path in existing.items():
        imported = _imported_module_paths(path, dotted)
        edges[dotted] = {m for m in imported if m in DOMAIN_SERVICE_MODULES and m != dotted}

    # Depth-first cycle detection over the (expected-empty) edge set.
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {node: WHITE for node in edges}

    def _has_cycle(node: str, stack: list[str]) -> list[str] | None:
        color[node] = GRAY
        stack.append(node)
        for nxt in edges.get(node, ()):  # neighbors that are domain services
            if nxt not in color:
                continue
            if color[nxt] == GRAY:
                return stack[stack.index(nxt):] + [nxt]
            if color[nxt] == WHITE:
                found = _has_cycle(nxt, stack)
                if found:
                    return found
        stack.pop()
        color[node] = BLACK
        return None

    for node in edges:
        if color[node] == WHITE:
            cycle = _has_cycle(node, [])
            assert cycle is None, (
                "Cross-domain dependency cycle detected among Domain_Metrics_"
                "Services (Requirement 19.3): " + " -> ".join(cycle)
            )
