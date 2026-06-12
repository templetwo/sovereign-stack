"""
Security module tests — session id derivation.

The module is currently UNWIRED (imported nowhere in the stack); these
tests exist so the primitives are honest the day something does wire
them in. The headline regression: _generate_secure_id built HMAC
material (random bytes + keyed digest) and then threw it away, returning
fresh `secrets.token_urlsafe` randomness instead — the id was never
derived from the HMAC at all.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from unittest.mock import patch

from sovereign_stack import security
from sovereign_stack.security import SessionManager


class TestGenerateSecureId:
    def test_id_is_derived_from_hmac_material(self):
        """With randomness and clock pinned, the id must be exactly the
        urlsafe encoding of random_bytes + HMAC(secret_key, ...) — proving
        the HMAC material is actually used, not discarded."""
        secret_key = b"\x02" * 32
        random_bytes = b"\x01" * 32
        now = 1750000000.0
        manager = SessionManager(secret_key=secret_key)

        with (
            patch.object(security.secrets, "token_bytes", return_value=random_bytes),
            patch.object(security.time, "time", return_value=now),
        ):
            session_id = manager._generate_secure_id()

        digest = hmac.new(secret_key, random_bytes + str(now).encode(), hashlib.sha256).digest()
        combined = random_bytes + digest[:16]
        expected = base64.urlsafe_b64encode(combined).rstrip(b"=").decode("ascii")[:48]
        assert session_id == expected

    def test_different_secret_keys_yield_different_ids(self):
        """Same randomness + same clock, different keys → different ids.
        Only true if the keyed HMAC participates in the derivation."""
        random_bytes = b"\x01" * 32
        now = 1750000000.0
        with (
            patch.object(security.secrets, "token_bytes", return_value=random_bytes),
            patch.object(security.time, "time", return_value=now),
        ):
            id_a = SessionManager(secret_key=b"\x02" * 32)._generate_secure_id()
            id_b = SessionManager(secret_key=b"\x03" * 32)._generate_secure_id()
        assert id_a != id_b

    def test_id_shape_unchanged(self):
        """48-char urlsafe string — same contract as before the fix."""
        session_id = SessionManager()._generate_secure_id()
        assert len(session_id) == 48
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
        assert set(session_id) <= allowed

    def test_ids_are_unique_across_calls(self):
        manager = SessionManager()
        ids = {manager._generate_secure_id() for _ in range(64)}
        assert len(ids) == 64


class TestCreateSession:
    def test_create_then_get_roundtrip(self):
        manager = SessionManager()
        session_id = manager.create_session(ttl_seconds=60, data={"who": "test"})
        session = manager.get_session(session_id)
        assert session.session_id == session_id
        assert session.data == {"who": "test"}
