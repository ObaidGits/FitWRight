"""Feed filtering: the list and its count must describe the same set.

These pin the behaviour the Discovery page depends on. The bug they replace was a
client-side filter over one page, which showed "3 of 228" and paginated through
rows the user had filtered out - so every case here asserts the count alongside
the rows.
"""
import pytest


@pytest.fixture
async def db(isolated_db):
    """The project's isolated per-test database (conftest owns its lifecycle)."""
    return isolated_db


def row(fingerprint, *, source, title, company, location="", is_remote=False):
    return {
        "fingerprint": fingerprint,
        "source": source,
        "title": title,
        "company": company,
        "location": location,
        "url": f"https://example.test/{fingerprint}",
        "is_remote": is_remote,
        "description": None,
        "salary": None,
        "posted_at": None,
        "match_score": 0.0,
        "matched": [],
        "missing": [],
        "partial": True,
    }


USER = "user-1"

SEED = [
    row("a", source="linkedin", title="Python Developer", company="Acme", location="Pune, India"),
    row("b", source="linkedin", title="Frontend Engineer", company="Acme", location="Remote", is_remote=True),
    row("c", source="hirist", title="Python Engineer", company="Globex", location="Bangalore"),
    row("d", source="naukri", title="Data Scientist", company="Initech", location="Pune, India"),
    row("e", source="google", title="Senior Python Developer", company="Hooli", location="Remote", is_remote=True),
]


@pytest.fixture
async def seeded(db):
    await db.upsert_discovery_results(USER, "test", SEED)
    return db


async def both(db, **filters):
    """Return (rows, total) so a divergence between them fails the test."""
    rows = await db.get_discovery_feed(USER, limit=100, **filters)
    total = await db.count_discovery_feed(USER, **filters)
    return rows, total


class TestSourceFilter:
    async def test_single_source_excludes_others(self, seeded):
        rows, total = await both(seeded, sources=["hirist"])
        assert total == 1
        assert len(rows) == 1
        assert rows[0]["source"] == "hirist"

    async def test_deselecting_a_source_drops_its_rows(self, seeded):
        """The reported bug: selecting hirist must not still show linkedin."""
        rows, total = await both(seeded, sources=["hirist", "naukri"])
        assert total == 2
        assert {r["source"] for r in rows} == {"hirist", "naukri"}

    async def test_no_sources_means_every_source(self, seeded):
        rows, total = await both(seeded, sources=None)
        assert total == len(SEED)
        assert len(rows) == len(SEED)

    async def test_unknown_source_yields_nothing(self, seeded):
        rows, total = await both(seeded, sources=["nowhere"])
        assert (rows, total) == ([], 0)


class TestQueryFilter:
    async def test_every_token_must_match(self, seeded):
        rows, total = await both(seeded, query="python developer")
        titles = {r["title"] for r in rows}
        assert titles == {"Python Developer", "Senior Python Developer"}
        assert total == 2

    async def test_matches_company_as_well_as_title(self, seeded):
        rows, total = await both(seeded, query="globex")
        assert total == 1
        assert rows[0]["company"] == "Globex"

    async def test_is_case_insensitive(self, seeded):
        _, total = await both(seeded, query="PYTHON")
        assert total == 3


class TestLocationAndRemote:
    async def test_location_substring(self, seeded):
        rows, total = await both(seeded, location="pune")
        assert total == 2
        assert all("Pune" in r["location"] for r in rows)

    async def test_remote_only(self, seeded):
        rows, total = await both(seeded, is_remote=True)
        assert total == 2
        assert all(r["is_remote"] for r in rows)

    async def test_remote_false_is_not_a_filter(self, seeded):
        """Unchecked "Remote" must mean "no preference", not "on-site only"."""
        _, total = await both(seeded, is_remote=False)
        assert total == len(SEED)


