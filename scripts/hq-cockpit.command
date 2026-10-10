#!/bin/bash
# hq-cockpit.command  (DRAFT 3, 2026-10-09; DRAFT 2 was HQ's corrections to the web seat's DRAFT 1,
# archive 2b34177e)
# Phase 1 of the hq-cockpit plan: one click brings up the console, HQ, and the browser.
# NOT YET RUN on the Studio in either mode; exercised only against fakes (tests/test_hq_cockpit_launcher.py).
# Draft 3 changes (each marked "draft 3" below):
#   A. The HQ guard also recognises the Claude app's Code-tab seat, whose binary lives under
#      ~/Library/Application Support/Claude/claude-code/, and matches the process name by
#      basename, so a comm reported as a full path (with the space in "Application Support")
#      is not dropped before the path test.
#   B. --dry-run (or HQ_COCKPIT_DRY_RUN=1): prints every decision as a "[dry-run]" line and acts
#      on nothing (no launchctl kickstart/bootstrap, no nohup, no osascript, no open). Exits 0.
#   C. The last-resort comment is corrected: a nohup'd console outlives this window.
#   (Also: env overrides HQ_COCKPIT_CONSOLE_URL/_PLIST/_BIN/_LOG for the test suite, same defaults.)
# Draft 2's notes, each measured on the Studio 2026-10-09:
#   1. The console is a launchd job (com.templetwo.sovereign-dashboard-web, RunAtLoad + KeepAlive,
#      since 2026-08-18). If it is down, ask launchd to restart it; never nohup a second copy, which
#      would race launchd's KeepAlive relaunch for port 3435. nohup is the last resort only when the
#      job is not loaded at all. Its log is ~/.cache/sovereign-dashboard/web.log (from the plist).
#   2. The duplicate-seat guard no longer uses pgrep: pgrep excludes its own ancestors, so run from
#      inside an HQ session it cannot see that seat. It now lists every process named `claude`
#      (the Terminal seat AND the Claude app's Code-tab seat both report comm=claude), skips
#      non-interactive `claude -p` children, and counts a seat as HQ if its cwd is $HOME or its
#      binary lives inside Claude.app (the Code-tab seat launches with a scratch cwd).
#      draft 3: the Code-tab seat may report comm as its full path, and its binary is under
#      ~/Library/Application Support/Claude/claude-code/, not /Applications/Claude.app; see change A.
#   3. hq-boot never starts the console (boot_checks.sh is verify-only), so there is no race there.
# It never stops, restarts HQ, or deletes anything. The only restart it can ask for is the console's.

set -u

# draft 3 (B): dry-run switch. Reads (curl /health, launchctl print, ps, lsof) still happen,
# because they are how the decisions are reached; nothing that changes state runs.
DRY_RUN=0
[ "${HQ_COCKPIT_DRY_RUN:-0}" = "1" ] && DRY_RUN=1
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1
say() { [ "$DRY_RUN" = 1 ] && echo "[dry-run] $*"; }   # decision line, dry-run only

# draft 3 (D): env overrides exist for the tests; the defaults are draft 2's.
CONSOLE_URL="${HQ_COCKPIT_CONSOLE_URL:-http://127.0.0.1:3435}"
CONSOLE_LABEL="com.templetwo.sovereign-dashboard-web"
CONSOLE_PLIST="${HQ_COCKPIT_CONSOLE_PLIST:-$HOME/Library/LaunchAgents/$CONSOLE_LABEL.plist}"
CONSOLE_BIN="${HQ_COCKPIT_CONSOLE_BIN:-$HOME/sovereign-stack/venv/bin/sovereign-dashboard-web}"  # what the plist runs
CONSOLE_LOG="${HQ_COCKPIT_CONSOLE_LOG:-$HOME/.cache/sovereign-dashboard/web.log}"                # the plist's StandardOutPath
CONSOLE_PORT="${CONSOLE_URL##*:}"   # HQ review (draft 3): the last-resort nohup follows the URL's port
DOMAIN="gui/$(id -u)"

console_up() {
  # HQ review (draft 3): match the status field, not the bare word, so a body that merely
  # mentions "healthy" (for example "healthy": false) does not read as up.
  curl -s --max-time 2 "$CONSOLE_URL/health" | grep -qE '"status": *"healthy"'
}

wait_console() {
  for _ in $(seq 1 20); do console_up && return 0; sleep 0.5; done
  return 1
}

