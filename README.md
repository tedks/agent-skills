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

Project repositories may use the stable installed paths or carry a reviewed,
commit-pinned vendor snapshot when they must remain self-contained. Vendored
copies record their source commit and retain this repository's license.

## Configuration

Machine-specific values live outside the repository in
`${XDG_CONFIG_HOME:-$HOME/.config}/agent-skills/config.toml`. Dotfiles seeds
that file from its own template. Existing valid machine-owned configuration
is preserved. The shared loader supports `AGENT_SKILLS_*` overrides for
tests and generic defaults; see its configuration documentation for keys.
Python 3.11 or later is required for TOML parsing. Config is data, never shell
code, and should not contain secrets.

## History

The current source snapshot is dotfiles commit
`63f99171a1a0a9f28788abe0c6b8514e2d4124bf`. `git subtree split -P .claude/skills`
produced `7b212b1dc61d3bc948612c56209f66ec6ca914de`, retaining 264 commits.
Its tree `754c0a3c7fe6d3415ddfe863186c1b3e92eb9ba6` exactly matched the source
subtree. A separate rename commit placed the original extraction under
`skills/`; the current branch merges the updated extraction so both source
histories remain reachable. The ignored sync cache was never part of that
history.

Current-tree cleanup does not remove former setup-specific values from
historical commits. The repository is initially private so its installation
and historical disclosure can be reviewed before a deliberate public release.

## License

Agent skills is free software licensed under the
[GNU Affero General Public License, version 3](LICENSE).

See [AGENTS.md](AGENTS.md) for contribution and verification instructions.
