#!/bin/zsh
# Restart the live TDTB cockpit on the most current code — one command.
#
# `launchctl kickstart` alone only refreshes the BACKEND. The cockpit UI is a
# committed Vite build served off disk, so a kickstart against a stale bundle
# gives you a current server rendering an old UI (2026-07-26: the served bundle
# was pre-T1 for hours while T1-T6 sat in git). This rebuilds both bundles
# first, reloads the current seat plist, then tells you exactly what went live.
#
# Before anything is stopped, the machine-local state under
# ~/.config/tdtb (honours TDTB_HOME) is snapshotted to
# ~/.config/tdtb/backups/pre-restart-YYYYMMDD-HHMMSS/ (newest 5 kept), and the
# last commit that served (backups/last-good-commit) is printed. If /health does not come back, the script
# prints the log tail, the snapshot path, and the exact rollback steps — it
# never reverts code or state by itself.
#
# Attended operator action. Automated sessions must not run this.
#
#   zsh restart-live.sh                        # rebuild + restart
#   zsh restart-live.sh --no-build             # restart only
#   ~/.local/bin/tdtb-restart                  # installed symlink to this script

set -u
setopt pipefail

readonly LABEL="com.walle.tdtb"
readonly PORT="${TDTB_PORT:-8746}"
# Overridable so an operator (or the restart tests) can shorten the wait.
readonly HEALTH_TIMEOUT="${TDTB_HEALTH_TIMEOUT:-25}"
readonly LOG_LINES=30
readonly TASK_DIR="${0:A:h}"
readonly PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
readonly PLIST_TEMPLATE="$TASK_DIR/launchd/$LABEL.plist"
readonly LOG="$HOME/Library/Logs/tdtb-server.log"
readonly STATE_HOME="${TDTB_HOME:-$HOME/.config/tdtb}"
readonly BACKUP_ROOT="$STATE_HOME/backups"
readonly BACKUP_KEEP=5
# The commit of the last restart that answered /health. The checkout's HEAD at
# restart time is the code being DEPLOYED, so it can never be the rollback
# target; this file names the code that was last known to serve.
readonly LAST_GOOD="$BACKUP_ROOT/last-good-commit"

skip_build=0
[[ "${1:-}" == "--no-build" ]] && skip_build=1

launchd_started=0

# `launchctl bootstrap` intermittently returns "Bootstrap failed: 5:
# Input/output error" when it lands immediately after a bootout, while launchd
# is still releasing the job (2026-10-27: the job stayed unloaded and the seat
# was left down). One short retry absorbs that transient; a real failure still
# exits non-zero, with both attempts' output surfaced.
bootstrap_job() {
  local attempt out

  for attempt in 1 2; do
    if out="$(launchctl bootstrap "gui/$UID" "$PLIST" 2>&1)"; then
      return 0
    fi
    out="${out//$'\n'/ }"
    if (( attempt == 1 )); then
      print -u2 -- "  launchd     bootstrap failed (${out}); retrying once"
      sleep 1
    else
      print -u2 -- "  launchd     bootstrap failed after retry (${out})"
    fi
  done
  return 1
}

