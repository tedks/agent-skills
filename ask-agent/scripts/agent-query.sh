#!/bin/bash
# agent-query.sh - Query an AI agent non-interactively (piped mode)
#
# Usage: agent-query.sh <agent> [options] <prompt>
#        agent-query.sh <agent> [options] --prompt-file <file>
#
# Agents: claude, codex, agy (Antigravity CLI)
#
# Options:
#   -d, --dir <dir>              Set working directory
#   -m, --model <model>          Specify model (agent-specific)
#   -f, --prompt-file <file>     Read prompt from file (avoids ARG_MAX)
#
# For claude and codex the prompt is piped via stdin, never passed as a CLI
# argument, which avoids ARG_MAX limits on execve(2).
#
# agy has no stdin mode. Rather than put the prompt in argv -- which would cap
# it at 128 KiB and expose it in `ps` -- the script stages it in a private temp
# directory and passes agy a path plus --add-dir. See the agy branch below.
#
# Exit status is the agent's own, with one addition: 3 means agy exited 0 but
# its reply did not carry the end-of-prompt sentinel, i.e. it may have answered
# without reading the staged prompt to the end. See the agy branch.
#
# Examples:
#   agent-query.sh claude "Explain this error"
#   agent-query.sh codex -d ./project "Review the auth module"
#   agent-query.sh codex --model o3 "Optimize this function"
#   agent-query.sh agy "Summarize this codebase"
#   agent-query.sh claude --prompt-file /tmp/review-prompt.txt

set -e

usage() {
    echo "Usage: agent-query.sh <agent> [options] <prompt>" >&2
    echo "       agent-query.sh <agent> [options] --prompt-file <file>" >&2
    echo "Agents: claude, codex, agy" >&2
    echo "Options:" >&2
    echo "  -d, --dir <dir>              Set working directory" >&2
    echo "  -m, --model <model>          Specify model" >&2
    echo "  -f, --prompt-file <file>     Read prompt from file" >&2
    exit 1
}

if [[ $# -lt 2 ]]; then
    usage
fi

agent="$1"
shift

# Parse options
dir=""
model=""
prompt_file=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -d|--dir)
            dir="$2"
            shift 2
            ;;
        -m|--model)
            model="$2"
            shift 2
            ;;
        -f|--prompt-file)
            prompt_file="$2"
            shift 2
            ;;
        -*)
            echo "Unknown option: $1" >&2
            usage
            ;;
        *)
            break
            ;;
    esac
done

# Normalize: ensure we always have a prompt_file to pipe from.
# This avoids passing the prompt as a CLI argument to the downstream
# agent, which would hit ARG_MAX for large prompts.
_cleanup_prompt_file=""

# The agy path stages a copy of the prompt in a private directory (see the agy
# branch). Unlike the claude/codex path it does NOT exec, so this trap is what
# actually removes it -- on success, failure, and signals alike.
_cleanup_agy_dir=""

# Trap to clean up temp files on any exit (failure paths).
# On the claude/codex success path, we manually delete before exec, because
# exec replaces this process and the trap would never fire.
# An EXIT trap's status BECOMES the script's, overriding even an explicit
# `exit N`. Cleanup must therefore be unable to fail: a removal that errored
# would rewrite the sentinel's 3 -- and 143/129/137 -- to a meaningless 1.
# Both halves are needed. `|| true` keeps `set -e` from killing the trap at the
# failing rm, and `return 0` covers the guards themselves being false.
cleanup() {
    if [[ -n "$_cleanup_prompt_file" ]]; then rm -f "$_cleanup_prompt_file" || true; fi
    if [[ -n "$_cleanup_agy_dir" ]]; then rm -rf -- "$_cleanup_agy_dir" || true; fi
    return 0
}
trap cleanup EXIT

if [[ -z "$prompt_file" ]]; then
    prompt="$*"
    if [[ -z "$prompt" ]]; then
        echo "Error: prompt required (positional arg or --prompt-file)" >&2
        usage
    fi
    prompt_file=$(mktemp /tmp/agent-query-prompt.XXXXXX)
    _cleanup_prompt_file="$prompt_file"
    printf '%s' "$prompt" > "$prompt_file"
else
    if [[ ! -f "$prompt_file" ]]; then
        echo "Error: prompt file not found: $prompt_file" >&2
        exit 1
    fi
    if [[ ! -s "$prompt_file" ]]; then
        echo "Error: prompt file is empty: $prompt_file" >&2
        exit 1
    fi
fi

# Canonicalize prompt_file to an absolute path so that a subsequent
# cd (via --dir) doesn't break a relative path.
prompt_file="$(cd -- "$(dirname -- "$prompt_file")" && pwd)/$(basename -- "$prompt_file")"

# Change directory if specified (must happen after canonicalization)
if [[ -n "$dir" ]]; then
    cd "$dir"
fi

