---
name: initialize-repo
description: Initialize a new Sui dapp project with bare git repo, Nix flake, beads, branch protection, and agent instructions. Use when starting a new project from scratch.
argument-hint: <project-name> [description]
allowed-tools: Bash(~/.claude/skills/initialize-repo/scripts/*), Read, Write, Edit, Glob, Grep, WebFetch
---

# initialize-repo

Set up a new Sui dapp project from scratch with the full development environment: bare git repo with worktrees, Nix flake with pinned Sui binary, Move contract scaffold, frontend skeleton, beads issue tracking, GitHub repo with branch protection, and agent instruction files.

## Usage

```
/initialize-repo <project-name> [description]
```

## Arguments

- `<project-name>`: The project name (used for directory, GitHub repo, and package names)
- `[description]`: Optional one-line description for GitHub repo and README

## What It Creates

```
~/Projects/<project-name>/
├── .git/                          # Bare git repo
├── .beads/                        # Beads issue database
└── master/                        # Primary worktree
    ├── .beads/redirect            # Points to bare root's .beads/
    ├── .claude/skills/            # Agent skills (copied from dotfiles)
    ├── .envrc                     # Nix flake integration for direnv
    ├── .gitignore                 # Standard ignores
    ├── .planning/
    │   └── PLANS.md               # ExecPlan format guide
    ├── AGENTS.md                  # Agent instructions (source of truth)
    ├── CLAUDE.md -> AGENTS.md     # Symlink for Claude Code
    ├── README.md                  # Project README
    ├── contracts/<project-name>/  # Sui Move package
    │   ├── Move.toml
    │   ├── sources/
    │   └── tests/
    ├── contracts/docs/
    ├── flake.nix                  # Nix dev environment with Sui
    ├── flake.lock                 # Pinned dependencies
    ├── frontend/
    │   ├── src/
    │   ├── static/
    │   └── docs/
    └── zklogin-backend/           # Optional zkLogin service
```

## Instructions

When this skill is invoked, follow these steps in order. Each step depends on the previous one succeeding.

### Step 1: Create bare repo and master worktree

```bash
cd ~/Projects
mkdir <project-name>
cd <project-name>
git init --bare .git
git worktree add -b master master
cd master
```

The `git worktree add -b master master` command creates an orphan branch since there are no existing refs.

### Step 2: Create directory structure

```bash
cd ~/Projects/<project-name>/master
mkdir -p contracts/docs frontend/src frontend/static frontend/docs .planning .claude/skills
```

### Step 3: Scaffold the Move contract

```bash
cd ~/Projects/<project-name>/master/contracts
nix develop --command sui move new <project-name>
```

This generates `Move.toml`, `sources/<project-name>.move`, and `tests/<project-name>_tests.move` automatically. Do not write these by hand.

### Step 4: Create placeholder files

Write empty `.gitkeep` files in:
- `contracts/docs/.gitkeep`
- `frontend/src/.gitkeep`
- `frontend/static/.gitkeep`
- `frontend/docs/.gitkeep`

### Step 5: Write flake.nix

Write a Nix flake that provides a dev shell with the Sui CLI (pinned binary release), Node.js, pnpm, TypeScript, Rust toolchain, and general utilities. The flake should:

- Use `nixpkgs` (nixos-unstable) and `flake-utils` as inputs
- Fetch the Sui binary from GitHub releases using `pkgs.fetchurl`
- Support `x86_64-linux` and `aarch64-linux` with platform-specific hashes
- Use `autoPatchelfHook` for binary compatibility on NixOS/non-FHS systems
- Include a shell hook that prints tool versions on entry

To get the correct Sui version and hashes:
1. Check the latest Sui release: `gh api repos/MystenLabs/sui/releases/latest --jq '.tag_name'`
2. Download the tarball for each platform and compute the SRI hash: `nix hash to-sri --type sha256 $(nix-prefetch-url --unpack <url>)`

### Step 6: Write .envrc

Content: `use flake`

### Step 7: Write .gitignore

Standard ignores for Nix, Node, IDE, OS, and environment files:

```
# Nix
result
result-*
.direnv/

# Node
node_modules/
dist/
.next/
.nuxt/

# IDE
.idea/
.vscode/
*.swp
*.swo
*~

# OS
.DS_Store
Thumbs.db

# Environment
.env
.env.local
.env.*.local
```

### Step 8: Fetch PLANS.md

Fetch the ExecPlan format guide from the OpenAI cookbook:

```bash
gh api repos/openai/openai-cookbook/contents/articles/codex_exec_plans.md --jq '.content' | base64 -d
```

Extract the content inside the `~~~md` code fence (starting with `# Codex Execution Plans (ExecPlans):`) and write it to `.planning/PLANS.md`. Do not include the article wrapper.

### Step 9: Write AGENTS.md

Write agent instructions covering:
- Project overview and description
- Project structure (directory layout)
- Environment setup (Nix, direnv)
- ExecPlans reference to `.planning/PLANS.md`
- Beads issue tracking quick reference (`bd ready`, `bd show`, `bd create`, etc.)
- Beads worktree setup (the portable redirect recipe using `git worktree list --porcelain`)
- Build and test commands for contracts (`sui move build`, `sui move test`) and frontend (`pnpm install/dev/build/test`)
- Git workflow: PR-only, draft PRs early, normal merges, stacked PRs, granular commits, worktrees

### Step 10: Create CLAUDE.md symlink

```bash
cd ~/Projects/<project-name>/master
ln -s AGENTS.md CLAUDE.md
```

### Step 11: Write README.md

A user-facing README with:
- Project title and description
- Prerequisites (Nix)
- Getting started (nix develop, contract build/test, frontend dev)
- Project structure overview
- Link to AGENTS.md for contributing

### Step 12: Copy skills from dotfiles

Copy the skill directories from `~/Projects/dotfiles/.claude/skills/` into the repo's `.claude/skills/`:
- `stacked-prs/`
- `ask-agent/`
- `spawn-agent/`
- `initialize-repo/` (this skill)

Preserve executable permissions on scripts.

### Step 13: Generate flake.lock

```bash
cd ~/Projects/<project-name>/master
nix develop --command echo "flake loaded"
```

This generates `flake.lock` and validates the flake works.

### Step 14: Initial commit

```bash
cd ~/Projects/<project-name>/master
git add -A
git commit -m "Initial project scaffold: <project-name>

Directory structure, Nix dev environment with pinned Sui binary,
Move contract scaffold, agent instructions, ExecPlan format guide,
beads issue tracking, and development skills."
```

### Step 15: Create GitHub repo and push

```bash
gh repo create tedks/<project-name> --private --description "<description>"
cd ~/Projects/<project-name>/master
git remote add origin git@github.com:tedks/<project-name>.git
git push -u origin master
```

Note: `gh repo create --source` does not work from a worktree. Create the repo without `--source`, then add the remote manually.

### Step 16: Initialize beads

Beads must be initialized from the bare repo root, not from a worktree:

```bash
cd ~/Projects/<project-name>
bd init --branch beads-metadata --prefix <project-name>
```

Then configure it:
1. Edit `.beads/config.yaml`: set `no-daemon: true`
2. Delete the stray `AGENTS.md` that `bd init` creates at the bare root: `rm ~/Projects/<project-name>/AGENTS.md`
3. Create the redirect in the worktree:
   ```bash
   cd ~/Projects/<project-name>/master
   mkdir -p .beads
   printf '%s\n' ../.beads > .beads/redirect
   ```
4. Commit and push the redirect:
   ```bash
   git add .beads/redirect
   git commit -m "Add beads issue tracking redirect for worktree"
   git push
   ```

### Step 17: Set up branch protection

Create a GitHub ruleset requiring PRs for the master branch:

```bash
cd ~/Projects/<project-name>/master
gh api repos/tedks/<project-name>/rulesets -X POST --input - <<'RULES'
{
  "name": "protect-master",
  "target": "branch",
  "enforcement": "active",
  "conditions": {
    "ref_name": {
      "include": ["refs/heads/master"],
      "exclude": []
    }
  },
  "rules": [
    {
      "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": false,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": false
      }
    }
  ]
}
RULES
```

### Step 18: Verify

Run the following checks:
- `cd ~/Projects/<project-name>/master && git status` — clean
- `nix develop --command sui --version` — Sui CLI works
- `nix develop --command sui move build` (from contracts dir) — Move compiles
- `git worktree list` — shows bare root and master worktree
- `bd ready` — beads works (returns empty list)

Report the results to the user.

## Notes

- The Sui binary version and hashes will need updating as new releases come out. Check `gh api repos/MystenLabs/sui/releases/latest` for the current version.
- The skill assumes the user's GitHub username is `tedks`. Adjust the `gh repo create` and remote URL if needed.
- Skills are copied into the repo (not symlinked) so the repo is self-contained. Check `~/Projects/dotfiles/.claude/skills/` for upstream updates periodically.
