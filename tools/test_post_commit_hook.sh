#!/bin/sh
# Guard-logic tests for hooks/post-commit.
#
#   sh tools/test_post_commit_hook.sh
#
# Never touches the live seat: every case either hits a skip guard or runs a
# stub in place of restart-live.sh (TDTB_AUTORESTART_CMD), logs to a temp dir
# (TDTB_AUTORESTART_LOG), locks in a temp dir (TDTB_AUTORESTART_LOCK), and
# probes a throwaway loopback status server on a random port
# (TDTB_AUTORESTART_STATUS_URL). No request ever reaches :8746, and launchctl
# is never called.
#
# A pty is required because the hook's first guard is "stderr is a terminal",
# so each run goes through `script -q /dev/null`; `env -i` keeps the caller's
# own PI_*/OPENCODE_* markers out of the runs that test other guards.
set -u

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
HOOK="$REPO_ROOT/hooks/post-commit"
WORK=$(mktemp -d)
LOG="$WORK/autorestart.log"
LOCK="$WORK/autorestart.lock"
FIRED="$WORK/fired"
SERVER_PID=""
FAILURES=0

cleanup() {
  [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null
  rm -rf "$WORK"
}
trap cleanup EXIT

ok() { printf 'ok   %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1"; FAILURES=$((FAILURES + 1)); }

check() { # $1 description, $2 haystack, $3 needle
  case "$2" in
    (*"$3"*) ok "$1" ;;
    (*) bad "$1 (expected to see: $3)" ;;
  esac
}

# --- fixtures ---------------------------------------------------------------

# A restart stub: stands in for restart-live.sh and records that it ran.
cat > "$WORK/stub-restart" <<STUB
#!/bin/sh
echo "stub-restart ran"
: > "$FIRED"
exit 0
STUB
chmod +x "$WORK/stub-restart"

# Hermetic "main checkout": branch main, HEAD is a non-root commit that
# touches app/ (the only commit shape the hook is allowed to act on).
make_repo() { # $1 path
  git init -q -b main "$1"
  mkdir -p "$1/docs"
  echo seed > "$1/docs/readme.md"
  git -C "$1" add -A
  git -C "$1" -c user.email=t@example.com -c user.name=t commit -qm "seed"
  mkdir -p "$1/app"
  echo "x = 1" > "$1/app/x.py"
  git -C "$1" add -A
  git -C "$1" -c user.email=t@example.com -c user.name=t commit -qm "app change"
}

MAIN="$WORK/main-checkout"
make_repo "$MAIN"

# A linked worktree of the same repo: shares the hook, must never restart.
LINKED="$WORK/linked-worktree"
git -C "$MAIN" worktree add -q -b topic "$LINKED"

# A repo whose only commit is the root commit.
ROOT_REPO="$WORK/root-only"
git init -q -b main "$ROOT_REPO"
mkdir -p "$ROOT_REPO/app"
echo "y = 2" > "$ROOT_REPO/app/y.py"
git -C "$ROOT_REPO" add -A
git -C "$ROOT_REPO" -c user.email=t@example.com -c user.name=t commit -qm "only commit"

start_status_server() { # $1 raw response body -> STATUS_URL
  STATUS_PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
  cat > "$WORK/status_server.py" <<'PY'
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[1])
PAYLOAD = sys.argv[2].encode()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(PAYLOAD)))
        self.end_headers()
        self.wfile.write(PAYLOAD)

    def log_message(self, *args):
        pass


HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
PY
  python3 "$WORK/status_server.py" "$STATUS_PORT" "$1" &
  SERVER_PID=$!
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    if curl -sf -m 1 "http://127.0.0.1:$STATUS_PORT/" >/dev/null 2>&1; then
      STATUS_URL="http://127.0.0.1:$STATUS_PORT/commit/status"
      return 0
    fi
    sleep 0.1
  done
  return 1
}

