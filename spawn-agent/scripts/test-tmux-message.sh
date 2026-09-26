#!/usr/bin/env bash
# Regression: repository and installed aliases must resolve sibling helpers.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
TEST_DIR=$(mktemp -d)
trap 'rm -rf -- "$TEST_DIR"' EXIT
mkdir -p "$TEST_DIR/installed bin" "$TEST_DIR/fake bin"
ln -s "$REPO_ROOT/.bin/tmux-message" "$TEST_DIR/installed bin/tmux-message"
cat > "$TEST_DIR/fake bin/tmux" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$TMUX_TEST_ARGS"
case " $* " in
    *' display-message '*) printf '%%88\n';;
    *' capture-pane '*) printf 'controlled pane history\n';;
    *) exit 99;;
esac
STUB
chmod +x "$TEST_DIR/fake bin/tmux"
export PATH="$TEST_DIR/fake bin:$PATH"
export TMUX_TEST_ARGS="$TEST_DIR/args"
export SPAWN_TMUX_LABEL=alias-test
unset SPAWN_TMUX_SOCKET
for entry in "$REPO_ROOT/.bin/tmux-message" "$TEST_DIR/installed bin/tmux-message"; do
    status=0
    "$entry" > "$TEST_DIR/out" 2> "$TEST_DIR/err" || status=$?
    [[ $status == 2 ]]
    [[ $(cat "$TEST_DIR/err") == Usage:* ]]
    [[ $("$entry" read session:window) == 'controlled pane history' ]]
    printf '%s\n' -L alias-test capture-pane -e -p -t %88 -S -200 > "$TEST_DIR/expected"
    cmp "$TEST_DIR/expected" "$TEST_DIR/args"
done
printf 'tmux-message alias tests passed\n'