# Build command array. claude and codex read the prompt from stdin, which keeps
# it out of argv and sidesteps the per-argument length cap. agy cannot -- see
# the agy branch -- so it is handled separately below.
prompt_via_file=""

case "$agent" in
    claude)
        cmd=(claude -p)
        [[ -n "$model" ]] && cmd+=(--model "$model")
        ;;
    codex)
        cmd=(codex exec -)
        [[ -n "$model" ]] && cmd+=(-m "$model")
        ;;
    agy)
        # agy has no stdin mode, and `agy -p -` does not error -- it takes "-" as
        # the prompt and discards stdin, exiting 0 with a plausible answer to
        # nothing (agy 1.1.3). So agy gets a PATH and reads the prompt itself: an
        # argv prompt would cap at MAX_ARG_STRLEN (128 KiB, which review prompts
        # carrying a diff exceed) and expose private source in `ps`. -p consumes
        # the next argv entry, so the instruction is appended LAST, after flags.
        cmd=(agy)
        prompt_via_file=1
        [[ -n "$model" ]] && cmd+=(--model "$model")
        ;;
    *)
        echo "Unknown agent: $agent" >&2
        echo "Supported agents: claude, codex, agy" >&2
        exit 1
        ;;
esac

if [[ -n "$prompt_via_file" ]]; then
    # --add-dir grants a whole DIRECTORY, so the prompt is copied into a private
    # one holding nothing else rather than granting wherever a caller's
    # --prompt-file lives. Fixed /tmp, not $TMPDIR: it is interpolated into the
    # instruction below, and a relative value resolves after the `cd` above.
    agy_dir="$(mktemp -d /tmp/ask-agent-agy.XXXXXX)"   # mktemp -d is already 0700
    _cleanup_agy_dir="$agy_dir"
    agy_prompt="$agy_dir/prompt.txt"

    # Nothing forces agy to read the file to its end, and a partial read answers
    # plausibly and exits 0. So the file ends with a marker agy must echo back
    # (checked below). Nothing that reaches argv may allow the marker to be
    # RECONSTRUCTED, or agy could produce it unread and the check would prove
    # nothing. That rules out deriving it from the staging directory: the
    # instruction below has to name the path, and a prompt containing this
    # script -- every council review of ask-agent itself -- would hand over the
    # derivation rule to go with it. Independent randomness instead.
    #
    # The pipeline's status is tr's, so a failing od would sail straight past
    # `set -e` and leave "__ASK_AGENT_EOP___" -- a constant, published in this
    # repo, reconstructible from any prompt that quotes this script. That is the
    # hole above, silently reopened. Assert the randomness rather than trust it.
    agy_rand="$(od -An -N12 -tx1 /dev/urandom | tr -d ' \n')"
    if (( ${#agy_rand} != 24 )); then
        echo "Error: could not generate a random completion sentinel" >&2
        exit 1
    fi
    agy_sentinel="__ASK_AGENT_EOP_${agy_rand}__"

    # The redirect belongs on the inner group, after umask: a redirect attached
    # to `( ... )` is performed at fork time, under the OUTER umask.
    # Nothing but the marker is appended: a explanatory line here would sit
    # ABOVE the final line, so by the instruction's own wording it would be part
    # of the prompt, and it would perturb any request with a strict output
    # format. The instruction is the only place the marker is explained.
    ( umask 077
      { cat -- "$prompt_file"
        printf '\n%s\n' "$agy_sentinel"
      } > "$agy_prompt"
    )

    cmd+=(--add-dir "$agy_dir" -p "Read the file $agy_prompt in full and follow the instructions in it. Treat its entire contents as your prompt, except for its final line, which is an end-of-prompt marker rather than part of the prompt. Do not ask for confirmation before reading it. When you have finished, reproduce that final marker line verbatim, on a line of its own, as the very last line of your reply, so the caller can confirm you read the whole prompt.")

    # NOT exec'd: agy must read the staging file for its whole run, so this
    # script outlives it to clean up -- forfeiting exec's signal transparency,
    # hence the forwarding below. Without it a `timeout` (callers do use one)
    # deletes the staging directory under a live agy and orphans it.
    #
    # stdout is captured, not inherited, so the marker can be checked and
    # stripped. Kept OUT of $agy_dir ("nothing but this prompt" is what bounds
    # the --add-dir grant) and unlinked once both fds are open, as the stdin
    # path below does, so a SIGKILL cannot strand it.
    # Let bash allocate the fds rather than naming 4 and 5, which a caller may
    # already be using for something of its own.
    agy_out="$(mktemp /tmp/ask-agent-agy-out.XXXXXX)"
    exec {agy_w}> "$agy_out" {agy_r}< "$agy_out"
    rm -f -- "$agy_out"

    "${cmd[@]}" >&"$agy_w" &
    agy_pid=$!
    exec {agy_w}>&-   # only the child needs the write end now

    # Forward the actual signal, not a translated TERM. HUP matters: these run
    # from tmux, where a hangup delivers HUP. (INT/QUIT are inert in background
    # -- SIG_IGN on entry -- and redundant in a terminal; trapped for symmetry.)
    _agy_signalled=""
    _forward_to_agy() { _agy_signalled=1; kill -"$1" "$agy_pid" 2>/dev/null || true; }
    for _sig in HUP INT TERM QUIT; do
        # shellcheck disable=SC2064  # expand _sig now, at trap definition
        trap "_forward_to_agy $_sig" "$_sig"
    done
    unset _sig

    # A trapped signal makes `wait` return >128 WITHOUT reaping, so we must wait
    # again or the EXIT trap deletes the staging dir under a live agy. Tell that
    # from a real signal death by the trap's flag, never `kill -0`: a reaped PID
    # can be recycled, and probing it spins on the remembered status forever.
    status=0
    _agy_signalled=""
    wait "$agy_pid" || status=$?
    while (( status > 128 )) && [[ -n "$_agy_signalled" ]]; do
        _agy_signalled=""
        _prev_status=$status
        status=0
        wait "$agy_pid" 2>/dev/null || status=$?
        # Insurance for shells that discard a reaped child's status (bash 5.2
        # keeps it): "not a child" means the earlier wait already reaped agy.
        if (( status == 127 )); then
            status=$_prev_status
            break
        fi
    done

    response="$(cat <&"$agy_r")"   # command substitution drops trailing newlines

    # Everything below runs on model-generated text built from an untrusted
    # prompt, AFTER agy has already answered, and callers run this under
    # `timeout`. So the string handling has to be linear-ish: burning seconds
    # here gets the wrapper killed and loses a reply that was already in hand
    # (the capture file is unlinked, so there is nothing left to recover).
    # ${x%"${x##*[![:space:]]}"} is O(len x trailing-ws-run) -- 18.0s on a 200KB
    # reply with 1000 trailing spaces. The regex is 20ms on the same input.
    _agy_rtrim() {   # sets _rtrim_out; no subshell, no fork
        if [[ "$1" =~ ^(.*[^[:space:]])[[:space:]]*$ ]]; then
            _rtrim_out="${BASH_REMATCH[1]}"
        else
            _rtrim_out=""    # nothing but whitespace
        fi
    }

    # Trim the tail BEFORE splitting off the last line. Command substitution
    # dropped trailing newlines but nothing else, so a whitespace-only trailing
    # line -- one stray space -- would become "the last line", fail the check,
    # and reject a compliant reply while printing the raw marker.
    _agy_rtrim "$response"; response="$_rtrim_out"

    # The contract is "the marker, on the LAST line". Check exactly that, not
    # "appears anywhere": anywhere would accept a reply whose tail is wrong, and
    # stripping every occurrence would silently delete the marker out of the
    # middle of a legitimate reply that happens to quote it.
    #
    # Exit 0 without it is the failure this guard exists for. Distinct status
    # (3); the reply is still emitted, since withholding it would not save a
    # caller who ignores both stderr and the exit status.
    # Split off the last line by locating the final newline, not with
    # ${response##*$'\n'}: that is O(len x last-line-len) -- 9.0s on a 200KB
    # reply containing no newline at all, 1.8s on one with a 20k-char last line.
    # This form is 0.36s and 0.01s on the same two.
    agy_prefix="${response%$'\n'*}"
    if [[ "$agy_prefix" == "$response" ]]; then
        agy_prefix=""                              # single-line reply
        agy_last="$response"
        agy_joiner=""
    else
        agy_last="${response:${#agy_prefix}+1}"
        agy_joiner=$'\n'
    fi

    if [[ "$agy_last" == *"$agy_sentinel"* ]]; then
        # Remove the marker from that line alone. Not the whole line: a terse
        # model may put its answer and the marker on the same one. Rebuilt from
        # the prefix we already have, so no second scan of the whole reply.
        response="$agy_prefix$agy_joiner${agy_last//"$agy_sentinel"/}"
        _agy_rtrim "$response"; response="$_rtrim_out"
        # The marker and nothing else is not a clean run: it is the emptiest
        # possible answer wearing the proof-of-reading. Without this it would
        # be the only reply that returns 0 with no output at all.
        if [[ -z "$response" ]] && (( status == 0 )); then
            echo "Error: response was the completion sentinel and nothing else" >&2
            status=3
        fi
    elif (( status == 0 )); then
        echo "Error: response missing prompt-completion sentinel; prompt may have been read partially" >&2
        status=3
    fi

    if [[ -n "$response" ]]; then
        printf '%s\n' "$response"
    fi

    exit "$status"
fi

# Pipe prompt via stdin — this is the key ARG_MAX fix.
# We open the file descriptor, delete the temp file (if we created it),
# then exec. The fd survives exec; the unlinked file stays readable
# through the open fd until the process exits.
exec 3< "$prompt_file"
[[ -n "$_cleanup_prompt_file" ]] && rm -f "$_cleanup_prompt_file"
exec "${cmd[@]}" <&3
