# Shard and recovery contract

The offer assigns `<git-common-dir>/retirements/<unique-event>.shard`, a private pending
JSON file outside the working tree. After
validation the controller preserves its bytes in
`waiting/YYYY-MM-DD-sessionprefix-retire-<event>.md`, one shard per known
session, and updates only its bounded generated section in `waiting/INDEX.md`. The `.md` file contains JSON.
The agent must already have permission to write that private path. Workspace
sandboxing or an approval modal can prevent this; never bypass permissions or
send into a modal. The agent can decline, letting the authorized caller write
the recovery shard through the deliberate caller continuation.
A completion is a fresh assistant response row `RETIRE-DONE-<event>` after
writing the shard; a decline is `RETIRE-DECLINED-<event>`. The marker must be
the agent's entire final reply, without prose before or after it. The terminal
must show that marker as the final assistant block; trailing continuation text
or a later assistant response keeps completion pending. Blank rows and the
pure horizontal Claude composer separator are the only permitted suffix rows.
The observed terminal
must render an assistant `●` or `•` row marker. Bare wrapped user text and
live composer text do not count. Unsupported response layouts stay pending.
The controller never accepts a reported alternate shard path.

The complete envelope (use explicit `"none"` or empty lists where appropriate):

```json
{
  "event": "copy the assigned event",
  "identity": {"copy": "the complete identity object in the offer"},
  "author": "agent",
  "date": "2026-09-26",
  "session_id": "your real provider-native session ID",
  "harness": "codex",
  "model": "your actual model",
  "working_directory": "/actual/worktree",
  "tmux_window": "session:window",
  "role": "your role",
  "landed_work": "PR and exact SHA, or explicit none",
  "lessons": "One to five lines of lessons learned",
  "provenance": "agent-authored from named evidence",
  "recovery": {
    "mission": "mission",
    "authorization": "scope and authority",
    "pauses": "explicit pauses, or none",
    "worktree": "/actual/worktree",
    "branch": "branch",
    "heads": "exact heads",
    "tested_vs_merged": "distinguish tested commits from merged commits",
    "evidence": "evidence paths",
    "limits": "evidence and reconstruction limits",
    "failures": "failed attempts, or none",
    "open_decisions": [{"decision": "pending decision", "owner": "named owner"}],
    "outstanding_sends": "ambiguous sends that must never be replayed, or none",
    "next_action": "first bounded next action"
  },
  "resources": [{"name": "owned server or lease", "status": "released", "receipt": "verification evidence"}],
  "waiting": {"decision": "pending human decision (waiting mode only)", "owner": "human owner"},
  "exact_resume_command": "optional provider-native command; this field is last"
}
```

An empty resources list explicitly claims none remain. A transferred resource
needs `status: "transferred"`, a named `owner`, and a transfer acceptance
`receipt`. Unknown or pending resources block exit. Optional personal thanks
use `personal_thanks` and `thanks_provenance`, apart from recovery. Recovery
must stand alone without live chat, pane inspection or predecessor resumption.
The optional native resume handle is last and is not a reconstruction shortcut.

After an observed decline, the caller writes the assigned file with
`author: "caller"`, `declined: true`, and honest evidence attribution and
uncertainty. Preserve a known session ID; explicitly use `"unknown"` if it
cannot be established. Never invent the agent's own-word thanks. A caller may
include an actual quotation only with `thanks_provenance: "quoted-agent"`.
Then invoke `--resume PRIVATE_STATE --caller-shard` in the same mode.

The checkout must already have private `origin/master`. Privacy is checked for
every fetch/push URL. GitHub uses authenticated `gh repo view` with an explicit
github.com URL, independent of `GH_HOST`; unknown/public
remotes fail closed. `RETIRE_PRIVATE_CHECK` names an optional trusted site-owned
executable accepting one URL and returning zero only when that URL is verifiably
private (tests use local bare repositories). It is a privacy authority; never
set it to `true`. `RETIRE_REPO` and `RETIRE_SPAWN_SKILL` select alternate private
records and the existing shared skill. The default shared skill is the
sibling spawn-agent in the same resolved checkout; overrides must provide the
matching published Claude/Codex model-selection and guarded literal-Claude-exit
contracts. No pane or unsent human text is saved.

