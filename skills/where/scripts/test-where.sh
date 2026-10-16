#!/usr/bin/env bash
# where against temp git repos in a private HOME and XDG_CACHE_HOME.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WHERE="$HERE/where"
WORK="$(mktemp -d)"
trap 'rm -rf -- "$WORK"' EXIT
export HOME="$WORK/home"
export XDG_CACHE_HOME="$WORK/cache"
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.com GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.com
mkdir -p "$HOME/Projects"
fail=0
check() { local what="$1"; shift; if "$@"; then echo "ok   $what"; else echo "FAIL $what"; fail=1; fi; }
not() { ! "$@"; }

# Bare-repo + worktrees layout: $HOME/Projects/proj/{.git,master,feat}
SEED="$WORK/seed"
git init -q -b master "$SEED"
mkdir -p "$SEED/src/core" "$SEED/docs"
echo 'print(1)' > "$SEED/src/core/a.py"
echo 'hello' > "$SEED/docs/guide.md"
head -c 5000 /dev/zero > "$SEED/src/blob.bin"
cat > "$SEED/AGENTS.md" <<'DOC'
# Agent Instructions

## Project structure

- `src/` -- the code
- `docs/` -- the docs

## Environment

Not part of the structure section.
DOC
git -C "$SEED" add -A
git -C "$SEED" commit -q -m seed
PROJ="$HOME/Projects/proj"
mkdir -p "$PROJ"
git clone -q --bare "$SEED" "$PROJ/.git"
git -C "$PROJ/.git" worktree add -q "$PROJ/master" master
git -C "$PROJ/.git" worktree add -q -b feat "$PROJ/feat" master
# Untracked noise that must not appear in the map.
mkdir -p "$PROJ/master/node_modules/dep"
echo x > "$PROJ/master/node_modules/dep/index.js"

cd "$PROJ/master"
"$WHERE" > "$WORK/out" 2> "$WORK/err"
check "names the project root" grep -q "root: $PROJ\$" "$WORK/out"
check "lists worktrees of a bare layout" grep -q "$PROJ/feat" "$WORK/out"
check "tree shows top-level dirs with sizes" grep -Eq '^ *src/ +[0-9.]+ ?[KMB]' "$WORK/out"
check "tree goes to depth 2" grep -q 'core/' "$WORK/out"
check "tree stops at depth 2" not grep -q 'a\.py' "$WORK/out"
check "untracked node_modules is not mapped" not grep -q node_modules "$WORK/out"
check "prints the Project structure section verbatim" grep -q '^- `docs/` -- the docs' "$WORK/out"
check "stops the section at the next heading" not grep -q 'Not part of the structure' "$WORK/out"

# Cache
CACHEFILE_COUNT=$(find "$XDG_CACHE_HOME/where" -type f | wc -l)
check "writes one cache file under XDG_CACHE_HOME" test "$CACHEFILE_COUNT" -eq 1
"$WHERE" > "$WORK/out2" 2> "$WORK/err2"
check "second run is a cache hit" grep -qi 'cached' "$WORK/err2"
check "cache hit prints the same map" cmp -s "$WORK/out" "$WORK/out2"
"$WHERE" --refresh > /dev/null 2> "$WORK/err3"
check "--refresh bypasses the cache" not grep -qi 'cached' "$WORK/err3"
"$WHERE" --max-age 0 > /dev/null 2> "$WORK/err4"
check "an expired cache is rebuilt" not grep -qi 'cached' "$WORK/err4"

echo more > docs/more.md
git add docs/more.md
git commit -q -m more
"$WHERE" > "$WORK/out5" 2> "$WORK/err5"
check "a new HEAD invalidates the cache" not grep -qi 'cached' "$WORK/err5"
check "the rebuilt map sees the new file's directory size" grep -q 'docs/' "$WORK/out5"

# Path argument from an unrelated cwd
cd "$WORK"
"$WHERE" "$PROJ/feat" > "$WORK/out6" 2> /dev/null
check "path argument selects that worktree" grep -q "worktree: $PROJ/feat" "$WORK/out6"

# Bare-layout root: worktrees listed, tree skipped with a note
"$WHERE" "$PROJ" > "$WORK/out7" 2> /dev/null
check "project root without a worktree still lists worktrees" grep -q "$PROJ/master" "$WORK/out7"
check "project root without a worktree says why there is no tree" grep -qi 'no worktree' "$WORK/out7"

# Missing AGENTS.md section
PLAIN="$HOME/Projects/plain"
git init -q -b master "$PLAIN"
echo x > "$PLAIN/f.txt"
git -C "$PLAIN" add -A
git -C "$PLAIN" commit -q -m init
"$WHERE" "$PLAIN" > "$WORK/out8" 2> /dev/null
check "plain repo is mapped" grep -q "root: $PLAIN\$" "$WORK/out8"
check "missing AGENTS.md is a one-line note" grep -q 'no AGENTS.md' "$WORK/out8"
printf '# Hi\n\n## Other\n\ntext\n' > "$PLAIN/AGENTS.md"
"$WHERE" --refresh "$PLAIN" > "$WORK/out9" 2> /dev/null
check "AGENTS.md without the section is a one-line note" grep -q 'no "Project structure" section' "$WORK/out9"
rm "$PLAIN/AGENTS.md"
printf '# Hi\n\n## Project structure\n\nfrom claude\n' > "$PLAIN/CLAUDE.md"
"$WHERE" --refresh "$PLAIN" > "$WORK/out10" 2> /dev/null
check "falls back to CLAUDE.md" grep -q 'from claude' "$WORK/out10"

# Truncation
BIG="$HOME/Projects/big"
git init -q -b master "$BIG"
for i in $(seq 1 80); do mkdir -p "$BIG/d$i/sub"; echo x > "$BIG/d$i/sub/f"; done
{ echo '## Project structure'; for i in $(seq 1 200); do echo "- line $i"; done; } > "$BIG/AGENTS.md"
git -C "$BIG" add -A
git -C "$BIG" commit -q -m big
"$WHERE" "$BIG" > "$WORK/out11" 2> /dev/null
check "output is capped near 120 lines" test "$(wc -l < "$WORK/out11")" -le 125
check "truncation is announced" grep -qi 'truncated' "$WORK/out11"

# Refusal at broad roots (exit 2 with a hint, nothing mapped)
for broad in "$HOME" "$HOME/Projects" / /tmp; do
  set +e
  "$WHERE" "$broad" > "$WORK/o" 2> "$WORK/e"
  rc=$?
  set -e
  check "refuses $broad with exit 2" test "$rc" -eq 2
  check "refusal of $broad carries a hint" grep -qi 'broad' "$WORK/e"
  check "refusal of $broad maps nothing" test ! -s "$WORK/o"
done
set +e
(cd "$HOME/Projects" && "$WHERE" > /dev/null 2> "$WORK/e")
rc=$?
set -e
check "refuses when the cwd is a broad root" test "$rc" -eq 2

# Not a project
mkdir -p "$WORK/notgit"
set +e
"$WHERE" "$WORK/notgit" > /dev/null 2> "$WORK/e"
rc=$?
set -e
check "a non-git directory exits 2" test "$rc" -eq 2

exit "$fail"