refresh_plist() {
  local temp

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
    -e "s|__TDTB_REPO__|$TASK_DIR|g" \
    "$PLIST_TEMPLATE" > "$temp"; then
    rm -f "$temp"
    print -u2 -- "  plist       FAILED — could not render $PLIST_TEMPLATE"
    return 1
  fi
  # Reject any remaining template placeholders before plutil/mutation
  if grep -q '__[A-Z_]*__' "$temp" 2>/dev/null; then
    rm -f "$temp"
    print -u2 -- "  plist       FAILED — unresolved template placeholder in rendered plist"
    print -u2 -- "              update the sed substitution list to cover all placeholders"
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

# Snapshot the machine-local state the next restart would otherwise overwrite
# without a copy: config, producer rules, state/ (artifact, producer cache,
# app stores), and the Capacities caches. Locks belong to live writers, and
# env files and the capacities-mcp projection are deliberately excluded.
copy_state_into() {
  local dest="$1" item
  local -a caches

  for item in config.json producer-rules.json; do
    [[ -f "$STATE_HOME/$item" ]] || continue
    if ! cp -p "$STATE_HOME/$item" "$dest/$item"; then
      print -u2 -- "  snapshot    FAILED — could not copy $item"
      return 1
    fi
  done

  if [[ -d "$STATE_HOME/state" ]]; then
    # The seat is still running, so a run record can vanish between rsync's
    # listing and its copy. One retry settles that race; a real failure
    # (permissions, full disk) fails both attempts.
    if ! copy_state_dir "$dest" && ! copy_state_dir "$dest"; then
      print -u2 -- "  snapshot    FAILED — could not copy state/"
      return 1
    fi
  fi

  # Same vanishing-file race as state/: re-glob and retry once.
  caches=( $STATE_HOME/tdtb-capacities-*.json(N.) )
  if (( $#caches )) && ! cp -p "${caches[@]}" "$dest/" 2>/dev/null; then
    caches=( $STATE_HOME/tdtb-capacities-*.json(N.) )
    if (( $#caches )) && ! cp -p "${caches[@]}" "$dest/"; then
      print -u2 -- "  snapshot    FAILED — could not copy the Capacities caches"
      return 1
    fi
  fi

  return 0
}

copy_state_dir() {
  rsync -a \
    --exclude='*.lock' \
    --exclude='.env' \
    --exclude='*.env' \
    --exclude='capacities-mcp' \
    "$STATE_HOME/state/" "$1/state/"
}

# Keep the snapshot just taken plus the newest BACKUP_KEEP-1 others. Ranking
# is by NAME (the timestamp in it), never by mtime, and `keep` itself is never
# a deletion candidate, so clock skew or restored backup sets cannot delete
# the backup this run is about to advertise.
prune_snapshots() {
  local keep="$1" old
  local -a snaps

  snaps=( $BACKUP_ROOT/pre-restart-*(N/On) )
  snaps=( ${snaps:#$keep} )
  (( $#snaps > BACKUP_KEEP - 1 )) || return 0
  for old in "${snaps[@]:$(( BACKUP_KEEP - 1 ))}"; do
    rm -rf -- "$old" 2>/dev/null
  done
  return 0
}

# Prints the snapshot path on success; leaves nothing behind on failure so a
# half-copied directory can never be mistaken for a usable backup.
snapshot_state() {
  local stamp dest n=1

  stamp="$(date +%Y%m%d-%H%M%S)" || return 1
  dest="$BACKUP_ROOT/pre-restart-$stamp"
  while [[ -e "$dest" ]]; do
    n=$(( n + 1 ))
    # Zero-padded so a same-second suffix still sorts by name in order.
    dest="$BACKUP_ROOT/pre-restart-$stamp-${(l:2::0:)n}"
  done

  if ! mkdir -p "$dest"; then
    print -u2 -- "  snapshot    FAILED — could not create $dest"
    return 1
  fi
  if ! copy_state_into "$dest"; then
    rm -rf -- "$dest"
    return 1
  fi

  prune_snapshots "$dest"
  print -- "$dest"
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

# --- 2. snapshot state and record the live code, before anything stops ------
deploy_head="$(git -C "$TASK_DIR" rev-parse HEAD 2>/dev/null)"
last_good=""
[[ -f "$LAST_GOOD" ]] && last_good="$(head -1 "$LAST_GOOD" 2>/dev/null | tr -d '[:space:]')"
if [[ -n "$last_good" ]]; then
  last_good_subject="$(git -C "$TASK_DIR" log -1 --format='%s' "$last_good" 2>/dev/null)"
  print -- "  last good   ${last_good[1,12]} $last_good_subject"
else
  print -- "  last good   unknown (no healthy restart recorded yet)"
fi
print -- "  deploying   ${deploy_head[1,12]}"

snapshot_path="$(snapshot_state)" || snapshot_path=""
if [[ -z "$snapshot_path" ]]; then
  print -u2 -- "  restart     ABORTED — state snapshot failed, so nothing was stopped"
  print -u2 -- "              $LABEL is still running the code it was already running"
  exit 1
fi
print -- "  snapshot    $snapshot_path"

# --- 3. refresh the seat plist and restart the launchd job -------------------
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
  if ! bootstrap_job; then
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
  if ! bootstrap_job; then
    print -u2 -- "  restart     FAILED — could not reload $LABEL from $PLIST"
    exit 1
  fi
  print -- "  launchd     reloaded $LABEL from the current plist"
fi

# --- 4. wait for it to actually serve ----------------------------------------
health=""
for _ in {1..$HEALTH_TIMEOUT}; do
  health="$(curl -sf -m 2 "http://127.0.0.1:$PORT/health" 2>/dev/null)" && [[ -n "$health" ]] && break
  sleep 1
done

if [[ -z "$health" ]]; then
  print -u2 -- ""
  print -u2 -- "  health      NO RESPONSE after ${HEALTH_TIMEOUT}s — the seat did not come back"
  print -u2 -- "  snapshot    $snapshot_path"
  if [[ -n "$last_good" ]]; then
    print -u2 -- "  last good   ${last_good[1,12]} $last_good_subject (last restart that served)"
  else
    print -u2 -- "  last good   unknown — pick the commit to return to from: git -C '$TASK_DIR' log --oneline"
  fi
  print -u2 -- ""
  print -u2 -- "  last $LOG_LINES log lines ($LOG):"
  if [[ -f "$LOG" ]]; then
    tail -n "$LOG_LINES" "$LOG" | sed 's/^/    /' >&2
  else
    print -u2 -- "    (no log file at $LOG)"
  fi
  print -u2 -- ""
  print -u2 -- "  rollback — run these by hand; nothing was reverted automatically:"
  print -u2 -- "    1. unload the broken job:      launchctl bootout gui/$UID/$LABEL"
  print -u2 -- "    2. return to the last good:    git -C '$TASK_DIR' switch --detach ${last_good:-<commit>}"
  # Name only what the snapshot actually holds, so every printed command runs.
  # The restore overlays: files the broken run created are left in place.
  local_files=( $snapshot_path/*(N.) )
  print -u2 -- "    3. restore the snapshot (overlays; new files are kept):"
  [[ -d "$snapshot_path/state" ]] \
    && print -u2 -- "       rsync -a '$snapshot_path'/state/ '$STATE_HOME'/state/"
  (( $#local_files )) \
    && quoted=( "${(@qq)local_files}" ) \
    && print -u2 -- "       cp -p ${(j: :)quoted} '$STATE_HOME'/"
  print -u2 -- "    4. restart on that code:       zsh '$TASK_DIR'/restart-live.sh --no-build"
  print -u2 -- ""
  exit 1
fi

# --- 5. report what is actually live -----------------------------------------
# launchctl is authoritative: `lsof -ti :PORT` also matches CLIENTS with an open
# connection to the port, so it can report a browser tab instead of the server.
pid="$(launchctl print "gui/$UID/$LABEL" 2>/dev/null | awk -F'= ' '/^\tpid = /{print $2; exit}')"
[[ -n "$pid" ]] || pid="$(lsof -ti ":$PORT" -sTCP:LISTEN 2>/dev/null | head -1)"
started="$(ps -p "$pid" -o lstart= 2>/dev/null | sed 's/^ *//')"
# Last commit that touched the APP, not repo HEAD — HEAD is often an unrelated
# skill/config commit and answers the wrong question ("which app version is live?").
head_commit="$(git -C "$TASK_DIR" log -1 --format='%h %s' -- "$TASK_DIR/app" "$TASK_DIR/frontend" 2>/dev/null | cut -c1-72)"
served="$(grep -o 'assets/index-[A-Za-z0-9_-]*\.js' "$TASK_DIR/app/static/cockpit/index.html" 2>/dev/null | head -1)"
dirty="$(git -C "$TASK_DIR" status --porcelain -- "$TASK_DIR/app" "$TASK_DIR/frontend" 2>/dev/null | wc -l | tr -d ' ')"

print -- "  health      $health"
if [[ -n "$deploy_head" ]] && (( dirty == 0 )); then
  print -- "$deploy_head" > "$LAST_GOOD" 2>/dev/null \
    || print -u2 -- "  note        could not record $LAST_GOOD"
fi
print -- "  pid         $pid  (started $started)"
print -- "  restarted   $head_commit"
print -- "  bundle      $served"
(( dirty > 0 )) && print -- "  note        $dirty uncommitted file(s) in the app source — live now runs your working tree, not HEAD"
print -- ""
print -- "  open http://127.0.0.1:$PORT/static/cockpit/"
