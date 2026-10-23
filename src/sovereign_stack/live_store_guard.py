"""
Live-store guard: a pytest run can never write the operator's live record.

Every write funnel for a store under ~/.sovereign (the chronicle's insights,
open threads, learnings, transformations, thread touches, archives and
supersession ledger; the handoffs; the nape store) calls
``refuse_if_test_targets_live`` as its first statement. Outside pytest the
call returns at once. Under pytest it refuses a target that resolves inside
the live root, so nothing lands and the failing test is named.

WHY REFUSE AT THE WRITE AND NOT DIFF THE STORE. Live seats write the record
during every test run, so a before/after size check would fail tests for
another process's writes. A refusal at the moment of the write says something
true whatever else is running on the machine, and no erratum is ever needed
for a catch.

WHY THE LIVE ROOT IGNORES SOVEREIGN_ROOT. Tests redirect stores by setting
SOVEREIGN_ROOT (the ``tmp_sovereign_root`` fixture points it at a tmp root),
so that variable cannot also be what defines "live": keyed on it, the guard
would refuse every fixture write. The leak this exists to stop is the other
direction: a module-level singleton (``server.experiential``,
``server.handoff_engine``, ``server.nape_daemon``) built from the live root at
import keeps writing there after a test sets SOVEREIGN_ROOT.

WHY IT IGNORES $HOME AND Path.home() TOO. Tests redirect the home directory as
well: they set HOME or patch Path.home() (seven sites in the suite when this was
written; the contract walker patches Path.home() for its whole fixture while it
walks every tool). A root read through either moves to the fake home for as long
as such a test runs, and that is exactly when a writer still bound to the real
store at import goes unrefused. So the account's home comes from the password
database, read on every call. Only a uid with no passwd entry (or a platform
with no pwd module) falls back to $HOME as it stood when this module was
imported: the same import-time binding tests/conftest.py gives its own
_LIVE_SOVEREIGN.

Moved here from ``handoff._refuse_live_store_during_tests``, written
2026-08-01 to guard the handoff store alone after two test fixtures
(tests/test_resume_in_context.py and tests/test_nape_autohook.py
``_isolated_server``) had, through the stale ``server.handoff_engine``
singleton, respectively consumed 249 of 251 real handoffs and written 51
"healthy probe" records into ~/.sovereign/handoffs/. The containment test is the same one,
widened from ~/.sovereign/handoffs to the whole ~/.sovereign tree (design
T0.1).
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    import pwd
except ImportError:  # not a POSIX platform: _account_home() falls back
    pwd = None

# $HOME as it stood when this module was first imported, before any test could
# move it. Read only when the password database has no entry for this account.
_HOME_AT_IMPORT = Path(os.path.expanduser("~"))


class LiveStoreWriteRefused(RuntimeError):
    """A write aimed at the live ~/.sovereign tree from inside a pytest run.

    A RuntimeError, as the handoff guard it replaces raised, and deliberately
    NOT an AssertionError: conftest's own containment guards raise that, and
    the two stay distinguishable in a failure.
    """

    def __init__(self, path: Path, store: str, test_id: str) -> None:
        self.path = path
        self.store = store
        self.test_id = test_id
        super().__init__(
            f"Refusing to write to the live Sovereign Stack store {store!r} from inside "
            f"a pytest run ({test_id}): {path} resolves inside the live root "
            "(~/.sovereign). Root the writer at tmp_path or the tmp_sovereign_root "
            "fixture. A module-level singleton built from the live root at import "
            "(server.experiential, server.handoff_engine, server.nape_daemon) does not "
            "move when a test sets SOVEREIGN_ROOT afterwards: rebind the singleton itself "
            "to an instance rooted at a tmp path."
        )


def _account_home() -> Path:
    """This account's home directory, from the password database, never from $HOME.

    Falls back to ``_HOME_AT_IMPORT`` only when the database cannot answer: a uid
    with no passwd entry (some containers), an empty home field, or no pwd module.
    """
    if pwd is not None:
        try:
            home = pwd.getpwuid(os.getuid()).pw_dir
        except KeyError:
            home = ""
        if home:
            return Path(home)
    return _HOME_AT_IMPORT


def live_root() -> Path:
    """The operator's live store root, resolved on every call.

    The account's home plus ``.sovereign``. The home is read from the password
    database, so a test that sets HOME or patches Path.home() does not move it,
    and SOVEREIGN_ROOT is never consulted (see the module docstring for both).
    A module-level function so a test can repoint it at a tmp root to prove a
    funnel refuses.
    """
    return (_account_home() / ".sovereign").resolve()


def refuse_if_test_targets_live(path: Path | str, *, store: str) -> None:
    """Raise LiveStoreWriteRefused when a pytest run aims a write at the live root.

    Refuses only while ``PYTEST_CURRENT_TEST`` is set (pytest sets it for every
    test's setup, call and teardown) AND ``path`` resolves equal to, under, or
    above the live root. Both sides are resolved, so a symlink is judged by
    where it lands, not how it is spelled. ``store`` names the store in the
    refusal (e.g. "chronicle/insights").
    """
    test_id = os.environ.get("PYTEST_CURRENT_TEST")
    if not test_id:
        return
    target = Path(path)
    try:
        resolved = target.resolve()
    except (OSError, RuntimeError):  # a symlink loop: judge the spelled path
        resolved = Path(os.path.abspath(target))
    root = Path(live_root()).resolve()
    if resolved == root or root in resolved.parents or resolved in root.parents:
        raise LiveStoreWriteRefused(target, store, test_id)