Git transactions hold `.git/retirement.lock` (the common directory for
worktrees), around duplicate-session validation, canonical shard creation and
publication together; a per-event lock protects longer orchestration.
Commits are path-only. Uncommitted steward edits outside the generated INDEX
section and unowned unpushed commits block publication. Retirement commits
are owned only with private ledger evidence of their SHA and exact paths.
Noncooperating Git actors still require coordination. Behind or diverged
master requires deliberate reconciliation; failed push blocks exit.
Publication preconditions run before INDEX writes; exact script-owned
outside-section hashes allow recovery after a later Git hook failure. A
HEAD-tracked INDEX deletion stops rather than recreating steward metadata.
Foreign JSON records are indexed only after a matching receipt claims their
event and path; malformed claimed metadata stops with its bytes preserved.
Verify the shard blob on fetched
private `origin/master`, publish its unique ledger receipt, then send the bare
harness exit through the shared sender. Claude `/exit` uses the sender's
`--literal-exit` guard: literal typing and one Enter, confirmed by a fresh
`Resume this session with:` / `claude --resume UUID` receipt. Pasted Claude
`/exit` is ordinary text and cannot retire a harness. `/exit` is used for Codex;
`/quit` for agy. Verify the original PID/start time/boot ID ended, even when its
pane survives as a shell. Recognized Node entrypoints resolve through symlinks;
the outer wrapper and its native Codex child are one logical harness, and
retirement waits for both processes to end. A replacement harness blocks completion. Nothing
kills a process. Resumes reverify the preserved blob and receipt before exit
or successor. If the original ended independently after that verified handoff,
record `exit-not-needed` and continue without sending into its shell. A
replacement actor still blocks succession. Malformed private records stop
with their bytes preserved for operator reconciliation.
After a failed immutable-shard or receipt revalidation, failure handling only
records the private attempt audit; it does not publish a handoff-authorizing
receipt from cached state. Repeated resumes preserve the rejected evidence
until the operator reconciles it deliberately.
The ledger records pending handoff, pending exit and retired;
its index preserves all steward-owned legacy metadata outside the bounded
generated section. A legacy filename matching the same session ID prefix
(eight characters or more) blocks another shard until deliberate reconciliation.

Waits are bounded: `--wait` defaults to 300 seconds (maximum 600), model-result
polling `--poll` defaults to 60 seconds (maximum 90); tests may shorten polling.
Process exit has a separate fast poll. The shared sender has its own bounded
waits. Exit may end the harness before a sender receipt; original process end
is the exit evidence. Offer acceptance and DONE remain separate. An attempted
successor launch is never automatically repeated, including after a crash or
unconfirmed initial delivery. Inspect the launcher's pane/receipt before any
deliberate operator recovery. There is no ambiguous-send override or manual
relay in this script.

Screen markers are heuristics, not atomic human exclusion or cryptographic
actor attribution. An assistant/user deliberately spoofing another actor's
terminal framing can be ambiguous. Schema validation checks fields and resource
receipts, not the truth of claims, PR hashes, model/session identification or
operational prose; the caller must audit those before requesting retirement.
Observation consumes only spawn-agent's published `read_response_rows(pane,
socket)` and `DeliveryError` Python contract. It excludes archived redraws and
the live composer. Keep the final marker visible until the controller records
DONE; after that, resume uses the recorded disposition and revalidates identity.
The socket is pinned in private state and reused for observation and transport;
server failure is not evidence that the pane disappeared. Recognized
`codex mcp-server` support processes are excluded from harness identity, but
their resources still require shard receipts. Unknown or additional harness
invocations remain a safe deferral pending operator reconciliation.
Follow-up `retire-agent-helper-identification` tracks richer auxiliary-command
classification; do not weaken identity checks to bypass that deferral.
The npm `node …/codex.js mcp-server` shape also remains a safe helper-identity
deferral. `retire-agent-commit-ownership-recovery` tracks crashes between Git
commit and its ownership audit: reconcile an orphaned commit deliberately,
never infer ownership from its subject. `retire-agent-concurrent-lock-budget`
tracks the bounded Git-lock wait; busy transactions require a deliberate
resume rather than an overlapping publication attempt.

Each event also has an append-only private `<event>.jsonl` attempt ledger in
the Git common directory: target/pane, event marker, transport exit and receipt
type, completion disposition, verified shard path and original process exit.
It contains no payload or pane text; exact outgoing payloads remain separate.
