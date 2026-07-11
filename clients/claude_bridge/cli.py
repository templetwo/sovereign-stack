"""
HQ command line for the Claude connector's credential surface.

  python -m clients.claude_bridge.cli list
  python -m clients.claude_bridge.cli revoke --family <family_id>
  python -m clients.claude_bridge.cli revoke-all

revoke-all is the one-call kill switch: every access token deleted, every
refresh token tombstoned, every elevation dropped, every registered client
forgotten. The connector on claude.ai then fails its next request with a 401
and must be re-authorized through the consent page.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import elevation
from .oauth import _CLIENTS_FILE, _REFRESH_DIR, _TOKENS_DIR, revoke_family


def _iter_records(directory):
    for path in sorted(directory.glob("*.json")):
        try:
            yield path, json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue


def cmd_list() -> int:
    families: dict[str, dict] = {}
    for _, rec in _iter_records(_TOKENS_DIR):
        fam = families.setdefault(rec.get("family_id", "?"), {"access": 0, "refresh": 0})
        fam["access"] += 1
        fam["client_id"] = rec.get("client_id")
        fam["expires_at"] = rec.get("expires_at")
    for _, rec in _iter_records(_REFRESH_DIR):
        fam = families.setdefault(rec.get("family_id", "?"), {"access": 0, "refresh": 0})
        fam["refresh"] += 1
        fam.setdefault("client_id", rec.get("client_id"))
        fam[f"refresh_{rec.get('status', '?')}"] = (
            fam.get(f"refresh_{rec.get('status', '?')}", 0) + 1
        )
    print(json.dumps({"families": families}, indent=2))
    return 0


def cmd_revoke(family_id: str) -> int:
    touched = revoke_family(family_id)
    print(f"revoked family {family_id}: {touched} records touched")
    return 0 if touched else 1


def cmd_revoke_all() -> int:
    access = 0
    for path in _TOKENS_DIR.glob("*.json"):
        path.unlink(missing_ok=True)
        access += 1
    refresh = 0
    for path, rec in list(_iter_records(_REFRESH_DIR)):
        if rec.get("status") != "revoked":
            rec["status"] = "revoked"
            path.write_text(json.dumps(rec, indent=2))
            refresh += 1
    elevations = elevation.revoke_all_elevations()
    clients = 0
    if _CLIENTS_FILE.exists():
        try:
            clients = len(json.loads(_CLIENTS_FILE.read_text()))
        except json.JSONDecodeError:
            clients = -1
        _CLIENTS_FILE.unlink()
    elevation.audit(
        "revoke_all_cli", access=access, refresh=refresh, elevations=elevations, clients=clients
    )
    print(
        f"revoke-all: {access} access tokens deleted, {refresh} refresh tokens "
        f"tombstoned, {elevations} elevations dropped, {clients} clients forgotten"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="claude_bridge.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    p_revoke = sub.add_parser("revoke")
    p_revoke.add_argument("--family", required=True)
    sub.add_parser("revoke-all")
    args = parser.parse_args(argv)

    if args.command == "list":
        return cmd_list()
    if args.command == "revoke":
        return cmd_revoke(args.family)
    if args.command == "revoke-all":
        return cmd_revoke_all()
    return 2


if __name__ == "__main__":
    sys.exit(main())
