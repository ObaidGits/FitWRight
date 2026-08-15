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


def _metered_routes(app) -> dict[tuple[str, str], str]:
    """Endpoints that ACTUALLY carry the metering dependency, read off the live app.

    Detected via the marker ``ai_metered`` stamps on the dependency it builds, so
    this reflects wiring that really exists. The earlier version of this file kept a
    hand-maintained set, which would have gone stale the first time a path was
    renamed - and a stale entry here claims coverage that is not there, which is
    worse than no ratchet at all.
    """
    found: dict[tuple[str, str], str] = {}
    for route in app.routes:
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        for sub in _walk_dependants(dependant):
            feature = getattr(sub.call, "__fw_metered_feature__", None)
            if feature:
                for method in getattr(route, "methods", set()) or set():
                    if method in ("GET", "HEAD", "OPTIONS"):
                        continue
                    found[(method, route.path)] = feature
    return found


def _walk_dependants(dependant):
    """Yield a dependant and everything it depends on, transitively."""
    yield dependant
    for sub in getattr(dependant, "dependencies", []) or []:
        yield from _walk_dependants(sub)

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

    def test_wired_endpoints_are_a_subset_of_intended_ones(self, app):
        """Something cannot be wired without being intended - that would mean an
        endpoint is charging users while classified as not-to-be-charged."""
        stray = set(_metered_routes(app)) - METERED_ENDPOINTS
        assert not stray, f"Wired but not listed as metered: {sorted(stray)}"

    def test_every_metered_endpoint_is_actually_wired(self, app):
        """The real guarantee, now that wiring is complete.

        This started as an informational report while the wiring was in progress.
        It is an assertion now, because the failure it guards against is invisible
        by nature: an endpoint that was never wired behaves EXACTLY like a working
        one and is simply free forever. Nobody files a bug for that.
        """
        wired = set(_metered_routes(app))
        missing = METERED_ENDPOINTS - wired
        assert not missing, (
            "These endpoints spend provider money but carry no metering "
            "dependency. Add Depends(ai_metered(\"<feature>\")) to each:\n"
            + "\n".join(f"  {m} {p}" for m, p in sorted(missing))
        )

    def test_exempt_endpoints_are_not_secretly_metered(self, app):
        """An endpoint documented as free must not quietly charge users."""
        wired = set(_metered_routes(app))
        contradictory = set(EXEMPT_ENDPOINTS) & wired
        assert not contradictory, (
            "Documented as exempt but actually metered: " f"{sorted(contradictory)}"
        )

    def test_every_feature_name_has_a_price(self, app):
        """A metered feature with no price entry falls back to a generic guess, so it
        is neither visible nor editable in Admin > Feature prices.

        Checked against the seeded default list rather than the database, because this
        is a static guardrail: it asks "did someone add a metered endpoint without
        giving it a price?", which is a code review question, not a runtime one.
        """
        from app.ai_feature_prices import DEFAULT_FEATURE_PRICES

        for endpoint, feature in sorted(_metered_routes(app).items()):
            assert feature in DEFAULT_FEATURE_PRICES, (
                f"{endpoint} meters as {feature!r}, which has no entry in "
                "DEFAULT_FEATURE_PRICES - it would charge a fallback price that no "
                "operator can see or edit. Add it there and to the seed script."
            )

    def test_reports_how_much_wiring_remains(self, app):
        """Informational: prints the billing identity of every metered endpoint.

        Kept after the wiring finished because the useful question changed. It is no
        longer "what is left to do?" but "what is each endpoint charging as?" - a
        mis-mapped feature (tailoring billed as a cover letter) is a real defect that
        no other test here can see, and it is obvious at a glance in this output.
        """
        wired = _metered_routes(app)
        print(f"\nAI metering: {len(wired)}/{len(METERED_ENDPOINTS)} endpoints wired.")
        for (method, path), feature in sorted(wired.items()):
            print(f"  {feature:<22} {method} {path}")
        for method, path in sorted(METERED_ENDPOINTS - set(wired)):
            print(f"  pending: {method} {path}")
        assert True
