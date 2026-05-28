"""Unit tests for the ~/.env fallback in scribe.haiku_client.

These tests cover the key-resolution priority (process env -> ~/.env)
and the env-file parser. No network calls; the HaikuClient itself is
not instantiated."""

from __future__ import annotations

import pytest

from sovereign_stack.scribe import haiku_client


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Each test runs without ANTHROPIC_* in os.environ unless it sets them."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY_SCRIBE", raising=False)
    haiku_client.reset_env_cache()
    yield
    haiku_client.reset_env_cache()


class TestParseEnvFile:
    def test_simple_pairs(self, tmp_path):
        p = tmp_path / ".env"
        p.write_text("FOO=bar\nBAZ=qux\n")
        d = haiku_client._parse_env_file(p)
        assert d == {"FOO": "bar", "BAZ": "qux"}

    def test_strips_double_quotes(self, tmp_path):
        p = tmp_path / ".env"
        p.write_text('FOO="bar value"\n')
        d = haiku_client._parse_env_file(p)
        assert d == {"FOO": "bar value"}

    def test_strips_single_quotes(self, tmp_path):
        p = tmp_path / ".env"
        p.write_text("FOO='bar value'\n")
        d = haiku_client._parse_env_file(p)
        assert d == {"FOO": "bar value"}

    def test_skips_comments_and_blank(self, tmp_path):
        p = tmp_path / ".env"
        p.write_text("# comment\n\nFOO=bar\n# another\nBAZ=qux\n")
        d = haiku_client._parse_env_file(p)
        assert d == {"FOO": "bar", "BAZ": "qux"}

    def test_missing_file_returns_empty(self, tmp_path):
        p = tmp_path / "doesnotexist.env"
        assert haiku_client._parse_env_file(p) == {}

    def test_skips_lines_without_equals(self, tmp_path):
        p = tmp_path / ".env"
        p.write_text("FOO=bar\nthis is not an env line\nBAZ=qux\n")
        d = haiku_client._parse_env_file(p)
        assert d == {"FOO": "bar", "BAZ": "qux"}

    def test_handles_value_with_equals(self, tmp_path):
        p = tmp_path / ".env"
        p.write_text("URL=https://example.com/?foo=bar&baz=qux\n")
        d = haiku_client._parse_env_file(p)
        assert d == {"URL": "https://example.com/?foo=bar&baz=qux"}

    def test_empty_value(self, tmp_path):
        p = tmp_path / ".env"
        p.write_text("EMPTY=\n")
        d = haiku_client._parse_env_file(p)
        assert d == {"EMPTY": ""}


class TestApiKeyFromEnvProcessFirst:
    def test_process_scribe_key_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ANTHROPIC_API_KEY_SCRIBE", "from-process-scoped")
        # Even if ~/.env has different value, process env wins
        monkeypatch.setattr(haiku_client, "_env_file_path", tmp_path / ".env")
        (tmp_path / ".env").write_text("ANTHROPIC_API_KEY_SCRIBE=from-file\n")
        assert haiku_client._api_key_from_env() == "from-process-scoped"

    def test_process_general_key_fallback(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "from-process-general")
        monkeypatch.setattr(haiku_client, "_env_file_path", tmp_path / ".env")
        assert haiku_client._api_key_from_env() == "from-process-general"

    def test_scribe_beats_general_in_process(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ANTHROPIC_API_KEY_SCRIBE", "scoped")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "general")
        monkeypatch.setattr(haiku_client, "_env_file_path", tmp_path / ".env")
        assert haiku_client._api_key_from_env() == "scoped"


class TestApiKeyFromEnvFileFallback:
    def test_scribe_key_from_file(self, monkeypatch, tmp_path):
        p = tmp_path / ".env"
        p.write_text("ANTHROPIC_API_KEY_SCRIBE=from-file-scoped\n")
        monkeypatch.setattr(haiku_client, "_env_file_path", p)
        haiku_client.reset_env_cache()
        assert haiku_client._api_key_from_env() == "from-file-scoped"

    def test_general_key_from_file(self, monkeypatch, tmp_path):
        p = tmp_path / ".env"
        p.write_text("ANTHROPIC_API_KEY=from-file-general\n")
        monkeypatch.setattr(haiku_client, "_env_file_path", p)
        haiku_client.reset_env_cache()
        assert haiku_client._api_key_from_env() == "from-file-general"

    def test_scribe_beats_general_in_file(self, monkeypatch, tmp_path):
        p = tmp_path / ".env"
        p.write_text("ANTHROPIC_API_KEY=general\nANTHROPIC_API_KEY_SCRIBE=scoped\n")
        monkeypatch.setattr(haiku_client, "_env_file_path", p)
        haiku_client.reset_env_cache()
        assert haiku_client._api_key_from_env() == "scoped"

    def test_alongside_other_env_vars(self, monkeypatch, tmp_path):
        p = tmp_path / ".env"
        p.write_text(
            "BRIDGE_TOKEN=tok\n"
            "OPENAI_API_KEY=other\n"
            "ANTHROPIC_API_KEY_SCRIBE=mine\n"
            "SOVEREIGN_ROOT=/tmp/x\n"
        )
        monkeypatch.setattr(haiku_client, "_env_file_path", p)
        haiku_client.reset_env_cache()
        assert haiku_client._api_key_from_env() == "mine"


class TestApiKeyMissing:
    def test_raises_with_clear_message(self, monkeypatch, tmp_path):
        # Empty ~/.env, no process env
        p = tmp_path / ".env"
        p.write_text("")
        monkeypatch.setattr(haiku_client, "_env_file_path", p)
        haiku_client.reset_env_cache()
        with pytest.raises(RuntimeError, match="No Anthropic API key found"):
            haiku_client._api_key_from_env()

    def test_raises_when_env_file_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(haiku_client, "_env_file_path", tmp_path / "nope")
        haiku_client.reset_env_cache()
        with pytest.raises(RuntimeError, match="No Anthropic API key found"):
            haiku_client._api_key_from_env()


class TestEnvCache:
    def test_cache_avoids_repeated_reads(self, monkeypatch, tmp_path):
        p = tmp_path / ".env"
        p.write_text("ANTHROPIC_API_KEY_SCRIBE=first\n")
        monkeypatch.setattr(haiku_client, "_env_file_path", p)
        haiku_client.reset_env_cache()

        first = haiku_client._api_key_from_env()
        assert first == "first"

        # Change the file; cached read should still return the original
        p.write_text("ANTHROPIC_API_KEY_SCRIBE=second\n")
        cached = haiku_client._api_key_from_env()
        assert cached == "first"

        # After reset, re-read picks up the new value
        haiku_client.reset_env_cache()
        fresh = haiku_client._api_key_from_env()
        assert fresh == "second"
