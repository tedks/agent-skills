#!/bin/bash
# agent-spawn.sh - Spawn an AI agent in a tmux window (interactive mode)
#
# Usage: agent-spawn.sh <session:window> <agent> [directory] [prompt]
#
# Agents: claude, codex
#
# Examples:
#   agent-spawn.sh chaos:review claude ./project "Review auth module"
#   agent-spawn.sh chaos:codex-help codex . "Help me refactor this"

set -e

usage() {
    echo "Usage: agent-spawn.sh <session:window> <agent> [directory] [prompt]" >&2
    echo "  session:window: tmux target (e.g., 'chaos:review')" >&2
    echo "  agent: claude, codex" >&2
    echo "  directory: working directory (default: current)" >&2
    echo "  prompt: initial prompt for the agent" >&2
    exit 1
}

if [[ $# -lt 2 ]]; then
    usage
fi

target="$1"
agent="$2"
directory="${3:-.}"
prompt="${4:-}"

# Parse session and window from target
session="${target%%:*}"
window="${target#*:}"

# Build agent command
case "$agent" in
    claude)
        agent_bin="claude"
        ;;
    codex)
        agent_bin="codex"
        ;;
    *)
        echo "Unknown agent: $agent" >&2
        echo "Supported agents: claude, codex" >&2
        exit 1
        ;;
esac

# Ensure session exists
if ! tmux has-session -t "$session" 2>/dev/null; then
    echo "Creating tmux session: $session" >&2
    tmux new-session -d -s "$session"
fi

# Create the window
# Use bash -c with proper escaping to handle prompts with spaces/special chars
if [[ -n "$prompt" ]]; then
    # Escape single quotes in prompt for bash -c
    escaped_prompt="${prompt//\'/\'\\\'\'}"
    tmux new-window -t "$session" -n "$window" -c "$directory" \
        bash -c "$agent_bin '${escaped_prompt}'"
else
    tmux new-window -t "$session" -n "$window" -c "$directory" "$agent_bin"
fi
echo "Spawned $agent in $target (dir: $directory)"
