"""Unit tests for scribe.redactor.

The redaction layer is load-bearing per SCRIBE_SPEC.md. These tests
cover each pattern's positive match, common false-positive shapes that
must NOT be redacted, and the recursive-structure helper.

No real credentials are used; all tokens here are synthetic strings
that match pattern shapes.
"""

from __future__ import annotations

from sovereign_stack.scribe.redactor import (
    pattern_names,
    redact,
    redact_iter,
    redact_structure,
)

# ----------------------------------------------------------------------
# Bearer tokens
# ----------------------------------------------------------------------


class TestBearerToken:
    def test_redacts_standard_bearer(self):
        text = "Authorization: Bearer abcdef1234567890_abc-DEF.HIJ12345"
        result = redact(text)
        assert "abcdef1234567890_abc-DEF.HIJ12345" not in result.text
        assert "Bearer <redacted-token>" in result.text
        assert result.counts.get("bearer_token") == 1

    def test_redacts_long_hex_bearer(self):
        # Shape of the real bridge token (synthetic value here).
        synthetic_token = "f" * 64
        text = f"curl -H 'Authorization: Bearer {synthetic_token}' http://x"
        result = redact(text)
        assert synthetic_token not in result.text
        assert "Bearer <redacted-token>" in result.text

    def test_does_not_match_short_bearer(self):
        # Below the 20-char threshold; not a real token shape.
        text = "the word Bearer alone"
        result = redact(text)
        assert result.text == text
        assert result.total_redactions == 0


# ----------------------------------------------------------------------
# API key shapes
# ----------------------------------------------------------------------


class TestApiKeyAnthropic:
    def test_redacts_sk_ant(self):
        text = "ANTHROPIC_API_KEY=sk-ant-api03-AAAABBBBCCCCDDDD1234567890xyz"
        result = redact(text)
        assert "sk-ant-" not in result.text
        # env_credential also fires here, so the value gets double-stripped.
        # The point is: no key leaks through.
        assert "<redacted" in result.text


class TestApiKeyGeneric:
    def test_redacts_sk_prefix(self):
        text = "key was sk-1234567890ABCDEFGHIJ"
        result = redact(text)
        assert "sk-1234567890" not in result.text
        assert "<redacted-key>" in result.text

    def test_redacts_pk_prefix(self):
        text = "publishable pk-abc1234567890DEFGHIJK"
        result = redact(text)
        assert "<redacted-key>" in result.text
        assert "pk-abc" not in result.text

    def test_does_not_match_short_prefix(self):
        # "sk-abc" is too short to be a real key.
        text = "see sk-abc and pk-xy in the docs"
        result = redact(text)
        assert "sk-abc" in result.text
        assert "pk-xy" in result.text


# ----------------------------------------------------------------------
# Env-style credential assignments
# ----------------------------------------------------------------------


class TestEnvCredential:
    def test_redacts_token_env(self):
        text = "BRIDGE_TOKEN=abc123XYZ_456 then more text"
        result = redact(text)
        assert "abc123XYZ_456" not in result.text
        assert "BRIDGE_TOKEN=<redacted-env>" in result.text
        assert result.counts.get("env_credential") == 1

    def test_redacts_api_key_env(self):
        text = "ANTHROPIC_API_KEY=some-value-here"
        result = redact(text)
        assert "some-value-here" not in result.text
        assert "<redacted-env>" in result.text

    def test_redacts_secret_env(self):
        text = "JWT_SECRET=supersecretvalue"
        result = redact(text)
        assert "supersecretvalue" not in result.text

    def test_redacts_password_env(self):
        text = "DB_PASSWORD=mypassword123"
        result = redact(text)
        assert "mypassword123" not in result.text

    def test_redacts_auth_env(self):
        text = "AUTH_HEADER=Basic xyz789"
        result = redact(text)
        assert "Basic xyz789" not in result.text

    def test_does_not_redact_innocuous_env(self):
        # No credential-flavored substring in the LHS.
        text = "SOVEREIGN_ROOT=~/.sovereign and HOME=/Users/x"
        result = redact(text)
        assert result.text == text
        assert result.total_redactions == 0

    def test_does_not_redact_python_kwargs(self):
        # Lowercase, looks like a kwarg, not an env var.
        text = "func(token=None, secret=False)"
        result = redact(text)
        # No uppercase env-style assignment here.
        assert result.text == text


