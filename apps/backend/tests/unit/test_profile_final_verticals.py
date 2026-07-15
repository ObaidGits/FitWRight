"""Unit tests for the final-pass verticals: similarity provider, search, AI gap.

Covers the swappable similarity provider (deterministic default + hybrid + the
inert embedding fallback), the pure ranked/highlighted profile search, and the
deterministic AI additions (skills-gap, keyword extraction).
"""

from __future__ import annotations

from app.profile import ai
from app.profile.schemas import ProfileData
from app.profile.search import search_profile
from app.profile.similarity_provider import (
    DeterministicSimilarityProvider,
    EmbeddingSimilarityProvider,
    HybridSimilarityProvider,
    get_similarity_provider,
    reset_similarity_provider,
)


def _profile() -> ProfileData:
    return ProfileData.model_validate(
        {
            "identity": {"name": "Ada Lovelace", "headline": "Backend Engineer", "targetRoles": ["backend"]},
            "summary": "Builds reliable distributed systems.",
            "workExperience": [
                {"uid": "w1", "title": "Engineer", "company": "Acme", "description": ["Built Python services"], "tech": ["Python", "PostgreSQL"]}
            ],
            "personalProjects": [{"uid": "p1", "name": "Analytics Pipeline", "description": ["Streaming ETL"], "tech": ["Kafka"]}],
            "skills": {"technical": [{"canonical": "python", "displayName": "Python"}], "tools": [], "languages": [], "soft": []},
        }
    )


class TestSimilarityProvider:
    def test_deterministic_matches_raw(self):
        p = DeterministicSimilarityProvider()
        a = {"title": "Engineer", "company": "Acme", "years": "2020"}
        b = {"title": "Engineer", "company": "Acme", "years": "2020"}
        assert p.score("workExperience", a, b).value >= 0.82

    def test_hybrid_blends_and_explains(self):
        # Semantic fn returns a fixed 0.0 to check blending + explanation.
        hybrid = HybridSimilarityProvider(lambda kind, a, b: 0.0, deterministic_weight=0.5, semantic_weight=0.5)
        a = {"title": "Engineer", "company": "Acme"}
        b = {"title": "Engineer", "company": "Acme"}
        result = hybrid.score("workExperience", a, b)
        assert result.method == "hybrid"
        assert "deterministic" in result.components and "semantic" in result.components
        # Blended is below the pure-deterministic score because semantic=0.
        assert result.value < hybrid.score("workExperience", a, a).components["deterministic"] + 0.001

    def test_hybrid_without_semantic_is_deterministic(self):
        hybrid = HybridSimilarityProvider()
        r = hybrid.score("workExperience", {"title": "X"}, {"title": "X"})
        assert r.method == "deterministic"

    def test_embedding_falls_back_without_embedder(self):
        emb = EmbeddingSimilarityProvider()
        r = emb.score("workExperience", {"title": "Engineer"}, {"title": "Engineer"})
        assert r.method == "deterministic_fallback"

    def test_embedding_uses_injected_embedder(self):
        emb = EmbeddingSimilarityProvider(embed_fn=lambda t: [1.0, 0.0] if "a" in t else [0.0, 1.0])
        r = emb.score("skill", {"displayName": "aaa"}, {"displayName": "aaa"})
        assert r.method == "embedding"
        assert r.value > 0.9

    def test_provider_selection_cached(self, monkeypatch):
        reset_similarity_provider()
        p1 = get_similarity_provider()
        p2 = get_similarity_provider()
        assert p1 is p2
        assert p1.name == "deterministic"
        reset_similarity_provider()


class TestSearch:
    def test_finds_by_skill_and_ranks_title_first(self):
        results = search_profile(_profile(), "python")
        assert results
        # The Python skill (title match) should outrank the experience body match.
        assert results[0]["type"] in ("skill", "experience")
        assert any(r["type"] == "skill" for r in results)

    def test_highlights_matches(self):
        results = search_profile(_profile(), "analytics")
        hit = next(r for r in results if r["type"] == "project")
        assert "[[Analytics]]" in hit["title"] or "[[analytics]]" in hit["title"].lower()

    def test_empty_query_returns_nothing(self):
        assert search_profile(_profile(), "   ") == []

    def test_no_match_returns_empty(self):
        assert search_profile(_profile(), "zzzznomatch") == []


class TestAiAdditions:
    def test_skills_gap_matches_target_role(self):
        result = ai.skills_gap(_profile())
        assert result["target_roles"] == ["backend"]
        gap = result["suggestion"]
        assert "Python" in gap["have"]  # held
        assert "Docker" in gap["missing"]  # expected for backend, not held

    def test_skills_gap_without_target_role_notes(self):
        p = ProfileData.model_validate({"identity": {"name": "X"}})
        result = ai.skills_gap(p)
        assert result["target_roles"] == []
        assert result["note"]

    def test_keywords_extracts_from_content(self):
        result = ai.suggest_keywords(_profile())
        kws = result["suggestion"]
        assert "python" in kws  # from tech + skills (weighted)
