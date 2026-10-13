#!/bin/bash
# tmux-ctx.sh - Shared tmux context helper for spawn-agent scripts
#
# Source this file to get a TMUX_CMD array that automatically targets
# the correct tmux server socket.
#
# Usage (in other scripts):
#   SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#   source "$SCRIPT_DIR/tmux-ctx.sh"
#   "${TMUX_CMD[@]}" list-windows
#
# Override with:
#   SPAWN_TMUX_SOCKET=/tmp/example-tmux/server  (full path)
#   SPAWN_TMUX_LABEL=example                   (-L name)
#
# Priority: explicit env > valid $TMUX > shared config fallback > default socket
source "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")/../../_shared/config.sh"
_AGENT_SKILLS_TMUX_SOCKET=$(agent_skills_get tmux.socket) || return $?

_resolve_tmux_cmd() {
    # Explicit socket path takes highest priority
    if [[ -n "${SPAWN_TMUX_SOCKET:-}" ]]; then
        TMUX_CMD=(tmux -S "$SPAWN_TMUX_SOCKET")
        return
    fi

    # Explicit -L label
    if [[ -n "${SPAWN_TMUX_LABEL:-}" ]]; then
        TMUX_CMD=(tmux -L "$SPAWN_TMUX_LABEL")
        return
    fi

    # Explicit common override selects a label or socket path.
    if [[ -n "${AGENT_SKILLS_TMUX_SOCKET:-}" ]]; then
        if [[ "$_AGENT_SKILLS_TMUX_SOCKET" == */* ]]; then
            TMUX_CMD=(tmux -S "$_AGENT_SKILLS_TMUX_SOCKET")
        else
            TMUX_CMD=(tmux -L "$_AGENT_SKILLS_TMUX_SOCKET")
        fi
        return
    fi

    # Auto-detect from $TMUX env var (format: /path/to/socket,pid,pane)
    if [[ -n "${TMUX:-}" ]]; then
        local socket_path="${TMUX%%,*}"
        if [[ -S "$socket_path" ]]; then
            TMUX_CMD=(tmux -S "$socket_path")
            return
        fi
    fi

    # File configuration is a fallback outside an active tmux server.
    if [[ -n "$_AGENT_SKILLS_TMUX_SOCKET" ]]; then
        if [[ "$_AGENT_SKILLS_TMUX_SOCKET" == */* ]]; then
            TMUX_CMD=(tmux -S "$_AGENT_SKILLS_TMUX_SOCKET")
        else
            TMUX_CMD=(tmux -L "$_AGENT_SKILLS_TMUX_SOCKET")
        fi
        return
    fi

    # Fallback: bare tmux (uses default socket)
    TMUX_CMD=(tmux)
}

_resolve_tmux_cmd
