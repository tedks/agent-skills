#!/bin/bash
# agent-query.sh - Query an AI agent non-interactively (piped mode)
#
# Usage: agent-query.sh <agent> [options] <prompt>
#        agent-query.sh <agent> [options] --prompt-file <file>
#
# Agents: claude, codex, agy (Antigravity CLI)
#
# Options:
#   -d, --dir <dir>              Set working directory
#   -m, --model <model>          Specify model (agent-specific)
#   -f, --prompt-file <file>     Read prompt from file (avoids ARG_MAX)
#
# For claude and codex the prompt is piped via stdin, never passed as a CLI
# argument, which avoids ARG_MAX limits on execve(2).
#
# agy has no stdin mode. Rather than put the prompt in argv -- which would cap
# it at 128 KiB and expose it in `ps` -- the script stages it in a private temp
# directory and passes agy a path plus --add-dir. See the agy branch below.
#
# Examples:
#   agent-query.sh claude "Explain this error"
#   agent-query.sh codex -d ./project "Review the auth module"
#   agent-query.sh codex --model o3 "Optimize this function"
#   agent-query.sh agy "Summarize this codebase"
#   agent-query.sh claude --prompt-file /tmp/review-prompt.txt

set -e

usage() {
    echo "Usage: agent-query.sh <agent> [options] <prompt>" >&2
    echo "       agent-query.sh <agent> [options] --prompt-file <file>" >&2
    echo "Agents: claude, codex, agy" >&2
    echo "Options:" >&2
    echo "  -d, --dir <dir>              Set working directory" >&2
    echo "  -m, --model <model>          Specify model" >&2
    echo "  -f, --prompt-file <file>     Read prompt from file" >&2
    exit 1
}

if [[ $# -lt 2 ]]; then
    usage
fi

agent="$1"
shift

# Parse options
dir=""
model=""
prompt_file=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -d|--dir)
            dir="$2"
            shift 2
            ;;
        -m|--model)
            model="$2"
            shift 2
            ;;
        -f|--prompt-file)
            prompt_file="$2"
            shift 2
            ;;
        -*)
            echo "Unknown option: $1" >&2
            usage
            ;;
        *)
            break
            ;;
    esac
done

# Normalize: ensure we always have a prompt_file to pipe from.
# This avoids passing the prompt as a CLI argument to the downstream
# agent, which would hit ARG_MAX for large prompts.
_cleanup_prompt_file=""

# The agy path stages a copy of the prompt in a private directory (see the agy
# branch). Unlike the claude/codex path it does NOT exec, so this trap is what
# actually removes it -- on success, failure, and signals alike.
_cleanup_agy_dir=""

# Trap to clean up temp files on any exit (failure paths).
# On the claude/codex success path, we manually delete before exec, because
# exec replaces this process and the trap would never fire.
cleanup() {
    [[ -n "$_cleanup_prompt_file" ]] && rm -f "$_cleanup_prompt_file"
    [[ -n "$_cleanup_agy_dir" ]] && rm -rf -- "$_cleanup_agy_dir"
}
trap cleanup EXIT

if [[ -z "$prompt_file" ]]; then
    prompt="$*"
    if [[ -z "$prompt" ]]; then
        echo "Error: prompt required (positional arg or --prompt-file)" >&2
        usage
    fi
    prompt_file=$(mktemp /tmp/agent-query-prompt.XXXXXX)
    _cleanup_prompt_file="$prompt_file"
    printf '%s' "$prompt" > "$prompt_file"
else
    if [[ ! -f "$prompt_file" ]]; then
        echo "Error: prompt file not found: $prompt_file" >&2
        exit 1
    fi
    if [[ ! -s "$prompt_file" ]]; then
        echo "Error: prompt file is empty: $prompt_file" >&2
        exit 1
    fi
fi

# Canonicalize prompt_file to an absolute path so that a subsequent
# cd (via --dir) doesn't break a relative path.
prompt_file="$(cd -- "$(dirname -- "$prompt_file")" && pwd)/$(basename -- "$prompt_file")"

# Change directory if specified (must happen after canonicalization)
if [[ -n "$dir" ]]; then
    cd "$dir"
fi

# Build command array. claude and codex read the prompt from stdin, which keeps
# it out of argv and sidesteps the per-argument length cap. agy cannot -- see
# the agy branch -- so it is handled separately below.
prompt_via_file=""

