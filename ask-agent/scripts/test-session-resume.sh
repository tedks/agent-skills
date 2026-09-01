#!/bin/bash
# test-session-resume.sh - tripwire for agent-query.sh's --resume / --session-id-file
#
# Runs the REAL agent-query.sh against stub `claude`, `codex` and `agy`
# executables on PATH. No network, no API calls, no agent state touched.
#
# What it is guarding, in rough order of how badly it would hurt:
#
#   1. A resume that does not resume, silently. agy answers an unknown
#      conversation id by starting a NEW conversation and exiting 0, so the
#      script compares the id agy logged against the one it asked for. If that
#      comparison ever stops firing, a fixpoint council goes cold with no
#      signal at all -- the reviewer just quietly forgets rounds 1..n-1.
#   2. A stale id in --session-id-file. The file is truncated at startup so a
#      round that cannot determine its id leaves it EMPTY. If it ever stopped
#      being truncated, the next round would chain onto the PREVIOUS round's
#      session and look perfectly healthy while answering into the wrong thread.
#   3. Argv mapping per agent, including the negative: a plain query must not
#      grow --log-file / --session-id, or every existing caller changes
#      behaviour at once.
#   4. The codex capture path, which is the only path that gave up `exec`, so
#      it is the only one that can drop the reply, lose the exit status, or
#      orphan a child under `timeout`.
#
# Run after touching option parsing, the per-agent command build, the agy log
# handling, or the tail of the script:
#
#     .claude/skills/ask-agent/scripts/test-session-resume.sh

set -u

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SRC="$SCRIPT_DIR/agent-query.sh"
[[ -x "$SRC" ]] || { echo "not executable: $SRC" >&2; exit 2; }

T="$(mktemp -d /tmp/test-session-resume.XXXXXX)"
trap 'rm -r -f -- "$T"' EXIT
BIN="$T/bin"; mkdir -p "$BIN"
printf 'probe prompt\n' > "$T/prompt.txt"

checked=0
fails=0

# Every stub records its full argv, one arg per line, so assertions can look for
# exact adjacent pairs rather than substring-matching a flattened string.
cat > "$BIN/claude" <<'STUB'
#!/bin/bash
printf '%s\n' "$@" > "$STUB_ARGV"
cat > /dev/null            # drain the piped prompt like the real CLI does
printf 'claude reply\n'
exit "${STUB_RC:-0}"
STUB

cat > "$BIN/codex" <<'STUB'
#!/bin/bash
printf '%s\n' "$@" > "$STUB_ARGV"
cat > /dev/null
# The real codex puts its banner -- and its echo of the prompt and reply -- on
# stderr, and ONLY the reply on stdout. The stub reproduces that split, because
# the capture path depends on it.
{
  echo "OpenAI Codex v0.0.0-stub"
  echo "--------"
  [[ -n "${STUB_NO_BANNER:-}" ]] || echo "session id: ${STUB_SID:-11111111-2222-4333-8444-555555555555}"
  echo "--------"
} >&2
if [[ -n "${STUB_SLEEP:-}" ]]; then
    echo "$$" > "$STUB_PIDFILE"
    sleep "$STUB_SLEEP"
fi
printf 'codex reply\n'
exit "${STUB_RC:-0}"
STUB

cat > "$BIN/agy" <<'STUB'
#!/bin/bash
printf '%s\n' "$@" > "$STUB_ARGV"
dir=""; log=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --add-dir)  dir="$2";  shift 2;;
    --log-file) log="$2";  shift 2;;
    -p)         shift 2;;
    *)          shift;;
  esac
done
if [[ -n "$log" ]]; then
    echo "I0101 00:00:00.000000 1 printmode.go:104] Print mode: starting (promptLength=1, model=\"\", conversationID=\"\")" > "$log"
    [[ -n "${STUB_NO_CONV:-}" ]] || \
      echo "I0101 00:00:00.000001 1 printmode.go:209] Print mode: conversation=${STUB_CONV:-aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee}, sending message" >> "$log"
fi
M="$(tail -1 "$dir/prompt.txt")"
if [[ -n "${STUB_NO_MARKER:-}" ]]; then printf 'agy reply\n'; else printf 'agy reply\n\n%s\n' "$M"; fi
exit "${STUB_RC:-0}"
STUB
chmod +x "$BIN"/claude "$BIN"/codex "$BIN"/agy

