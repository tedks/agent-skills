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
# TRUST IS INHERITED, NEAREST ANCESTOR WINS. A directory with no entry of its
# own takes the value of the closest ancestor that has one. So
# ~/Projects=true blanket-trusts everything beneath it -- but a nearer
# ancestor set to false overrides that for its own subtree, which is the
# failure this script exists to inspect and repair.
#
# Usage:
#   claude-trust-dir.sh --list                     show every stored entry
#   claude-trust-dir.sh --check <dir>...           report effective trust, set nothing
#   claude-trust-dir.sh [--dry-run] <dir>...       mark directories trusted
#
# Granting trust is an operator decision. Nothing here runs automatically:
# agent-spawn.sh calls it only behind an explicit --trust flag.
#
# CONCURRENCY: every running Claude Code instance writes ~/.claude.json. This
# script serialises its own writers with flock and replaces the file by
# atomic rename, so it can never leave a truncated or half-written file. It
# cannot stop a live session from later flushing its own in-memory copy over
# the result. Prefer running it when few sessions are live, and confirm with
# --list afterwards.

set -euo pipefail

CONFIG="${CLAUDE_CONFIG_FILE:-$HOME/.claude.json}"
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
print(f"\n{len(rows)} entries; descendants inherit from the nearest listed ancestor.")
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

if [[ "$mode" == "check" ]]; then
    python3 - "$CONFIG" "${canonical[@]}" <<'PY'
import json, sys, os
cfg = json.load(open(sys.argv[1]))
projects = cfg.get("projects", {})

def effective(path):
    """Nearest ancestor with an explicit entry decides; None if nothing does."""
    cur = path
    while True:
        entry = projects.get(cur)
        if isinstance(entry, dict) and "hasTrustDialogAccepted" in entry:
            return entry["hasTrustDialogAccepted"], cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None, None
        cur = parent

for path in sys.argv[2:]:
    trusted, source = effective(path)
    if trusted is None:
        print(f"  PROMPTS   {path}\n            (no entry on any ancestor)")
    elif trusted:
        via = "own entry" if source == path else f"inherited from {source}"
        print(f"  trusted   {path}\n            ({via})")
    else:
        via = "own entry" if source == path else f"inherited from {source}"
        print(f"  DECLINED  {path}\n            ({via}) -- will prompt, and a blind Enter re-declines")
PY
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
    os.replace(tmp, config_path)
except BaseException:
    os.unlink(tmp)
    raise

print(f"\nWrote {len(changed)} entr{'y' if len(changed)==1 else 'ies'} to {config_path}.")
PY