case "$agent" in
    claude)
        cmd=(claude -p)
        [[ -n "$model" ]] && cmd+=(--model "$model")
        ;;
    codex)
        cmd=(codex exec -)
        [[ -n "$model" ]] && cmd+=(-m "$model")
        ;;
    agy)
        # Antigravity CLI has NO stdin mode. Verified empirically against
        # agy 1.1.3 on 2026-08-12:
        #
        #   agy -p -            -> takes the literal "-" as the prompt and
        #                          IGNORES stdin entirely, answering with a
        #                          generic greeting. Silently wrong: exit 0,
        #                          plausible-looking output, prompt discarded.
        #   agy -p              -> "flag needs an argument: -p"
        #   agy -p "<prompt>"   -> works.
        #
        # Do not "fix" the dash form back in; its failure mode is invisible --
        # every review would come back as cheerful nonsense and nothing would
        # error.
        #
        # Rather than put the prompt itself in argv, hand agy a PATH and let it
        # read the file with its own tools. agy is an agent, so this works, and
        # it avoids both problems an argv prompt would create:
        #
        #   * SIZE. A single argv entry is capped at MAX_ARG_STRLEN (128 KiB),
        #     which review prompts carrying a diff can exceed. Via file there is
        #     no cap: verified reading a 251031-byte prompt in full, returning a
        #     marker planted on its last line.
        #   * PRIVACY. An argv prompt is world-readable in `ps` and
        #     /proc/<pid>/cmdline for as long as agy runs, and review prompts
        #     routinely contain private source. Only the path is exposed here.
        #
        # --add-dir is REQUIRED. Without it the read is auto-denied: headless
        # mode cannot prompt for the read_file permission, and agy exits having
        # produced no output with an explicit message saying so. Loud, not
        # silent -- verified.
        #
        # The grant is scoped to a private 0700 directory holding only this
        # prompt, so we never hand agy the caller's directory (which for a
        # --prompt-file inside a repo would be the whole repo).
        #
        # NOTE: -p consumes the very next argv entry as its value, so the
        # instruction must be appended LAST, after any flags. Putting --model
        # after -p would make the string "--model" the prompt.
        cmd=(agy)
        prompt_via_file=1
        [[ -n "$model" ]] && cmd+=(--model "$model")
        ;;
    *)
        echo "Unknown agent: $agent" >&2
        echo "Supported agents: claude, codex, agy" >&2
        exit 1
        ;;
esac

if [[ -n "$prompt_via_file" ]]; then
    # agy only: stage the prompt in a private directory and point agy at it.
    #
    # Copy rather than reuse $prompt_file in place. --add-dir grants agy read
    # access to a whole directory, and a caller-supplied --prompt-file may sit
    # anywhere -- inside a repo, in a shared /tmp. Copying into a 0700 dir that
    # contains nothing else keeps the grant to exactly this one prompt.
    # Fixed /tmp base, matching the inline-prompt path above, rather than
    # ${TMPDIR:-/tmp}. TMPDIR would be trusted twice over: as a filesystem
    # location, and as text interpolated into the natural-language instruction
    # below, where newlines or control characters in it would become extra
    # prompt content. A relative TMPDIR would also resolve after the `cd "$dir"`
    # above and stage the prompt somewhere unintended.
    #
    # mktemp -d already creates the directory 0700, so no chmod is needed.
    agy_dir="$(mktemp -d /tmp/ask-agent-agy.XXXXXX)"
    _cleanup_agy_dir="$agy_dir"
    agy_prompt="$agy_dir/prompt.txt"

    # 0600 before content lands: on a shared /tmp the window between create and
    # chmod is a real read window, and the prompt may be private source.
    ( umask 077; cat -- "$prompt_file" > "$agy_prompt" )

    cmd+=(--add-dir "$agy_dir" -p "Read the file $agy_prompt in full and follow the instructions in it. Treat its entire contents as your prompt. Do not ask for confirmation before reading it.")

    # Deliberately NOT exec: agy must be able to read the staging file for its
    # whole run, so this script has to outlive it in order to clean up.
    #
    # That costs exec's signal transparency, which has to be paid back by hand.
    # Without the forwarding below, a `timeout` or supervisor signalling only
    # this PID would kill the wrapper, fire the EXIT trap, delete the staging
    # directory out from under a still-running agy, and leave it orphaned on
    # the caller's stdout. Callers here DO run this under `timeout`, so that is
    # the expected path, not a corner case.
    "${cmd[@]}" &
    agy_pid=$!

    # Forward the signal we actually received rather than translating
    # everything to TERM, and cover the whole set that can reach us. HUP is not
    # optional: these run from tmux, and a TTY hangup delivers HUP, not TERM --
    # forwarding only TERM/INT leaves exactly the orphan-with-deleted-prompt
    # bug this construction exists to prevent.
    # The INT and QUIT traps are best-effort by nature, and their absence is
    # not a gap worth chasing: when this script is itself started in the
    # background by a non-interactive shell, POSIX has already set INT and QUIT
    # to SIG_IGN, and bash cannot trap a signal that was ignored on entry -- so
    # those two traps are simply inert there, and the signal never arrives to
    # be forwarded either. In a real terminal the opposite holds: Ctrl-C goes
    # to the whole foreground process group, so agy receives INT directly and
    # the forwarding is redundant. HUP and TERM are the ones that genuinely
    # need this, and they are the ones exercised by `timeout` and by tmux.
    _forward_to_agy() { kill -"$1" "$agy_pid" 2>/dev/null || true; }
    for _sig in HUP INT TERM QUIT; do
        # shellcheck disable=SC2064  # expand _sig now, at trap definition
        trap "_forward_to_agy $_sig" "$_sig"
    done
    unset _sig

    status=0
    wait "$agy_pid" || status=$?

    # A trapped signal makes `wait` return >128 immediately while the child may
    # still be shutting down. Keep waiting until it is genuinely reaped, so the
    # EXIT trap never removes the staging directory while agy is alive. If the
    # child is already gone, kill -0 fails and a >128 status is its real
    # died-by-signal status.
    while (( status > 128 )) && kill -0 "$agy_pid" 2>/dev/null; do
        status=0
        wait "$agy_pid" || status=$?
    done

    exit "$status"
fi

# Pipe prompt via stdin — this is the key ARG_MAX fix.
# We open the file descriptor, delete the temp file (if we created it),
# then exec. The fd survives exec; the unlinked file stays readable
# through the open fd until the process exits.
exec 3< "$prompt_file"
[[ -n "$_cleanup_prompt_file" ]] && rm -f "$_cleanup_prompt_file"
exec "${cmd[@]}" <&3