ok() { checked=$(( checked + 1 )); printf '    %-42s OK\n' "$1"; }
bad() {
    checked=$(( checked + 1 )); fails=$(( fails + 1 ))
    printf '    %-42s FAIL  %s\n' "$1" "$2" >&2
}
is() { [[ "$2" == "$3" ]] && ok "$1" || bad "$1" "got [$2] want [$3]"; }
has() { [[ "$2" == *"$3"* ]] && ok "$1" || bad "$1" "[$2] lacks [$3]"; }
hasnt() { [[ "$2" != *"$3"* ]] && ok "$1" || bad "$1" "[$2] unexpectedly holds [$3]"; }

# run <agent> [args...]  -> sets rc, out, err, argv (newline-joined stub argv)
run() {
    local agent="$1"; shift
    : > "$T/argv"
    PATH="$BIN:$PATH" STUB_ARGV="$T/argv" \
        "$SRC" "$agent" "$@" -f "$T/prompt.txt" > "$T/out" 2> "$T/err"
    rc=$?
    out="$(cat "$T/out")"; err="$(cat "$T/err")"; argv="$(cat "$T/argv")"
}
# adjacent <flag> <value> -- true only if they appear as consecutive argv entries
adjacent() { grep -A1 -x -F -e "$1" "$T/argv" 2>/dev/null | grep -q -x -F -e "$2"; }

echo "  option validation (nothing should reach an agent):"
for badid in "-m" "a b" "" "x;y" "../etc"; do
    run codex --resume "$badid"
    if (( rc == 1 )) && [[ -z "$argv" ]]; then ok "rejects --resume [$badid]"
    else bad "rejects --resume [$badid]" "rc=$rc argv=[$argv]"; fi
done
run codex --session-id-file "$T/no/such/dir/sid"
if (( rc == 1 )) && [[ -z "$argv" ]]; then ok "rejects unwritable --session-id-file"
else bad "rejects unwritable --session-id-file" "rc=$rc"; fi

echo "  argv mapping:"
run claude
hasnt "claude plain: no --session-id"          "$argv" "--session-id"
hasnt "claude plain: no --resume"              "$argv" "--resume"
run claude --resume 1e5a4dcb-0000-4000-8000-000000000001 --session-id-file "$T/sid"
adjacent --resume 1e5a4dcb-0000-4000-8000-000000000001 \
  && ok "claude resume: --resume <id>" || bad "claude resume: --resume <id>" "$argv"
is "claude resume: sid file echoes the id" "$(cat "$T/sid")" "1e5a4dcb-0000-4000-8000-000000000001"

run claude --session-id-file "$T/sid"
minted="$(cat "$T/sid")"
if [[ "$minted" =~ ^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$ ]]; then
    ok "claude fresh: mints a real uuid"
else bad "claude fresh: mints a real uuid" "got [$minted]"; fi
adjacent --session-id "$minted" \
  && ok "claude fresh: passes the minted id" || bad "claude fresh: passes the minted id" "$argv"

run codex
is "codex plain: argv is exec -" "$argv" "$(printf 'exec\n-')"
run codex --resume 1e5a4dcb-0000-4000-8000-000000000002 --session-id-file "$T/sid"
is "codex resume: argv is exec resume <id> -" "$argv" \
   "$(printf 'exec\nresume\n1e5a4dcb-0000-4000-8000-000000000002\n-')"
is "codex resume: sid file echoes the id" "$(cat "$T/sid")" "1e5a4dcb-0000-4000-8000-000000000002"

run agy
hasnt "agy plain: NO --log-file (unchanged path)" "$argv" "--log-file"
hasnt "agy plain: no --conversation"              "$argv" "--conversation"
is    "agy plain: stderr stays empty"             "$err"  ""
run agy --session-id-file "$T/sid"
has "agy + sid file: passes --log-file"           "$argv" "--log-file"
is  "agy + sid file: id read out of the log"      "$(cat "$T/sid")" "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"

echo "  the stale-id guard:"
printf 'STALE-ID-FROM-LAST-ROUND\n' > "$T/sid"
STUB_NO_BANNER=1 run codex --session-id-file "$T/sid"
is "no id determinable => file is EMPTY, not stale" "$(cat "$T/sid")" ""
has "no id determinable => says so on stderr"       "$err" "could not read a session id"
is  "no id determinable => reply still delivered"   "$out" "codex reply"

