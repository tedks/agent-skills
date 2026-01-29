#!/bin/bash
# claude-send.sh - Send a message to a Claude Code instance in tmux
#
# Usage: claude-send.sh <window> <message>
#
# Handles the timing issue where Enter gets swallowed if sent too quickly
# after the text. Uses a 1.5 second delay between text and Enter.

set -e

if [[ $# -lt 2 ]]; then
    echo "Usage: claude-send.sh <window> <message>" >&2
    echo "  window: tmux window target (e.g., 'chaos:newchan-review')" >&2
    echo "  message: the prompt to send to Claude" >&2
    exit 1
fi

window="$1"
shift
message="$*"

tmux send-keys -t "$window" "$message"
sleep 1.5
tmux send-keys -t "$window" Enter
