---
name: council-review
description: Multi-agent code review council - gets reviews from Claude, Codex, and Antigravity (agy) simultaneously
argument-hint: '[target] — e.g. PR #42, staged, branch, or a file path'
---

# council-review

Launch a code review "council" — get reviews from all three AI agents (Claude, Codex, agy) simultaneously, then synthesize the results.

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

### Step 3: Launch all three reviews in parallel

There are two modes depending on the target:

#### For PR targets

PR targets are simpler — each agent can pull context from the PR itself. Determine `owner/repo` from `gh repo view --json nameWithOwner -q .nameWithOwner`.

Launch all three in a single parallel tool invocation:
1. **Your own review (subagent):** Use the `Agent` tool with the review prompt + PR diff
2. **Codex:** `/ask-agent codex "Review PR #N in <owner/repo>. <review prompt template from Step 2, without the diff — the agent will pull it>"`
3. **agy (Antigravity CLI):** **must be given the diff inline** — see below. Do *not* tell agy to pull the PR.

> **agy cannot fetch anything.** Headless agy has no usable tool access: the
> `command` permission cannot be prompted for in headless mode and is
> auto-denied, so `gh pr diff`, `git`, `cat` — anything but reading the one
> staged prompt file — will fail, and the run produces no output at all. See
> ditz `agy-headless-command-permission`; granting it is an operator decision
> and is deliberately not part of the tooling.
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

Then launch all three in a single parallel tool invocation:
1. **Your own review (subagent):** Use the `Agent` tool with the review prompt
2. **Codex:** `/ask-agent codex --prompt-file "$PROMPT_FILE"`
3. **agy (Antigravity CLI):** `/ask-agent agy -d /tmp --prompt-file "$PROMPT_FILE"`

This mode already inlines the diff, which is why it suits agy. Still add the
"everything you need is in this prompt, do not run commands" line for agy, and
keep `-d /tmp` — a neutral cwd. Reading the staged prompt file is the *one*
tool agy is permitted (that is what `--add-dir` grants); anything else is
auto-denied and costs you the whole review.

### Step 4: Present the council's findings

Once all three reviews are collected, present a unified summary:

```markdown
## Council Review Summary

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

Highlight **consensus issues** (flagged by multiple reviewers) as highest priority — these are almost certainly real. Different agents may describe the same issue differently, so match semantically, not by wording. Note any disagreements between reviewers as discussion points.

Omit sections for agents that failed to respond.

## Notes

- All three reviews run in parallel for speed
- The native agent's review has full project context; external agents only see the diff (or PR)
- If an external agent fails (not installed, timeout, etc.), note it and continue with whatever reviews succeed
