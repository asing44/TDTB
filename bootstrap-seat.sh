#!/bin/zsh
# TDTB bootstrap-seat.sh — portable machine-local seat bootstrap for
# /Users/adam and /Users/walle-mini.
#
# Usage:
#   zsh bootstrap-seat.sh                         # validate, install deps, symlink
#   zsh bootstrap-seat.sh --launchd               # + stage ~/Library/LaunchAgents plist
#   zsh bootstrap-seat.sh --dry-run               # read-only report; no changes
#   zsh bootstrap-seat.sh --help                  # print usage
#
# Dependency freshness is tracked via SHA-256 marker files:
#   app/.venv/.tdtb-req-hash       — hash of app/requirements.txt
#   frontend/.tdtb-lock-hash       — hash of frontend/package-lock.json
# If the source file hash matches the saved marker, the install step is skipped.
# If the marker is missing or mismatched, the step is re-run.
# Markers are written only after a successful install.
#
# This script is the authoritative machine-local bootstrap. It never
# activates launchd, never restarts :8746, and never touches the real vault.

set -u
setopt pipefail
setopt errreturn

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
readonly SCRIPT_DIR="${0:A:h}"
readonly LABEL="com.walle.tdtb"
readonly RESTART_LINK="$HOME/.local/bin/tdtb-restart"
readonly VENV_MARKER=".tdtb-req-hash"
readonly FRONTEND_MARKER=".tdtb-lock-hash"

# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------
usage() {
  cat <<EOF
Usage: $(basename "$0") [--launchd] [--dry-run] [--help]

Portable TDTB seat bootstrap for /Users/adam and /Users/walle-mini.

Modes:
  (default)   Validate app/frontend/launchd paths; create app/.venv via uv
              (Python 3.12); install frontend deps via npm ci; symlink
              ~/.local/bin/tdtb-restart → restart-live.sh.
              Dependency freshness is tracked by SHA-256 marker files; stale
              or missing markers trigger reinstallation.
  --launchd   Also stage \$HOME/Library/LaunchAgents/\$LABEL.plist from the
              canonical launchd template. Does NOT activate launchd.
  --dry-run   Read-only. Report what would change; make no modifications.
  --help      Print this message and exit.

Environment:
  TDTB_REPO       Override the repo root (default: parent of this script).
  TDTB_VAULT_ROOT  Override the vault root used for --launchd plist rendering.
EOF
}

# ---------------------------------------------------------------------------
# Option parsing
# ---------------------------------------------------------------------------
launchd_mode=0
dry_run=0

for opt in "$@"; do
  case "$opt" in
    --launchd) launchd_mode=1 ;;
    --dry-run) dry_run=1 ;;
    --help) usage; exit 0 ;;
    *)
      print -u2 -- "ERROR: unknown option: $opt"
      usage >&2
      exit 1
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Repo root resolution
# ---------------------------------------------------------------------------
REPO_ROOT="${TDTB_REPO:-"$SCRIPT_DIR"}"
REPO_ROOT="${REPO_ROOT:A}"  # absolute, symlink-resolved

# ---------------------------------------------------------------------------
# Utility: print a step header
# ---------------------------------------------------------------------------
step()  { print -- "  $1"; }
ok()    { print -- "    $1"; }
warn()  { print -u2 -- "    WARN  $1"; }
fail()  { print -u2 -- "    FAIL  $1"; }
dry()   { print -- "    (dry-run) $1"; }

# ---------------------------------------------------------------------------
# Derived paths (always relative to REPO_ROOT)
# ---------------------------------------------------------------------------
APP_DIR="$REPO_ROOT/app"
FRONTEND_DIR="$REPO_ROOT/frontend"
VENV_DIR="$APP_DIR/.venv"
VENV_MARKER_FILE="$VENV_DIR/$VENV_MARKER"
FRONTEND_MARKER_FILE="$FRONTEND_DIR/$FRONTEND_MARKER"
RESTART_SCRIPT="$REPO_ROOT/restart-live.sh"
PLIST_TEMPLATE="$REPO_ROOT/launchd/$LABEL.plist"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_DEST="$LAUNCH_AGENTS_DIR/$LABEL.plist"

# ---------------------------------------------------------------------------
# Hash computation — SHA-256 of a file
# ---------------------------------------------------------------------------
_hash_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

