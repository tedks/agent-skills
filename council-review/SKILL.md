---
name: council-review
description: Multi-agent code review council - gets reviews from Claude, Codex, and Gemini simultaneously
argument-hint: [target] -- e.g. "PR #42", "staged", "branch", or a file path
allowed-tools: Bash(~/.claude/skills/ask-agent/scripts/*)
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

### Step 2: Identify the other two agents

You know which agent you are. The other two get queried via `agent-query.sh`:

| If you are | Query these two |
|------------|-----------------|
| Claude     | codex, gemini   |
| Codex      | claude, gemini  |
| Gemini     | claude, codex   |

### Step 3: Build the review prompt

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

The prompt should cover: correctness, security (OWASP top 10), design, edge cases, and concrete suggestions.

### Step 4: Launch all three reviews in parallel

Write the review prompt to a temp file first (to avoid shell quoting issues with large diffs):

```bash
PROMPT_FILE=$(mktemp)
cat > "$PROMPT_FILE" << 'REVIEW_EOF'
<the review prompt from step 3>
REVIEW_EOF
```

Then launch all three simultaneously — your native subagent review and both `agent-query.sh` calls — in a single parallel tool invocation:

1. **Your own review:** Use your native subagent mechanism (e.g., Claude's `Agent` tool)
2. **External agent A:** `~/.claude/skills/ask-agent/scripts/agent-query.sh <agent> "$(cat $PROMPT_FILE)"`
3. **External agent B:** `~/.claude/skills/ask-agent/scripts/agent-query.sh <agent> "$(cat $PROMPT_FILE)"`

All three MUST be launched in the same parallel tool call, not sequentially.

If an external agent fails (not installed, timeout, etc.), note it and continue with whatever reviews succeed.

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

Highlight **consensus issues** (flagged by multiple reviewers) as highest priority — these are almost certainly real. Note any disagreements between reviewers as discussion points.

## Notes

- The native agent's review has full project context; external agents only see the diff
- If only one or two agents are available, run the council with whoever responds
- Clean up the temp file after all reviews complete