echo "  codex capture path (the one that cannot exec):"
STUB_SID=1e5a4dcb-0000-4000-8000-00000000000c run codex --session-id-file "$T/sid"
is  "captures the id from the banner"  "$(cat "$T/sid")" "1e5a4dcb-0000-4000-8000-00000000000c"
is  "stdout is the reply and only that" "$out" "codex reply"
has "stderr still passes through live"  "$err" "OpenAI Codex v0.0.0-stub"
STUB_RC=7 run codex --session-id-file "$T/sid"
is "propagates the agent's exit status" "$rc" "7"

# `timeout` is how every real caller runs this. Without signal forwarding the
# wrapper would die and leave codex orphaned -- still running, still billing.
: > "$T/argv"
: > "$T/childpid"
PATH="$BIN:$PATH" STUB_ARGV="$T/argv" STUB_SLEEP=30 STUB_PIDFILE="$T/childpid" \
    "$SRC" codex --session-id-file "$T/sid" -f "$T/prompt.txt" >/dev/null 2>&1 &
wrapper=$!
for _ in $(seq 1 100); do [[ -s "$T/childpid" ]] && break; sleep 0.1; done
kill -TERM "$wrapper" 2>/dev/null
wait "$wrapper" 2>/dev/null
child="$(cat "$T/childpid" 2>/dev/null || true)"
for _ in $(seq 1 50); do kill -0 "$child" 2>/dev/null || break; sleep 0.1; done
if [[ -n "$child" ]] && ! kill -0 "$child" 2>/dev/null; then
    ok "TERM is forwarded (no orphaned agent)"
else
    bad "TERM is forwarded (no orphaned agent)" "child [$child] survived"
    [[ -n "$child" ]] && kill -KILL "$child" 2>/dev/null
fi

echo "  agy resume verification (the silent-fork guard):"
STUB_CONV=1e5a4dcb-0000-4000-8000-00000000000a \
  run agy --resume 1e5a4dcb-0000-4000-8000-00000000000a
is "id matches => exit 0" "$rc" "0"
adjacent --conversation 1e5a4dcb-0000-4000-8000-00000000000a \
  && ok "id matches => passed --conversation" || bad "id matches => passed --conversation" "$argv"

STUB_CONV=99999999-9999-4999-8999-999999999999 \
  run agy --resume 1e5a4dcb-0000-4000-8000-00000000000a --session-id-file "$T/sid"
is  "silent fork => exit 4"                  "$rc" "4"
has "silent fork => names both ids"          "$err" "did NOT resume 1e5a4dcb-0000-4000-8000-00000000000a"
has "silent fork => names the one it got"    "$err" "99999999-9999-4999-8999-999999999999"
is  "silent fork => reply still printed"     "$out" "agy reply"
is  "silent fork => sid file holds the REAL conversation" \
    "$(cat "$T/sid")" "99999999-9999-4999-8999-999999999999"

STUB_NO_CONV=1 run agy --resume 1e5a4dcb-0000-4000-8000-00000000000a
is  "unverifiable => exit 4 (fails closed)" "$rc" "4"
has "unverifiable => says why"              "$err" "cannot confirm agy resumed"

# A partial prompt read is the more fundamental problem, so 3 outranks 4.
STUB_NO_MARKER=1 STUB_CONV=99999999-9999-4999-8999-999999999999 \
  run agy --resume 1e5a4dcb-0000-4000-8000-00000000000a
is  "missing sentinel outranks mismatch => 3" "$rc" "3"
has "...but the mismatch is still reported"   "$err" "did NOT resume"

# A resume must not be able to widen the --add-dir grant: the granted directory
# holds the prompt and nothing else, and in particular not the CLI log.
STUB_CONV=1e5a4dcb-0000-4000-8000-00000000000a \
  run agy --resume 1e5a4dcb-0000-4000-8000-00000000000a --session-id-file "$T/sid"
granted="$(grep -A1 -x -F -e '--add-dir' "$T/argv" | tail -1)"
logpath="$(grep -A1 -x -F -e '--log-file' "$T/argv" | tail -1)"
if [[ -n "$granted" && "$logpath" != "$granted"/* ]]; then
    ok "CLI log lives OUTSIDE the --add-dir grant"
else
    bad "CLI log lives OUTSIDE the --add-dir grant" "granted=$granted log=$logpath"
fi

echo
if (( fails == 0 )); then
    echo "PASS  $checked assertions"
    exit 0
fi
echo "FAIL  $fails of $checked assertions" >&2
exit 1
