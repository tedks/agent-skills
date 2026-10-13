# Agent skills

Shared Claude and Codex skills, with their history extracted from dotfiles.
Dotfiles references this repository; it does not vendor it or use a submodule.

## Installation contract

The checkout contains `skills/<name>/SKILL.md` and the scripts and templates
owned by each skill. The expected local layout is a bare repository at
`~/Projects/agent-skills/.git` with its `master` worktree at
`~/Projects/agent-skills/master`.

Dotfiles records the remote URL, branch, and checkout in `.agent-skills.ref`.
Its Claude and Codex installers resolve that reference, clone a missing
checkout, and reject dirty or stale checkouts. Update a clean checkout with
`git pull --ff-only` before rerunning an installer when it is behind.

Claude's installed `~/.claude/skills` points to this checkout's `skills/`.
Codex uses individual `~/.codex/skills/<name>` links so its local `.system`
contents remain intact. These installed paths remain the stable interface
for skill scripts, allowed-tools declarations, and global instructions.
The app-managed `synced/` cache is ignored and preserved during migration.

Project repositories contain small `SKILL.md` pointers to these installed
paths. They require the shared installation; they do not carry skill copies.

## Configuration

Machine-specific values live outside the repository in
`${XDG_CONFIG_HOME:-$HOME/.config}/agent-skills/config.toml`. Dotfiles seeds
that file from its own template. Existing valid machine-owned configuration
is preserved. The shared loader supports `AGENT_SKILLS_*` overrides for
tests and generic defaults; see its configuration documentation for keys.
Python 3.11 or later is required for TOML parsing. Config is data, never shell
code, and should not contain secrets.

## History

The source snapshot is dotfiles commit
`c9eaba9e7630dc732ec092220a32478f09199171`. `git subtree split -P .claude/skills`
produced `8c6a04d5b86fc95ce2faed19e7c86be64850fb75`, retaining 263 commits.
Its tree `9049dd69e812c9febd229ca6d6315f4a6486a1b2` exactly matched the source
subtree. A separate rename commit placed it under `skills/`, preserving the
extracted commit graph. The ignored sync cache was never part of that history.

Current-tree cleanup does not remove former setup-specific values from
historical commits. Publication visibility remains a separate decision.

See [AGENTS.md](AGENTS.md) for contribution and verification instructions.
