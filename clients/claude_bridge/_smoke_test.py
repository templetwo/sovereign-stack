"""
Smoke test: prove the claude bridge's tier + OAuth invariants hold.

Run from sovereign-stack root:
  python -m clients.claude_bridge._smoke_test

Every test should print PASS. OFFLINE-SAFE by construction: no Door That
Asks, no network, no real ~/.sovereign/claude_bridge/ mutations — the
mint/validate/expire section rebinds the oauth module's storage Path
constants to a temporary directory and restores them afterwards.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import sys
import traceback
import urllib.parse
from pathlib import Path
from tempfile import TemporaryDirectory

# Add project root + src so the package imports work regardless of launch dir
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "src"), str(_REPO_ROOT / "clients")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from clients.claude_bridge import oauth, tiers  # noqa: E402
from clients.grok_bridge import oauth as grok_oauth  # noqa: E402
from clients.openai_bridge import oauth as openai_oauth  # noqa: E402

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"

CLIENT_ID = "claude-smoke-client"


def check(name: str, condition: bool, detail: str = "") -> bool:
    tag = PASS if condition else FAIL
    print(f"  {tag}  {name}" + (f" — {detail}" if detail else ""))
    return condition


def call_form_handler(handler, form: dict) -> tuple[int, dict]:
    """Drive an async (form, send) OAuth handler; return (status, json body)."""
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    asyncio.run(handler(form, send))
    status = next(m for m in sent if m["type"] == "http.response.start")["status"]
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, json.loads(body or b"{}")


def run() -> bool:
    results: list[bool] = []

    print("\n── Tier classification ──────────────────────────────────────────")

    results.append(
        check(
            "DESTRUCTIVE_TOOLS and BASE_TOOLS disjoint",
            not (tiers.DESTRUCTIVE_TOOLS & tiers.BASE_TOOLS),
            f"destructive={len(tiers.DESTRUCTIVE_TOOLS)} base={len(tiers.BASE_TOOLS)}",
        )
    )
    results.append(
        check(
            "every destructive tool → step_up",
            all(tiers.classify(t) == tiers.TIER_STEP_UP for t in tiers.DESTRUCTIVE_TOOLS),
        )
    )
    results.append(
        check(
            "base spot-checks → base",
            all(
                tiers.classify(t) == tiers.TIER_BASE
                for t in ("recall_insights", "record_insight", "arrive_lineage", "my_toolkit")
            ),
        )
    )
    results.append(
        check(
            "unknown tool fails closed → step_up",
            tiers.classify("tool_that_does_not_exist") == tiers.TIER_STEP_UP,
        )
    )

    print("\n── PKCE (S256 only) ─────────────────────────────────────────────")

    verifier = "smoke-" + secrets.token_urlsafe(32)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .decode("ascii")
        .rstrip("=")
    )
    results.append(check("S256 verifier accepted", oauth._verify_pkce_s256(verifier, challenge)))
    results.append(
        check(
            "wrong verifier rejected",
            not oauth._verify_pkce_s256("wrong-" + verifier, challenge),
        )
    )
    results.append(check("empty verifier rejected", not oauth._verify_pkce_s256("", challenge)))

    print("\n── Resource normalization / audience ────────────────────────────")

    canonical = oauth.CANONICAL_RESOURCE
    results.append(check("canonical resource accepted", oauth.resource_acceptable(canonical)))
    results.append(check("trailing slash normalized", oauth.resource_acceptable(canonical + "/")))
    parts = urllib.parse.urlsplit(canonical)
    shouty = urllib.parse.urlunsplit(
        (parts.scheme.upper(), parts.netloc.upper(), parts.path, "", "")
    )
    results.append(check("scheme/host case normalized", oauth.resource_acceptable(shouty)))
    results.append(
        check(
            "foreign resource refused",
            not oauth.resource_acceptable("https://evil.example/claude/mcp"),
        )
    )

    print("\n── Redirect pinning ─────────────────────────────────────────────")

    results.append(
        check(
            "claude.ai callback pinned",
            oauth.redirect_uri_pinned("https://claude.ai/api/mcp/auth_callback"),
        )
    )
    results.append(
        check(
            "claude.com callback pinned",
            oauth.redirect_uri_pinned("https://claude.com/api/mcp/auth_callback"),
        )
    )
    results.append(
        check("https://evil.example refused", not oauth.redirect_uri_pinned("https://evil.example"))
    )
    results.append(
        check(
            "http loopback OFF by default (hardened)",
            not oauth.redirect_uri_pinned("http://localhost:8123/callback"),
        )
    )
    _saved_loopback = oauth._ALLOW_LOOPBACK
    oauth._ALLOW_LOOPBACK = True
    try:
        results.append(
            check(
                "http loopback allowed when enabled (RFC 8252)",
                oauth.redirect_uri_pinned("http://localhost:8123/callback"),
            )
        )
        results.append(
            check(
                "http on LAN address refused even when loopback enabled",
                not oauth.redirect_uri_pinned("http://192.168.1.5/callback"),
            )
        )
    finally:
        oauth._ALLOW_LOOPBACK = _saved_loopback

    print("\n── Mint / validate / expire cycle (temp storage) ────────────────")

    path_constants = ("_CODES_DIR", "_TOKENS_DIR", "_REFRESH_DIR", "_CLIENTS_FILE")
    saved = {name: getattr(oauth, name) for name in path_constants}
    try:
        with TemporaryDirectory(prefix="claude_smoke_") as tmp:
            tmp_path = Path(tmp)
            for name in ("_CODES_DIR", "_TOKENS_DIR", "_REFRESH_DIR"):
                d = tmp_path / name.strip("_").lower()
                d.mkdir()
                setattr(oauth, name, d)
            oauth._CLIENTS_FILE = tmp_path / "oauth_clients.json"

            # Mint → validate
            fam = secrets.token_hex(16)
            pair = oauth._mint_token_pair(
                client_id=CLIENT_ID,
                scope=oauth.DEFAULT_SCOPE,
                audience=oauth.CANONICAL_RESOURCE,
                family_id=fam,
            )
            record = oauth.load_valid_access(pair["access_token"])
            results.append(
                check(
                    "minted access token validates",
                    record is not None and record.get("family_id") == fam,
                )
            )

            # Tamper audience → refused (RFC 8707 enforcement point)
            at_path = oauth._token_path("access", pair["access_token"])
            tampered = json.loads(at_path.read_text())
            tampered["audience"] = "https://evil.example/mcp"
            at_path.write_text(json.dumps(tampered))
            results.append(
                check(
                    "tampered audience refused",
                    oauth.load_valid_access(pair["access_token"]) is None,
                )
            )

            # Expiry → refused and lazily deleted
            pair2 = oauth._mint_token_pair(
                client_id=CLIENT_ID,
                scope=oauth.DEFAULT_SCOPE,
                audience=oauth.CANONICAL_RESOURCE,
                family_id=secrets.token_hex(16),
            )
            at2_path = oauth._token_path("access", pair2["access_token"])
            expired = json.loads(at2_path.read_text())
            expired["expires_at"] = "2020-01-01T00:00:00+00:00"
            at2_path.write_text(json.dumps(expired))
            refused = oauth.load_valid_access(pair2["access_token"]) is None
            results.append(
                check("expired access token refused + deleted", refused and not at2_path.exists())
            )

            # Refresh rotation → new pair in the same family
            pair3 = oauth._mint_token_pair(
                client_id=CLIENT_ID,
                scope=oauth.DEFAULT_SCOPE,
                audience=oauth.CANONICAL_RESOURCE,
                family_id=secrets.token_hex(16),
            )
            status, rotated = call_form_handler(
                oauth._token_refresh,
                {"refresh_token": pair3["refresh_token"], "client_id": CLIENT_ID},
            )
            results.append(
                check(
                    "refresh rotation issues a new valid pair",
                    status == 200
                    and rotated.get("refresh_token") not in ("", pair3["refresh_token"])
                    and oauth.load_valid_access(rotated.get("access_token", "")) is not None,
                )
            )

            # Reuse of the rotated refresh token → family revoked
            status, reuse = call_form_handler(
                oauth._token_refresh,
                {"refresh_token": pair3["refresh_token"], "client_id": CLIENT_ID},
            )
            results.append(
                check(
                    "rotated refresh reuse → 400 invalid_grant",
                    status == 400 and reuse.get("error") == "invalid_grant",
                )
            )
            results.append(
                check(
                    "family revoked: post-rotation access token now invalid",
                    oauth.load_valid_access(rotated.get("access_token", "")) is None,
                )
            )
    finally:
        for name, value in saved.items():
            setattr(oauth, name, value)

    print("\n── Other-bridges-unaffected guard ───────────────────────────────")

    results.append(
        check(
            "openai TOKEN_TTL_SECONDS still 0 (untouched)",
            openai_oauth.TOKEN_TTL_SECONDS == 0,
        )
    )
    results.append(
        check(
            "grok TOKEN_TTL_SECONDS still 0 (untouched)",
            grok_oauth.TOKEN_TTL_SECONDS == 0,
        )
    )
    results.append(
        check(
            "openai _verify_pkce still tolerates method=plain",
            openai_oauth._verify_pkce("smoke-plain", "smoke-plain", "plain"),
        )
    )
    results.append(
        check(
            "grok _verify_pkce still tolerates method=plain",
            grok_oauth._verify_pkce("smoke-plain", "smoke-plain", "plain"),
        )
    )
    results.append(
        check(
            "claude oauth has NO plain-tolerant verifier",
            not hasattr(oauth, "_verify_pkce") and hasattr(oauth, "_verify_pkce_s256"),
        )
    )

    print()
    passed = sum(results)
    total = len(results)
    color = "\033[92m" if passed == total else "\033[91m"
    print(f"{color}{passed}/{total} passed\033[0m")
    return passed == total


if __name__ == "__main__":
    try:
        ok = run()
        sys.exit(0 if ok else 1)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
