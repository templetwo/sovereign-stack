"""A pytest run can never write the operator's live record (design T0.1).

WHY A REFUSAL AT THE WRITE AND NOT A DIFF OF SIZES. Live seats write the
record during every test run, so a before/after size check of ~/.sovereign
would fail tests for another process's writes. The guard refuses inside each
store's write funnel instead, at the moment of the write, whenever pytest has
set PYTEST_CURRENT_TEST and the target resolves under ~/.sovereign. Nothing
lands, live seats writing during the run are unaffected, and the refusal names
the test, the store and the path.

THREE KINDS OF TEST BELOW:
  1. the guard itself: live refused under pytest, tmp allowed, live allowed
     outside pytest, the containment edges, SOVEREIGN_ROOT never consulted,
     and the live root unmoved when a test redirects $HOME or Path.home();
  2. the census, which is the list of record: every funnel's first statement
     is the guard naming its store, and every file write in the guarded
     modules sits inside a funnel or is named as a writer of something that is
     not a T0.1 store;
  3. the controls: each funnel, aimed at a root the guard has been told is the
     live one, is refused with the path named and leaves that root
     byte-identical. A guard that is present but handed the wrong path passes
     the census and fails here.

Nothing here writes to the real ~/.sovereign: the tests that name it call the
guard alone, never a funnel, and every control runs against a tmp root, with
live_store_guard.live_root repointed at it or the password database stubbed to
name a tmp account home.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import pwd
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

import sovereign_stack
from sovereign_stack import live_store_guard, metabolism, provenance
from sovereign_stack.handoff import HandoffEngine
from sovereign_stack.live_store_guard import LiveStoreWriteRefused, refuse_if_test_targets_live
from sovereign_stack.memory import ExperientialMemory
from sovereign_stack.nape_daemon import NapeDaemon

# The account's real store, stated here independently of the guard: its home is
# read from the password database at collection, before any test can set $HOME
# or patch Path.home(), so the redirect tests below catch a guard that follows
# either. A uid with no passwd entry falls back to $HOME, as the guard does.
try:
    _ACCOUNT_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
except KeyError:
    _ACCOUNT_HOME = Path.home()
LIVE = _ACCOUNT_HOME / ".sovereign"
TEST_ID = "tests/x.py::t (call)"
CONTROL_SEAT = "guard-control-seat"


# ── 1. the guard ────────────────────────────────────────────────────────────


def test_refuses_live_path_under_pytest(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", TEST_ID)
    live = LIVE / "chronicle" / "insights" / "probe" / "x.jsonl"
    with pytest.raises(LiveStoreWriteRefused) as e:
        refuse_if_test_targets_live(live, store="chronicle/insights")
    assert "chronicle/insights" in str(e.value) and str(live) in str(e.value)
    assert (e.value.path, e.value.store, e.value.test_id) == (live, "chronicle/insights", TEST_ID)


def test_allows_tmp_root(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", TEST_ID)
    refuse_if_test_targets_live(
        tmp_path / "chronicle" / "insights" / "a.jsonl", store="chronicle/insights"
    )  # no raise


def test_allows_live_outside_pytest(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    refuse_if_test_targets_live(LIVE / "handoffs" / "x.json", store="handoffs")  # no raise


def test_the_refusal_is_a_runtime_error_and_not_an_assertion_error():
    # A RuntimeError so the old handoff guard's callers and tests keep working,
    # and NOT an AssertionError, which is the class conftest's own containment
    # guards raise: the two stay distinguishable in a failure.
    assert issubclass(LiveStoreWriteRefused, RuntimeError)
    assert not issubclass(LiveStoreWriteRefused, AssertionError)


@pytest.mark.parametrize("target", [LIVE, LIVE.parent], ids=["the-live-root", "above-it"])
def test_the_live_root_itself_and_its_ancestors_are_refused(target):
    # The containment test the handoff guard has used since 2026-08-01, now
    # for the whole tree: equal to, under, or above the live root.
    with pytest.raises(LiveStoreWriteRefused):
        refuse_if_test_targets_live(target, store="any")


def test_sovereign_root_is_never_consulted(tmp_path, monkeypatch):
    # tmp_sovereign_root REDIRECTS stores by setting SOVEREIGN_ROOT, so that
    # variable cannot also be what defines "live": keyed on it, the guard would
    # refuse every fixture write, and pointing it elsewhere would unguard the
    # real store.
    monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
    refuse_if_test_targets_live(tmp_path / "chronicle" / "insights", store="chronicle/insights")
    with pytest.raises(LiveStoreWriteRefused):
        refuse_if_test_targets_live(LIVE / "chronicle" / "insights", store="chronicle/insights")


def test_a_symlink_into_the_live_root_is_refused_by_where_it_resolves(tmp_path, monkeypatch):
    # A stand-in live root, so no link into the operator's real store is left
    # behind in pytest's retained basetemps.
    live = tmp_path / "live"
    live.mkdir()
    monkeypatch.setattr(live_store_guard, "live_root", lambda: live)
    link = tmp_path / "looks-like-tmp"
    link.symlink_to(live, target_is_directory=True)
    with pytest.raises(LiveStoreWriteRefused):
        refuse_if_test_targets_live(link / "chronicle" / "insights", store="chronicle/insights")


def test_the_live_root_is_the_accounts_home_from_the_password_database():
    assert live_store_guard.live_root() == LIVE.resolve()


@pytest.mark.parametrize("redirect", ["HOME", "Path.home", "both"])
def test_redirecting_the_home_directory_leaves_the_real_store_guarded(
    redirect, tmp_path, monkeypatch
):
    # Seven sites in this suite set HOME or patch Path.home() when this was
    # written; the contract walker patches Path.home() for its whole fixture
    # while it walks every tool. A root read through either moved to the fake
    # home for as long as such a test ran, and a writer still bound to the real
    # store went unrefused there.
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    if redirect in ("HOME", "both"):
        monkeypatch.setenv("HOME", str(fake_home))
    if redirect in ("Path.home", "both"):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setenv("PYTEST_CURRENT_TEST", TEST_ID)
    assert Path.home() == fake_home, "the redirect did not take, so this test proves nothing"
    with pytest.raises(LiveStoreWriteRefused):
        refuse_if_test_targets_live(
            LIVE / "chronicle" / "insights" / "probe" / "x.jsonl", store="chronicle/insights"
        )
    assert live_store_guard.live_root() == LIVE.resolve()
    # The fake home is a tmp dir like any other: its .sovereign is not live.
    refuse_if_test_targets_live(
        fake_home / ".sovereign" / "chronicle" / "insights" / "x.jsonl", store="chronicle/insights"
    )


def _no_passwd_entry(uid: int):
    raise KeyError(f"getpwuid(): uid not found: {uid}")


@pytest.mark.parametrize("no_entry", ["no-passwd-entry", "no-pwd-module"])
def test_without_a_passwd_entry_the_root_is_home_as_it_stood_at_import(
    no_entry, tmp_path, monkeypatch
):
    # The one case the password database cannot answer (a uid with no entry,
    # as in some containers, or a platform with no pwd module) falls back to
    # $HOME as it was when the guard was imported, never to $HOME now.
    if no_entry == "no-passwd-entry":
        monkeypatch.setattr(pwd, "getpwuid", _no_passwd_entry)
    else:
        monkeypatch.setattr(live_store_guard, "pwd", None)
    fake_home = tmp_path / "fake-home"
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    root = live_store_guard.live_root()
    assert root == (live_store_guard._HOME_AT_IMPORT / ".sovereign").resolve()
    assert tmp_path not in root.parents


# ── 2. the census: the list of record ───────────────────────────────────────

# Every writer of a T0.1 store, by its real qualified name, with the store its
# guard call must name. Writers of chronicle/transformations,
# chronicle/thread_touches.jsonl and chronicle/archives are here as well as the
# six stores the design lists: they write the same chronicle tree, and the
# plan's Review Focus 1 names archive_exchange.
FUNNELS: dict[tuple[str, str], str] = {
    ("sovereign_stack.memory", "ExperientialMemory.record_insight"): "chronicle/insights",
    ("sovereign_stack.memory", "ExperientialMemory.record_learning"): "chronicle/learnings",
    ("sovereign_stack.memory", "ExperientialMemory.record_transformation"): (
        "chronicle/transformations"
    ),
    ("sovereign_stack.memory", "ExperientialMemory.record_open_thread"): "chronicle/open_threads",
    ("sovereign_stack.memory", "ExperientialMemory.resolve_thread"): "chronicle/open_threads",
    ("sovereign_stack.memory", "ExperientialMemory.resolve_thread_by_id"): (
        "chronicle/open_threads"
    ),
    ("sovereign_stack.memory", "ExperientialMemory.touch_thread"): (
        "chronicle/thread_touches.jsonl"
    ),
    ("sovereign_stack.memory", "ExperientialMemory.archive_exchange"): "chronicle/archives",
    ("sovereign_stack.provenance", "append_supersession"): "chronicle/supersessions.jsonl",
    ("sovereign_stack.nape_daemon", "_append_jsonl"): "nape",
    ("sovereign_stack.handoff", "HandoffEngine.write"): "handoffs",
    ("sovereign_stack.handoff", "HandoffEngine.mark_consumed"): "handoffs",
    ("sovereign_stack.handoff", "HandoffEngine.mark_acted_on"): "handoffs",
    ("sovereign_stack.handoff", "HandoffEngine.sign"): "handoffs",
    ("sovereign_stack.handoff", "HandoffEngine.retire"): "handoffs",
    ("sovereign_stack.metabolism", "_archive_test_artifacts_impl"): "chronicle/insights",
}

# Writers in the census modules (the funnels' modules and every daemon module)
# whose targets are not T0.1 stores, each with the reason, so a NEW write in
# those modules cannot arrive unguarded unnoticed.
NOT_T01_STORES: dict[tuple[str, str], str] = {
    ("sovereign_stack.memory", "MemoryEngine.remember"): (
        "the coherence engine's routed memory (~/.sovereign/memory), not the chronicle"
    ),
    ("sovereign_stack.metabolism", "_dedup_self_model_impl"): (
        "the self-model mirror and its archive"
    ),
    ("sovereign_stack.metabolism", "handle_metabolism_tool"): (
        "a multi-tool handler: the metabolism log, the self-model mirror and "
        "session_handoff are not T0.1 stores; its retire_hypothesis branch rewrites "
        "insight shards and is pinned by "
        "test_retire_hypothesis_is_refused_against_a_live_chronicle below"
    ),
    ("sovereign_stack.daemons.base", "BaseDaemon._perform_halt"): (
        "a daemon halt note in its halt_dir"
    ),
    ("sovereign_stack.daemons.base", "BaseDaemon._save_state"): "a daemon's own state file",
    ("sovereign_stack.daemons.entrypoint", "_comms_post_fn_real"): (
        "a comms channel post under <root>/comms"
    ),
    ("sovereign_stack.daemons.metabolize_daemon", "MetabolizeDaemon._write_decision"): (
        "the metabolize daemon's nightly decision note"
    ),
    ("sovereign_stack.daemons.synthesis_daemon", "write_reflections"): (
        "the reflector's own reflections/ store; it reads the chronicle, never writes it"
    ),
}

_WRITE_MODE_CHARS = frozenset("awx+")
_MUTATING_METHODS = frozenset({"write_text", "write_bytes", "unlink", "rmdir", "rename"})
_MUTATING_OS_CALLS = frozenset({"remove", "unlink", "replace", "rename", "move", "rmtree"})


def _funnel_id(key: tuple[str, str]) -> str:
    module_name, qualname = key
    return f"{module_name.rsplit('.', 1)[1]}.{qualname}"


_SRC = Path(sovereign_stack.__file__).parent


def _source_path(module_name: str) -> Path:
    return _SRC.joinpath(*module_name.split(".")[1:]).with_suffix(".py")


def _module_tree(module_name: str) -> ast.Module:
    # Parsed from the FILE, never inspect.getsource on the live attribute:
    # conftest's nape containment replaces nape_daemon._append_jsonl with a
    # wrapper for every test, so the attribute's source is the wrapper's. Never
    # imported either: a daemon import can have side effects the census has
    # no business triggering.
    return ast.parse(_source_path(module_name).read_text(encoding="utf-8"))


def _census_modules() -> list[str]:
    """The funnels' own modules, plus every daemon module, found on disk so a new
    daemon is scanned the day it lands (Review Focus 1: a daemon's direct
    append to a chronicle shard must fail by name)."""
    daemons = [
        f"sovereign_stack.daemons.{path.stem}"
        for path in sorted((_SRC / "daemons").glob("*.py"))
        if path.stem != "__init__"
    ]
    return sorted({module for module, _ in FUNNELS}) + daemons


def _find_def(tree: ast.Module, qualname: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    node: ast.AST | None = tree
    for part in qualname.split("."):
        node = next(
            (
                child
                for child in ast.iter_child_nodes(node)
                if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and child.name == part
            ),
            None,
        )
        if node is None:
            return None
    return node


def _first_statement(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.stmt:
    body = fn.body
    docstring = ast.get_docstring(fn, clean=False) is not None
    return body[1] if docstring else body[0]


def _guard_store(stmt: ast.stmt) -> str | None:
    """The `store=` a guard call names, or None when `stmt` is not a guard call."""
    if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
        return None
    func = stmt.value.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
    if name != "refuse_if_test_targets_live":
        return None
    for keyword in stmt.value.keywords:
        if keyword.arg == "store" and isinstance(keyword.value, ast.Constant):
            return keyword.value.value
    return None


def _is_write(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr in _MUTATING_METHODS:
        return True
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id in ("os", "shutil")
        and func.attr in _MUTATING_OS_CALLS
    ):
        return True
    if isinstance(func, ast.Name) and func.id == "open":
        mode = call.args[1] if len(call.args) > 1 else None  # builtin open(path, mode)
    elif isinstance(func, ast.Attribute) and func.attr == "open":
        mode = call.args[0] if call.args else None  # Path.open(mode)
    else:
        return False
    for keyword in call.keywords:
        if keyword.arg == "mode":
            mode = keyword.value
    return (
        isinstance(mode, ast.Constant)
        and isinstance(mode.value, str)
        and bool(_WRITE_MODE_CHARS & set(mode.value))
    )


def _write_sites(tree: ast.Module) -> list[tuple[int, str]]:
    """(line, qualified name of the enclosing def) for every file write in the module."""
    sites: list[tuple[int, str]] = []

    def visit(node: ast.AST, scope: list[str], in_function: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef) and not in_function:
                visit(child, [*scope, child.name], False)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # A def nested inside a function is credited to the outermost
                # function, the one whose name the census can list.
                visit(child, scope if in_function else [*scope, child.name], True)
            else:
                if isinstance(child, ast.Call) and _is_write(child):
                    sites.append((child.lineno, ".".join(scope) or "<module>"))
                visit(child, scope, in_function)

    visit(tree, [], False)
    return sites


@pytest.mark.parametrize(
    ("module_name", "qualname"), sorted(FUNNELS), ids=[_funnel_id(key) for key in sorted(FUNNELS)]
)
def test_every_funnel_calls_the_guard(module_name, qualname):
    fn = _find_def(_module_tree(module_name), qualname)
    assert fn is not None, f"the census names {module_name}.{qualname}, which no longer exists"
    store = _guard_store(_first_statement(fn))
    assert store == FUNNELS[(module_name, qualname)], (
        f"{module_name}.{qualname} must begin with "
        f"refuse_if_test_targets_live(..., store={FUNNELS[(module_name, qualname)]!r}) "
        f"(first statement after the docstring names store={store!r})"
    )


def test_every_write_in_the_census_modules_is_accounted_for():
    written: set[tuple[str, str]] = set()
    unaccounted: list[str] = []
    modules = _census_modules()
    assert "sovereign_stack.daemons.synthesis_daemon" in modules  # the daemons are in scope
    for module_name in modules:
        for lineno, qualname in _write_sites(_module_tree(module_name)):
            key = (module_name, qualname)
            written.add(key)
            if key not in FUNNELS and key not in NOT_T01_STORES:
                unaccounted.append(f"{module_name}:{lineno} in {qualname}")
    assert not unaccounted, (
        "a file write outside every census funnel: guard it and add it to FUNNELS, "
        f"or name why it is not a T0.1 store in NOT_T01_STORES: {unaccounted}"
    )
    stale = sorted((set(FUNNELS) | set(NOT_T01_STORES)) - written)
    assert not stale, f"census entries that no longer write anything: {stale}"


# ── 3. the controls: each funnel refused against a root the guard calls live ─


def _snapshot(root: Path) -> dict[str, bytes | None]:
    """Every path under root, files with their bytes: a refused write left nothing."""
    return {
        str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None)
        for p in sorted(root.rglob("*"))
    }


def _chronicle(root: Path) -> ExperientialMemory:
    return ExperientialMemory(root=str(root / "chronicle"))


def _seeded_thread(root: Path) -> tuple[ExperientialMemory, str]:
    memory = _chronicle(root)
    memory.record_open_thread(question="Does the control hold?", domain="probe")
    shard = root / "chronicle" / "open_threads" / "probe.jsonl"
    return memory, json.loads(shard.read_text().splitlines()[0])["thread_id"]


def _seeded_handoff(root: Path) -> tuple[HandoffEngine, str]:
    engine = HandoffEngine(root=str(root))
    written = engine.write(
        note="a control handoff", source_instance=CONTROL_SEAT, source_session_id="control"
    )
    return engine, written["_path"]


def _seeded_insight(root: Path, content: str) -> ExperientialMemory:
    memory = _chronicle(root)
    memory.record_insight(domain="probe", content=content, session_id="control")
    return memory


# Each builder seeds what its funnel needs (while the guard still calls the
# tmp root safe) and returns the one call that, without the guard, WOULD write.
def _record_insight(root: Path) -> Callable[[], object]:
    memory = _chronicle(root)
    return lambda: memory.record_insight(domain="probe", content="a leak", session_id="control")


def _record_learning(root: Path) -> Callable[[], object]:
    memory = _chronicle(root)
    return lambda: memory.record_learning(
        what_happened="a leak", what_learned="refuse it", applies_to="probe"
    )


def _record_transformation(root: Path) -> Callable[[], object]:
    memory = _chronicle(root)
    return lambda: memory.record_transformation(from_state="open", to_state="guarded", trigger="T0")


def _record_open_thread(root: Path) -> Callable[[], object]:
    memory = _chronicle(root)
    return lambda: memory.record_open_thread(question="Is this refused?", domain="probe")


def _resolve_thread(root: Path) -> Callable[[], object]:
    memory, _ = _seeded_thread(root)
    return lambda: memory.resolve_thread(
        domain="probe", question_fragment="control hold", resolution="it holds"
    )


def _resolve_thread_by_id(root: Path) -> Callable[[], object]:
    memory, thread_id = _seeded_thread(root)
    return lambda: memory.resolve_thread_by_id(thread_id=thread_id, resolution="it holds")


def _touch_thread(root: Path) -> Callable[[], object]:
    memory, thread_id = _seeded_thread(root)
    return lambda: memory.touch_thread(thread_id=thread_id, note="seen", instance_id=CONTROL_SEAT)


def _archive_exchange(root: Path) -> Callable[[], object]:
    memory = _chronicle(root)
    return lambda: memory.archive_exchange(content="verbatim bytes", source="guard-control")


def _append_supersession(root: Path) -> Callable[[], object]:
    record = provenance.build_supersession_record(action="revoke", superseded_id="a" * 64)
    ledger = root / "chronicle" / "supersessions.jsonl"
    return lambda: provenance.append_supersession(ledger, record)


def _nape_append(root: Path) -> Callable[[], object]:
    daemon = NapeDaemon(root=str(root))
    return lambda: daemon.observe(
        tool_name="record_insight", arguments={}, result="ok", session_id="control"
    )


def _handoff_write(root: Path) -> Callable[[], object]:
    engine = HandoffEngine(root=str(root))
    return lambda: engine.write(
        note="a leaking handoff", source_instance=CONTROL_SEAT, source_session_id="control"
    )


def _handoff_mark_consumed(root: Path) -> Callable[[], object]:
    engine, path = _seeded_handoff(root)
    return lambda: engine.mark_consumed([path], consumed_by=CONTROL_SEAT)


def _handoff_mark_acted_on(root: Path) -> Callable[[], object]:
    engine, path = _seeded_handoff(root)
    return lambda: engine.mark_acted_on(path, CONTROL_SEAT, "refused it")


def _handoff_sign(root: Path) -> Callable[[], object]:
    engine, path = _seeded_handoff(root)
    return lambda: engine.sign(path, CONTROL_SEAT)


def _handoff_retire(root: Path) -> Callable[[], object]:
    engine, path = _seeded_handoff(root)
    return lambda: engine.retire(path, CONTROL_SEAT, "a control")


def _archive_test_artifacts(root: Path) -> Callable[[], object]:
    _seeded_insight(root, "STRESS TEST: a row the hygiene pass would move")
    return lambda: metabolism._archive_test_artifacts_impl(root / "chronicle")


CONTROLS: dict[tuple[str, str], Callable[[Path], Callable[[], object]]] = {
    ("sovereign_stack.memory", "ExperientialMemory.record_insight"): _record_insight,
    ("sovereign_stack.memory", "ExperientialMemory.record_learning"): _record_learning,
    ("sovereign_stack.memory", "ExperientialMemory.record_transformation"): (
        _record_transformation
    ),
    ("sovereign_stack.memory", "ExperientialMemory.record_open_thread"): _record_open_thread,
    ("sovereign_stack.memory", "ExperientialMemory.resolve_thread"): _resolve_thread,
    ("sovereign_stack.memory", "ExperientialMemory.resolve_thread_by_id"): _resolve_thread_by_id,
    ("sovereign_stack.memory", "ExperientialMemory.touch_thread"): _touch_thread,
    ("sovereign_stack.memory", "ExperientialMemory.archive_exchange"): _archive_exchange,
    ("sovereign_stack.provenance", "append_supersession"): _append_supersession,
    ("sovereign_stack.nape_daemon", "_append_jsonl"): _nape_append,
    ("sovereign_stack.handoff", "HandoffEngine.write"): _handoff_write,
    ("sovereign_stack.handoff", "HandoffEngine.mark_consumed"): _handoff_mark_consumed,
    ("sovereign_stack.handoff", "HandoffEngine.mark_acted_on"): _handoff_mark_acted_on,
    ("sovereign_stack.handoff", "HandoffEngine.sign"): _handoff_sign,
    ("sovereign_stack.handoff", "HandoffEngine.retire"): _handoff_retire,
    ("sovereign_stack.metabolism", "_archive_test_artifacts_impl"): _archive_test_artifacts,
}


def test_every_census_funnel_has_a_control():
    assert set(CONTROLS) == set(FUNNELS)


_REAL_LIVE_ROOT = live_store_guard.live_root


@pytest.mark.parametrize("funnel", sorted(CONTROLS), ids=_funnel_id)
def test_control_a_leaking_write_is_refused(funnel, monkeypatch, tmp_sovereign_root):
    # Positive control: point the guard's live root at tmp_sovereign_root and
    # call the real funnel rooted there. It must be refused, name the path,
    # and leave the root exactly as it was.
    act = CONTROLS[funnel](tmp_sovereign_root)
    before = _snapshot(tmp_sovereign_root)
    monkeypatch.setattr(live_store_guard, "live_root", lambda: tmp_sovereign_root)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "t")
    with pytest.raises(LiveStoreWriteRefused) as refused:
        act()
    assert refused.value.store == FUNNELS[funnel]
    assert str(tmp_sovereign_root) in str(refused.value), "the refusal must name the path"
    assert _snapshot(tmp_sovereign_root) == before, "the refused write still landed"
    # The same call, once the guard calls that root safe, does write: the
    # unchanged snapshot above is the guard's doing, not a control with no teeth.
    monkeypatch.setattr(live_store_guard, "live_root", _REAL_LIVE_ROOT)
    act()
    assert _snapshot(tmp_sovereign_root) != before, "this control's call writes nothing"


def test_retire_hypothesis_is_refused_against_a_live_chronicle(monkeypatch, tmp_sovereign_root):
    # retire_hypothesis rewrites insight shards inside metabolism's multi-tool
    # handler, so it is pinned here rather than by the census. Its
    # CHRONICLE_DIR is bound from Path.home() at import and ignores
    # SOVEREIGN_ROOT: the exact shape that lets a test aim it at the live store.
    _seeded_insight(tmp_sovereign_root, "a hypothesis the control would retire")
    monkeypatch.setattr(metabolism, "CHRONICLE_DIR", tmp_sovereign_root / "chronicle")
    before = _snapshot(tmp_sovereign_root)
    monkeypatch.setattr(live_store_guard, "live_root", lambda: tmp_sovereign_root)
    arguments = {"content_fragment": "would retire", "reason": "control", "replaced_by": "none"}
    retire = metabolism.handle_metabolism_tool("retire_hypothesis", arguments)
    with pytest.raises(LiveStoreWriteRefused) as refused:
        asyncio.run(retire)
    assert refused.value.store == "chronicle/insights"
    assert _snapshot(tmp_sovereign_root) == before, "the refused retirement still rewrote"
    monkeypatch.setattr(live_store_guard, "live_root", _REAL_LIVE_ROOT)
    asyncio.run(metabolism.handle_metabolism_tool("retire_hypothesis", arguments))
    assert _snapshot(tmp_sovereign_root) != before, "this control's retirement rewrites nothing"


def test_mark_consumed_refuses_a_live_handoff_handed_to_an_engine_rooted_elsewhere(
    tmp_path, monkeypatch
):
    # mark_consumed rewrites the paths it is HANDED, not files under its own
    # root, so a guard on the root alone would let a tmp-rooted engine
    # consume a live handoff.
    live = tmp_path / "live"
    _, live_handoff = _seeded_handoff(live)
    elsewhere = HandoffEngine(root=str(tmp_path / "elsewhere"))
    before = Path(live_handoff).read_bytes()
    monkeypatch.setattr(live_store_guard, "live_root", lambda: live)
    with pytest.raises(LiveStoreWriteRefused) as refused:
        elsewhere.mark_consumed([live_handoff], consumed_by=CONTROL_SEAT)
    assert live_handoff in str(refused.value)
    assert Path(live_handoff).read_bytes() == before
    monkeypatch.setattr(live_store_guard, "live_root", _REAL_LIVE_ROOT)
    assert elsewhere.mark_consumed([live_handoff], consumed_by=CONTROL_SEAT) == 1


def test_a_writer_bound_to_the_account_store_is_refused_while_home_is_redirected(
    tmp_path, monkeypatch
):
    # The contract walker's window, end to end through the real live_root(): a
    # writer built against the account's store before the test moved the home
    # directory (as a module singleton is at import), then $HOME and
    # Path.home() redirected. Only the password database is stubbed, to name a
    # stand-in account home, so a regressed guard still could not reach the
    # real store from here.
    account, fake_home = tmp_path / "account", tmp_path / "fake-home"

    def account_home_is(home: Path) -> None:
        monkeypatch.setattr(pwd, "getpwuid", lambda uid: SimpleNamespace(pw_dir=str(home)))

    account_home_is(account)
    bound = _chronicle(account / ".sovereign")
    elsewhere = _chronicle(fake_home / ".sovereign")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setenv("PYTEST_CURRENT_TEST", TEST_ID)

    before = _snapshot(account)
    with pytest.raises(LiveStoreWriteRefused) as refused:
        bound.record_insight(domain="probe", content="a leak", session_id="control")
    assert refused.value.store == "chronicle/insights"
    assert str(account) in str(refused.value), "the refusal must name the path"
    assert _snapshot(account) == before, "the refused write still landed"

    # The redirected home is a tmp dir like any other: a writer rooted there writes.
    home_before = _snapshot(fake_home)
    elsewhere.record_insight(domain="probe", content="not live", session_id="control")
    assert _snapshot(fake_home) != home_before, "the write under the redirected HOME did not land"

    # Teeth: the same bound call writes once the account's home is elsewhere.
    account_home_is(tmp_path / "another-account")
    bound.record_insight(domain="probe", content="a leak", session_id="control")
    assert _snapshot(account) != before, "this control's call writes nothing"