# Is an interactive claude seat already live? Sets HQ_PID to the first one found.
# draft 3 (A): no pipeline-into-while any more, so dry-run lines are not swallowed and `return`
# leaves the function, not a subshell. Name match is by basename over the WHOLE comm (awk strips
# the pid instead of reading $2, because a full-path comm contains a space).
HQ_PID=""
HQ_KIND=""   # HQ review (draft 3): "terminal" or "app", so the right window comes forward
hq_running() {
  candidates=$(ps -axo pid=,comm= | awk '{p=$1; sub(/^[ \t]*[0-9]+[ \t]+/, ""); if ($0 == "claude" || $0 ~ /\/claude$/) print p "\t" $0}')
  [ -z "$candidates" ] && say "no process named claude found"
  while IFS="$(printf '\t')" read -r pid comm; do
    [ -z "$pid" ] && continue
    args=$(ps -o args= -p "$pid" 2>/dev/null)
    case " $args " in *" -p "*|*" --print "*)
      say "pid $pid comm=$comm: non-interactive (-p/--print) child, not HQ"; continue;; esac
    exe=$(lsof -a -p "$pid" -d txt -Fn 2>/dev/null | sed -n 's/^n//p' | grep -m1 -E '/claude(\.exe)?$')
    cwd=$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')
    in_app=0
    # draft 3 (A): the Code-tab binary is under Application Support, not /Applications/Claude.app;
    # test the comm too, since a full-path comm is the binary path.
    for p in "$exe" "$comm"; do
      case "$p" in
        /Applications/Claude.app/*|*"/Application Support/Claude/claude-code/"*) in_app=1;;
      esac
    done
    if [ "$cwd" = "$HOME" ] || [ "$in_app" = 1 ]; then
      say "pid $pid comm=$comm cwd=$cwd exe=$exe: counts as HQ"
      HQ_PID=$pid; HQ_KIND=terminal; [ "$in_app" = 1 ] && HQ_KIND=app
      return 0
    fi
    say "pid $pid comm=$comm cwd=$cwd exe=$exe: not HQ (cwd is not \$HOME, binary not in a Claude app)"
  done <<CANDIDATES
$candidates
CANDIDATES
  return 1
}

# 1. Console
if console_up; then
  echo "Console already up at $CONSOLE_URL"
  say "console: healthy, no action"
elif launchctl print "$DOMAIN/$CONSOLE_LABEL" >/dev/null 2>&1; then
  if [ "$DRY_RUN" = 1 ]; then
    say "console: job loaded but not answering; would run launchctl kickstart -k $DOMAIN/$CONSOLE_LABEL and wait for healthy"
  else
    echo "Console job is loaded but not answering; asking launchd to restart it..."
    launchctl kickstart -k "$DOMAIN/$CONSOLE_LABEL"
    wait_console || { echo "Still not answering. Last lines of $CONSOLE_LOG:"; tail -5 "$CONSOLE_LOG"; exit 1; }
  fi
elif [ -f "$CONSOLE_PLIST" ]; then
  if [ "$DRY_RUN" = 1 ]; then
    say "console: job not loaded, plist present; would run launchctl bootstrap $DOMAIN $CONSOLE_PLIST and wait for healthy"
  else
    echo "Console job is not loaded; loading its LaunchAgent..."
    launchctl bootstrap "$DOMAIN" "$CONSOLE_PLIST"
    wait_console || { echo "Did not come up. Last lines of $CONSOLE_LOG:"; tail -5 "$CONSOLE_LOG"; exit 1; }
  fi
else
  # draft 3 (C): nohup ignores SIGHUP, so this console OUTLIVES the window that started it. It is
  # still the last resort because launchd does not supervise it: nothing restarts it if it dies.
  if [ "$DRY_RUN" = 1 ]; then
    say "console: no job and no plist; would nohup $CONSOLE_BIN --port $CONSOLE_PORT (log $CONSOLE_LOG) and wait for healthy"
  else
    echo "No LaunchAgent found; starting the console directly (last resort, not supervised by launchd)..."
    mkdir -p "$(dirname "$CONSOLE_LOG")"
    nohup "$CONSOLE_BIN" --port "$CONSOLE_PORT" >>"$CONSOLE_LOG" 2>&1 &
    wait_console || { echo "Did not come up. Last lines of $CONSOLE_LOG:"; tail -5 "$CONSOLE_LOG"; exit 1; }
  fi
fi

# 2. HQ: never start a second HQ seat (two seats would share one handoff queue and one text outbox).
if hq_running; then
  # HQ review (draft 3): a Code-tab seat lives in the Claude app, so bring that app forward with
  # `open -a` (no Apple event, so no Automation prompt). Any interactive Code-tab session counts,
  # on any project, because its cwd cannot tell HQ from another session; the message says so.
  if [ "$DRY_RUN" = 1 ]; then
    if [ "$HQ_KIND" = app ]; then
      say "hq: found pid $HQ_PID in the Claude app (Code tab); would open -a Claude, would NOT start a seat"
    else
      say "hq: found pid $HQ_PID in Terminal; would bring Terminal forward, would NOT start a seat"
    fi
  elif [ "$HQ_KIND" = app ]; then
    echo "A Claude app Code-tab session is open (pid $HQ_PID) and counts as HQ; not starting a second seat."
    echo "If that session is not HQ, close it, or start HQ by hand with: cd ~ && claude -n HQ '/hq-boot'"
    open -a Claude
  else
    echo "HQ is already running in Terminal (pid $HQ_PID). Bringing Terminal forward instead of starting a second seat."
    osascript -e 'tell application "Terminal" to activate'
  fi
elif [ "$DRY_RUN" = 1 ]; then
  say "hq: none found; would start HQ in Terminal: cd ~ && claude -n HQ '/hq-boot'"
else
  echo "Starting HQ in Terminal..."
  # A .command opened from Finder already runs inside Terminal, so this Apple event is Terminal
  # talking to itself: measured 2026-10-09, no Automation prompt from a Terminal-descended shell.
  osascript <<'APPLESCRIPT'
tell application "Terminal"
  activate
  do script "cd ~ && claude -n HQ '/hq-boot'"
end tell
APPLESCRIPT
fi

# 3. Browser
if [ "$DRY_RUN" = 1 ]; then
  say "browser: would open $CONSOLE_URL"
  exit 0
fi
open "$CONSOLE_URL"
