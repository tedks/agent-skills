#!/bin/bash
# claude-trust-dir.sh - Inspect and set Claude Code's workspace trust.
#
# Claude Code shows a workspace trust dialog the first time it starts in a
# directory whose trust it cannot resolve. There is no CLI flag and no
# settings.json key to pre-grant it (checked against `claude --help` and the
# settings surface as of 2.1.259 -- `trustedNetworkDirectories` is unrelated,
# it guards /net automounts and UNC paths). The only store is
#
#   projects."<absolute path>".hasTrustDialogAccepted
#
# in ~/.claude.json, which is Claude Code's own state file rather than a
# documented config surface. Treat the shape as version-dependent.
#
# HOW TRUST RESOLVES. `resolve()` below is a port of the walker extracted from
# claude.exe v2.1.261 (minified `AB`), not a rule inferred from behaviour --
# an earlier inferred rule ("nearest ancestor wins") was wrong in both
# directions and PR #111's council caught it. The real rule:
#
#   * only a TRUTHY hasTrustDialogAccepted grants. A `false` entry does NOT
#     veto: it is skipped and the walk continues upward.
#   * the walk is BOUNDED by the project container -- the directory holding the
#     git common dir, shared by every worktree of a repo. A truthy entry above
#     that boundary never applies, so ~/Projects=true does NOT blanket-trust a
#     repo beneath it. Outside a repo the walk runs to the filesystem root.
#
# This is an undocumented internal and WILL drift. `--verify` is the tripwire:
# it launches Claude in a throwaway tmux window, observes whether the dialog
# actually appears, and reports any disagreement with the prediction. Run it
# after a Claude upgrade. It never answers the dialog.
#
# Usage:
#   claude-trust-dir.sh --list                     show every stored entry
#   claude-trust-dir.sh --check <dir>...           predict trust, set nothing
#   claude-trust-dir.sh --verify <dir>...          check the prediction against reality
#   claude-trust-dir.sh [--dry-run] <dir>...       mark directories trusted
#
# Granting trust is an operator decision. Nothing here runs automatically:
# agent-spawn.sh calls it only behind an explicit --trust flag.
#
# CONCURRENCY: every running Claude Code instance writes this file, using its
# own lockfile protocol (<config>.lock) that flock does not participate in.
# So the risk runs in BOTH directions: a live session can flush its copy over
# our change, and we could clobber a setting it wrote after we read.
#
# Atomic rename prevents a torn file; it does not prevent a lost update. To
# stop the second direction being SILENT, the write re-stats the file
# immediately beforehand and refuses to replace it if it changed since we read
# it -- turning data loss into a loud retry. A microsecond-wide window remains
# between that check and the rename. Prefer running with few sessions live.

set -euo pipefail

# Claude resolves its config as $CLAUDE_CONFIG_DIR/.claude.json, falling back
# to $HOME. Honouring the same override matters: with an alternate profile in
# play, defaulting to $HOME writes a store the launched process never reads, so
# --trust would report success and the child would still prompt.
CONFIG="${CLAUDE_CONFIG_FILE:-${CLAUDE_CONFIG_DIR:-$HOME}/.claude.json}"
LOCKFILE="${CONFIG}.trust-lock"

usage() {
    sed -n '/^# Usage:/,/^#$/p' "${BASH_SOURCE[0]}" | sed 's/^# \?//' >&2
    exit 1
}

# Canonicalise to the physical path. A tmux window opened with `new-window -c`
# gives the agent that directory as its cwd, and a process's cwd is always
# physical, so symlinked paths must be resolved the same way here or the key
# we write will never be the key Claude Code looks up.
canonicalise() {
    local d="$1"
    if [[ ! -d "$d" ]]; then
        echo "Error: not a directory: $d" >&2
        return 1
    fi
    (CDPATH='' cd -P -- "$d" && pwd -P)
}

mode="set"
dry_run=0
dirs=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --list)    mode="list"; shift ;;
        --check)   mode="check"; shift ;;
        --verify)  mode="verify"; shift ;;
        --dry-run) dry_run=1; shift ;;
        -h|--help) usage ;;
        --) shift; while [[ $# -gt 0 ]]; do dirs+=("$1"); shift; done ;;
        -*) echo "Unknown option: $1" >&2; usage ;;
        *)  dirs+=("$1"); shift ;;
    esac
done

if [[ ! -f "$CONFIG" ]]; then
    echo "Error: $CONFIG does not exist." >&2
    echo "  Run Claude Code once so it creates its state file, then retry." >&2
    echo "  This script deliberately does not synthesise one: guessing the" >&2
    echo "  shape of another program's state file is how you corrupt it." >&2
    exit 1
