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
- `-f, --prompt-file <file>`: Read prompt from a file instead of inline (use for large prompts to avoid ARG_MAX limits)

## Instructions

When this skill is invoked, **always pass the prompt via a temp file** to avoid
ARG_MAX errors with large prompts:

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

The script pipes the prompt via stdin to `claude` and `codex`, so even inline
prompts are safe at the script-to-agent boundary for those two. `agy` is the
exception -- it has no stdin mode and its prompt travels in argv, subject to
the 120000-byte cap and `ps` visibility described under Notes. The --prompt-file approach
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
| agy | `agy -p <prompt>` | **No** - agy has no stdin mode; prompt goes in argv (see caveats) |

## Notes

- Response is synchronous - the calling agent waits for the response
- Useful for getting a second opinion or different perspective
- Each agent has different training data and reasoning patterns
- Prompts are piped via stdin to `claude` and `codex` (never as CLI args)
- **`agy` is the exception**: it has no stdin mode, so its prompt is passed in
  argv. Two consequences, both enforced/documented by the script:
  - **Size**: capped at 120000 bytes (Linux `MAX_ARG_STRLEN` is 131072 for a
    single argv entry). Over that the script refuses with an explanatory error.
    `--prompt-file` does NOT lift this for agy -- it only changes where the
    script reads the prompt from, not how agy receives it.
  - **Privacy**: an argv prompt is readable via `ps` and `/proc/<pid>/cmdline`
    by any local account while agy runs. Prefer `claude` or `codex` for
    prompts containing private source.
