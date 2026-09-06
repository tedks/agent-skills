# claude-pane-detect.sh - shared Claude Code pane classifiers. SOURCE, don't run.
#
# Lives apart from its callers so agent-spawn.sh (which must never paste into a
# dialog) and claude-trust-dir.sh --verify (which detects that same dialog to
# check its own prediction) cannot drift. A tripwire built on a private copy of
# the thing it is meant to police is worth very little.

# Claude Code's workspace trust dialog. Unlike agy's, its default button is
# "No, exit", so a blind Enter does not wrongly grant trust -- it exits, tmux
# reaps the window, and a spawn reports success having launched nothing. Worse,
# that answer is persisted and suppresses trust well beyond the directory it was
# asked about.
#
# Matching is deliberately on SHORT fragments. Claude re-flows the dialog to the
# pane width, and tmux sizes a session to its SMALLEST attached client, so a long
# literal silently stops matching on a narrow terminal -- exactly when a paste
# would be most damaging. "No, exit" survives any width a terminal can plausibly
# have. A false positive here only refuses to act; a false negative pastes into
# the dialog. Fail closed.
claude_trust_modal_active() {
    local active_tail
    active_tail=$(printf '%s\n' "$1" | tail -n 25)
    [[ "$active_tail" == *"Quick safety check"* || \
       "$active_tail" == *"Yes, I trust this folder"* || \
       "$active_tail" == *"No, exit"* ]]
}

# Claude's composer, established from positive evidence the dialog cannot
# supply -- the same three-signal discipline codex_composer_ready uses. The "❯"
# glyph alone is NOT evidence: the dialog draws it as its selection cursor, so
# requiring only that (plus the absence of a footer that wraps out of view) let
# a wrapped dialog read as a live composer.
claude_composer_ready() {
    local active_tail
    active_tail=$(printf '%s\n' "$1" | tail -n 25)
    # A dialog on screen is never a ready composer, at any width.
    if claude_trust_modal_active "$1"; then
        return 1
    fi
    printf '%s\n' "$active_tail" | grep -q '─' && \
        printf '%s\n' "$active_tail" | grep -q '❯'
}
