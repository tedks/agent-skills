#!/usr/bin/env bash
# Publish selected files from a caller-owned tree without changing the shared checkout.
set -euo pipefail

repo=
source_root=
message=
allow_path=0
paths=()
while (($#)); do
    case $1 in
        --repo|--source-root|-m)
            (($# >= 2)) || { echo "missing value for $1" >&2; exit 2; }
            case $1 in
                --repo) repo=$2 ;;
                --source-root) source_root=$2 ;;
                -m) message=$2 ;;
            esac
            shift 2 ;;
        --allow-path) allow_path=1; shift ;;
        --) shift; paths+=("$@"); break ;;
        -*) echo "unknown option: $1" >&2; exit 2 ;;
        *) paths+=("$1"); shift ;;
    esac
done
[[ -n $repo && -n $message && ${#paths[@]} -gt 0 ]] || {
    echo 'usage: offduty-push.sh --repo REPO [--source-root DIR] [--allow-path] FILE... -m MESSAGE' >&2
    exit 2
}
[[ -n $source_root ]] || source_root=$repo
repo=$(realpath -- "$repo")
source_root=$(realpath -- "$source_root")
[[ -d $source_root ]] || { echo 'source root missing' >&2; exit 2; }
[[ $(git -C "$repo" rev-parse --is-inside-work-tree) == true ]] || {
    echo 'repo must be a working tree' >&2; exit 2;
}
for path in "${paths[@]}"; do
    [[ $path != /* && $path != *//* && $path != */ && $path != .* && $path != */.* && $path != *'/../'* && $path != *'/./'* ]] || {
        echo "unsafe path: $path" >&2; exit 2;
    }
    if (( ! allow_path )); then
        case $path in waiting/*|receipts/*|lane-notes/*|writing/*) ;; *) echo "path not allowed: $path" >&2; exit 2 ;; esac
    fi
    source_file=$source_root/$path
    [[ -f $source_file && ! -L $source_file ]] || { echo "source is not a regular file: $path" >&2; exit 2; }
    canonical=$(realpath -- "$source_file")
    [[ $canonical == "$source_root/"* ]] || { echo "source escapes root: $path" >&2; exit 2; }
done

scratch_base=${XDG_RUNTIME_DIR:-${TMPDIR:-/tmp}}
[[ -d $scratch_base && -w $scratch_base ]] || { echo 'scratch directory unavailable' >&2; exit 2; }
scratch=$(mktemp -d "$scratch_base/offduty-push.XXXXXXXX")
worktree=$scratch/worktree
cleanup() {
    if [[ -e $worktree/.git ]]; then
        git -C "$repo" worktree remove --force "$worktree" >&2 || true
    fi
    rmdir -- "$scratch" 2>/dev/null || true
}
trap cleanup EXIT

for attempt in 1 2 3; do
    git -C "$repo" fetch --quiet origin master
    git -C "$repo" worktree add --quiet --detach "$worktree" origin/master
    base=$(git -C "$worktree" rev-parse HEAD)
    for path in "${paths[@]}"; do
        # An existing remote tree must not redirect a copy through a symlink.
        part=$worktree
        IFS=/ read -r -a components <<< "$path"
        for component in "${components[@]}"; do
            part=$part/$component
            [[ ! -L $part ]] || { echo "destination symlink: $path" >&2; exit 2; }
        done
        mkdir -p -- "$(dirname -- "$worktree/$path")"
        cp -- "$source_root/$path" "$worktree/$path"
    done
    git -C "$worktree" add -- "${paths[@]}"
    if git -C "$worktree" diff --cached --quiet; then
        printf '%s\n' "$base"
        exit 0
    fi
    git -C "$worktree" commit --quiet -m "$message"
    commit=$(git -C "$worktree" rev-parse HEAD)
    if git -C "$worktree" push --quiet origin HEAD:master; then
        printf '%s\n' "$commit"
        exit 0
    fi
    git -C "$repo" fetch --quiet origin master
    current=$(git -C "$repo" rev-parse origin/master)
    [[ $current != "$base" ]] || { echo 'push failed without a remote advance' >&2; exit 1; }
    git -C "$repo" worktree remove --force "$worktree"
done
echo 'push failed after three remote advances' >&2
exit 1
