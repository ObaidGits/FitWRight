"""Unit tests for version-history pure logic (P3 §A, R1–R3).

Covers the deterministic helpers with no DB: canonical hashing, gzip
compress/decompress round-trip + corruption handling, and the field-level diff.
Orchestration (dedupe/debounce/restore/undo/prune) is covered by the integration
suite against a real isolated DB.
"""

from __future__ import annotations

import gzip

import pytest

from app.versions import service as vs
from app.versions.service import VersionServiceError


class TestContentHash:
    def test_hash_is_stable_across_key_order(self):
        a = {"summary": "x", "skills": ["a", "b"], "n": 1}
        b = {"n": 1, "skills": ["a", "b"], "summary": "x"}
        assert vs.compute_content_hash(a) == vs.compute_content_hash(b)

    def test_hash_changes_with_content(self):
        assert vs.compute_content_hash({"a": 1}) != vs.compute_content_hash({"a": 2})

    def test_hash_distinguishes_list_order(self):
        assert vs.compute_content_hash({"s": [1, 2]}) != vs.compute_content_hash({"s": [2, 1]})

    def test_unicode_preserved_in_hash(self):
        # ensure_ascii=False → é hashed as its UTF-8 bytes, stable.
        h1 = vs.compute_content_hash({"name": "José"})
        h2 = vs.compute_content_hash({"name": "José"})
        assert h1 == h2


class TestCompression:
    def test_round_trip(self, sample_resume):
        blob, size, digest = vs.compress_processed_data(sample_resume)
        assert isinstance(blob, bytes)
        assert size > 0
        assert digest == vs.compute_content_hash(sample_resume)
        assert vs.decompress_version(blob) == sample_resume

    def test_compression_actually_shrinks_repetitive_payload(self):
        data = {"items": ["the same line repeated"] * 500}
        blob, size, _ = vs.compress_processed_data(data)
        assert len(blob) < size  # gzip smaller than raw JSON

    def test_size_is_uncompressed_length(self):
        data = {"a": "b"}
        blob, size, _ = vs.compress_processed_data(data)
        assert size == len(gzip.decompress(blob))

    def test_corrupt_blob_raises_invalid(self):
        with pytest.raises(VersionServiceError) as exc:
            vs.decompress_version(b"not-gzip-data")
        assert exc.value.code == "invalid"


class TestDiff:
    def test_no_changes_for_identical(self, sample_resume):
        assert vs.diff_processed_data(sample_resume, sample_resume) == []

    def test_scalar_change(self):
        changes = vs.diff_processed_data({"summary": "old"}, {"summary": "new"})
        assert changes == [
            {"path": "summary", "action": "changed", "before": "old", "after": "new"}
        ]

    def test_added_and_removed_keys(self):
        changes = vs.diff_processed_data({"a": 1}, {"b": 2})
        paths = {(c["path"], c["action"]) for c in changes}
        assert ("a", "removed") in paths
        assert ("b", "added") in paths

    def test_nested_list_append(self):
        before = {"items": ["x"]}
        after = {"items": ["x", "y"]}
        changes = vs.diff_processed_data(before, after)
        assert changes == [
            {"path": "items[1]", "action": "added", "before": None, "after": "y"}
        ]

    def test_nested_list_removed(self):
        before = {"items": ["x", "y"]}
        after = {"items": ["x"]}
        changes = vs.diff_processed_data(before, after)
        assert changes == [
            {"path": "items[1]", "action": "removed", "before": "y", "after": None}
        ]

    def test_deep_nested_change_path(self):
        before = {"work": [{"desc": ["a"]}]}
        after = {"work": [{"desc": ["b"]}]}
        changes = vs.diff_processed_data(before, after)
        assert changes == [
            {"path": "work[0].desc[0]", "action": "changed", "before": "a", "after": "b"}
        ]

    def test_diff_is_deterministic(self):
        before = {"z": 1, "a": 2, "m": 3}
        after = {"z": 9, "a": 8, "m": 7}
        first = vs.diff_processed_data(before, after)
        second = vs.diff_processed_data(before, after)
        assert first == second
        assert [c["path"] for c in first] == ["a", "m", "z"]  # sorted keys


class TestSourceValidation:
    async def test_unknown_source_rejected(self):
        with pytest.raises(VersionServiceError) as exc:
            await vs.capture_snapshot("u", "r", {"a": 1}, "bogus")
        assert exc.value.code == "invalid"