fi

if [[ "$mode" == "list" ]]; then
    python3 - "$CONFIG" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1]))
projects = cfg.get("projects", {})
rows = [(p, v.get("hasTrustDialogAccepted"))
        for p, v in projects.items()
        if isinstance(v, dict) and "hasTrustDialogAccepted" in v]
for path, trusted in sorted(rows):
    print(f"{'trusted' if trusted else 'DECLINED':>8}  {path}")
print(f"\n{len(rows)} entries. Only truthy entries grant, and the search stops at the"
      "\ngit root -- use --check to resolve a specific directory.")
PY
    exit 0
fi

if [[ ${#dirs[@]} -eq 0 ]]; then
    usage
fi

canonical=()
for d in "${dirs[@]}"; do
    canonical+=("$(canonicalise "$d")")
done

# The walk's boundary. NOT `--show-toplevel`: in a bare-repo-plus-worktrees
# layout that returns the individual worktree, and --verify proved that wrong
# (it predicted PROMPTS for a directory Claude actually trusts). The boundary
# is the directory holding the git COMMON dir -- the project container, shared
# by every worktree -- which explains both observed cases. Empty means "not in
# a repo", i.e. an unbounded walk.
git_root_of() {
    local common
    common=$(cd "$1" 2>/dev/null && git rev-parse --git-common-dir 2>/dev/null) || return 0
    [[ -n "$common" ]] || return 0
    # --git-common-dir may come back relative to the directory it resolved in.
    common=$(cd "$1" && cd "$common" 2>/dev/null && pwd -P) || return 0
    dirname "$common"
}

predict() {
    local pairs=()
    local d
    for d in "$@"; do
        pairs+=("$d" "$(git_root_of "$d")")
    done
    python3 - "$CONFIG" "${pairs[@]}" <<'PY'
import json, sys, os

cfg = json.load(open(sys.argv[1]))
projects = cfg.get("projects", {})

def resolve(start, boundary):
    """Port of `AB` from claude.exe v2.1.261.

    Only truthy entries grant. `boundary` (the git root, or None) stops the
    walk. Returns (trusted, granting_path_or_None)."""
    cur = start
    while True:
        if boundary is not None and cur != boundary and            not cur.startswith(boundary.rstrip("/") + "/"):
            return False, None
        entry = projects.get(cur)
        if isinstance(entry, dict) and entry.get("hasTrustDialogAccepted"):
            return True, cur
        if cur == boundary:
            return False, None
        parent = os.path.dirname(cur)
        if parent == cur:
            return False, None
        cur = parent

args = sys.argv[2:]
for path, root in zip(args[0::2], args[1::2]):
    boundary = root or None
    trusted, granter = resolve(path, boundary)
    where = (f"project root {boundary}" if boundary
             else "/ (not in a git repo, so the walk is unbounded)")
    if trusted:
        via = "own entry" if granter == path else f"granted by {granter}"
        print(f"  trusted   {path}\n            ({via}; {where})")
    else:
        blocked = ""
        entry = projects.get(path)
        if isinstance(entry, dict) and entry.get("hasTrustDialogAccepted") is False:
            blocked = "; its own entry is false, which does NOT veto -- it is simply not a grant"
        print(f"  PROMPTS   {path}\n            (no truthy entry at or under {where}{blocked})")
print("\nPredicted from a port of Claude's internal resolver; run --verify to confirm.")
PY
}

if [[ "$mode" == "check" ]]; then
    predict "${canonical[@]}"
    exit 0
fi

if [[ "$mode" == "verify" ]]; then
    # The tripwire. Launch Claude in a throwaway tmux window, watch for the
    # dialog or the composer, kill it. No key is ever sent, so this cannot
    # answer the dialog or alter stored trust.
    source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/claude-pane-detect.sh"
    command -v tmux >/dev/null || { echo "Error: --verify needs tmux." >&2; exit 1; }
    command -v claude >/dev/null || { echo "Error: --verify needs claude on PATH." >&2; exit 1; }

    predict "${canonical[@]}"
    echo
    echo "Observed:"
    sock="/tmp/claude-trust-verify.$$"
    trap 'tmux -S "$sock" kill-server 2>/dev/null || true' EXIT
    mismatch=0
    for d in "${canonical[@]}"; do
        expected="PROMPTS"
        predict "$d" | grep -q '^  trusted' && expected="trusted"
        tmux -S "$sock" new-session -d -s v -n w -c "$d" claude 2>/dev/null || {
            echo "  ERROR     $d (could not start a tmux window)"; mismatch=1; continue; }
        observed="timeout"
        for _ in $(seq 1 120); do
            pane=$(tmux -S "$sock" capture-pane -p -t v:w 2>/dev/null) || { observed="exited"; break; }
            if claude_trust_modal_active "$pane"; then observed="PROMPTS"; break; fi
            if claude_composer_ready "$pane"; then observed="trusted"; break; fi
            sleep 0.25
        done
        tmux -S "$sock" kill-session -t v 2>/dev/null || true
        if [[ "$observed" == "$expected" ]]; then
            echo "  agrees    $d ($observed)"
        else
            echo "  MISMATCH  $d: predicted $expected, observed $observed"
            mismatch=1
        fi
    done
    if [[ "$mismatch" -ne 0 ]]; then
        echo
        echo "The prediction no longer matches this Claude build. resolve() is a port of" >&2
        echo "an internal that has evidently changed; re-extract it before trusting --check." >&2
        exit 2
    fi
    echo
    echo "Prediction matches observed behaviour on this build."
    exit 0
fi

# --- set ---------------------------------------------------------------
# flock serialises concurrent runs of this script; the temp-file rename makes
# the replacement atomic for everyone else.
exec 9>"$LOCKFILE"
if ! flock -w 10 9; then
    echo "Error: could not acquire $LOCKFILE within 10s." >&2
    exit 1
fi

live=$(pgrep -c -u "$(id -u)" -x claude 2>/dev/null || true)
if [[ "${live:-0}" -gt 0 && "$dry_run" -eq 0 ]]; then
    echo "Warning: $live Claude Code process(es) are running. Each holds" >&2
    echo "  $CONFIG in memory and may flush over this change on exit." >&2
    echo "  Re-check with --list afterwards." >&2
fi

DRY_RUN="$dry_run" python3 - "$CONFIG" "${canonical[@]}" <<'PY'
import json, os, sys, tempfile

config_path = sys.argv[1]
targets = sys.argv[2:]
dry_run = os.environ.get("DRY_RUN") == "1"

# Fingerprint the file we are about to base our edit on, so a concurrent
# writer between here and the rename is detected rather than silently undone.
def fingerprint(path):
    st = os.stat(path)
    return (st.st_mtime_ns, st.st_size)

before = fingerprint(config_path)

with open(config_path) as fh:
    cfg = json.load(fh)

projects = cfg.setdefault("projects", {})
changed = []

for path in targets:
    entry = projects.get(path)
    if not isinstance(entry, dict):
        # A fresh entry carrying only this key is the same shape Claude Code
        # writes for a directory it has only ever asked about; it fills in the
        # rest on first use.
        entry = {}
        projects[path] = entry
    previous = entry.get("hasTrustDialogAccepted")
    if previous is True:
        print(f"  already trusted  {path}")
        continue
    entry["hasTrustDialogAccepted"] = True
    changed.append(path)
    print(f"  {'would trust' if dry_run else 'trusted'}      {path}"
          + ("  (was an explicit decline)" if previous is False else ""))

if not changed:
    print("\nNothing to change.")
    sys.exit(0)

if dry_run:
    print(f"\n--dry-run: {len(changed)} entr{'y' if len(changed)==1 else 'ies'} left unwritten.")
    sys.exit(0)

# Same directory, so the rename is atomic on the same filesystem. Preserve
# the original mode rather than inheriting mkstemp's 0600 by accident.
directory = os.path.dirname(config_path) or "."
mode = os.stat(config_path).st_mode & 0o777
fd, tmp = tempfile.mkstemp(dir=directory, prefix=".claude.json.trust.")
try:
    with os.fdopen(fd, "w") as fh:
        json.dump(cfg, fh, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, mode)
    # Refuse rather than clobber. Everything a live Claude wrote since we read
    # is in the file and absent from our copy; replacing it would revert it
    # silently while we report success.
    if fingerprint(config_path) != before:
        os.unlink(tmp)
        sys.exit(
            "\nAborted: %s changed while this ran (a live Claude Code session "
            "wrote to it).\n  Nothing was written -- replacing it would have "
            "silently reverted that change.\n  Re-run; it is idempotent." % config_path
        )
    os.replace(tmp, config_path)
except BaseException:
    if os.path.exists(tmp):
        os.unlink(tmp)
    raise

print(f"\nWrote {len(changed)} entr{'y' if len(changed)==1 else 'ies'} to {config_path}.")
PY
