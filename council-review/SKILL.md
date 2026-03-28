---
name: council-review
description: Multi-agent code review council - gets reviews from Claude, Codex, and Gemini simultaneously
argument-hint: [target] -- e.g. "PR #42", "staged", "branch", or a file path
allowed-tools: Bash(~/.claude/skills/ask-agent/scripts/*), Bash(~/.claude/skills/council-review/scripts/*)
---

# council-review

Launch a code review "council" — get reviews from all three AI agents (Claude, Codex, Gemini) simultaneously, then synthesize the results.

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
  - *(no argument)* — review all uncommitted changes (`git diff`)

## Instructions

When this skill is invoked, follow these steps:

### Step 1: Determine what to review

Based on the `[target]` argument (or lack thereof), gather the review material:

| Target | How to gather context |
|--------|----------------------|
| *(none)* | `git diff` (unstaged + staged changes) |
| `staged` | `git diff --cached` |
| `branch` | `git log --oneline main..HEAD` + `git diff main...HEAD` (detect main vs master) |
| `PR #N` / `#N` | `gh pr diff <N>` + `gh pr view <N>` |
| `<file>` | Read the file contents |

If the diff is empty, tell the user there's nothing to review and stop.

### Step 2: Detect which agent you are

You know which agent you are:
- If you are **Claude** → `self=claude`
- If you are **Codex** → `self=codex`
- If you are **Gemini** → `self=gemini`

### Step 3: Build the review prompt

Construct a review prompt that includes the diff/context gathered in Step 1. The prompt should ask for:

1. **Correctness** — bugs, logic errors, off-by-one, race conditions
2. **Security** — injection, auth issues, secret exposure, OWASP top 10
3. **Design** — naming, structure, separation of concerns, unnecessary complexity
4. **Edge cases** — what inputs or conditions would break this?
5. **Suggestions** — concrete improvements (not vague advice)

Format the prompt as:

```
Review the following code changes. For each issue found, cite the specific file and line.

Organize your review into these sections:
- **Critical** (bugs, security issues — must fix)
- **Important** (design problems, missing edge cases — should fix)
- **Nits** (style, naming, minor improvements — nice to fix)

If the code looks good, say so briefly and note any minor suggestions.

<diff/context here>
```

### Step 4: Launch all three reviews in parallel

**Your own review (native subagent):**
- Use your native subagent/tool mechanism to run the review
- For Claude: use the `Agent` tool to spawn a subagent with the review prompt
- For Codex: use your built-in parallel execution
- For Gemini: use your built-in parallel execution

**The other two reviews (via ask-agent):**
- Run the council-review.sh script, which calls agent-query.sh for the two non-self agents in parallel:

```bash
~/.claude/skills/council-review/scripts/council-review.sh --self <self-agent> --dir "$(pwd)" "<review-prompt>"
```

This script returns both external reviews as labeled output.

**IMPORTANT:** Launch your native review and the council-review.sh script at the same time (in parallel), not sequentially.

### Step 5: Present the council's findings

Once all three reviews are collected, present a unified summary:

```markdown
## Council Review Summary

### Consensus (issues flagged by 2+ reviewers)
- ...

### Claude's Review
<summary of unique points>

### Codex's Review
<summary of unique points>

### Gemini's Review
<summary of unique points>

### Recommendations
<synthesized action items, prioritized>
```

Highlight **consensus issues** (flagged by multiple reviewers) as highest priority. Note any disagreements between reviewers — these are interesting discussion points.

## Notes

- All three reviews run in parallel for speed
- The native agent's review has full project context; external agents only see the diff
- If an external agent fails (not installed, timeout), note it and continue with available reviews
- Review prompt is written to a temp file to avoid shell quoting issues
