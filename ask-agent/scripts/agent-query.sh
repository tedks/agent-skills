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
#   -r, --resume <id>            Continue an existing session instead of a new one
#   -S, --session-id-file <file> Write the session id this run YIELDED to <file>
#
# For claude and codex the prompt is piped via stdin, never passed as a CLI
# argument, which avoids ARG_MAX limits on execve(2).
#
# agy has no stdin mode. Rather than put the prompt in argv -- which would cap
# it at 128 KiB and expose it in `ps` -- the script stages it in a private temp
# directory and passes agy a path plus --add-dir. See the agy branch below.
#
# --resume maps to each agent's own continuation flag (claude --resume, codex
# exec resume, agy --conversation). All three APPEND to the existing session
# rather than forking it, so the yielded id equals the requested one. The
# non-mutating alternatives -- `codex exec fork`, `claude --fork-session` -- are
# deliberately not wrapped: a council round wants the seat's history to
# accumulate. See docs/ask-agent-session-resume.md for the evidence.
#
# --session-id-file exists because two of the three agents only reveal the id
# they generated in output the caller cannot reliably parse. The file gets the
# bare id and a newline, nothing else; it is truncated at startup so a failed
# run can never leave a stale id there for the next round to chain onto.
#
# Exit status is the agent's own, with two additions:
#   3  agy exited 0 but its reply did not carry the end-of-prompt sentinel, i.e.
#      it may have answered without reading the staged prompt to the end.
#   4  --resume was asked for and the run did not demonstrably land in that
#      session. Only reachable for agy, which answers an unknown conversation id
#      by silently starting a NEW conversation and exiting 0 (codex and claude
#      both exit 1 on an unknown id, so their own status already says so).
# Both print the reply anyway; both mean "this answer is not what you asked for".
#
# Examples:
#   agent-query.sh claude "Explain this error"
#   agent-query.sh codex -d ./project "Review the auth module"
#   agent-query.sh codex --model o3 "Optimize this function"
#   agent-query.sh agy "Summarize this codebase"
#   agent-query.sh claude --prompt-file /tmp/review-prompt.txt
#   agent-query.sh codex --session-id-file /tmp/sid "Round 1: review this diff"
#   agent-query.sh codex --resume "$(cat /tmp/sid)" "Round 2: re-check your findings"

set -e

usage() {
    echo "Usage: agent-query.sh <agent> [options] <prompt>" >&2
    echo "       agent-query.sh <agent> [options] --prompt-file <file>" >&2
    echo "Agents: claude, codex, agy" >&2
    echo "Options:" >&2
    echo "  -d, --dir <dir>              Set working directory" >&2
    echo "  -m, --model <model>          Specify model" >&2
    echo "  -f, --prompt-file <file>     Read prompt from file" >&2
    echo "  -r, --resume <id>            Continue an existing session" >&2
    echo "  -S, --session-id-file <file> Write the yielded session id to <file>" >&2
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
resume=""
resume_given=""
session_id_file=""
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
        -r|--resume)
            resume="$2"
            resume_given=1
            shift 2
            ;;
        -S|--session-id-file)
            session_id_file="$2"
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

# A session id reaches argv of the downstream CLI, so it is checked before it
# gets there rather than trusted. Nothing here is about shell injection -- every
# command is built as an array and never eval'd -- it is about the two ways a
# malformed id turns into a wrong answer rather than an error:
#
#   * an id starting with "-" is read by the agent as a FLAG. `--resume -m` would
#     silently become a model override on a fresh session, i.e. exactly the cold
#     context this option exists to avoid, with no diagnostic.
#   * whitespace or a newline breaks the id back out of its own line, which is
#     the format --session-id-file promises and the agy comparison below relies
#     on -- an id with a trailing newline would never compare equal to the one
#     agy logs, turning every resume into a spurious exit 4.
#
# UUIDs are what all three actually mint, but codex also accepts a thread NAME
# where an id goes, so the class is kept a little wider than UUID and no wider.
#
# An EMPTY id is rejected rather than quietly meaning "fresh". The intended
# calling shape is `--resume "$(cat sid)"`, and that sid file is deliberately
# left EMPTY whenever a previous round could not determine its id -- so treating
# empty as "no resume requested" would turn the one case that most needs a
# diagnostic into a silent cold-context run. Which is the whole failure this
# option exists to prevent, arrived at from the other direction.
if [[ -n "$resume_given" ]]; then
    if [[ -z "$resume" ]]; then
        echo "Error: --resume was given an empty id (an empty --session-id-file from a previous round?)" >&2
        exit 1
    fi
    if [[ "$resume" != [A-Za-z0-9]* || "$resume" =~ [^A-Za-z0-9._-] ]]; then
        echo "Error: --resume id must start alphanumeric and hold only [A-Za-z0-9._-]: $resume" >&2
        exit 1
    fi