# ----------------------------------------------------------------------
# Long hex tokens
# ----------------------------------------------------------------------


class TestHexToken:
    def test_redacts_64_char_hex(self):
        text = f"signature: {'a' * 64}"
        result = redact(text)
        assert "a" * 64 not in result.text
        assert "<redacted-hex>" in result.text
        assert result.counts.get("hex_token") == 1

    def test_does_not_redact_short_sha(self):
        # Short git SHAs are kept for chronicle archaeology.
        text = "commit cb50bfe pushed to main"
        result = redact(text)
        assert "cb50bfe" in result.text

    def test_does_not_redact_40_char_sha(self):
        # 40-char SHA-1 is kept; threshold is 48+.
        sha40 = "cb50bfe1234567890abcdef1234567890abcdef1"
        assert len(sha40) == 40
        text = f"full SHA: {sha40}"
        result = redact(text)
        # Should not be redacted at the 40-char length.
        assert sha40 in result.text


# ----------------------------------------------------------------------
# Sensitive paths
# ----------------------------------------------------------------------


class TestSensitivePath:
    def test_redacts_env_path(self):
        text = "config at /Users/anthony/.env stored here"
        result = redact(text)
        assert "/Users/anthony/.env" not in result.text
        assert "<redacted-path>" in result.text

    def test_redacts_credentials_path(self):
        text = "see /home/me/credentials/aws.json"
        result = redact(text)
        assert "credentials/aws.json" not in result.text

    def test_redacts_secrets_path(self):
        text = "loaded from /opt/secrets/db.yaml"
        result = redact(text)
        assert "/opt/secrets/" not in result.text

    def test_redacts_pem_path(self):
        text = "TLS cert at /etc/tls/server.pem read"
        result = redact(text)
        assert "/etc/tls/server.pem" not in result.text

    def test_redacts_ssh_key_path(self):
        text = "SSH key /Users/me/.ssh/id_ed25519 loaded"
        result = redact(text)
        assert "id_ed25519" not in result.text

    def test_does_not_redact_normal_path(self):
        text = "logs at /var/log/system.log and /tmp/output"
        result = redact(text)
        assert result.text == text
        assert result.total_redactions == 0


# ----------------------------------------------------------------------
# Private key blocks
# ----------------------------------------------------------------------


class TestPrivateKeyBlock:
    def test_redacts_rsa_block(self):
        text = (
            "here is a key:\n"
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEowIBAAKCAQEAxYZ...fakekey...abc\n"
            "-----END RSA PRIVATE KEY-----\n"
            "after the key"
        )
        result = redact(text)
        assert "MIIEowIBAA" not in result.text
        assert "<redacted-private-key>" in result.text
        assert "after the key" in result.text

    def test_redacts_ed25519_block(self):
        text = (
            "-----BEGIN OPENSSH PRIVATE KEY-----\n"
            "b3BlbnNzaC1rZXktdjEAAAAABG5vbmU...\n"
            "-----END OPENSSH PRIVATE KEY-----"
        )
        result = redact(text)
        assert "b3BlbnNzaC1r" not in result.text
        assert "<redacted-private-key>" in result.text


# ----------------------------------------------------------------------
# Multi-pattern strings
# ----------------------------------------------------------------------


class TestMultiPattern:
    def test_multiple_patterns_in_one_string(self):
        text = (
            "curl -H 'Authorization: Bearer aaaaaaaaaaaaaaaaaaaa' "
            "http://api.x.com -d 'API_KEY=secret123abc' "
            "log at /tmp/.env.production read"
        )
        result = redact(text)
        # All three patterns should fire.
        assert "Bearer <redacted-token>" in result.text
        assert "<redacted-env>" in result.text
        assert "<redacted-path>" in result.text
        assert result.total_redactions >= 3

    def test_counts_are_accurate(self):
        text = "Bearer aaaaaaaaaaaaaaaaaaaa and Bearer bbbbbbbbbbbbbbbbbbbb"
        result = redact(text)
        assert result.counts.get("bearer_token") == 2


