#!/bin/bash
# Hermetic tests for claude-trust-dir.sh's boundary resolution and predictor.
#
# No network, and the real ~/.claude.json is never read or written: every case
# runs against a throwaway config via CLAUDE_CONFIG_FILE. --verify is NOT
# exercised here (it launches a real Claude); it has its own guard rails and is
# checked by hand.
#
# These exist because the boundary rule has been wrong three times: an inferred
# "nearest ancestor wins", then a port that used --show-toplevel, then one that
# used dirname(--git-common-dir) and broke submodules and bare repos. Each
# shape below is a bug that actually shipped in review.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TRUST="$SCRIPT_DIR/claude-trust-dir.sh"
TEST_DIR="$(mktemp -d /tmp/test-trust-dir.XXXXXX)"
trap 'chmod -R u+w "$TEST_DIR" 2>/dev/null || true; rm -rf -- "$TEST_DIR"' EXIT

CFG="$TEST_DIR/config.json"
printf '%s\n' '{"projects":{}}' > "$CFG"

git_q() { git -c user.email=t@e -c user.name=t -c protocol.file.allow=always "$@"; }

fail() { echo "FAIL: $1" >&2; exit 1; }

# git_root_of is the unit under test; source just that function so the tests
# exercise the shipped implementation rather than a copy.
sed -n '/^git_root_of() {/,/^}/p' "$TRUST" > "$TEST_DIR/git_root_of.sh"
# shellcheck source=/dev/null
. "$TEST_DIR/git_root_of.sh"

# --- repo shapes -------------------------------------------------------
git init -q "$TEST_DIR/plain"
git_q -C "$TEST_DIR/plain" commit -q --allow-empty -m x
mkdir -p "$TEST_DIR/plain/deep/deeper"

# bare repo plus a linked worktree, the layout this fleet actually uses
git init -q --bare "$TEST_DIR/container/.bare"
git_q clone -q "$TEST_DIR/container/.bare" "$TEST_DIR/container/master" 2>/dev/null || true
git_q -C "$TEST_DIR/container/master" commit -q --allow-empty -m x
git_q -C "$TEST_DIR/container/master" push -q origin HEAD:master 2>/dev/null || true

git init -q "$TEST_DIR/inner"
git_q -C "$TEST_DIR/inner" commit -q --allow-empty -m x
git init -q "$TEST_DIR/super"
git_q -C "$TEST_DIR/super" submodule add -q "$TEST_DIR/inner" sub 2>/dev/null
git_q -C "$TEST_DIR/super" commit -q -m add

git init -q --bare "$TEST_DIR/bare.git"
mkdir -p "$TEST_DIR/norepo"

expect_root() {
    local dir="$1" want="$2" got
    got="$(git_root_of "$dir")"
    [[ "$got" == "$want" ]] || fail "boundary for $dir: want '$want', got '$got'"
}

expect_root "$TEST_DIR/plain"              "$TEST_DIR/plain"
expect_root "$TEST_DIR/plain/deep/deeper"  "$TEST_DIR/plain"
# The regression that shipped: in a submodule --git-common-dir points into the
# SUPERPROJECT's .git/modules/<name>, whose parent is not an ancestor of the
# submodule at all. Using it made even a direct truthy entry unreachable, so
# --check contradicted --trust on the same directory.
expect_root "$TEST_DIR/super/sub"          "$TEST_DIR/super/sub"
expect_root "$TEST_DIR/super"              "$TEST_DIR/super"
# A bare repo's common dir IS the repo, so dirname() would hand back its parent
# -- a boundary one level too permissive.
expect_root "$TEST_DIR/bare.git"           "$TEST_DIR/bare.git"
expect_root "$TEST_DIR/norepo"             ""

# "git is missing" must not be mistaken for "not in a repo", which would
# silently restore an unbounded walk.
[[ "$(PATH=/nonexistent git_root_of "$TEST_DIR/plain")" == "?" ]] \
    || fail "git-absent must yield '?', not an unbounded walk"

# --- predictor ---------------------------------------------------------
check() { CLAUDE_CONFIG_FILE="$CFG" "$TRUST" --check "$@" 2>&1; }
grant() { CLAUDE_CONFIG_FILE="$CFG" "$TRUST" "$@" >/dev/null 2>&1; }

# A false entry does NOT veto: it is skipped and the walk continues upward.
python3 - "$CFG" "$TEST_DIR" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1])); root = sys.argv[2]
cfg["projects"] = {
    f"{root}/plain":            {"hasTrustDialogAccepted": False},
    f"{root}/plain/deep":       {"hasTrustDialogAccepted": True},
}
json.dump(cfg, open(sys.argv[1], "w"))
PY
check "$TEST_DIR/plain/deep/deeper" | grep -q '^  trusted' \
    || fail "a truthy entry below a false one should still grant"

# A truthy entry ABOVE the boundary must not apply.
python3 - "$CFG" "$TEST_DIR" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1])); root = sys.argv[2]
cfg["projects"] = {root: {"hasTrustDialogAccepted": True}}
json.dump(cfg, open(sys.argv[1], "w"))
PY
check "$TEST_DIR/plain" | grep -q '^  PROMPTS' \
    || fail "a truthy entry above the project boundary must not grant"
# ...but with no repo there is no boundary, so the same entry does apply.
check "$TEST_DIR/norepo" | grep -q '^  trusted' \
    || fail "outside a repo the walk is unbounded and should reach the ancestor"

# --check must never contradict --trust on the same directory.
printf '%s\n' '{"projects":{}}' > "$CFG"
check "$TEST_DIR/super/sub" | grep -q '^  PROMPTS' || fail "submodule should start untrusted"
grant "$TEST_DIR/super/sub"
check "$TEST_DIR/super/sub" | grep -q '^  trusted' \
    || fail "--check contradicts --trust on a submodule (the round-2 regression)"

# Granting is idempotent and reports it.
CLAUDE_CONFIG_FILE="$CFG" "$TRUST" "$TEST_DIR/super/sub" 2>&1 | grep -q 'already trusted' \
    || fail "second grant should report already-trusted"

echo 'PASS: boundary resolution across repo shapes, and predictor/grant agreement'