fi

# Normalize: ensure we always have a prompt_file to pipe from.
# This avoids passing the prompt as a CLI argument to the downstream
# agent, which would hit ARG_MAX for large prompts.
_cleanup_prompt_file=""

# The agy path stages a copy of the prompt in a private directory (see the agy
# branch). Unlike the claude/codex path it does NOT exec, so this trap is what
# actually removes it -- on success, failure, and signals alike.
_cleanup_agy_dir=""

# The codex capture path tees codex's stderr to a file so the session id can be
# read out of the banner. That stream carries codex's echo of the PROMPT and of
# the reply, not just the banner, so the file is mode 0600 (mktemp's default)
# and removed here. A SIGKILL strands it, same residual class as the agy
# staging directory (ditz ask-agent-sigkill-staging-leak).
_cleanup_err_file=""

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
    if [[ -n "$_cleanup_err_file" ]]; then rm -f -- "$_cleanup_err_file" || true; fi
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

# Same treatment, same reason: --dir must not silently relocate the caller's
# file. The directory has to exist already; the file itself need not.
if [[ -n "$session_id_file" ]]; then
    session_id_dir="$(cd -- "$(dirname -- "$session_id_file")" 2>/dev/null && pwd)" || session_id_dir=""
    if [[ -z "$session_id_dir" ]]; then
        echo "Error: directory for --session-id-file does not exist: $session_id_file" >&2
        exit 1
    fi
    session_id_file="$session_id_dir/$(basename -- "$session_id_file")"

    # Truncate NOW, for two reasons. It proves the path is writable before an
    # API call is spent rather than after; and it guarantees that a run which
    # fails to determine an id leaves the file EMPTY rather than holding the
    # previous round's id. A stale id here is the worst outcome available: the
    # caller would chain onto a session that answered a different question and
    # never learn it did.
    if ! : > "$session_id_file"; then
        echo "Error: cannot write --session-id-file: $session_id_file" >&2
        exit 1
    fi
fi

# Records the session id this run yielded, in both places a caller might look:
# the file it asked for (bare id + newline, the whole contents) and stderr, for
# whoever is reading the transcript. stdout is deliberately untouched -- it
# carries the reply and nothing else.
emit_session_id() {
    if [[ -n "$session_id_file" ]]; then
        printf '%s\n' "$1" > "$session_id_file"
    fi
    echo "ask-agent: session id: $1" >&2
}

# Shared by the minting path and by every id read back out of an agent, because
# an id is only useful if the NEXT round can pass it back in -- and --resume
# above will reject anything that is not shaped like this.
uuid_re='^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$'

# claude is the one agent that lets the CALLER choose the session id, which is
# strictly better than parsing one back out: the id is known before the run
# starts, so nothing has to survive the exec to report it.
# /proc first -- always present on Linux, no fork, no dependency on util-linux
# being installed -- with uuidgen as the fallback. The result is validated
# because a silently empty id would become `--session-id ''`.
new_uuid() {
    local u=""
    if [[ -r /proc/sys/kernel/random/uuid ]]; then
        read -r u < /proc/sys/kernel/random/uuid || u=""
    fi
    if [[ ! "$u" =~ $uuid_re ]]; then
        u="$(uuidgen 2>/dev/null || true)"
    fi
    [[ "$u" =~ $uuid_re ]] || return 1
    printf '%s' "$u"
}

