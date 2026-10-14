# Native harness identity readers

These three modules are the canonical native identity implementation shared by
retire-agent and the dotfiles harness-control MCP server. They were moved without
behavior changes from dotfiles `mcp/claude-introspect` at commit
`5fc9f92d5a4a23dbe32507256743cbaff3ad33c9`.

Retirement imports this sibling directory from its installed skills tree.
Dotfiles retains compatibility shims that execute these sources while preserving
the shim module's `__file__`; `__agent_skills_source__` records the canonical source.
The shims require installed `~/.claude/skills`, or an explicit absolute
`AGENT_SKILLS_ROOT` pointing to a staged `skills` directory. There is no bundled
implementation fallback or implicit network fetch.

The tests in `tests/` moved from the matching dotfiles `scripts/test-*.py` paths.
Run each with Python 3, then with `--mutation-check`. Dotfiles keeps forwarding
test entrypoints and its MCP-specific wiring suites. Run those with
`AGENT_SKILLS_ROOT` pointing to this checkout's `skills` directory.