class TestCombined:
    async def test_filters_are_anded(self, seeded):
        rows, total = await both(seeded, sources=["linkedin", "google"], query="python")
        assert total == 2
        assert {r["fingerprint"] for r in rows} == {"a", "e"}

    async def test_count_matches_paginated_list(self, seeded):
        """Pagination must walk the filtered set, not the unfiltered one."""
        total = await seeded.count_discovery_feed(USER, sources=["linkedin"])
        page = await seeded.get_discovery_feed(USER, sources=["linkedin"], limit=1, offset=0)
        rest = await seeded.get_discovery_feed(USER, sources=["linkedin"], limit=100, offset=1)
        assert total == 2
        assert len(page) == 1
        assert len(rest) == total - 1


class TestScoreFilter:
    """`min_score` is stored 0..1; the router converts from the UI's percent."""

    async def test_floor_excludes_weaker_matches(self, db):
        scored = [
            {**row("s1", source="linkedin", title="Strong", company="A"), "match_score": 0.9},
            {**row("s2", source="linkedin", title="Middling", company="B"), "match_score": 0.55},
            {**row("s3", source="linkedin", title="Weak", company="C"), "match_score": 0.1},
        ]
        await db.upsert_discovery_results(USER, "test", scored)

        rows, total = await both(db, min_score=0.7)
        assert [r["title"] for r in rows] == ["Strong"]
        assert total == 1

    async def test_no_floor_returns_everything(self, seeded):
        rows, total = await both(seeded, min_score=None)
        assert total == len(SEED)
        assert len(rows) == len(SEED)

    async def test_zero_floor_is_not_treated_as_no_filter(self, db):
        """0.0 must still be a filter, not silently dropped by a falsy check."""
        await db.upsert_discovery_results(
            USER,
            "test",
            [{**row("z", source="linkedin", title="Any", company="A"), "match_score": 0.0}],
        )
        rows, total = await both(db, min_score=0.0)
        # Every score is >= 0, so this is a filter that legitimately matches all.
        assert total == 1
        assert len(rows) == 1


class TestRecencyFilter:
    """Recency reads `posted_at`, falling back to `created_at` when it is null."""

    async def test_excludes_older_postings(self, db):
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        fresh = {
            **row("r1", source="linkedin", title="Fresh", company="A"),
            "posted_at": (now - timedelta(hours=2)).isoformat(),
        }
        stale = {
            **row("r2", source="linkedin", title="Stale", company="B"),
            "posted_at": (now - timedelta(days=9)).isoformat(),
        }
        await db.upsert_discovery_results(USER, "test", [fresh, stale])

        rows, total = await both(db, posted_within_hours=24)
        assert [r["title"] for r in rows] == ["Fresh"]
        assert total == 1

    async def test_undated_rows_fall_back_to_when_we_found_them(self, seeded):
        """A board that publishes no date must not vanish from a recency filter.

        Every seeded row has `posted_at=None` and was just created, so a 24-hour
        window has to keep them. Dropping them would hide a job harvested minutes
        ago, which reads as a broken filter.
        """
        rows, total = await both(seeded, posted_within_hours=24)
        assert total == len(SEED)
        assert len(rows) == len(SEED)

    async def test_zero_hours_means_no_window(self, seeded):
        rows, total = await both(seeded, posted_within_hours=0)
        assert total == len(SEED)
        assert len(rows) == len(SEED)


class TestFiltersCombine:
    async def test_score_and_source_and_query_intersect(self, db):
        rows_in = [
            {
                **row("c1", source="linkedin", title="Python Developer", company="Acme"),
                "match_score": 0.95,
            },
            {
                **row("c2", source="hirist", title="Python Developer", company="Globex"),
                "match_score": 0.95,
            },
            {
                **row("c3", source="linkedin", title="Java Developer", company="Acme"),
                "match_score": 0.95,
            },
            {
                **row("c4", source="linkedin", title="Python Developer", company="Initech"),
                "match_score": 0.2,
            },
        ]
        await db.upsert_discovery_results(USER, "test", rows_in)

        rows, total = await both(db, sources=["linkedin"], query="python", min_score=0.7)
        assert [r["company"] for r in rows] == ["Acme"]
        assert total == 1