# Wait for background child $1, forwarding signals to it, and set `status` to
# its exit status. Extracted from the agy path, which has always needed it, so
# that the codex capture path below does not carry a second copy of logic this
# subtle. Both use it for the same reason: neither can `exec`, so neither gets
# exec's signal transparency for free, and callers DO run this under `timeout`.
# Without forwarding, a timeout kills only the wrapper and leaves the agent
# orphaned -- still running, still billing, and in agy's case still holding the
# staging directory the EXIT trap is about to delete underneath it.
#
# Forward the actual signal, not a translated TERM. HUP matters: these run from
# tmux, where a hangup delivers HUP. (INT/QUIT are inert in background --
# SIG_IGN on entry -- and redundant in a terminal.)
_fwd_pid=""
_fwd_signalled=""
_forward_signal() { _fwd_signalled=1; kill -"$1" "$_fwd_pid" 2>/dev/null || true; }
wait_forwarding_signals() {
    _fwd_pid="$1"
    for _fwd_sig in HUP INT TERM QUIT; do
        # shellcheck disable=SC2064  # expand _fwd_sig now, at trap definition
        trap "_forward_signal $_fwd_sig" "$_fwd_sig"
    done
    unset _fwd_sig

    # A trapped signal makes `wait` return >128 WITHOUT reaping, so we must
    # wait again or the EXIT trap deletes the staging dir under a live agent.
    # Tell that from a real signal death by the trap's flag, never `kill -0`:
    # a reaped PID can be recycled, and probing it spins forever on the
    # remembered status.
    # Re-waiting relies on bash remembering a reaped child's status and
    # returning it again rather than failing "not a child" (bash >= 5.1 --
    # verified on 5.2.21: three successive waits on the same reaped PID all
    # returned 143). An earlier version carried a 127 fallback for shells
    # that discard it; on this fleet that branch was unreachable except in
    # the one case where it did harm -- a child that ignored our signal and
    # then genuinely exited 127 was reported as 143.
    status=0
    _fwd_signalled=""
    wait "$_fwd_pid" || status=$?
    while (( status > 128 )) && [[ -n "$_fwd_signalled" ]]; do
        _fwd_signalled=""
        status=0
        wait "$_fwd_pid" || status=$?
    done

    # Disarm before the caller touches the reply: from here on $_fwd_pid is
    # reaped, so a late signal would fire `kill` at a PID that may already
    # belong to someone else -- the same reuse hazard the loop above avoids on
    # the `kill -0` side. The window is however long the caller's handling takes.
    trap - HUP INT TERM QUIT
}

# Change directory if specified (must happen after canonicalization)
if [[ -n "$dir" ]]; then
    cd "$dir"
fi

# Build command array. claude and codex read the prompt from stdin, which keeps
# it out of argv and sidesteps the per-argument length cap. agy cannot -- see
# the agy branch -- so it is handled separately below.
prompt_via_file=""

# Set when the id is known WITHOUT having to read anything back out of the
# agent, which is every case but two (codex starting fresh, and agy always).
known_sid=""
# Set when the only way to learn the id is to capture the agent's own output.
capture_sid=""

