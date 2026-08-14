# TDTB server — launchd auto-start

Repo copy is the seat-neutral template; `~/Library/LaunchAgents/` copy is what
runs. Both `adam` and `walle-mini` are supported. The staged plist resolves the
seat's local `WALL⋅E-THNK` vault path at bootstrap time.

The server is a single-writer process. Both seats may have launch access, but
only one seat may run `com.walle.tdtb` at a time against the synced vault.
Never bootstrap a second live instance while the other seat owns `:8746`.

## Install (once)

```zsh
cd ~/Development/Claudius
zsh Configurations/gpt-stack/seat-bootstrap.sh --launchd
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.walle.tdtb.plist
```

The bootstrap command stages the seat-rewritten plist but does not load it.
`launchctl bootstrap` starts it immediately (`RunAtLoad`) and at every login
thereafter. Run the install block on each seat once; load only the seat you are
actively using.

## Restart with fresh code — use this

```zsh
tdtb-restart
```

Refreshes the seat-local plist from the current repo template, validates it,
rebuilds both committed bundles, and reloads the launchd job from that current
plist. It reloads even when the projection is byte-identical: a previously
staged plist can be newer than the already-loaded launchd environment. It then
waits for `/health` and prints the effective judgment model along with the live
PID, last app commit, bundle hash, and cockpit URL. `--no-build` skips only the
frontend rebuild.

`tdtb-restart` is `~/.local/bin/tdtb-restart`, symlinked to `../restart-live.sh` by
`Configurations/gpt-stack/seat-bootstrap.sh`. Before restarting, it renders the
current repo launchd template into the seat-local plist, validates it, and always
reloads the job so environment changes (including the judgment model) are actually
picked up even after an earlier staging-only operation. It is **portable across both seats** — the script resolves
its own directory and uses `$UID`/`$HOME`, so a new seat needs
`git pull` plus one `seat-bootstrap.sh` run, with no per-seat shell edits. Do not
reintroduce a `tdtb-restart` shell function: a function shadows the PATH command and
goes stale independently on each machine.

### Why not just kickstart

```zsh
launchctl kickstart -k gui/$UID/com.walle.tdtb   # backend only
```

The cockpit UI is a **committed Vite build served off disk**, and launchd keeps
its environment from the loaded plist, so kickstart alone can leave both an old
UI and an old judgment-model route in place. On 2026-07-26 the UI gap ran for
hours: the served bundle was pre-T1 while T1–T6 frontend source sat in git, and
the only symptom was missing buttons. `restart-live.sh` closes both gaps by
rebuilding first and bootstrapping the current plist; `Scripts/precommit-bundle-check.py`
stops the stale bundle from being committed in the first place.

Either path is an attended operator action only. A Codex/Claude session must
never restart or kill the live `:8746` process.

## Everything else

```zsh
launchctl print gui/$UID/com.walle.tdtb | head    # status
tail -f ~/Library/Logs/tdtb-server.log            # logs (incl. X-TDTB-Token line)
launchctl bootout gui/$UID/com.walle.tdtb         # stop + disable (uninstall)
```

## Notes

- The session token prints to the log on each start; the UI fetches it via
  `/session-token` itself, so you normally never need it.
- Plist edits: `tdtb-restart` rerenders and reloads the seat-local plist. Use
  `seat-bootstrap.sh --launchd` for initial staging or if the restart wrapper is
  not installed; activation still obeys the one-writer rule.
- One-writer rule unchanged: this makes *starting* automatic; Claude sessions
  still never kill/restart the port.
