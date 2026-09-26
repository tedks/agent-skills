# Tmux transport details

Read this for timing overrides, deferred/ambiguous recovery, or a UI blocker.
`tmux-message send` calls the compatibility backend `claude-send.sh`; callers
use the shared interface.

Uses tmux load-buffer/paste-buffer for the message body (avoids ARG_MAX).
It observes a stable empty composer and rechecks under a lock shared by all
aliases of the same server/pane. Human or unknown unsent text and modals defer
delivery; unrelated agent output can continue while the empty composer accepts
a pending message. After paste, the visible composer must match the outgoing
payload throughout the wait and immediately before a single Enter.
During initial rendering, empty or unknown frames may settle within the
`--render-wait` budget without sending keys. A recognized mismatch or modal
stops immediately. Unknown frames never authorize Enter. After exact ownership
is first observed, every later check must remain an exact match. A late render
stops as ambiguous with the original payload retained.

Options before the message or `--prompt-file`:

Timing policy is explicit per call. Keep the 8-second baseline unless repeated
receipt measurements for the receiver CLI/version/model/state and geometry
justify a different `--submit-delay`. This override is independent of render
waiting; model identity alone does not select a timing default. Launcher startup uses a 30-second composer budget; running-agent messages
use 10 seconds. Both use 8-second submit delay and 3-second render budget.
Claude and Codex share these settings; no evidence justifies a model-specific
shorter pause. Agy task delivery remains unsupported until its real composer
is recognized by the shared guard.

- `--render-wait <seconds>`: integer 1-30, default 3, initial render budget.
- `--submit-delay <seconds>`: integer 0-30, default 8, paste-to-Enter pause.
- `--max-wait <seconds>`: integer 1-600, default 10, composer wait budget.
- `--poll-interval <seconds>`: decimal 0.1-2, default 0.5. Composer deferral
  backs off to at most 8 seconds; payload verification uses this cadence.

Requires Bash, Python 3.9+, tmux, flock, and GNU command-line utilities.
Payloads must be nonblank UTF-8 text, with no control characters except
newlines and no trailing spaces or tabs on a line. Wrapped or collapsed
composers may be unverifiable and produce an ambiguous outcome. During Codex
generation, only the measured short, single-line composer followed by a blank
row and styled `tab to queue message` hint is recognized, either combined with
the context percentage or below the measured model/context row; wrapped or multiline
queue layouts remain ambiguous. The measured Codex0.157.1/Sol fresh-fork
model/branch footer and exact `/compact` completion-menu frame are also
recognized. Other context-free model/footer shapes remain unknown.
This includes a prompt file ending in a newline: its pasted cursor geometry
cannot be verified by the single-line queue shape. Preserve those bytes rather
than trimming them for a retry; broader support is tracked in ditz.

There is no reliable screen-only way to tell Claude Code's rotating `Try …`
suggestion from a person who typed the same text and moved the cursor Home;
tmux captures show identical text, cursor position, and no style distinction.
The sender treats that Claude frame as unknown and defers with the payload
preserved. Codex's empty `Ask Codex to do anything` suggestion is accepted only
when `capture-pane -e` exposes SGR dim styling over the full suggestion. If that
style evidence is absent or unparseable, the sender defers. Neither signal
inspection nor cursor position excludes typing between screen observations.

The lock directory defaults to the checked private `/tmp/claude-send-locks-$UID`,
independent of desktop/SSH environment. `CLAUDE_SEND_LOCK_DIR` can override it
for testing; all cooperating senders must use the same override when configured.
The separate alias lock wait is bounded to 90 seconds. Output reports
`status=sent receipt=unverified` on transport submission (exit 0).
`status=deferred` (exit 2) means no paste was attempted;
`status=ambiguous` (exit 3) means paste may have changed the composer and no
further input is safe. Deferred/ambiguous outcomes report `pending_file=...`
with the exact outgoing payload in a private directory (mode 700, file 600).
If state storage fails, `retention=...` reports the existing mode-600 temporary
snapshot retained instead. Signals before any paste attempt report deferred;
signals during or after a paste attempt report ambiguity. Both retain the exact
payload and keep the conventional signal exit code (129/130/143). An operational
failure after payload validation also reports and retains: deferred before any
paste attempt, ambiguous once a paste may have started. It keeps the original
nonzero exit status so callers can distinguish the failure from a normal
defer/ambiguous decision.
Keep that file for deliberate retry and remove it when resolved. The sender
never clears mixed input or automatically repastes/retries Enter.

Inspect application history or its explicit pending-message queue for the
exact message before claiming receipt. If the composer still contains only
the exact outgoing payload, inspect it before any deliberate Enter-only retry.
Never use a fresh send to retry an ambiguous attempt blindly.

Screen polling is a conservative guard, not atomic human-input exclusion.
Invisible input, cursor movement, clipped/collapsed pasted blocks, and TUI
layout changes limit what can be verified. Unsupported surfaces fail closed.
See [the delivery boundaries](../../../../docs/control-delivery.md) and the
fixture-based regression suite `scripts/test-claude-send.sh`.

