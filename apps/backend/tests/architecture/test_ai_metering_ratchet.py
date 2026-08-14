"""Ratchet: every provider-cost endpoint must be explicitly metered or exempted.

The migration risk for AI credits is not the accounting - that is tested to death in
``test_credit_accounting.py`` and ``test_ai_spend.py``. The risk is that ONE of the
fifteen AI endpoints never gets wired, and that feature stays silently free forever.
Nobody notices, because an unmetered endpoint behaves exactly like a working one.

So this test enumerates the real route table, finds every endpoint that can spend
provider money (identified the same way the app already identifies them - the
``llm_rate_limit_dep`` dependency), and requires each to appear in exactly one of two
lists below. Adding a new AI endpoint without a decision fails here.

This mirrors ``test_authz_matrix``'s self-maintaining inventory walk, which exists for
the same reason: a route that forgets its authz test fails loudly rather than shipping
unprotected.
"""

from __future__ import annotations

import pytest

#: Endpoints that consume credits. Each MUST use ``ai_spend`` (or call the
#: reserve/settle protocol directly) once the feature flag is on.
#:
#: Migrating one is a small, self-contained change; the point of this list is that
#: the remaining ones cannot be quietly forgotten. ``WIRED`` below tracks which have
#: actually been done, so the gap is visible rather than assumed.
METERED_ENDPOINTS: set[tuple[str, str]] = {
    # --- Resume generation: the expensive core --------------------------------
    ("POST", "/api/v1/resumes/upload"),
    ("POST", "/api/v1/resumes/upload/stream"),
    ("POST", "/api/v1/resumes/{resume_id}/retry-processing"),
    ("POST", "/api/v1/resumes/improve"),
    ("POST", "/api/v1/resumes/improve/preview"),
    ("POST", "/api/v1/resumes/improve/confirm"),
    ("POST", "/api/v1/resumes/{resume_id}/generate-cover-letter"),
    ("POST", "/api/v1/resumes/{resume_id}/generate-outreach"),
    ("POST", "/api/v1/resumes/{resume_id}/generate-interview-prep"),
    ("POST", "/api/v1/resume-wizard/finalize"),
    # --- Enrichment ------------------------------------------------------------
    ("POST", "/api/v1/enrichment/analyze/{resume_id}"),
    ("POST", "/api/v1/enrichment/enhance"),
    ("POST", "/api/v1/enrichment/regenerate"),
    # --- Discovery AI ----------------------------------------------------------
    ("POST", "/api/v1/discovery/recommend"),
    ("POST", "/api/v1/discovery/tailor"),
    # --- Extension -------------------------------------------------------------
    ("POST", "/api/v1/extension/draft"),
    ("POST", "/api/v1/extension/match"),
}

#: Of the above, which are ACTUALLY wired to ``ai_spend`` today. Kept separate from
#: the intent list so "we plan to meter this" can never be mistaken for "this is
#: metered" - that conflation is how a feature ends up permanently free.
WIRED_ENDPOINTS: set[tuple[str, str]] = set()

#: Endpoints deliberately NOT charged, each with a stated reason. An exemption is a
#: decision, not an oversight, so it lives here in writing.
EXEMPT_ENDPOINTS: dict[tuple[str, str], str] = {
    ("POST", "/api/v1/jobs/analyze"): (
        "Keyword extraction over a pasted job description - no generation. Metering "
        "it would charge a user for pasting text, and it is already capped by the "
        "per-minute limiter."
    ),
}


def _provider_cost_routes(app) -> set[tuple[str, str]]:
    """Every route whose dependency graph includes the LLM rate limiter.

    Using the app's OWN definition of "this endpoint can spend provider money"
    rather than a hand-maintained list, so the set cannot drift from reality.
    """
    from app.llm_ratelimit import llm_rate_limit_dep

    found: set[tuple[str, str]] = set()
    for route in app.routes:
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        stack = list(dependant.dependencies)
        calls = set()
        while stack:
            dep = stack.pop()
            if dep.call is not None:
                calls.add(dep.call)
            stack.extend(dep.dependencies)
        if llm_rate_limit_dep in calls:
            for method in sorted(getattr(route, "methods", set()) or set()):
                if method in ("HEAD", "OPTIONS"):
                    continue
                found.add((method, route.path))
    return found


@pytest.fixture(scope="module")
def app():
    from app.main import app as fastapi_app

    return fastapi_app


class TestMeteringRatchet:
    def test_the_detector_actually_finds_endpoints(self, app):
        """Guard the guard. If the detection broke, every other assertion here
        would pass vacuously - the exact failure mode that let a gate script report
        success while running one test file instead of twenty-nine."""
        assert len(_provider_cost_routes(app)) >= 8, (
            "Expected to detect the app's provider-cost endpoints; the detector is "
            "probably broken, which would make this whole ratchet vacuous."
        )

    def test_every_provider_cost_endpoint_has_a_decision(self, app):
        """The ratchet. A new AI endpoint must be classified, not defaulted."""
        detected = _provider_cost_routes(app)
        classified = METERED_ENDPOINTS | set(EXEMPT_ENDPOINTS)
        undecided = detected - classified

        assert not undecided, (
            "These endpoints can spend provider money but are neither metered nor "
            "explicitly exempt. Add each to METERED_ENDPOINTS (and wire ai_spend) "
            "or to EXEMPT_ENDPOINTS with a reason:\n"
            + "\n".join(f"  {m} {p}" for m, p in sorted(undecided))
        )

    def test_no_stale_entries(self, app):
        """A route that was renamed or removed must not leave a phantom entry
        implying coverage that no longer exists."""
        detected = _provider_cost_routes(app)
        classified = METERED_ENDPOINTS | set(EXEMPT_ENDPOINTS)
        stale = classified - detected
        assert not stale, (
            "These are listed but no longer detected as provider-cost routes "
            "(renamed, removed, or lost their rate-limit dependency):\n"
            + "\n".join(f"  {m} {p}" for m, p in sorted(stale))
        )

    def test_every_exemption_states_a_reason(self):
        """An exemption without a reason is indistinguishable from an oversight
        when someone reads this list in six months."""
        for endpoint, reason in EXEMPT_ENDPOINTS.items():
            assert len(reason.strip()) > 30, f"{endpoint} needs a real reason, got: {reason!r}"

    def test_an_endpoint_is_not_both_metered_and_exempt(self):
        overlap = METERED_ENDPOINTS & set(EXEMPT_ENDPOINTS)
        assert not overlap, f"Contradictory classification: {sorted(overlap)}"

    def test_wired_endpoints_are_a_subset_of_intended_ones(self):
        """Something cannot be wired without being intended - that would mean an
        endpoint is charging users while classified as not-to-be-charged."""
        stray = WIRED_ENDPOINTS - METERED_ENDPOINTS
        assert not stray, f"Wired but not listed as metered: {sorted(stray)}"

    def test_reports_how_much_wiring_remains(self):
        """Deliberately informational, and deliberately NOT a failure.

        The accounting core is complete and tested; connecting it to each endpoint is
        mechanical. This keeps the remaining gap visible in CI output instead of
        living in someone's memory - and the moment WIRED covers METERED, this line
        becomes the proof that nothing was skipped.
        """
        remaining = sorted(METERED_ENDPOINTS - WIRED_ENDPOINTS)
        print(
            f"\nAI metering: {len(WIRED_ENDPOINTS)}/{len(METERED_ENDPOINTS)} endpoints wired."
        )
        for method, path in remaining:
            print(f"  pending: {method} {path}")
        assert True
