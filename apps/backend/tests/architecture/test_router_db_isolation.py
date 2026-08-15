"""A router must never quietly read the real database during tests.

``isolated_db`` swaps the global ``db`` singleton for a temp one, and separately
re-points every router module that captured ``db`` at import time. That second list is
maintained by hand, and a module missing from it does not fail loudly - it keeps the
ORIGINAL singleton and reads the developer's actual database. Its tests still pass or
fail, just against the wrong data.

This was not hypothetical: the first version of the credits router captured ``db`` at
module level, was absent from the list, and its tests asserted against real data until
two of them disagreed with the fixture and exposed it.

Two ways to be safe, both accepted here:

* Resolve the database inside the function (``def _db(): from app.database import db``),
  so there is nothing to patch. Preferred for new code.
* Capture it at module level AND appear in ``ISOLATED_DB_ROUTER_MODULES``.
"""

from __future__ import annotations

import importlib
import pkgutil

import app.routers as routers_pkg
from tests.conftest import ISOLATED_DB_ROUTER_MODULES


def _router_modules() -> list[str]:
    return [m.name for m in pkgutil.iter_modules(routers_pkg.__path__)]


class TestRouterDatabaseIsolation:
    def test_the_detector_finds_the_router_modules(self):
        """Guard the guard: an empty list would make the real assertion vacuous."""
        assert len(_router_modules()) >= 10

    def test_no_router_captures_the_db_singleton_without_being_patched(self):
        """The actual guard."""
        offenders = []
        for name in _router_modules():
            try:
                module = importlib.import_module(f"app.routers.{name}")
            except Exception:  # pragma: no cover - an import error is another test's job
                continue
            if hasattr(module, "db") and name not in ISOLATED_DB_ROUTER_MODULES:
                offenders.append(name)

        assert not offenders, (
            "These router modules hold a module-level `db`, so `isolated_db` cannot "
            "re-point them and their tests would run against the REAL database:\n"
            + "\n".join(f"  app/routers/{n}.py" for n in sorted(offenders))
            + "\n\nEither resolve the db inside the function, or add the module to "
            "ISOLATED_DB_ROUTER_MODULES in tests/conftest.py."
        )

    def test_the_patch_list_has_no_phantom_entries(self):
        """A listed module that no longer captures `db` implies protection that is no
        longer needed - harmless, but it makes the list untrustworthy as documentation
        of which modules actually need patching."""
        known = set(_router_modules())
        missing = [n for n in ISOLATED_DB_ROUTER_MODULES if n not in known]
        assert not missing, f"Listed but no such router module: {sorted(missing)}"
