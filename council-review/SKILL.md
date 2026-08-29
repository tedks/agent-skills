---
name: council-review
description: Multi-agent code review council - one seat per provider (Claude, Codex, Antigravity/agy), reviewed simultaneously; the host fills its own provider's seat with its native subagent and the foreign seats via ask-agent
argument-hint: '[target] — e.g. PR #42, staged, branch, or a file path'
---

# council-review

Launch a code review "council" — get one review per AI provider (Claude, Codex, agy) simultaneously, then synthesize the results.

## Usage

```
/council-review [target]
```

## Arguments

- `[target]`: What to review (optional, defaults to uncommitted changes)
  - `staged` — review staged changes (`git diff --cached`)
  - `branch` — review all commits on this branch vs main/master
  - `PR #<number>` or `#<number>` — review a pull request
  - `<file-path>` — review a specific file
  - *(no argument)* — review all uncommitted changes (`git diff HEAD`)

## Provider-aware seating

This one SKILL.md is loaded by every host (Claude Code reads it from
`~/.claude/skills`, Codex from `~/.codex/skills`, which symlinks to the same
directory). The council has **one seat per provider**, and which seat is
*yours* depends on which host you are running as — identify yourself from your
own harness/system prompt, not from anything in the repo.

| Host you are running as | Your own seat — **native subagent** | Foreign seats — **`/ask-agent`** |
|-------------------------|-------------------------------------|----------------------------------|
| Claude Code             | Claude, via the `Agent` tool         | `/ask-agent codex` + `/ask-agent agy` |
| Codex CLI               | Codex, via its built-in subagent facility | `/ask-agent claude` + `/ask-agent agy` |
| Antigravity (agy)       | agy, via its built-in subagent facility | `/ask-agent claude` + `/ask-agent codex` |
| any other host          | your own provider's native subagent  | `/ask-agent <p>` for every provider `p` that is not yours |

The same thing as an algorithm, for a host the table does not name:

```
me = the provider I am running as
for seat in {claude, codex, agy}:
    if seat == me:  fill it with my native/internal subagent
    else:           fill it with /ask-agent <seat>
                    (unavailable → leave the seat EMPTY and say so; never backfill)
```

Rules — these are not style preferences, they are what makes the council a council:

1. **Your own provider's seat is always your native subagent.** Never
   `/ask-agent` your own provider: Claude must not ask-agent `claude`, Codex
   must not ask-agent `codex`, agy must not ask-agent `agy`. Doing so spawns a
   second copy of yourself through a CLI — same model, same blind spots, none
   of your project context — and fills a seat that was supposed to bring a
   different model to the table.
2. **Every foreign seat goes through `/ask-agent`.** That is what gives the
   council provider diversity. Do not make the council internal-only, and do
   not "save time" by having your native subagent play a foreign seat.
3. **An unavailable foreign reviewer leaves an empty seat, noted in the
   summary.** Not installed, usage limit, timeout, the agy permission flake —
   whatever the cause, report which seat is missing and continue with the
   rest. Do **not** substitute a second native subagent, or any other reviewer
   from your own provider, for the missing one: two reviews from one provider
   are one opinion twice, and a summary that hides the gap overstates the
   consensus.

The worked examples below are written from a **Claude Code host** (native seat =
Claude; foreign seats = Codex, agy). A Codex host swaps the roles — native seat
= Codex, foreign seats = Claude and agy — and so on down the table.

## Instructions

When this skill is invoked, follow these steps:

### Step 1: Determine what to review

Based on the `[target]` argument (or lack thereof), gather the review material:

| Target | How to gather context |
|--------|----------------------|
| *(none)* | `git diff HEAD` (all uncommitted changes — staged and unstaged) |
| `staged` | `git diff --cached` |
| `branch` | Detect default branch (see below), then `git log --oneline <default>..HEAD` + `git diff <default>...HEAD` |
| `PR #N` / `#N` | This is handled differently — see Step 3 |
| `<file>` | Read the file contents |

**Detecting the default branch:** Run `git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|.*/||'`. If that fails, check whether `main` or `master` exists as a local branch.

If the diff is empty, tell the user there's nothing to review and stop.

### Step 2: Build the review prompt

Construct a review prompt that includes the diff/context gathered in Step 1:

```
Review the following code changes. For each issue found, cite the specific file and line.

Organize your review into these sections:
- **Critical** (bugs, security issues — must fix)
- **Important** (design problems, missing edge cases — should fix)
- **Nits** (style, naming, minor improvements — nice to fix)

If the code looks good, say so briefly and note any minor suggestions.

<diff/context here>
```

The prompt should cover: correctness, security, design, edge cases, and concrete suggestions.

### Step 3: Launch every seat in parallel

Seat the council per the matrix above: one native subagent for your own
provider, one `/ask-agent` per foreign provider, all in a **single parallel
tool invocation**. There are two modes depending on the target:

#### For PR targets