# ----------------------------------------------------------------------
# Edge cases
# ----------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_string(self):
        result = redact("")
        assert result.text == ""
        assert result.counts == {}
        assert not result.was_redacted

    def test_no_match(self):
        text = "this is plain text with no secrets in it"
        result = redact(text)
        assert result.text == text
        assert result.counts == {}
        assert not result.was_redacted

    def test_total_redactions_property(self):
        text = "Bearer aaaaaaaaaaaaaaaaaaaa and TOKEN_X=secret"
        result = redact(text)
        assert result.total_redactions == result.counts.get("bearer_token", 0) + result.counts.get(
            "env_credential", 0
        )
        assert result.total_redactions >= 2

    def test_pattern_names_returns_all(self):
        names = pattern_names()
        # Sanity check: all expected patterns present.
        expected = {
            "private_key_block",
            "bearer_token",
            "api_key_anthropic",
            "api_key_generic",
            "env_credential",
            "hex_token",
            "sensitive_path",
        }
        assert expected.issubset(set(names))


# ----------------------------------------------------------------------
# Recursive structure redaction
# ----------------------------------------------------------------------


class TestRedactStructure:
    def test_redacts_dict(self):
        obj = {
            "name": "log entry",
            "token": "Bearer aaaaaaaaaaaaaaaaaaaa",
            "path": "/Users/me/.env",
        }
        redacted, counts = redact_structure(obj)
        assert redacted["name"] == "log entry"
        assert "Bearer <redacted-token>" in redacted["token"]
        assert "<redacted-path>" in redacted["path"]
        assert counts.get("bearer_token") == 1
        assert counts.get("sensitive_path") == 1

    def test_redacts_nested_dict(self):
        obj = {
            "outer": {
                "inner": "API_TOKEN=supersecret",
                "ok": "fine",
            },
        }
        redacted, counts = redact_structure(obj)
        assert "supersecret" not in redacted["outer"]["inner"]
        assert redacted["outer"]["ok"] == "fine"
        assert counts.get("env_credential") == 1

    def test_redacts_list(self):
        obj = ["safe", "Bearer aaaaaaaaaaaaaaaaaaaa", 42]
        redacted, counts = redact_structure(obj)
        assert redacted[0] == "safe"
        assert "Bearer <redacted-token>" in redacted[1]
        assert redacted[2] == 42
        assert counts.get("bearer_token") == 1

    def test_redacts_tuple(self):
        obj = ("safe", "API_KEY=secret", 3.14)
        redacted, counts = redact_structure(obj)
        assert isinstance(redacted, tuple)
        assert redacted[0] == "safe"
        assert "secret" not in redacted[1]
        assert counts.get("env_credential") == 1

    def test_passes_through_non_string_types(self):
        obj = {"int": 1, "float": 2.5, "bool": True, "none": None}
        redacted, counts = redact_structure(obj)
        assert redacted == obj
        assert counts == {}

    def test_depth_limit_protects(self):
        # Build a deeply-nested object — deeper than the 32 depth cap.
        obj: dict = {"x": "safe"}
        cur = obj
        for _ in range(40):
            cur["x"] = {"x": "safe"}
            cur = cur["x"]
        # Should not raise / hang.
        redacted, _ = redact_structure(obj)
        assert redacted is not None


# ----------------------------------------------------------------------
# Convenience: redact_iter
# ----------------------------------------------------------------------


class TestRedactIter:
    def test_redacts_sequence(self):
        strings = [
            "safe text",
            "Bearer aaaaaaaaaaaaaaaaaaaa",
            "API_KEY=secret",
        ]
        results, counts = redact_iter(strings)
        assert len(results) == 3
        assert results[0] == "safe text"
        assert "Bearer <redacted-token>" in results[1]
        assert "<redacted-env>" in results[2]
        assert counts.get("bearer_token") == 1
        assert counts.get("env_credential") == 1
