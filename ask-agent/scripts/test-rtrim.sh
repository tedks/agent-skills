#!/bin/bash
# test-rtrim.sh - tripwire for agent-query.sh's _agy_rtrim
#
# Run by hand, or from a review, after touching the reply-trimming code:
#     .claude/skills/ask-agent/scripts/test-rtrim.sh
# Exit 0 = pass. Any failure prints the case, the locale, and the bytes.
#
# WHY THIS EXISTS. Trimming the tail of agy's reply has shipped a bug twice,
# from opposite directions, and neither showed up in ordinary use:
#
#   * under a UTF-8 locale glibc's regexec REFUSES to match invalid multibyte
#     input, and treating no-match as "all whitespace" BLANKED whole replies --
#     including the timeout path, where agy killed mid-reply ends inside a
#     character;
#   * under a byte locale [[:space:]] stops matching U+2003/U+3000, so
#     "<marker><EM SPACE>" survived as invisible content and passed as success;
#     and naming those characters in a bracket class made it worse, because a
#     byte locale then ate individual bytes off the end of ordinary replies.
#
# The function is therefore locale-sensitive by nature, and the caller's locale
# is NOT reliable -- LANG is unset under cron, systemd units, `ssh host cmd`,
# `docker exec` and `sudo` with env_reset, and a LANG naming an ungenerated
# locale silently degrades to C too. So every case runs under four locales and
# the results must be byte-identical.
#
# The function is extracted from agent-query.sh rather than copied, so this
# cannot drift from the code it guards.

set -u

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SRC="$SCRIPT_DIR/agent-query.sh"
[[ -r "$SRC" ]] || { echo "cannot read $SRC" >&2; exit 2; }

# Pull _agy_rtrim out of the script it lives in. If this stops matching, the
# test fails loudly rather than silently testing nothing.
fn="$(awk '/^    _agy_rtrim\(\) \{/{f=1} f{print} f&&/^    \}$/{exit}' "$SRC")"
[[ "$fn" == *"_rtrim_out"* ]] || { echo "could not extract _agy_rtrim from $SRC" >&2; exit 2; }
eval "$fn"

hex() { printf '%s' "$1" | od -An -tx1 | tr -d ' \n'; }

fails=0
checked=0

