"""Professional Profile System (docs/architecture/PROFILE_SYSTEM_PLAN.md).

The canonical profile is a single document per user (``ProfileData``); resumes
are generated, provenance-stamped snapshots produced by the Projection Engine.
This package contains the pure domain logic (schema, projection, completion,
merge/backfill) and the orchestrating service; all owned-table persistence goes
through the ``app.database`` facade (the scoping boundary).
"""
