#!/bin/zsh
# Restart the live TDTB cockpit on the most current code — one command.
#
# `launchctl kickstart` alone only refreshes the BACKEND. The cockpit UI is a
# committed Vite build served off disk, so a kickstart against a stale bundle
# gives you a current server rendering an old UI (2026-07-26: the served bundle
# was pre-T1 for hours while T1-T6 sat in git). This rebuilds both bundles
# first, reloads the current seat plist, then tells you exactly what went live.
#
# Attended operator action. Automated sessions must not run this.
#
#   zsh restart-live.sh                            # rebuild + restart
#   zsh restart-live.sh --no-build                 # restart only
#
# Optional alias:  alias tdtb-restart='zsh ~/Repos/Projects/TDTB/restart-live.sh'

set -u
setopt pipefail

readonly LABEL="com.walle.tdtb"
readonly PORT="${TDTB_PORT:-8746}"
readonly HEALTH_TIMEOUT=25
readonly TASK_DIR="${0:A:h}"
readonly PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
readonly PLIST_TEMPLATE="$TASK_DIR/launchd/$LABEL.plist"
readonly LOG="$HOME/Library/Logs/tdtb-server.log"

skip_build=0
[[ "${1:-}" == "--no-build" ]] && skip_build=1

launchd_started=0

refresh_plist() {
  local vault="" candidate temp

  if [[ -n "${TDTB_VAULT_ROOT:-}" && -d "$TDTB_VAULT_ROOT" ]]; then
    vault="$TDTB_VAULT_ROOT"
  else
    for candidate in \
      "$HOME/Local Documents/Obsidian/WALL⋅E-THNK" \
      "$HOME/Obsidian/WALL⋅E-THNK"; do
      if [[ -d "$candidate" ]]; then
        vault="$candidate"
        break
      fi
    done
  fi

  if [[ -z "$vault" ]]; then
    print -u2 -- "  plist       FAILED — no local WALL⋅E-THNK vault found"
    print -u2 -- "              set TDTB_VAULT_ROOT or sync the vault before restarting"
    return 1
  fi
  if [[ ! -f "$PLIST_TEMPLATE" ]]; then
    print -u2 -- "  plist       FAILED — repo template missing: $PLIST_TEMPLATE"
    return 1
  fi

  mkdir -p "${PLIST:h}" || {
    print -u2 -- "  plist       FAILED — could not create ${PLIST:h}"
    return 1
  }
  temp="$(mktemp "${PLIST}.tmp.XXXXXX")" || {
    print -u2 -- "  plist       FAILED — could not create a temporary plist"
    return 1
  }
  if ! sed \
    -e "s|__WALLE_HOME__|$HOME|g" \
    -e "s|__TDTB_VAULT_ROOT__|$vault|g" \
    "$PLIST_TEMPLATE" > "$temp"; then
    rm -f "$temp"
    print -u2 -- "  plist       FAILED — could not render $PLIST_TEMPLATE"
    return 1
  fi
  if ! plutil -lint "$temp" >/dev/null 2>&1; then
    rm -f "$temp"
    print -u2 -- "  plist       FAILED — rendered plist is invalid"
    return 1
  fi
  chmod 644 "$temp"

  if [[ -f "$PLIST" ]] && cmp -s "$temp" "$PLIST"; then
    rm -f "$temp"
    print -- "  plist       current"
  else
    mv -f "$temp" "$PLIST" || {
      rm -f "$temp"
      print -u2 -- "  plist       FAILED — could not install $PLIST"
      return 1
    }
    print -- "  plist       refreshed from current repo template"
  fi
}

print -- "TDTB restart — $LABEL on :$PORT"

# --- 1. rebuild the committed bundles so the served UI matches the source ----
if (( skip_build )); then
  print -- "  bundles     SKIPPED (--no-build)"
elif [[ ! -d "$TASK_DIR/frontend/node_modules" ]]; then
  print -u2 -- "  bundles     SKIPPED — frontend/node_modules missing"
  print -u2 -- "              run 'npm install' in $TASK_DIR/frontend to rebuild on restart"