# check <name> <input-hex> <expected-hex>
check() {
    local name="$1" in_hex="$2" want="$3" got in
    # An odd-length literal silently mis-tests: the trailing digit becomes an
    # ASCII character rather than half a byte. Refuse it rather than measure it.
    if (( ${#in_hex} % 2 != 0 )); then
        echo "bad hex literal in case '$name': odd length (${#in_hex})" >&2
        exit 2
    fi
    in="$(printf '%b' "$(sed 's/../\\x&/g' <<<"$in_hex")")"
    _rtrim_out=""
    _agy_rtrim "$in"
    got="$(hex "$_rtrim_out")"
    checked=$(( checked + 1 ))
    if [[ "$got" != "$want" ]]; then
        printf 'FAIL  [%s] %-34s in=%s want=%s got=%s\n' "${LC_ALL:-<unset>}" "$name" "$in_hex" "$want" "$got" >&2
        fails=$(( fails + 1 ))
    fi
}

# Every case as hex, so expectations are byte-exact and this file stays ASCII.
#   61 6e 73 77 65 72 = "answer",  20 = space
#   c2a0 = U+00A0 NBSP      e280af = U+202F NNBSP     efbbbf = U+FEFF ZWNBSP
#   e28083 = U+2003 EM      e38080 = U+3000 IDEO      e28089 = U+2009 THIN
#   f09f = truncated emoji (invalid)   e9 = lone Latin-1 byte (invalid)
run_matrix() {
    # --- ordinary trimming ---
    check "ascii tail"                  "616e7377657220202020"   "616e73776572"
    check "no tail"                     "616e73776572"           "616e73776572"
    check "empty"                       ""                       ""
    check "only ascii spaces"           "202020"                 ""

    # --- space-class Unicode (glibc says these ARE space) ---
    check "U+2003 tail"                 "616e73776572e28083"     "616e73776572"
    check "U+3000 tail"                 "616e73776572e38080"     "616e73776572"
    check "U+2009 tail"                 "616e73776572e28089"     "616e73776572"
    check "only U+2003"                 "e28083"                 ""

    # --- non-breaking spaces (glibc says these are NOT space; named explicitly)
    check "U+00A0 tail"                 "616e73776572c2a0"       "616e73776572"
    check "U+202F tail"                 "616e73776572e280af"     "616e73776572"
    check "U+FEFF tail"                 "616e73776572efbbbf"     "616e73776572"
    check "only U+00A0"                 "c2a0"                   ""
    check "all unicode whitespace"      "c2a0e28083e38080"       ""
    check "mixed ascii+nbsp tail"       "616e7377657220c2a020"  "616e73776572"

    # --- must NOT be trimmed: these are content ---
    check "mid-string NBSP"             "616ec2a073776572"       "616ec2a073776572"
    check "leading NBSP kept"           "c2a0616e73776572"       "c2a0616e73776572"
    check "leading+trailing NBSP"       "c2a0616e73776572c2a0"   "c2a0616e73776572"

    # --- invalid bytes: content must survive (pass 3) ---
    check "truncated emoji tail"        "626164f09f"             "626164f09f"
    check "lone latin1 byte"            "436166e9"               "436166e9"
    check "invalid + ascii tail"        "626164f09f2020"         "626164f09f"
    check "invalid + NBSP tail"         "626164f09fc2a0"         "626164f09fc2a0"

    # --- the byte-eating repros: a byte locale ate the final byte of these ---
    check "tail a-grave (c3a0)"         "616e7377657220c3a0"     "616e7377657220c3a0"
    check "tail i-diaeresis (c3af)"     "616e7377657220c3af"     "616e7377657220c3af"
    check "tail u-circumflex (c3bb)"    "616e7377657220c3bb"     "616e7377657220c3bb"
    check "tail inverted ? (c2bf)"      "616e7377657220c2bf"     "616e7377657220c2bf"
    check "tail fullwidth grave (efbd80)"   "616e7377657220efbd80"   "616e7377657220efbd80"
    check "tail CJK zhong (e4b8ad)"     "616e7377657220e4b8ad"   "616e7377657220e4b8ad"
    check "tail emoji (f09f918d)"       "616e7377657220f09f918d" "616e7377657220f09f918d"
}

echo "== _agy_rtrim tripwire =="
for loc in en_US.UTF-8 C.UTF-8 C POSIX; do
    export LC_ALL="$loc"
    before=$checked
    run_matrix
    printf '  %-14s %2d cases\n' "$loc" $(( checked - before ))
done
unset LC_ALL

# Degraded mode. C.UTF-8 is built into glibc, so it cannot be made unavailable
# with LOCPATH -- an earlier version of this test tried that and silently
# exercised the normal path instead. Simulate the real residual directly: take
# the function and pin C where it pins C.UTF-8, i.e. "the pin did not take".
# The guard must then notice it is in a byte locale (_nb measures 8 bytes rather
# than 3 characters), drop the named characters, and fall back to an ASCII-only
# trim -- degraded, but never corrupting.
echo "  degraded (C.UTF-8 unavailable, simulated by pinning C):"
if [[ "$fn" != *"local LC_ALL=C.UTF-8"* ]]; then
    echo "    cannot simulate: no C.UTF-8 pin found in the function" >&2
    fails=$(( fails + 1 ))
else
    eval "${fn/local LC_ALL=C.UTF-8/local LC_ALL=C}"

    _rtrim_out=""; _agy_rtrim "$(printf '%b' 'answer \xc3\xa0')"
    if [[ "$(hex "$_rtrim_out")" == "616e7377657220c3a0" ]]; then
        echo "    multibyte tail intact (no corruption)   OK"
    else
        echo "    multibyte tail CORRUPTED: $(hex "$_rtrim_out")" >&2; fails=$(( fails + 1 ))
    fi

    _rtrim_out=""; _agy_rtrim "answer   "
    if [[ "$(hex "$_rtrim_out")" == "616e73776572" ]]; then
        echo "    ascii trim still works                  OK"
    else
        echo "    ascii trim broken: $(hex "$_rtrim_out")" >&2; fails=$(( fails + 1 ))
    fi

    # Documented consequence of degrading: the non-breaking spaces are no longer
    # trimmed. Asserted so the residual is pinned, not merely described.
    _rtrim_out=""; _agy_rtrim "$(printf '%b' 'answer\xc2\xa0')"
    if [[ "$(hex "$_rtrim_out")" == "616e73776572c2a0" ]]; then
        echo "    NBSP left untrimmed (documented residual) OK"
    else
        echo "    unexpected NBSP handling: $(hex "$_rtrim_out")" >&2; fails=$(( fails + 1 ))
    fi

    # Restore the real function for anything that follows.
    eval "$fn"
fi

# --- the counter must be honest -------------------------------------------
# A deleted or commented-out case used to leave the suite green. Pin the total.
EXPECTED=112
if (( checked != EXPECTED )); then
    echo "FAIL  case count is $checked, expected $EXPECTED -- a case was added or lost" >&2
    fails=$(( fails + 1 ))
fi

# --- end-to-end: the call sites, not just the function ---------------------
# The function is only half the story. Both bugs that shipped lived inside it,
# but the damage a user sees -- a blanked reply, a leaked marker, an invisible
# answer passing as success -- is produced where it is CALLED. Deleting either
# call site left this suite green while reopening a shipped bug, so the call
# sites get asserted too, through the real script with a stub agy.
echo "  end-to-end (real script, stub agy):"
e2e_dir="$(mktemp -d /tmp/test-rtrim-e2e.XXXXXX)"
trap 'rm -r -f -- "$e2e_dir"' EXIT
cat > "$e2e_dir/agy" <<'STUB'
#!/bin/bash
dir=""
while [[ $# -gt 0 ]]; do case "$1" in --add-dir) dir="$2"; shift 2;; -p) shift 2;; *) shift;; esac; done
M="$(tail -1 "$dir/prompt.txt")"
case "$E2E_MODE" in
  compliant) printf 'Answer body.\n\n%s\n' "$M";;
  ws_after)  printf 'Answer body.\n\n%s\n   \n' "$M";;
  mk_em)     printf '%s\xe2\x80\x83\n' "$M";;
  utf8bad)   printf 'Findings \xf0\x9f partial\n\n%s\n' "$M";;
esac
STUB
chmod +x "$e2e_dir/agy"
printf 'probe prompt\n' > "$e2e_dir/prompt.txt"

e2e() {  # <mode> <want-exit> <want-stdout-hex> <why>
    local mode="$1" want_rc="$2" want_hex="$3" why="$4" got rc
    # Captured to a FILE, not "$( )". Command substitution strips trailing
    # newlines, so a $()-based assertion cannot see stray blank lines at the end
    # of a reply -- which is one of the two bugs these cases exist to catch. The
    # first version of this helper used $() and missed it.
    E2E_MODE="$mode" PATH="$e2e_dir:$PATH" "$SRC" agy -f "$e2e_dir/prompt.txt" \
        > "$e2e_dir/out.bin" 2>/dev/null; rc=$?
    got="$(od -An -tx1 < "$e2e_dir/out.bin" | tr -d ' \n')"
    if [[ "$rc" == "$want_rc" && "$got" == "$want_hex" ]]; then
        printf '    %-10s OK   (%s)\n' "$mode" "$why"
    else
        printf '    %-10s FAIL exit=%s want=%s  out=%s want=%s\n' \
               "$mode" "$rc" "$want_rc" "$got" "$want_hex" >&2
        fails=$(( fails + 1 ))
    fi
}
# "Answer body.\n" = 416e7377657220626f64792e0a -- the trailing 0a is asserted
# on purpose: without it, extra blank lines at the end would go unnoticed.
e2e compliant 0 "416e7377657220626f64792e0a" "exactly one trailing newline"
e2e ws_after  0 "416e7377657220626f64792e0a" "whitespace-only final line, no marker leak"
e2e mk_em     3 ""                           "marker+EM SPACE is not an answer"
e2e utf8bad   0 "46696e64696e677320f09f207061727469616c0a" "undecodable byte preserved"

echo
if (( fails == 0 )); then
    echo "PASS  $checked assertions across 4 locales + degraded mode"
    exit 0
fi
echo "FAIL  $fails of $checked assertions" >&2
exit 1
