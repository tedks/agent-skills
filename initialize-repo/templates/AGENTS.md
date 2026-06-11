# Agent Instructions: {{PROJECT_NAME}}

{{PROJECT_DESCRIPTION}}

## Scope of this file

Global workflow rules live in `~/.claude/CLAUDE.md` (installed from the
dotfiles repo) and apply to every project: landing the plane (session
completion and mandatory push), branch + draft-PR discipline, granular
commits, stacked PRs, the bare-repo/worktree layout, and Nix/Bazel
environment detection. **Do not restate them here.** This file covers only
what is specific to {{PROJECT_NAME}}; if a rule belongs to every repo, it
belongs in the global instructions instead.

`AGENTS.md` is the canonical instruction file. Keep `CLAUDE.md` (and
`GEMINI.md`, `COPILOT.md` if present) as symlinks to it unless a specific
agent genuinely needs divergent instructions.

## Project structure

<!-- TODO: directory layout, one line per top-level entry -->

## Environment

This repo uses a Nix flake. Run project tooling through it:

```bash
nix develop --command <cmd>
```

If using direnv, `.envrc` is `use flake`.

<!-- TODO: note anything beyond the flake (submodules, secrets, services) -->

## Build and test

<!-- TODO: exact build/test/lint commands, e.g.:
nix develop --command <build>
nix develop --command <test>
-->

## Issue tracking (beads)

Issues use the prefix `{{ISSUE_PREFIX}}-` (e.g. `{{ISSUE_PREFIX}}-1`).

```bash
bd ready                                    # find available work
bd show {{ISSUE_PREFIX}}-1                  # view issue details
bd update {{ISSUE_PREFIX}}-1 --status in_progress   # claim work
bd comment {{ISSUE_PREFIX}}-1 "progress"    # add notes
bd close {{ISSUE_PREFIX}}-1                 # complete work
bd sync                                     # sync with git
```

The canonical `.beads/` lives in the primary worktree; feature worktrees
reach it via `.beads/redirect` (created automatically by
`create-worktree`). Include `bd sync` in the end-of-session push workflow.
If `bd sync` complains about the merge driver, rerun
`scripts/configure-git-beads-merge-driver.sh`.

## Planning

ExecPlans for non-trivial work follow the format in
[.planning/PLANS.md](.planning/PLANS.md).

## Repo specifics

<!-- TODO: anything an agent must know that is unique to this repo:
domain constraints, legal/licensing notes, safety gates, deploy targets -->
