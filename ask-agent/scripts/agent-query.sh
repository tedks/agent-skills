#!/bin/bash
# agent-query.sh - Query an AI agent non-interactively (piped mode)
#
# Usage: agent-query.sh <agent> [options] <prompt>
#        agent-query.sh <agent> [options] --prompt-file <file>
#
# Agents: claude, codex, gemini
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
#   agent-query.sh gemini "Summarize this codebase"
#   agent-query.sh claude --prompt-file /tmp/review-prompt.txt

set -e

usage() {
    echo "Usage: agent-query.sh <agent> [options] <prompt>" >&2
    echo "       agent-query.sh <agent> [options] --prompt-file <file>" >&2
    echo "Agents: claude, codex, gemini" >&2
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

# Build command array — the prompt is NOT included in argv.
# It will be piped via stdin to avoid ARG_MAX limits.
# All three agents (claude, codex, gemini) read from stdin when
# no positional prompt argument is given — verified empirically.
case "$agent" in
    claude)
        cmd=(claude -p)
        [[ -n "$model" ]] && cmd+=(--model "$model")
        ;;
    codex)
        cmd=(codex exec -)
        [[ -n "$model" ]] && cmd+=(-m "$model")
        ;;
    gemini)
        # gemini requires -p for headless mode; with -p "" it reads
        # the actual prompt from stdin ("Appended to input on stdin").
        cmd=(gemini -p "" -o text)
        [[ -n "$model" ]] && cmd+=(-m "$model")
        ;;
    *)
        echo "Unknown agent: $agent" >&2
        echo "Supported agents: claude, codex, gemini" >&2
        exit 1
        ;;
esac

# Pipe prompt via stdin — this is the key ARG_MAX fix.
# We open the file descriptor, delete the temp file (if we created it),
# then exec. The fd survives exec; the unlinked file stays readable
# through the open fd until the process exits.
exec 3< "$prompt_file"
[[ -n "$_cleanup_prompt_file" ]] && rm -f "$_cleanup_prompt_file"
exec "${cmd[@]}" <&3
