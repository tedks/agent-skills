#!/bin/bash
# agent-query.sh - Query an AI agent non-interactively (piped mode)
#
# Usage: agent-query.sh <agent> [options] <prompt>
#
# Agents: claude, codex
#
# Options:
#   -d, --dir <dir>    Set working directory
#   -m, --model <model> Specify model (agent-specific)
#
# Examples:
#   agent-query.sh claude "Explain this error"
#   agent-query.sh codex -d ./project "Review the auth module"
#   agent-query.sh codex --model o3 "Optimize this function"

set -e

usage() {
    echo "Usage: agent-query.sh <agent> [options] <prompt>" >&2
    echo "Agents: claude, codex" >&2
    echo "Options:" >&2
    echo "  -d, --dir <dir>      Set working directory" >&2
    echo "  -m, --model <model>  Specify model" >&2
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
        -*)
            echo "Unknown option: $1" >&2
            usage
            ;;
        *)
            break
            ;;
    esac
done

prompt="$*"

if [[ -z "$prompt" ]]; then
    echo "Error: prompt required" >&2
    usage
fi

# Change directory if specified
if [[ -n "$dir" ]]; then
    cd "$dir"
fi

case "$agent" in
    claude)
        cmd=(claude -p)
        [[ -n "$model" ]] && cmd+=(--model "$model")
        cmd+=("$prompt")
        ;;
    codex)
        cmd=(codex exec)
        [[ -n "$model" ]] && cmd+=(-m "$model")
        cmd+=("$prompt")
        ;;
    *)
        echo "Unknown agent: $agent" >&2
        echo "Supported agents: claude, codex" >&2
        exit 1
        ;;
esac

exec "${cmd[@]}"
