---
name: ask-agent
description: Ask another AI agent a question and get the response back (subagent pattern)
argument-hint: <agent> [options] <prompt>
allowed-tools: Bash(~/.claude/skills/ask-agent/scripts/*)
---

# ask-agent

Query another AI agent non-interactively and get the response back. Use this
when you want a second opinion from a different agent (Claude, Codex, Antigravity/agy, etc.).

## Usage

```
/ask-agent <agent> [options] <prompt>
```

## Arguments

- `<agent>`: The agent to query (`claude`, `codex`, `agy`)
- `<prompt>`: The question or request for the agent

## Options

- `-d, --dir <dir>`: Set working directory for the agent
- `-m, --model <model>`: Specify model (agent-specific)
- `-f, --prompt-file <file>`: Read prompt from a file instead of inline (use for
  large prompts to avoid ARG_MAX limits at the caller-to-script boundary)

## Instructions

When this skill is invoked, **always pass the prompt via a temp file** to avoid
ARG_MAX errors with large prompts.

This protects the *caller-to-script* boundary. Past that point no agent is
constrained by argv: `claude` and `codex` receive the prompt on stdin, and
`agy` -- which has no stdin mode -- is handed a file path to read instead of
the prompt text, so nothing large ever transits argv. Ordinary limits still
apply of course: the model's context window, and whatever the agent decides to
do with a very long prompt.

```bash
# 1. Write prompt to a temp file
prompt_file=$(mktemp /tmp/ask-agent-prompt.XXXXXX)
cat << 'PROMPT_DELIM' > "$prompt_file"
<your prompt here>
PROMPT_DELIM

# 2. Run the script with --prompt-file
~/.claude/skills/ask-agent/scripts/agent-query.sh <agent> [options] --prompt-file "$prompt_file"

# 3. Clean up (the script does NOT delete caller-provided files)
rm -f "$prompt_file"
```

For short prompts that are clearly under the ARG_MAX limit (~128KB), inline
is also fine:

```bash
~/.claude/skills/ask-agent/scripts/agent-query.sh <agent> [options] <prompt>
```

The script pipes the prompt via stdin to `claude` and `codex`, and stages it in
a file for `agy`, so inline prompts are safe at the script-to-agent boundary for
all three. The --prompt-file approach
protects the caller-to-script boundary as well.

Report the response back to the user.

## Examples

```bash
# Get Codex opinion on an approach (short prompt, inline ok)
/ask-agent codex "What do you think of using JWT for this auth flow?"

# Ask Claude with a specific model
/ask-agent claude -m opus "Review this error handling pattern"

# Query from a specific directory
/ask-agent codex -d ./src "Explain what the auth module does"

# Ask agy (Antigravity CLI) for a summary
/ask-agent agy "Summarize this codebase"

# Ask agy with a specific model (see `agy models` for available names)
/ask-agent agy -m <model> "Review this architecture"

# Use a prompt file for large prompts (avoids ARG_MAX)
/ask-agent codex --prompt-file /tmp/review-prompt.txt
```

## Agent CLI Mappings

| Agent  | Non-interactive command | Stdin |
|--------|------------------------|-------|
| claude | `claude -p` | Yes - reads from stdin when no positional prompt given |
| codex  | `codex exec -` | Yes - `-` reads from stdin |
| agy | `agy --add-dir <dir> -p "Read <file>..."` | **No** - no stdin mode; the script stages the prompt in a private dir and passes the path |

## Notes

- Response is synchronous - the calling agent waits for the response
- Useful for getting a second opinion or different perspective
- Each agent has different training data and reasoning patterns
- Prompts are piped via stdin to `claude` and `codex` (never as CLI args)
- **`agy` has no stdin mode**, so the script stages the prompt in a private
  0700 temp directory and passes agy a *path* plus `--add-dir`, letting agy
  read it with its own tools. Only the path reaches argv, so there is no size
  cap (verified reading a 251031-byte prompt in full) and no prompt content in
  `ps` / `/proc/<pid>/cmdline`.
  - Do not "simplify" this to `agy -p "$prompt"`. That reintroduces the
    128 KiB `MAX_ARG_STRLEN` cap and exposes the prompt to every local account
    for as long as agy runs.
  - `--add-dir` is required: without it agy auto-denies its own `read_file`
    in headless mode and exits with no output. Loud, not silent.
  - The staged prompt ends with a per-run marker that agy is told to reproduce
    as the last line of its reply. A reply without it means agy may have
    answered without reading the prompt to the end, so the script says so on
    stderr and **exits 3**. The reply is still printed (marker stripped), but
    treat exit 3 as "this answer may be based on a partial prompt", not as a
    clean result.
