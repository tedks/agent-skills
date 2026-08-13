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
# The prompt is always piped via stdin to the downstream agent, never
# passed as a CLI argument. This avoids ARG_MAX limits on execve(2).
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

# Trap to clean up temp files on any exit (failure paths).
# On the success path, we manually delete before exec.
cleanup() {
    [[ -n "$_cleanup_prompt_file" ]] && rm -f "$_cleanup_prompt_file"
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
prompt_in_argv=""

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
        # So the prompt must travel in argv. Do not "fix" this back to a dash;
        # the failure mode is invisible -- every review would come back as
        # cheerful nonsense and nothing would error.
        #
        # KNOWN TRADEOFF, accepted because agy leaves no alternative: an argv
        # prompt is world-readable in `ps` / /proc/<pid>/cmdline for as long as
        # agy runs. The stdin path used by claude and codex does not expose it.
        # Review prompts routinely contain private source, so on a multi-user
        # box any local account can read them mid-run. Prefer claude or codex
        # for sensitive material until agy grows a stdin or --prompt-file mode.
        # NOTE: -p consumes the very next argv entry as its value, so the
        # prompt must be appended LAST, after any flags. Putting --model after
        # -p would make the string "--model" the prompt.
        cmd=(agy)
        prompt_in_argv=1
        [[ -n "$model" ]] && cmd+=(--model "$model")
        ;;
    *)
        echo "Unknown agent: $agent" >&2
        echo "Supported agents: claude, codex, agy" >&2
        exit 1
        ;;
esac

if [[ -n "$prompt_in_argv" ]]; then
    # agy only: the prompt goes in argv, so the stdin trick below does not
    # apply and the per-argument size cap does.
    #
    # Linux limits a SINGLE argv entry to MAX_ARG_STRLEN (128 KiB), separately
    # from the total ARG_MAX. A council-review prompt carrying a large diff can
    # exceed that, and the kernel's failure is E2BIG at exec -- so refuse
    # loudly here with an actionable message rather than letting it surface as
    # an opaque "Argument list too long".
    prompt_text="$(cat -- "$prompt_file")"

    # Measure BYTES, from the file. Do not use ${#prompt_text}: under a UTF-8
    # locale that counts characters, not bytes, so a prompt full of multi-byte
    # text -- em-dashes and box-drawing characters are common in diffs and in
    # our own comments -- undercounts by up to 4x and sails past this guard
    # into the very E2BIG it exists to prevent.
    prompt_bytes=$(wc -c < "$prompt_file")
    max_bytes=120000   # headroom under the 131072-byte MAX_ARG_STRLEN

    if (( prompt_bytes > max_bytes )); then
        echo "agent-query.sh: prompt is ${prompt_bytes} bytes, over the ${max_bytes}-byte limit for agy." >&2
        echo "  agy has no stdin mode, so the prompt must fit in a single argv entry (kernel cap: 131072)." >&2
        echo "  Use claude or codex for this prompt, or narrow the diff being reviewed." >&2
        exit 1
    fi

    cmd+=(-p "$prompt_text")
    [[ -n "$_cleanup_prompt_file" ]] && rm -f "$_cleanup_prompt_file"
    exec "${cmd[@]}"
fi

# Pipe prompt via stdin — this is the key ARG_MAX fix.
# We open the file descriptor, delete the temp file (if we created it),
# then exec. The fd survives exec; the unlinked file stays readable
# through the open fd until the process exits.
exec 3< "$prompt_file"
[[ -n "$_cleanup_prompt_file" ]] && rm -f "$_cleanup_prompt_file"
exec "${cmd[@]}" <&3