PR targets are simpler for reviewers that can pull context from the PR
themselves. Determine `owner/repo` from `gh repo view --json nameWithOwner -q .nameWithOwner`.

| Seat | If this is **your own** provider | If this is **foreign** to you |
|------|----------------------------------|-------------------------------|
| Claude | native subagent (Claude Code: `Agent` tool) with the review prompt + PR diff | `/ask-agent claude -d /tmp --prompt-file "$PROMPT_FILE"` with the **diff inline** — `claude -p` runs under whatever permission settings the machine has and may not be allowed to run `gh`, so do not tell it to pull the PR |
| Codex | native subagent (Codex CLI: built-in subagent) with the review prompt + PR diff | `/ask-agent codex "Review PR #N in <owner/repo>. <review prompt template from Step 2, without the diff — the agent will pull it>"` |
| agy | native subagent (Antigravity: built-in subagent) with the review prompt + PR diff | `/ask-agent agy -d /tmp --prompt-file "$PROMPT_FILE"` — **must be given the diff inline**, see below. Do *not* tell agy to pull the PR |

Whenever a seat needs the diff inline, stage it in a prompt file exactly as in
the non-PR mode below (`gh pr diff N` produces it).

> **agy cannot fetch anything.** Headless agy has no usable tool access: the
> `command` permission cannot be prompted for in headless mode and is
> auto-denied, so `gh pr diff`, `git`, `cat` — anything but reading the one
> staged prompt file — will fail. See ditz `agy-headless-command-permission`;
> granting it is an operator decision and is deliberately not part of the
> tooling.
>
> The denial shows up in one of two ways, and **neither is review content**:
> the *hard* shape produces no output at all, while the *soft* shape puts the
> denial text on stdout, where it looks superficially like a reply. ask-agent
> retries once and normally absorbs both — so if that text ever does reach you,
> read it as the flake, not as agy's opinion of the diff.
>
> So for **every** agy review: put the complete diff *in the prompt*, tell it
> explicitly not to run commands or fetch anything, and run it from a neutral
> directory so nothing in the cwd invites exploration:
>
> ```bash
> /ask-agent agy -d /tmp --prompt-file "$PROMPT_FILE"
> ```
>
> and open the prompt with a line like: *"Everything you need is in this
> prompt. Do not run any commands and do not fetch anything — review only what
> is written below."*

#### For all other targets

Write the review prompt (with the diff included) to a temp file:

```bash
PROMPT_FILE=$(mktemp)
trap 'rm -f "$PROMPT_FILE"' EXIT
cat > "$PROMPT_FILE" << 'REVIEW_EOF'
<the review prompt from step 2, including the diff>
REVIEW_EOF
```

Then launch every seat in a single parallel tool invocation. From a Claude
Code host that is:

1. **Your own seat (native subagent):** Use the `Agent` tool with the review prompt
2. **Foreign seat — Codex:** `/ask-agent codex --prompt-file "$PROMPT_FILE"`
3. **Foreign seat — agy:** `/ask-agent agy -d /tmp --prompt-file "$PROMPT_FILE"`

From a Codex host the native seat is Codex's built-in subagent and the foreign
seats are `/ask-agent claude --prompt-file "$PROMPT_FILE"` and the same agy
call; from any other host, apply the matrix.

This mode already inlines the diff, which is why it suits agy. Still add the
"everything you need is in this prompt, do not run commands" line for agy, and
keep `-d /tmp` — a neutral cwd. Reading the staged prompt file is the *one*
tool agy is permitted (that is what `--add-dir` grants); anything else is
auto-denied and costs you the whole review.

### Step 4: Present the council's findings

Once every seat has answered (or been recorded as missing), present a unified summary:

```markdown
## Council Review Summary

Seats: Claude (native subagent), Codex (ask-agent), agy (ask-agent — MISSING: <reason>)

### Consensus (issues flagged by 2+ reviewers)
- ...

### Claude's Review
<summary of unique points>

### Codex's Review
<summary of unique points>

### agy's Review
<summary of unique points>

### Recommendations
<synthesized action items, prioritized>
```

The `Seats:` line records which seat was native and which were foreign, and
names any seat that went unfilled. Highlight **consensus issues** (flagged by
multiple reviewers) as highest priority — these are almost certainly real.
Different agents may describe the same issue differently, so match
semantically, not by wording. Note any disagreements between reviewers as
discussion points.

Omit the review section for a seat that failed to respond, but keep it in the
`Seats:` line — a missing seat is a fact about the round, not something to
tidy away. Consensus is counted over the seats that actually answered.

## Notes

- All seats run in parallel for speed
- Your native subagent's review has full project context; foreign reviewers only see the diff (or PR)
- If a foreign reviewer fails (not installed, usage limit, timeout, etc.), note it and continue with whatever reviews succeed — **do not** fill its seat with another reviewer from your own provider
- For the PR merge gate (fixpoint, triage, proportionality, PR record), see "Council review to fixpoint" in the global agent instructions (`~/.claude/CLAUDE.md`); this skill is one round of that loop
