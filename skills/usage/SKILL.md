---
name: usage
description: Use when a CTO or lead asks how much its lanes are spending, which session is burning quota, whether a lane tripped the token tripwire, or where the provider quota meters stand. Reads the installed token-usage counts (turns/h, cached tokens/h, average context per session; per-project/model totals; quota meters) and the tripwire's open incidents. Counts only; never message content. Does not install anything or change thresholds.
---

# Usage

Answer "what are my lanes spending?" from the counts the token tripwire
already keeps. The numbers come from the `token-usage` and `token-tripwire`
CLIs that dotfiles installs (`scripts/usage`); this skill only calls them.

## Run

```bash
~/.claude/skills/usage/scripts/usage.sh [--project NAME] [--day YYYY-MM-DD] [--fresh] [--json]
```

- `--project NAME` limits the session view to one project: the first path
  segment under the projects root, the same name the tripwire's ownership
  table uses.
- `--day` gives that local day's per-project and per-model totals (default today).
- `--fresh` scans rollouts and transcripts first. Without it the script reads
  the store as the last cron tick (every 10 minutes) left it, which keeps your
  own context and the store's lock out of the way.
- `--json` emits one JSON object (`sessions`, `totals_project`,
  `totals_model`, `meters`, `incidents`) for further processing.

## Read the answer

- **Sessions** (last 60 minutes): turns/h, cached tokens/h and average
  context per turn. The tripwire's default lines are 300 turns/h, 60M cached
  tokens/h and 100k average context sustained for 30 minutes; the configured
  values may differ.
- **Meters** are account-wide quota windows with the time they were
  observed. A meter observed long ago is a snapshot, not the present.
- **Incidents** are the tripwire's open alerts and their delivery state.

When a lane is over the line, the doctrine's answer is the task-boundary
ritual: compact at a checkpoint, a fresh worker with a small brief, or stop
the loop. Report the numbers with their observation time and say which of
those you recommend; do not restate counts as costs without a fitted weight
(`token-calibrate report` says whether one is defensible yet).

## Failure

If the script reports that `token-usage` is not installed, say so and stop:
installing it is the host's dotfiles installer's job, not this skill's. A
missing config means the tripwire alerts nobody; report that as a finding.
