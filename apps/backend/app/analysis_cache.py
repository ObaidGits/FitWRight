"""Persistent AI analysis cache - the "compute once, reuse everywhere" service.

Thin, transport-agnostic layer over the ``analysis_artifacts`` table (see
``app.models.AnalysisArtifact``). Callers wrap an expensive operation in
:func:`get_or_compute`; identical inputs under an unchanged algorithm version
resolve to the stored result instead of another LLM/API call.

Design principles:
- **Honest reuse only.** A hit requires an *exact* match on the content checksum
  AND the algorithm version. A prompt/model change bumps the version and simply
  misses, so we never serve a stale result computed by a different algorithm
  (version awareness / lazy regeneration).
- **Never cache failures as hits.** ``compute`` failures propagate; only a
  successful result is written with ``status="ready"``.
- **User-scoped.** Every read/write is scoped to ``user_id`` via the facade.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Awaitable, Callable

import app.database as _database

logger = logging.getLogger(__name__)


def _db():
    """Resolve the live DB facade at call time.

    Referencing the module attribute (rather than binding ``db`` at import)
    means tests that swap ``app.database.db`` for an isolated database - via the
    ``isolated_db`` fixture - are transparently honored here too.
    """
    return _database.db

# ---------------------------------------------------------------------------
# Artifact types (stable string keys) + their algorithm versions.
# ---------------------------------------------------------------------------
# Bump a version whenever the prompt/algorithm for that artifact changes so old
# cached results are transparently ignored (they miss and are recomputed).

ARTIFACT_RESUME_PARSE = "resume_parse"
ARTIFACT_JOB_ANALYSIS = "job_analysis"

_ALGO_VERSION: dict[str, str] = {
    ARTIFACT_RESUME_PARSE: "1",
    ARTIFACT_JOB_ANALYSIS: "1",
}


def checksum_text(text: str) -> str:
    """SHA-256 hex of a UTF-8 string (content-addressed cache key)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def checksum_obj(obj: Any) -> str:
    """SHA-256 hex of a JSON-serializable object via a canonical encoding."""
    canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return checksum_text(canonical)


def version_key(artifact_type: str, model_name: str | None = None) -> str:
    """Composite algorithm version: ``<algo_version>|<model>``.

    Including the model name means switching providers/models is a cache miss
    (the new model may parse differently), preserving correctness.
    """
    algo = _ALGO_VERSION.get(artifact_type, "1")
    return f"{algo}|{model_name or 'default'}"


async def get_or_compute(
    *,
    user_id: str,
    artifact_type: str,
    source_id: str,
    checksum: str,
    version: str,
    compute: Callable[[], Awaitable[dict[str, Any]]],
    related_id: str | None = None,
    confidence: int | None = None,
    force: bool = False,
) -> tuple[dict[str, Any], bool]:
    """Return ``(result, from_cache)`` for an expensive operation.

    On a cache hit (exact reuse key, ``status="ready"``) the stored payload is
    returned without calling ``compute``. Otherwise ``compute`` runs and its
    result is persisted before being returned. ``force=True`` skips the lookup
    (an explicit "Regenerate") but still writes the fresh result back.

    Never writes a failed computation as a reusable hit - a ``compute`` raise
    propagates to the caller and nothing is cached.
    """
    if not force:
        try:
            hit = await _db().get_analysis_artifact(
                user_id,
                artifact_type=artifact_type,
                source_id=source_id,
                checksum=checksum,
                version=version,
            )
        except Exception as e:  # noqa: BLE001 - cache read must never break the op
            logger.warning("analysis-cache read failed (%s); computing: %s", artifact_type, e)
            hit = None
        if hit and hit.get("status") == "ready" and hit.get("analysis_data") is not None:
            return hit["analysis_data"], True

    result = await compute()

    try:
        await _db().put_analysis_artifact(
            user_id,
            artifact_type=artifact_type,
            source_id=source_id,
            related_id=related_id,
            checksum=checksum,
            version=version,
            analysis_data=result,
            confidence=confidence,
            status="ready",
        )
    except Exception as e:  # noqa: BLE001 - a cache write failure must not break the op
        logger.warning("analysis-cache write failed (%s): %s", artifact_type, e)

    return result, False


async def invalidate(
    user_id: str,
    resource_id: str,
    *,
    artifact_types: list[str] | None = None,
) -> int:
    """Drop cached artifacts that depend on ``resource_id`` (best-effort)."""
    try:
        return await _db().invalidate_analysis_artifacts(
            user_id, resource_id, artifact_types=artifact_types
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("analysis-cache invalidation failed for %s: %s", resource_id, e)
        return 0
