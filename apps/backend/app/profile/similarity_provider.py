"""Similarity provider abstraction - a swappable scoring backend (P-final).

The Merge Engine no longer hard-codes the deterministic scorer; it asks the
configured :class:`SimilarityProvider` for an entity-similarity score. This is
the seam that lets a future **semantic/embedding** provider (or a hybrid) drop in
with zero merge-engine changes (Open/Closed + dependency inversion).

- :class:`DeterministicSimilarityProvider` (default) wraps the pure, explainable
  functions in ``app/profile/similarity.py`` - identical behavior to before, so
  the refactor is behavior-preserving.
- :class:`HybridSimilarityProvider` blends deterministic + an injected semantic
  scorer with configurable weights, and **explains** each decision.
- :class:`EmbeddingSimilarityProvider` documents the vector-DB seam; it is inert
  unless an embedding function is injected (none ships - needs external infra),
  so it always falls back to deterministic rather than fabricating a score.

Selection is config-driven (``settings.profile_similarity_provider``) and cached.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol

from app.profile import similarity as det

__all__ = [
    "SimilarityScore",
    "SimilarityProvider",
    "DeterministicSimilarityProvider",
    "HybridSimilarityProvider",
    "EmbeddingSimilarityProvider",
    "get_similarity_provider",
    "reset_similarity_provider",
    "SCORERS",
]

# kind -> deterministic scorer. The single registry the Merge Engine dispatches
# through, so a new entity kind is one entry (not a merge-engine edit).
SCORERS: dict[str, Callable[[dict, dict], float]] = {
    "workExperience": det.experience_similarity,
    "education": det.education_similarity,
    "personalProjects": det.project_similarity,
    "certifications": det.certification_similarity,
    "achievements": det.achievement_similarity,
    "skill": det.skill_identity,
}


class SimilarityScore:
    """An explainable similarity result: the score plus how it was derived."""

    __slots__ = ("value", "method", "components")

    def __init__(self, value: float, method: str, components: dict[str, float] | None = None) -> None:
        self.value = max(0.0, min(1.0, value))
        self.method = method
        self.components = components or {}

    def explain(self) -> dict[str, Any]:
        return {"score": round(self.value, 3), "method": self.method, "components": self.components}


class SimilarityProvider(Protocol):
    """Scores how likely two same-kind entities refer to the same real thing."""

    name: str

    def score(self, kind: str, a: dict, b: dict) -> SimilarityScore: ...


class DeterministicSimilarityProvider:
    """Pure, explainable scorer (default). Wraps ``app/profile/similarity.py``."""

    name = "deterministic"

    def score(self, kind: str, a: dict, b: dict) -> SimilarityScore:
        scorer = SCORERS.get(kind)
        if scorer is None:
            return SimilarityScore(det.text_similarity(a, b), "text")
        return SimilarityScore(scorer(a, b), "deterministic")


class HybridSimilarityProvider:
    """Blends deterministic + an injected semantic scorer (weighted, explained).

    ``semantic_fn(kind, a, b) -> float`` is optional; absent => pure deterministic
    (so this is safe to configure even before a semantic backend exists).
    """

    name = "hybrid"

    def __init__(
        self,
        semantic_fn: Callable[[str, dict, dict], float] | None = None,
        *,
        deterministic_weight: float = 0.6,
        semantic_weight: float = 0.4,
    ) -> None:
        self._semantic_fn = semantic_fn
        self._dw = deterministic_weight
        self._sw = semantic_weight

    def score(self, kind: str, a: dict, b: dict) -> SimilarityScore:
        det_provider = DeterministicSimilarityProvider()
        det_score = det_provider.score(kind, a, b).value
        if self._semantic_fn is None:
            return SimilarityScore(det_score, "deterministic", {"deterministic": det_score})
        sem = max(0.0, min(1.0, self._semantic_fn(kind, a, b)))
        total = self._dw + self._sw or 1.0
        blended = (det_score * self._dw + sem * self._sw) / total
        return SimilarityScore(
            blended, "hybrid", {"deterministic": det_score, "semantic": sem}
        )


class EmbeddingSimilarityProvider:
    """Vector/embedding seam (needs external infra). Inert without an embedder.

    ``embed_fn(text) -> list[float]`` and a cosine over concatenated entity text.
    None ships (no vector DB / model in this environment), so it falls back to
    deterministic rather than fabricating a semantic score.
    """

    name = "embedding"

    def __init__(self, embed_fn: Callable[[str], list[float]] | None = None) -> None:
        self._embed_fn = embed_fn
        self._fallback = DeterministicSimilarityProvider()

    @staticmethod
    def _text(kind: str, item: dict) -> str:
        parts = [str(v) for v in item.values() if isinstance(v, (str, int, float))]
        for v in item.values():
            if isinstance(v, list):
                parts.extend(str(x) for x in v if isinstance(x, str))
        return " ".join(parts)

    @staticmethod
    def _cosine(u: list[float], v: list[float]) -> float:
        if not u or not v or len(u) != len(v):
            return 0.0
        dot = sum(a * b for a, b in zip(u, v))
        nu = sum(a * a for a in u) ** 0.5
        nv = sum(b * b for b in v) ** 0.5
        return dot / (nu * nv) if nu and nv else 0.0

    def score(self, kind: str, a: dict, b: dict) -> SimilarityScore:
        if self._embed_fn is None:
            fb = self._fallback.score(kind, a, b)
            return SimilarityScore(fb.value, "deterministic_fallback", fb.components)
        va = self._embed_fn(self._text(kind, a))
        vb = self._embed_fn(self._text(kind, b))
        return SimilarityScore(self._cosine(va, vb), "embedding")


_provider: SimilarityProvider | None = None


def get_similarity_provider() -> SimilarityProvider:
    """Return the configured provider (cached). Defaults to deterministic."""
    global _provider
    if _provider is None:
        try:
            from app.config import settings

            name = getattr(settings, "profile_similarity_provider", "deterministic")
        except Exception:  # pragma: no cover - config always present
            name = "deterministic"
        if name == "hybrid":
            _provider = HybridSimilarityProvider()
        elif name == "embedding":
            _provider = EmbeddingSimilarityProvider()
        else:
            _provider = DeterministicSimilarityProvider()
    return _provider


def reset_similarity_provider() -> None:
    """Drop the cached provider (tests / config reload)."""
    global _provider
    _provider = None
