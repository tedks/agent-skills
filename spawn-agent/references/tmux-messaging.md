# Blocking tmux delivery

Targets are exact `SESSION:WINDOW` names, stable `%PANE_ID`, or stable
`@WINDOW_ID` (its active pane). Prefixes, globs, bare names and implicit
window-index selectors are rejected. Named windows must be unique in the named
session. Resolution uses `list-windows`/`list-panes`, never `display-message`,
which may fall back to the active pane when a target is missing. Operations pin
the resolved pane ID; deletion afterward causes failure rather than retargeting.

The public sender uses `tmux-message.py`. It returns success only after observing
new application receipt evidence. The older `claude-send.sh` remains available
for compatibility, with its original transport-only semantics.

## Flow

1. Resolve the target to a stable pane and acquire one per-pane sender lock.
2. Compare the input text and relative cursor over a three-second stable period.
   Output moving above the input does not count as typing. A stable draft is
   still a draft; do not submit it. A stable Claude `Try …` suggestion gets one
   Space/Backspace probe. If it remains, defer.
3. Snapshot receipt history, recheck input, and paste actual text once through
   tmux bracketed paste. Compare the resulting input with the expected text,
   tolerating wrapping/whitespace layout, or recognize a newly introduced
   collapsed paste marker with surrounding text consistent with the payload.
4. Send Enter after paste evidence and the settling delay. Wait for a new
   submitted user row or pending queue entry. If the same input remains after
   the retry interval, recheck and send Enter once more. Never paste again.
5. Once the input empties, only wait for confirmation. Disappearance alone is
   not confirmation, and cannot authorize Enter into the next empty input.

New receipt means an additional matching record compared with the pre-paste
history. A collapsed receipt can match a marker observed in this attempt's
input; a marker found only in history is insufficient. Ordinary assistant prose
is not a receipt. `/compact` may instead be confirmed by a new visible
`Context compacted` completion event. Receipt and task completion remain distinct.

The observed Codex `[Pasted Content N chars]` form is supported when it is the
whole input and N matches the payload's Unicode code-point or UTF-16-unit
length. Visible text outside this whole-paste marker is mixed input and stops
submission. Existing `[Pasted text …]` markers retain their surrounding-text
checks. A retained file alone never establishes which paste is on screen.

## Options

Options precede actual message text or `--prompt-file FILE`:

| Option | Default seconds | Purpose |
| --- | ---: | --- |
| `--stable-wait` | 3 | Unchanging input before paste |
| `--max-wait` | 10 | Input readiness budget (launcher uses 30) |
| `--submit-delay` | 1 | Earliest first Enter after paste |
| `--render-wait` | 3 | Wait for initial paste evidence |
| `--receipt-wait` | 15 | Additional confirmation budget |
| `--retry-wait` | 3 | Minimum interval before the second Enter |
| `--poll-interval` | 0.25 | Screen sampling cadence |

`--backoff` waits within `--max-wait` for a draft/typing to clear, rather than
returning immediately when detected. Stability resets when input changes.
The lock wait has a separate `--max-wait` budget. Confirmation is bounded by
render + submit-delay + receipt-wait; operations also have subprocess timeouts.
The launcher gives `/compact` its configured compaction timeout as the receipt
budget, because some layouts show only completion rather than an initial user
row. Receipt waits may exceed 600 seconds when explicitly configured.
Payloads are nonblank UTF-8 with no control characters except newlines. Message
bytes, including trailing whitespace/newlines, are preserved in the paste.

Exit 0 emits delivered JSON. Exit 2 means deferred before paste. Exit 3 means
ambiguous after a paste attempt. Signals retain conventional 128+signal exit
codes. Unsuccessful attempts retain the outgoing message in a private temporary
directory (700) with a private file (600). Remove that file/directory after
resolving it. No human draft or pane contents are logged automatically.

## Agent escalation and extension

`needs_review=true` means a caller can use `tmux-message read TARGET` to inspect
an unsupported layout, modal or ambiguous attempt. The result also carries a
reason, paste-attempted flag and Enter count. An orchestrator may delegate this
inspection to an agent; there is no compulsory model call or model dependency
inside the sender. The inspecting agent must check for receipt before taking
any further action. Existing drafts and human typing normally need only backoff.

Screen interpretation uses semantic prompt/frame/footer and queue markers,
relative cursor position, and before/after receipt counts, not model identity
or fixed row offsets. Extend the composer/receipt functions with representative
frames for additional harnesses; the send/recovery loop stays shared.

These are evidence-based heuristics, not atomic ownership proofs. Humans can
type between observations; whitespace-only differences are not message identity;
assistant output deliberately formatted like a user message can be ambiguous.
History outside the captured 2000-row window and layouts that hide both input
and acceptance can cause a timeout. Such cases require inspection, not replay.
Unknown shells, modals and unsupported input surfaces do not authorize paste.

Run `test-tmux-message.py` for behavior tests and `test-tmux-message-live.py`
for a private real tmux test with a deterministic terminal. Neither uses model
tokens or touches installed tools or live controllers.
