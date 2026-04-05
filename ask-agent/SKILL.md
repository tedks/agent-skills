---
name: ask-agent
description: Ask another AI agent a question and get the response back (subagent pattern)
argument-hint: <agent> [options] <prompt>
allowed-tools: Bash(~/.claude/skills/ask-agent/scripts/*)
---

# ask-agent

Query another AI agent non-interactively and get the response back. Use this
when you want a second opinion from a different agent (Claude, Codex, Gemini, etc.).

## Usage

```
/ask-agent <agent> [options] <prompt>
```

## Arguments

- `<agent>`: The agent to query (`claude`, `codex`, `gemini`)
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

The script pipes the prompt via stdin to the downstream agent, so even inline
prompts are safe at the script-to-agent boundary. The --prompt-file approach
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

# Ask Gemini for a summary
/ask-agent gemini "Summarize this codebase"

# Ask Gemini with a specific model
/ask-agent gemini -m gemini-2.5-pro "Review this architecture"

# Use a prompt file for large prompts (avoids ARG_MAX)
/ask-agent codex --prompt-file /tmp/review-prompt.txt
```

## Agent CLI Mappings

| Agent  | Non-interactive command | Stdin |
|--------|------------------------|-------|
| claude | `claude -p` | Yes - reads from stdin when no positional prompt given |
| codex  | `codex exec -` | Yes - `-` reads from stdin |
| gemini | `gemini -o text` | Yes - reads from stdin when no `-p` flag given |

## Notes

- Response is synchronous - the calling agent waits for the response
- Useful for getting a second opinion or different perspective
- Each agent has different training data and reasoning patterns
- Prompts are piped via stdin to downstream agents (never as CLI args)
