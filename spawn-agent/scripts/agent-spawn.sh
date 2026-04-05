#!/bin/bash
# agent-spawn.sh - Spawn an AI agent in a tmux window (interactive mode)
#
# Usage: agent-spawn.sh <session:window> <agent> [directory] [prompt]
#        agent-spawn.sh <session:window> <agent> [directory] --prompt-file <file>
#
# Agents: claude, codex, gemini
#
# Options:
#   --prompt-file <file>   Read prompt from file (avoids ARG_MAX)
#
# Automatically targets the correct tmux server socket when run inside tmux.
# Override with SPAWN_TMUX_SOCKET or SPAWN_TMUX_LABEL env vars.
#
# Examples:
#   agent-spawn.sh chaos:review claude ./project "Review auth module"
#   agent-spawn.sh chaos:codex-help codex . "Help me refactor this"
#   agent-spawn.sh chaos:review claude ./project --prompt-file /tmp/prompt.txt

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/tmux-ctx.sh"

usage() {
    echo "Usage: agent-spawn.sh <session:window> <agent> [directory] [prompt]" >&2
    echo "       agent-spawn.sh <session:window> <agent> [directory] --prompt-file <file>" >&2
    echo "  session:window: tmux target (e.g., 'chaos:review')" >&2
    echo "  agent: claude, codex, gemini" >&2
    echo "  directory: working directory (default: current)" >&2
    echo "  prompt: initial prompt for the agent" >&2
    echo "  --prompt-file: read prompt from file (avoids ARG_MAX)" >&2
    exit 1
}

if [[ $# -lt 2 ]]; then
    usage
fi

target="$1"
agent="$2"
directory="${3:-.}"
shift 3 2>/dev/null || shift $#

# Check for --prompt-file flag or positional prompt
prompt_file=""
prompt=""
if [[ "$1" == "--prompt-file" || "$1" == "-f" ]]; then
    prompt_file="$2"
    if [[ -z "$prompt_file" ]]; then
        echo "Error: --prompt-file requires a file path" >&2
        exit 1
    fi
    if [[ ! -f "$prompt_file" ]]; then
        echo "Error: prompt file not found: $prompt_file" >&2
        exit 1
    fi
elif [[ -n "$1" ]]; then
    prompt="$1"
fi

# Parse session and window from target
session="${target%%:*}"
window="${target#*:}"

# Validate agent
case "$agent" in
    claude|codex|gemini) ;;
    *)
        echo "Unknown agent: $agent" >&2
        echo "Supported agents: claude, codex, gemini" >&2
        exit 1
        ;;
esac

# Ensure session exists
if ! "${TMUX_CMD[@]}" has-session -t "$session" 2>/dev/null; then
    echo "Creating tmux session: $session" >&2
    "${TMUX_CMD[@]}" new-session -d -s "$session"
fi

# Create the window and run the agent.
# Prompt is always passed via a temp file to avoid ARG_MAX on the
# tmux new-window command string.
if [[ -n "$prompt_file" || -n "$prompt" ]]; then
    # Normalize to a temp file the tmux command will read from
    if [[ -z "$prompt_file" ]]; then
        prompt_file=$(mktemp)
        printf '%s' "$prompt" > "$prompt_file"
    fi
    "${TMUX_CMD[@]}" new-window -t "$session" -n "$window" -c "$directory" \
        "prompt=\$(cat '$prompt_file'); rm -f '$prompt_file'; $agent \"\$prompt\""
else
    "${TMUX_CMD[@]}" new-window -t "$session" -n "$window" -c "$directory" "$agent"
fi

echo "Spawned $agent in $target (dir: $directory)"
