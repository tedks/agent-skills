#!/bin/bash
# council-review.sh - Run code reviews from non-self agents in parallel
#
# Usage: council-review.sh --self <agent> --dir <dir> <review-prompt>
#        council-review.sh --self <agent> --dir <dir> --prompt-file <file>
#
# Runs agent-query.sh for the two agents that are NOT the --self agent,
# collects results, and outputs them with labels.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_QUERY="$SCRIPT_DIR/../../ask-agent/scripts/agent-query.sh"

ALL_AGENTS=(claude codex gemini)

usage() {
    echo "Usage: council-review.sh --self <agent> --dir <dir> <review-prompt>" >&2
    echo "       council-review.sh --self <agent> --dir <dir> --prompt-file <file>" >&2
    exit 1
}

self=""
dir="."
prompt=""
prompt_file=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --self)
            self="$2"
            shift 2
            ;;
        --dir)
            dir="$2"
            shift 2
            ;;
        --prompt-file)
            prompt_file="$2"
            shift 2
            ;;
        -*)
            echo "Unknown option: $1" >&2
            usage
            ;;
        *)
            prompt="$*"
            break
            ;;
    esac
done

if [[ -z "$self" ]]; then
    echo "Error: --self is required" >&2
    usage
fi

# Validate self agent
valid=false
for a in "${ALL_AGENTS[@]}"; do
    if [[ "$a" == "$self" ]]; then
        valid=true
        break
    fi
done
if [[ "$valid" == "false" ]]; then
    echo "Error: --self must be one of: ${ALL_AGENTS[*]}" >&2
    exit 1
fi

# Get prompt from file if specified
if [[ -n "$prompt_file" ]]; then
    if [[ ! -f "$prompt_file" ]]; then
        echo "Error: prompt file not found: $prompt_file" >&2
        exit 1
    fi
    prompt="$(cat "$prompt_file")"
fi

if [[ -z "$prompt" ]]; then
    echo "Error: review prompt required (positional arg or --prompt-file)" >&2
    usage
fi

# Determine which agents to query (all except self)
others=()
for a in "${ALL_AGENTS[@]}"; do
    if [[ "$a" != "$self" ]]; then
        others+=("$a")
    fi
done

# Write prompt to temp file to avoid quoting issues
tmpfile="$(mktemp)"
trap 'rm -f "$tmpfile" "${tmpfile}."*' EXIT
echo "$prompt" > "$tmpfile"

# Run both external reviews in parallel
pids=()
for agent in "${others[@]}"; do
    outfile="${tmpfile}.${agent}"
    (
        if "$AGENT_QUERY" "$agent" -d "$dir" "$(cat "$tmpfile")" > "$outfile" 2>&1; then
            true
        else
            echo "(agent-query.sh failed with exit code $?)" > "$outfile"
        fi
    ) &
    pids+=($!)
done

# Wait for all to finish
for pid in "${pids[@]}"; do
    wait "$pid" 2>/dev/null || true
done

# Output labeled results
for agent in "${others[@]}"; do
    outfile="${tmpfile}.${agent}"
    echo "=========================================="
    echo "REVIEW FROM: ${agent^^}"
    echo "=========================================="
    if [[ -f "$outfile" ]]; then
        cat "$outfile"
    else
        echo "(no output — agent may not be installed)"
    fi
    echo ""
done
