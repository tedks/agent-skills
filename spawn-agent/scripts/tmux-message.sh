#!/usr/bin/env bash
# Shared tmux read/write interface. No agent registry or transcript dependency.
# Send forwards actual text/options to the installed guarded transport.
# Read shows terminal history; it does not infer delivery from transport success.
set -euo pipefail
SCRIPT_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(dirname "$SCRIPT_PATH")"
source "$SCRIPT_DIR/tmux-ctx.sh"
usage() {
    echo 'Usage: tmux-message.sh send TARGET [sender options] MESSAGE' >&2
    echo '       tmux-message.sh read TARGET' >&2
    exit 2
}
(($# >= 1)) || usage
command=$1
shift
case "$command" in
    send)
        (($# >= 2)) || usage
        exec "$SCRIPT_DIR/claude-send.sh" "$@";;
    read)
        (($# == 1)) || usage
        [[ -n "$1" && "$1" != -* ]] || usage
        pane=$("${TMUX_CMD[@]}" display-message -p -t "$1" '#{pane_id}')
        [[ "$pane" =~ ^%[0-9]+$ ]] || { echo 'Invalid tmux pane identity.' >&2; exit 2; }
        exec "${TMUX_CMD[@]}" capture-pane -e -p -t "$pane" -S -200;;
    *) usage;;
esac