# ---------------------------------------------------------------------------
# Tool checks — fail closed on missing prerequisites
# ---------------------------------------------------------------------------
tool_check() {
  local missing=0
  for tool in uv npm node; do
    if ! command -v "$tool" >/dev/null 2>&1; then
      fail "$tool not found in PATH"
      missing=1
    fi
  done
  if (( missing )); then
    print -u2 -- "ERROR: install missing tools and re-run"
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------
validate_paths() {
  local missing=0
  for p in "$APP_DIR" "$APP_DIR/requirements.txt" "$FRONTEND_DIR" \
           "$FRONTEND_DIR/package.json" "$FRONTEND_DIR/package-lock.json" \
           "$RESTART_SCRIPT"; do
    if [[ ! -e "$p" ]]; then
      fail "required path missing: $p"
      missing=1
    fi
  done

  if (( launchd_mode )) && [[ ! -f "$PLIST_TEMPLATE" ]]; then
    fail "launchd template missing: $PLIST_TEMPLATE"
    missing=1
  fi

  if (( missing )); then
    print -u2 -- "ERROR: repo structure incomplete at $REPO_ROOT"
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# Resolve vault root (for --launchd)
# ---------------------------------------------------------------------------
resolve_vault() {
  if [[ -n "${TDTB_VAULT_ROOT:-}" ]]; then
    if [[ -d "$TDTB_VAULT_ROOT" ]]; then
      print -- "$TDTB_VAULT_ROOT"
      return 0
    fi
    fail "TDTB_VAULT_ROOT set but not a directory: $TDTB_VAULT_ROOT"
    return 1
  fi

  local candidate
  for candidate in \
    "$HOME/Local Documents/Obsidian/WALL·E-THNK" \
    "$HOME/Obsidian/WALL·E-THNK"; do
    if [[ -d "$candidate" ]]; then
      print -- "$candidate"
      return 0
    fi
  done

  fail "no WALL·E-THNK vault found — set TDTB_VAULT_ROOT or sync vault first"
  return 1
}

# ---------------------------------------------------------------------------
# Step 1: Python venv with uv — content-aware via .tdtb-req-hash marker
# ---------------------------------------------------------------------------
ensure_venv() {
  step "venv ($VENV_DIR)"

  local current_hash=""
  [[ -f "$APP_DIR/requirements.txt" ]] && current_hash="$(_hash_file "$APP_DIR/requirements.txt")"

  # Check Python version when bin/python exists — recreate on mismatch
  if [[ -x "$VENV_DIR/bin/python" ]]; then
    local py_version
    py_version="$("$VENV_DIR/bin/python" --version 2>&1)"
    if [[ "$py_version" != *" 3.12"* ]]; then
      if (( dry_run )); then
        dry "rm -rf $VENV_DIR (wrong Python: ${py_version:-unknown})"
        dry "uv venv --python 3.12 $VENV_DIR"
        dry "uv pip install -r $APP_DIR/requirements.txt (in venv)"
        dry "write marker $VENV_MARKER_FILE"
        return 0
      fi
      warn "Python version mismatch (${py_version:-unknown}) — recreating venv"
      rm -rf "$VENV_DIR"
      # Fall through to recreate below
    elif [[ -f "$VENV_MARKER_FILE" ]]; then
      # Fast path: venv exists with correct version and matching marker
      local saved_hash
      saved_hash="$(<"$VENV_MARKER_FILE")"
      if [[ "$saved_hash" == "$current_hash" ]]; then
        ok "already up to date"
        return 0
      fi
      warn "requirements changed (hash mismatch) — reinstalling"
    else
      warn "missing requirements marker — reinstalling"
    fi
  elif [[ -d "$VENV_DIR" ]]; then
    warn "incomplete venv detected — repairing"
  fi

  # If bin/python is missing (no venv or incomplete venv), create it
  if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    if (( dry_run )); then
      dry "uv venv --python 3.12 $VENV_DIR"
    else
      if ! uv venv --python 3.12 "$VENV_DIR" >/dev/null 2>&1; then
        fail "uv venv failed — check Python 3.12 availability"
        return 1
      fi
      ok "uv venv created"
    fi
  fi

  if (( dry_run )); then
    dry "uv pip install -r $APP_DIR/requirements.txt (in venv)"
    dry "write marker $VENV_MARKER_FILE"
    return 0
  fi

  if ! VIRTUAL_ENV="$VENV_DIR" uv pip install -r "$APP_DIR/requirements.txt" >/dev/null 2>&1; then
    fail "uv pip install failed — check requirements.txt and network"
    return 1
  fi

  # Write marker only after successful install
  print -- "$current_hash" > "$VENV_MARKER_FILE"
  ok "dependencies installed"
}

# -- -------------------------------------------------------------------------
# Step 2: Frontend deps with npm ci — content-aware via .tdtb-lock-hash marker
# - -------------------------------------------------------------------------
ensure_frontend() {
  step "frontend ($FRONTEND_DIR)"

  local current_hash=""
  [[ -f "$FRONTEND_DIR/package-lock.json" ]] && current_hash="$(_hash_file "$FRONTEND_DIR/package-lock.json")"

  # Fast path: node_modules exists and saved lock hash matches
  if [[ -d "$FRONTEND_DIR/node_modules" && -f "$FRONTEND_MARKER_FILE" ]]; then
    local saved_hash
    saved_hash="$(<"$FRONTEND_MARKER_FILE")"
    if [[ "$saved_hash" == "$current_hash" ]]; then
      ok "already up to date"
      return 0
    fi
    warn "lock file changed (hash mismatch) — reinstalling"
  elif [[ -d "$FRONTEND_DIR/node_modules" ]]; then
    warn "missing lock marker — reinstalling"
  fi

  if (( dry_run )); then
    dry "npm ci --prefix $FRONTEND_DIR"
    dry "write marker $FRONTEND_MARKER_FILE"
    return 0
  fi

  if ! npm ci --prefix "$FRONTEND_DIR" >/dev/null 2>&1; then
    fail "npm ci failed — check package-lock.json and network"
    return 1
  fi

  # Write marker only after successful install
  print -- "$current_hash" > "$FRONTEND_MARKER_FILE"
  ok "dependencies installed"
}

# ---------------------------------------------------------------------------
# Step 3: restart symlink
# ---------------------------------------------------------------------------
ensure_restart_link() {
  step "restart symlink ($RESTART_LINK)"

  local link_parent="${RESTART_LINK:h}"
  local resolved_restart="${RESTART_SCRIPT:A}"

  if [[ -L "$RESTART_LINK" ]]; then
    local current resolved_current
    current="$(readlink "$RESTART_LINK")"
    if [[ "$current" == /* ]]; then
      resolved_current="$current"
    else
      resolved_current="${link_parent:A}/$current"
    fi
    resolved_current="${resolved_current:A}"
    if [[ "$resolved_current" == "$resolved_restart" ]]; then
      ok "symlink already points to $resolved_restart"
      return 0
    fi
  elif [[ -e "$RESTART_LINK" ]]; then
    fail "non-symlink exists at $RESTART_LINK — remove it manually"
    return 1
  fi

  if (( dry_run )); then
    dry "mkdir -p $link_parent"
    dry "ln -sf $resolved_restart $RESTART_LINK"
    return 0
  fi

  mkdir -p "$link_parent" || {
    fail "could not create $link_parent"
    return 1
  }
  ln -sf "$resolved_restart" "$RESTART_LINK" || {
    fail "could not create symlink $RESTART_LINK → $resolved_restart"
    return 1
  }
  ok "symlinked $RESTART_LINK → $resolved_restart"
}

# ---------------------------------------------------------------------------
# Step 4 (--launchd): stage plist with __WALLE_HOME__, __TDTB_REPO__,
# and __TDTB_VAULT_ROOT__ substitution
# ---------------------------------------------------------------------------
stage_plist() {
  step "launchd plist ($PLIST_DEST)"

  local vault
  vault="$(resolve_vault)" || return 1

  if (( dry_run )); then
    dry "mkdir -p $LAUNCH_AGENTS_DIR"
    dry "sed -e s|__WALLE_HOME__|\$HOME|g -e s|__TDTB_REPO__|$REPO_ROOT|g -e s|__TDTB_VAULT_ROOT__|$vault|g $PLIST_TEMPLATE > $PLIST_DEST"
    dry "plutil -lint $PLIST_DEST"
    return 0
  fi

  # Render to temp file first so we can validate atomically
  mkdir -p "$LAUNCH_AGENTS_DIR" || {
    fail "could not create $LAUNCH_AGENTS_DIR"
    return 1
  }
  local temp
  temp="$(mktemp "${PLIST_DEST}.tmp.XXXXXX")" || {
    fail "could not create temporary plist"
    return 1
  }

  if ! sed \
    -e "s|__WALLE_HOME__|$HOME|g" \
    -e "s|__TDTB_REPO__|$REPO_ROOT|g" \
    -e "s|__TDTB_VAULT_ROOT__|$vault|g" \
    "$PLIST_TEMPLATE" > "$temp"; then
    rm -f "$temp"
    fail "could not render plist from template"
    return 1
  fi

  if ! plutil -lint "$temp" >/dev/null 2>&1; then
    rm -f "$temp"
    fail "rendered plist is invalid (plutil -lint failed)"
    return 1
  fi

  # If existing plist is identical, skip
  if [[ -f "$PLIST_DEST" ]] && cmp -s "$temp" "$PLIST_DEST"; then
    rm -f "$temp"
    ok "plist already current"
    return 0
  fi

  # Set permissions on the temp file BEFORE replacing the existing plist,
  # so that a chmod failure preserves the old plist.
  if ! chmod 644 "$temp"; then
    rm -f "$temp"
    fail "could not set permissions on rendered plist — existing plist preserved"
    return 1
  fi

  # Preserve existing plist if we can't install the new one
  if mv -f "$temp" "$PLIST_DEST"; then
    ok "plist staged at $PLIST_DEST"
    warn "not activated — run 'launchctl bootstrap gui/\$UID $PLIST_DEST' to activate"
  else
    rm -f "$temp"
    fail "could not install plist to $PLIST_DEST — existing plist preserved"
    return 1
  fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
print -- "TDTB seat bootstrap — $LABEL"
print -- "  repo root: $REPO_ROOT"

# 1. Pre-requisites
tool_check
validate_paths

print -- ""

# 2. Dependency setup
overall=0
ensure_venv         || overall=1
ensure_frontend     || overall=1
ensure_restart_link || overall=1

if (( launchd_mode )); then
  stage_plist || overall=1
fi

print -- ""
if (( overall )); then
  print -u2 -- "BOOTSTRAP FAILED — review errors above"
  exit 1
fi

if (( dry_run )); then
  print -- "DRY-RUN complete — no changes made"
else
  print -- "BOOTSTRAP COMPLETE — seat is ready"
fi
exit 0