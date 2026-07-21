"""Contract tests for the import-adapter ecosystem (Open/Closed guarantee).

Every registered adapter must honor one contract: given its native payload it
returns a valid ``ProfileData`` (or raises ``ImportError_`` with a code for bad
input / unsupported sources). These tests fail loudly if a new adapter is added
that violates the contract - the guardrail that keeps the pipeline extensible
without pipeline changes.
"""

from __future__ import annotations

import pytest

from app.profile.import_adapters import IMPORT_SOURCES, ImportError_, derive_candidate
from app.profile.schemas import ProfileData

# Minimal valid payloads for the *implemented* adapters.
_IMPLEMENTED_PAYLOADS = {
    "resume": {"processed_data": {"personalInfo": {"name": "Ada"}}},
    "json_resume": {"data": {"basics": {"name": "Ada"}}},
}

# Sources declared but intentionally not implemented yet.
_STUB_SOURCES = {"linkedin", "github", "europass", "portfolio"}


def test_registry_covers_known_sources():
    """The registry is the single source of truth for supported import sources."""
    assert set(IMPORT_SOURCES) == set(_IMPLEMENTED_PAYLOADS) | _STUB_SOURCES


@pytest.mark.parametrize("source", sorted(_IMPLEMENTED_PAYLOADS))
def test_implemented_adapter_returns_profile_data(source):
    """Each implemented adapter yields a valid ProfileData from a minimal payload."""
    result = derive_candidate(source, _IMPLEMENTED_PAYLOADS[source])
    assert isinstance(result, ProfileData)
    assert result.identity.name == "Ada"


@pytest.mark.parametrize("source", sorted(_STUB_SOURCES))
def test_stub_adapters_raise_unsupported(source):
    """Declared-but-unimplemented adapters fail with a clear ``unsupported`` code."""
    with pytest.raises(ImportError_) as exc:
        derive_candidate(source, {})
    assert exc.value.code == "unsupported"


def test_unknown_source_raises_unsupported():
    with pytest.raises(ImportError_) as exc:
        derive_candidate("nope", {})
    assert exc.value.code == "unsupported"


@pytest.mark.parametrize(
    "source,payload",
    [("resume", {"processed_data": "not-a-dict"}), ("json_resume", {"data": None})],
)
def test_invalid_payload_raises_invalid(source, payload):
    """Malformed payloads raise ``invalid`` (never crash the pipeline)."""
    with pytest.raises(ImportError_) as exc:
        derive_candidate(source, payload)
    assert exc.value.code == "invalid"
