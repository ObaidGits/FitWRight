"""The extension origin allowlist.

The failure this covers cost a real debugging session: `EXTENSION_ORIGINS` written
as `chrome-extension:abc` instead of `chrome-extension://abc`. Browsers never send
that origin, so every extension request is rejected by CORS and nothing appears in
the logs - a configuration typo that presents as a broken extension.

Two behaviours are pinned: the missing slashes are repaired, because what was meant
is unambiguous, and anything still unusable produces a warning rather than silence.
"""
from app.config import Settings


def settings_with(origins: str) -> Settings:
    return Settings(extension_origins=origins)


class TestOriginRepair:
    def test_missing_slashes_are_repaired(self):
        s = settings_with("chrome-extension:cbhinjmfmpeognmgmjapoamgfedfdeeb")
        assert s.effective_extension_origins == [
            "chrome-extension://cbhinjmfmpeognmgmjapoamgfedfdeeb"
        ]

    def test_a_correct_origin_is_untouched(self):
        s = settings_with("chrome-extension://abc123")
        assert s.effective_extension_origins == ["chrome-extension://abc123"]

    def test_firefox_scheme_is_repaired_too(self):
        s = settings_with("moz-extension:abc123")
        assert s.effective_extension_origins == ["moz-extension://abc123"]

    def test_trailing_slash_and_whitespace_are_trimmed(self):
        s = settings_with("  chrome-extension://abc123/  ")
        assert s.effective_extension_origins == ["chrome-extension://abc123"]

    def test_several_origins_are_each_handled(self):
        s = settings_with("chrome-extension:abc, chrome-extension://def ,")
        assert s.effective_extension_origins == [
            "chrome-extension://abc",
            "chrome-extension://def",
        ]

    def test_blank_stays_empty(self):
        """The extension surface is opt-in; unset must add no origin."""
        assert settings_with("").effective_extension_origins == []

    def test_a_repaired_origin_reaches_the_cors_list(self):
        s = settings_with("chrome-extension:abc123")
        assert "chrome-extension://abc123" in s.effective_cors_origins


class TestWarnings:
    def test_a_valid_origin_warns_about_nothing(self):
        assert settings_with("chrome-extension://abc123").extension_origin_warnings == []

    def test_a_repaired_origin_warns_about_nothing(self):
        """Repaired means fixed, not merely reported."""
        assert settings_with("chrome-extension:abc123").extension_origin_warnings == []

    def test_a_bare_id_is_flagged(self):
        warnings = settings_with("cbhinjmfmpeognmgmjapoamgfedfdeeb").extension_origin_warnings
        assert len(warnings) == 1
        assert "chrome-extension://<id>" in warnings[0]

    def test_a_scheme_with_no_id_is_flagged(self):
        warnings = settings_with("chrome-extension://").extension_origin_warnings
        assert len(warnings) == 1
        assert "no extension id" in warnings[0]

    def test_an_unexpected_scheme_is_flagged(self):
        warnings = settings_with("ftp://abc123").extension_origin_warnings
        assert len(warnings) == 1
        assert "unexpected scheme" in warnings[0]

    def test_a_localhost_origin_is_accepted(self):
        """Some setups point the allowlist at a dev server rather than an id."""
        assert settings_with("http://localhost:3000").extension_origin_warnings == []