case "$agent" in
    claude)
        cmd=(claude -p)
        if [[ -n "$resume" ]]; then
            # Appends to the existing session and keeps its id -- verified: the
            # project directory still held exactly one jsonl afterwards.
            cmd+=(--resume "$resume")
            known_sid="$resume"
        elif [[ -n "$session_id_file" ]]; then
            known_sid="$(new_uuid)" || {
                echo "Error: could not generate a session id for --session-id-file" >&2
                exit 1
            }
            cmd+=(--session-id "$known_sid")
        fi
        [[ -n "$model" ]] && cmd+=(--model "$model")
        ;;
    codex)
        if [[ -n "$resume" ]]; then
            # `codex exec resume` is NOT `codex exec` plus an id: it rejects
            # -s/--sandbox, -C/--cd, --add-dir, --approve-for-me and -p/--profile.
            # -m is accepted by both, which is the only option this script adds,
            # so the two paths stay interchangeable -- but anything added below
            # has to be checked against `codex exec resume --help` as well.
            # Resume appends to the original rollout in place (14 -> 26 lines of
            # the same file, no new one) and reports the same id back, so there
            # is nothing to capture.
            cmd=(codex exec resume "$resume" -)
            known_sid="$resume"
        else
            cmd=(codex exec -)
            # A fresh codex session mints an id we can only learn from codex's
            # own banner, so this is the one path that has to give up `exec`.
            # Only when the caller actually asked for the id: the cost is real
            # and nobody else should pay it.
            [[ -n "$session_id_file" ]] && capture_sid=1
        fi
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
        # agy never reveals a conversation id on stdout or stderr, only in its
        # CLI log -- so unlike codex there is no "fresh vs resume" split here:
        # BOTH need the log. Resume needs it even when the caller did not ask
        # for an id, because agy answers an unknown conversation id by silently
        # starting a new one and exiting 0 (see the check after the run).
        if [[ -n "$resume" ]]; then
            cmd+=(--conversation "$resume")
        fi
        if [[ -n "$resume" || -n "$session_id_file" ]]; then
            capture_sid=1
        fi
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
    # The prompt lives one level DOWN from the private root, so that the root
    # can also hold the CLI log without widening the --add-dir grant: agy is
    # granted $agy_dir, which contains the prompt and nothing else, exactly as
    # before. Only the root is 0700-by-mktemp, which is what actually bounds
    # access -- the inner mode is belt-and-braces against a future refactor
    # moving it out from under the private root.
    agy_priv="$(mktemp -d /tmp/ask-agent-agy.XXXXXX)"   # mktemp -d is already 0700
    _cleanup_agy_dir="$agy_priv"                        # one rm -rf covers both
    agy_dir="$agy_priv/prompt"
    mkdir -m 700 -- "$agy_dir"
    agy_prompt="$agy_dir/prompt.txt"

    # agy prints no id anywhere the caller can see it, but --log-file redirects
    # the CLI log, and print mode logs the conversation id there on both fresh
    # and resumed runs. Verified to be a COMPLETE redirect: nothing is left in
    # ~/.gemini/antigravity-cli/log/ and the cli.log symlink is not repointed.
    #
    # That completeness is why this is conditional. Redirecting a log the user
    # would otherwise still have after a failure is a real (if small) cost, and
    # a plain one-shot query has no use for the id, so it does not pay it.
    #
    # The log is created 0644 by agy and we cannot change that, so it goes
    # inside the 0700 root rather than anywhere a mode matters. It records
    # promptLength but not the prompt itself -- checked by grepping a real log
    # for three separate strings from the prompt it had just been given.
    agy_log=""
    if [[ -n "$capture_sid" ]]; then
        agy_log="$agy_priv/cli.log"
        cmd+=(--log-file "$agy_log")
    fi

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

    # "with your built-in file-reading tool, not a shell command" is load-bearing,
    # not politeness: --add-dir grants a directory READ, and nothing else. If agy
    # decides to `cat` the file instead, that needs the "command" permission,
    # which headless mode cannot prompt for and therefore auto-denies -- the run
    # produces no output at all. Steering the tool choice is the cheap half of
    # the fix; the retry below is the other half.
    cmd+=(--add-dir "$agy_dir" -p "Read the file $agy_prompt in full, using your built-in file-reading tool -- do not use a shell command such as cat or head to read it. Follow the instructions in it. Treat its entire contents as your prompt, except for its final line, which is an end-of-prompt marker rather than part of the prompt. Do not ask for confirmation before reading it. When you have finished, reproduce that final marker line verbatim, on a line of its own, as the very last line of your reply, so the caller can confirm you read the whole prompt.")

    # NOT exec'd: agy must read the staging file for its whole run, so this
    # script outlives it to clean up -- forfeiting exec's signal transparency,
    # hence the forwarding below. Without it a `timeout` (callers do use one)
    # deletes the staging directory under a live agy and orphans it.
    #
    # stdout is captured, not inherited, so the marker can be checked and
    # stripped. Kept OUT of $agy_dir ("nothing but this prompt" is what bounds
    # the --add-dir grant) and unlinked once both fds are open, as the stdin
    # path below does, so a SIGKILL cannot strand the REPLY. The staging
    # directory is a different matter: agy has to open the prompt by path, so it
    # cannot be unlinked in use, and a SIGKILL does strand it (ditz
    # ask-agent-sigkill-staging-leak).
    # Let bash allocate the fds rather than naming 4 and 5, which a caller may
    # already be using for something of its own.
    # Bounded retry: even with the steer above, agy sometimes answers by shelling
    # out, and headless mode auto-denies the "command" permission it would need,
    # leaving a run that exits 0 having produced nothing. Which tool it reaches
    # for is a model decision that varies run to run, so one retry clears it.
    # Keyed on the auto-deny text specifically, NOT on exit 3 in general -- a
    # blanket retry would paper over genuine partial reads, which is the failure
    # this whole mechanism exists to surface.
    for agy_attempt in 1 2; do
        agy_out="$(mktemp /tmp/ask-agent-agy-out.XXXXXX)"
        exec {agy_w}> "$agy_out" {agy_r}< "$agy_out"
        rm -f -- "$agy_out"

        "${cmd[@]}" >&"$agy_w" &
        agy_pid=$!
        exec {agy_w}>&-   # only the child needs the write end now

        wait_forwarding_signals "$agy_pid"   # sets `status`; see the definition

        response="$(cat <&"$agy_r")"   # command substitution drops trailing \n
        exec {agy_r}<&-                # not into the next attempt

        # The headless auto-deny arrives in two shapes, and the retry has to
        # catch both without catching a real answer:
        #
        #   HARD  agy aborts. stdout is EMPTY, the jetski text goes to stderr
        #         (which we do not capture -- it passes through live, and we
        #         want that), exit 0.
        #   SOFT  agy carries on and puts the denial text in its own output.
        #
        # The marker's ABSENCE gates both. A genuine denial produced no answer,
        # so it cannot carry the marker; a real reply that merely quotes the
        # denial text -- which every "review ask-agent" prompt does, since that
        # phrase is a few lines above this one -- does carry it. Without this
        # gate a good reply was thrown away and re-asked, nondeterministically.
        #
        # Given that gate, "empty" is a safe trigger on its own and needs no
        # stderr capture: the marker is mandatory, so empty-and-exit-0 can never
        # be a valid reply.
        if (( agy_attempt == 1 && status == 0 )) &&
           [[ "$response" != *"$agy_sentinel"* ]]; then
            if [[ -z "$response" ]]; then
                echo "Note: agy produced no output and exited 0 (headless tool permission auto-denied); retrying once" >&2
                continue
            elif [[ "$response" == *"a tool required the"* && "$response" == *permission* ]]; then
                echo "Note: agy reported an auto-denied tool permission in its output (it tried to run a command rather than read the file); retrying once" >&2
                continue
            fi
        fi
        break
    done

    # Everything below runs on model-generated text built from an untrusted
    # prompt, AFTER agy has already answered, and callers run this under
    # `timeout`. So the string handling has to be linear-ish: burning seconds
    # here gets the wrapper killed and loses a reply that was already in hand
    # (the capture file is unlinked, so there is nothing left to recover).
    # ${x%"${x##*[![:space:]]}"} is O(len x trailing-ws-run) -- 18.0s on a 200KB
    # reply with 1000 trailing spaces. The regex is 20ms on the same input.
    _agy_rtrim() {   # sets _rtrim_out; no subshell, no fork
        # Trailing whitespace has to be trimmed correctly in two situations that
        # pull in opposite directions, so this is three passes rather than one.
        #
        # Under a UTF-8 locale glibc's regexec REFUSES to match a string holding
        # an invalid multibyte sequence -- and agy killed mid-reply ends inside a
        # character routinely, so that is the `timeout` path. Treating no-match
        # as "all whitespace" therefore blanked whole replies. But a byte locale
        # stops [[:space:]] matching U+2003 / U+3000, and a reply of
        # "<marker><EM SPACE>" then survives as invisible content, slipping past
        # the marker-only guard with status 0.
        #
        # So: trim multibyte-aware, and fall back to bytes only for input that
        # provably cannot be decoded. A MATCH is the only thing that rewrites
        # anything, so a refused match can never blank a reply.

        # Passes 1-2 PIN C.UTF-8 rather than inheriting the caller's locale.
        # They have to be multibyte-aware, and the caller's locale is not
        # reliably UTF-8: LANG unset (cron, systemd units, `ssh host cmd`,
        # `docker exec`, `sudo` with env_reset) lands in C, and so does LANG
        # naming a locale that was never generated. Under a byte locale the
        # named characters below decompose into their individual bytes and the
        # class eats any reply whose last character ends in one of them.
        # C.UTF-8 has the same space class as any UTF-8 locale and still refuses
        # invalid multibyte, so pass 3 stays reachable.
        local LC_ALL=C.UTF-8

        # glibc deliberately excludes the NON-BREAKING spaces from [[:space:]]
        # -- U+00A0, U+202F and U+FEFF are Zs/format characters that isspace()
        # says no to -- so the class has to name them. Without that,
        # "<marker><NBSP>" survives as one invisible character, the reply is not
        # empty, and it slips past the marker-only guard with status 0.
        # (U+2003, U+3000 and U+2009 ARE space-class and need no help.)
        #
        # Escapes rather than literals: these are invisible in an editor, and a
        # maintainer reflowing the line could delete them with no test failing
        # outside the marker-plus-NBSP cases.
        local _nb=$'\u00a0\u202f\ufeff'

        # If C.UTF-8 was unavailable bash has fallen back to a byte locale and
        # _nb is 8 bytes rather than 3 characters. Drop the named characters
        # rather than let the class shred multibyte tails: an ASCII-only trim
        # loses less than silent corruption does.
        (( ${#_nb} == 3 )) || _nb=""

        local _re_trim="^(.*[^[:space:]${_nb}])[[:space:]${_nb}]*\$"
        local _re_blank="^[[:space:]${_nb}]*\$"

        # 1. Full Unicode trim, the common case.
        if [[ "$1" =~ $_re_trim ]]; then
            _rtrim_out="${BASH_REMATCH[1]}"
            return
        fi
        # 2. Genuinely nothing but whitespace (Unicode too).
        if [[ "$1" =~ $_re_blank ]]; then
            _rtrim_out=""
            return
        fi
        # 3. Neither matched, so the bytes do not decode. Under C every byte is
        #    valid; trim ASCII whitespace and keep the content. Plain
        #    [[:space:]] here ON PURPOSE -- naming multibyte characters in a
        #    class evaluated byte-wise is the corruption this pass exists to
        #    avoid. LC_ALL is already function-local, so this reassignment does
        #    not leak (`local` is function-scoped, not block-scoped).
        LC_ALL=C
        if [[ "$1" =~ ^(.*[^[:space:]])[[:space:]]*$ ]]; then
            _rtrim_out="${BASH_REMATCH[1]}"
        else
            _rtrim_out=""
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

    # Which conversation did this run actually use? Parse the `printmode.go`
    # line rather than `Created conversation`: the former is logged on fresh AND
    # resumed runs, so one pattern covers both, while the latter only appears
    # when a new conversation is made.
    #
    # LAST match, not first: the retry above can put two agy runs into this same
    # log, and whether agy appends or truncates --log-file is not documented.
    # Taking the last is correct either way, and the last run is the one whose
    # reply we are holding.
    agy_conv=""
    if [[ -n "$agy_log" && -f "$agy_log" ]]; then
        agy_conv="$(grep -oE 'Print mode: conversation=[0-9a-fA-F-]+' "$agy_log" 2>/dev/null | tail -n 1)"
        agy_conv="${agy_conv#Print mode: conversation=}"
        # A log-format change in a future agy would leave a partial match here.
        # Better to report no id than a wrong one, since the whole point is to
        # hand this to the next round.
        [[ "$agy_conv" =~ $uuid_re ]] || agy_conv=""
    fi

    if [[ -n "$agy_conv" ]]; then
        emit_session_id "$agy_conv"
    elif [[ -n "$session_id_file" ]]; then
        echo "Error: could not read a conversation id out of agy's log; --session-id-file left empty" >&2
    fi

    # THE guard this whole option needs. agy answers an unknown conversation id
    # by silently starting a NEW conversation and exiting 0 -- verified: a
    # bogus id produced a clean answer, exit 0, empty stderr, and a brand-new
    # .db file. codex and claude both exit 1 in the same situation, so only agy
    # can lie about it.
    #
    # For a fixpoint council that is the worst failure available: round 3 thinks
    # it is talking to the reviewer that already settled rounds 1-2, and gets a
    # stranger who re-litigates them, with nothing to indicate why. So an
    # unverified resume is treated as a failed one.
    #
    # Not being able to determine the id counts as a mismatch on purpose. The
    # alternative -- proceeding on the assumption that a resume we cannot
    # confirm probably worked -- is exactly the silent failure above, just with
    # an extra step. The reply is still printed and status 3 still wins if the
    # sentinel was also missing: a partial read is the more fundamental problem.
    if [[ -n "$resume" ]]; then
        if [[ -z "$agy_conv" ]]; then
            echo "Error: cannot confirm agy resumed $resume (no conversation id in its log); treat this reply as cold context" >&2
            if (( status == 0 )); then status=4; fi
        elif [[ "$agy_conv" != "$resume" ]]; then
            echo "Error: agy did NOT resume $resume -- it started $agy_conv instead, so this reply has no memory of the earlier rounds" >&2
            if (( status == 0 )); then status=4; fi
        fi
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

# Ids we already know are reported BEFORE the run, because for the claude fresh
# case there is no later moment: the id exists only because we minted it and put
# it in argv, and the exec below never returns.
#
# The consequence to be honest about: for these cases the file records the
# session the run was ASKED to use, so a run that dies before the agent creates
# the session leaves an id for a session that never existed. That is survivable
# precisely because claude and codex both exit 1 on an unknown id -- the next
# round fails loudly instead of quietly going cold. (agy, which does not, is
# handled the other way round: its id is read back out and verified.)
if [[ -n "$known_sid" ]]; then
    emit_session_id "$known_sid"
fi

if [[ -z "$capture_sid" ]]; then
    exec "${cmd[@]}" <&3
fi

# --- codex, starting fresh, with the id requested ---
# The only path here that cannot exec: codex mints the id and announces it in
# its banner, so something has to outlive the run to read it. Everything else
# above keeps exec. See docs/ask-agent-session-resume.md for the case analysis.
#
# The banner goes to STDERR -- verified: stdout carried the reply and nothing
# else -- so capturing it cannot contaminate the reply. stdout is not touched
# at all here; it stays inherited and streams exactly as it did before.
codex_err="$(mktemp /tmp/ask-agent-codex-err.XXXXXX)"   # mktemp is 0600
_cleanup_err_file="$codex_err"

# tee rather than a plain `2>file`: codex's stderr is the run's only progress
# output, callers watch it live, and a plain redirect would swallow all of it
# and then lose it outright if `timeout` killed the run.
exec {tee_fd}> >(tee -- "$codex_err" >&2)
tee_pid=$!                       # must be read here: the `&` below overwrites $!

"${cmd[@]}" <&3 2>&"$tee_fd" &
codex_pid=$!
exec {tee_fd}>&-                 # leave the child holding the only write end,
                                 # so tee sees EOF when codex exits, not later

wait_forwarding_signals "$codex_pid"   # sets `status`

# bash sets $! to the pid of a process substitution and `wait` accepts it
# (checked on 5.2.21), which is what makes the read below ordered rather than a
# race against tee's last flush. Deliberately not load-bearing: a bash that left
# $! unset would skip the wait and at worst miss the id -- there is no path here
# that corrupts or withholds the reply, which never went through tee at all.
if [[ -n "$tee_pid" ]]; then
    wait "$tee_pid" 2>/dev/null || true
fi

codex_sid="$(grep -m1 -oE 'session id: [0-9a-fA-F-]+' "$codex_err" 2>/dev/null || true)"
codex_sid="${codex_sid#session id: }"
# Validated, not trusted: a banner change should yield "no id" rather than a
# truncated one that the next round would pass to --resume.
[[ "$codex_sid" =~ $uuid_re ]] || codex_sid=""
if [[ -n "$codex_sid" ]]; then
    emit_session_id "$codex_sid"
else
    echo "Error: could not read a session id out of codex's banner; --session-id-file left empty" >&2
fi

exit "$status"