stop_status_server() {
  [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null
  SERVER_PID=""
  wait 2>/dev/null
}

# run_hook <cwd> <status-url> [extra env assignments...]
#
# `env -i` clears the caller's own PI_*/OPENCODE_* markers so each case tests
# the guard it names. The startup files are deliberately NOT skipped: this
# machine's ~/.zshenv exports an OPENCODE_* knob and the hook must not treat
# that ambient configuration as an agent session.
run_hook() {
  hook_cwd=$1
  hook_url=$2
  shift 2
  rm -f "$FIRED"
  script -q /dev/null env -i \
    HOME="$HOME" PATH="$PATH" TERM="${TERM:-dumb}" \
    TDTB_AUTORESTART_CMD="$WORK/stub-restart" \
    TDTB_AUTORESTART_LOG="$LOG" \
    TDTB_AUTORESTART_LOCK="$LOCK" \
    TDTB_AUTORESTART_STATUS_URL="$hook_url" \
    TDTB_TEST_CWD="$hook_cwd" \
    "$@" \
    /bin/zsh -c 'cd "$TDTB_TEST_CWD" && exec /bin/zsh "$0"' "$HOOK" 2>&1
}

DEAD_URL="http://127.0.0.1:9/commit/status"
IDLE_JSON='{"in_flight": false, "mode": null, "since": null}'
BUSY_JSON='{"in_flight": true, "mode": "shadow", "since": "2026-01-01T00:00:00Z"}'

# --- 1. linked worktree skips ----------------------------------------------
output=$(run_hook "$LINKED" "$DEAD_URL")
check "linked worktree skips before any probe" "$output" "linked worktree"
[ -f "$FIRED" ] && bad "linked worktree must not restart" || ok "linked worktree ran no restart"

# --- 2-3. agent markers skip ------------------------------------------------
output=$(run_hook "$MAIN" "$DEAD_URL" PI_CODING_AGENT=1)
check "PI_CODING_AGENT skips" "$output" "agent session (PI_CODING_AGENT)"
output=$(run_hook "$MAIN" "$DEAD_URL" CLAUDE_AGENT_SDK_VERSION=9.9.9)
check "CLAUDE_AGENT_SDK_VERSION skips" "$output" "agent session (CLAUDE_AGENT_SDK_VERSION)"

# --- 4. unreachable commit status fails closed ------------------------------
output=$(run_hook "$MAIN" "$DEAD_URL")
check "unreachable /commit/status skips (fail closed)" "$output" "commit status unavailable"
[ -f "$FIRED" ] && bad "unreachable status must not restart" || ok "unreachable status ran no restart"

# --- 5. unparseable commit status fails closed ------------------------------
if start_status_server 'not-json'; then
  output=$(run_hook "$MAIN" "$STATUS_URL")
  check "unparseable /commit/status skips (fail closed)" "$output" "commit status unreadable"
  stop_status_server
else
  bad "could not start the status stub server"
fi

# --- 6. in-flight commit skips ----------------------------------------------
if start_status_server "$BUSY_JSON"; then
  output=$(run_hook "$MAIN" "$STATUS_URL")
  check "in_flight true skips" "$output" "a plan commit is in flight"
  [ -f "$FIRED" ] && bad "in-flight commit must not restart" || ok "in-flight commit ran no restart"
  stop_status_server
else
  bad "could not start the busy status stub server"
fi

# --- 7. every guard holding schedules the restart ---------------------------
if start_status_server "$IDLE_JSON"; then
  output=$(run_hook "$MAIN" "$STATUS_URL")
  check "idle commit status schedules the restart" "$output" "scheduled"
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 43 44 45 46 47 48 49 50; do
    [ -f "$FIRED" ] && break
    sleep 0.1
  done
  if [ -f "$FIRED" ]; then
    ok "the stub restart ran"
  else
    bad "the stub restart did not run (log: $(cat "$LOG" 2>/dev/null))"
  fi
  check "the log records the run" "$(cat "$LOG" 2>/dev/null)" "auto-restart after"
  check "the log records the exit status" "$(cat "$LOG" 2>/dev/null)" "exit 0"
  [ -d "$LOCK" ] && bad "the auto-restart lock was not released" || ok "the auto-restart lock was released"
  stop_status_server
else
  bad "could not start the idle status stub server"
fi

# --- 7b. ambient OPENCODE_* knobs are NOT agent markers ----------------------
# ~/.zshenv exports OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1 and ~/.zshrc exports
# two more for every shell on this machine. Treating them as agent sessions
# would skip every commit, so with all other guards satisfied the restart must
# still be scheduled and run.
if start_status_server "$IDLE_JSON"; then
  output=$(run_hook "$MAIN" "$STATUS_URL" \
    OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1 \
    OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true \
    OPENCODE_ENABLE_EXA=1)
  check "ambient OPENCODE_* does not skip" "$output" "scheduled"
  case "$output" in
    (*"agent session"*) bad "ambient OPENCODE_* must not read as an agent session" ;;
    (*) ok "ambient OPENCODE_* is not an agent marker" ;;
  esac
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 43 44 45 46 47 48 49 50; do
    [ -f "$FIRED" ] && break
    sleep 0.1
  done
  if [ -f "$FIRED" ]; then
    ok "the stub restart ran with ambient OPENCODE_* set"
  else
    bad "the stub restart did not run with ambient OPENCODE_* set (log: $(cat "$LOG" 2>/dev/null))"
  fi
  stop_status_server
else
  bad "could not start the idle status stub server for the OPENCODE case"
fi

# --- 8. a commit that misses app/ and frontend/ skips -----------------------
echo more >> "$MAIN/docs/readme.md"
git -C "$MAIN" add -A
git -C "$MAIN" -c user.email=t@example.com -c user.name=t commit -qm "docs only"
output=$(run_hook "$MAIN" "$DEAD_URL")
check "a commit outside app/ and frontend/ skips" "$output" "does not touch app/ or frontend/"
[ -f "$FIRED" ] && bad "a docs-only commit must not restart" || ok "a docs-only commit ran no restart"

# --- 9. a branch other than main skips --------------------------------------
git -C "$MAIN" checkout -q -b feature
output=$(run_hook "$MAIN" "$DEAD_URL")
check "a non-main branch skips" "$output" "branch feature is not main"
git -C "$MAIN" checkout -q main

# --- 10. a root commit skips ------------------------------------------------
output=$(run_hook "$ROOT_REPO" "$DEAD_URL")
check "a root commit skips" "$output" "root commit"

# --- summary ---------------------------------------------------------------
if [ "$FAILURES" -eq 0 ]; then
  printf '\nall hook guard cases passed\n'
  exit 0
fi
printf '\n%d hook guard case(s) failed\n' "$FAILURES"
exit 1