else
  for target in prod mockup; do
    if (cd "$TASK_DIR/frontend" && npm run "build:$target" >/tmp/tdtb-build-$target.log 2>&1); then
      print -- "  build:$target  ok"
    else
      print -u2 -- "  build:$target  FAILED — not restarting, the live server still serves the old build"
      tail -5 "/tmp/tdtb-build-$target.log" >&2
      exit 1
    fi
  done
fi

# --- 2. refresh the seat plist and restart the launchd job -------------------
if ! refresh_plist; then
  exit 1
fi
if ! launchctl print "gui/$UID/$LABEL" >/dev/null 2>&1; then
  if lsof -ti ":$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    print -u2 -- "  restart     FAILED — $LABEL is not loaded but :$PORT is occupied"
    print -u2 -- "              refusing to bootstrap a second TDTB writer"
    print -u2 -- "              confirm the owning seat, then bootstrap the staged plist"
    exit 1
  fi
  if ! launchctl bootstrap "gui/$UID" "$PLIST"; then
    print -u2 -- "  restart     FAILED — could not bootstrap $PLIST"
    exit 1
  fi
  launchd_started=1
  print -- "  launchd     bootstrapped $LABEL"
fi

if (( ! launchd_started )); then
  # Always reload the current projection. A staged plist can be newer than
  # the already-loaded launchd job; kickstart preserves that stale environment
  # and can silently revive an old judgment model.
  if ! launchctl bootout "gui/$UID/$LABEL" 2>/dev/null; then
    print -u2 -- "  restart     FAILED — could not unload the current $LABEL job"
    exit 1
  fi
  if ! launchctl bootstrap "gui/$UID" "$PLIST"; then
    print -u2 -- "  restart     FAILED — could not reload $LABEL from $PLIST"
    exit 1
  fi
  print -- "  launchd     reloaded $LABEL from the current plist"
fi

# --- 3. wait for it to actually serve ----------------------------------------
health=""
for _ in {1..$HEALTH_TIMEOUT}; do
  health="$(curl -sf -m 2 "http://127.0.0.1:$PORT/health" 2>/dev/null)" && [[ -n "$health" ]] && break
  sleep 1
done

if [[ -z "$health" ]]; then
  print -u2 -- "  health      NO RESPONSE after ${HEALTH_TIMEOUT}s — last log lines:"
  tail -12 "$LOG" >&2
  exit 1
fi

# --- 4. report what is actually live -----------------------------------------
# launchctl is authoritative: `lsof -ti :PORT` also matches CLIENTS with an open
# connection to the port, so it can report a browser tab instead of the server.
pid="$(launchctl print "gui/$UID/$LABEL" 2>/dev/null | awk -F'= ' '/^\tpid = /{print $2; exit}')"
[[ -n "$pid" ]] || pid="$(lsof -ti ":$PORT" -sTCP:LISTEN 2>/dev/null | head -1)"
started="$(ps -p "$pid" -o lstart= 2>/dev/null | sed 's/^ *//')"
# Last commit that touched the APP, not repo HEAD — HEAD is often an unrelated
# skill/config commit and answers the wrong question ("which app version is live?").
head_commit="$(git -C "$TASK_DIR" log -1 --format='%h %s' -- "$TASK_DIR" 2>/dev/null | cut -c1-72)"
served="$(grep -o 'assets/index-[A-Za-z0-9_-]*\.js' "$TASK_DIR/app/static/cockpit/index.html" 2>/dev/null | head -1)"
dirty="$(git -C "$TASK_DIR" status --porcelain -- "$TASK_DIR" 2>/dev/null | wc -l | tr -d ' ')"

print -- "  health      $health"
print -- "  pid         $pid  (started $started)"
print -- "  restarted   $head_commit"
print -- "  bundle      $served"
(( dirty > 0 )) && print -- "  note        $dirty uncommitted file(s) in the task — live now runs your working tree, not HEAD"
print -- ""
print -- "  open http://127.0.0.1:$PORT/static/cockpit/"
