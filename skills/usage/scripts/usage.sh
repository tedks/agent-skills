#!/usr/bin/env bash
# Lane usage from the installed token-usage / token-tripwire CLIs. Counts only.
set -euo pipefail

usage() {
  echo "Usage: usage.sh [--project NAME] [--day YYYY-MM-DD] [--fresh] [--json]" >&2
  exit "${1:-2}"
}
value() {  # an option's value must exist and must not be another option
  if [ $# -lt 2 ] || [ -z "$2" ] || [[ "$2" == -* ]]; then
    echo "usage.sh: $1 needs a value" >&2
    usage
  fi
}

project="" day="" fresh=false json=false
while [ $# -gt 0 ]; do
  case "$1" in
    --project) value "$@"; project="$2"; shift 2 ;;
    --day) value "$@"; day="$2"; shift 2 ;;
    --fresh) fresh=true; shift ;;
    --json) json=true; shift ;;
    -h|--help) usage 0 ;;
    *) usage ;;
  esac
done
if [ -n "$day" ] && ! [[ "$day" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "usage.sh: --day must be YYYY-MM-DD" >&2
  exit 2
fi

find_cli() {
  local name="$1"
  if command -v "$name" >/dev/null 2>&1; then command -v "$name"; return 0; fi
  if [ -x "$HOME/.bin/$name" ]; then echo "$HOME/.bin/$name"; return 0; fi
  return 1
}
if ! TU="$(find_cli token-usage)"; then
  echo "usage.sh: token-usage is not installed on this host (dotfiles scripts/usage)" >&2
  exit 3
fi
TW="$(find_cli token-tripwire || true)"

scan=(--no-scan)
$fresh && scan=()
sessions=("$TU" sessions "${scan[@]}")
[ -n "$project" ] && sessions+=(--project "$project")
totals=("$TU" totals --no-scan)
[ -n "$day" ] && totals+=(--day "$day")

if $json; then
  # Each view goes through a private file: outputs can exceed an argument's size limit.
  dir="$(mktemp -d)"
  trap 'rm -rf -- "$dir"' EXIT
  "${sessions[@]}" --json > "$dir/sessions"
  "${totals[@]}" --by project --json > "$dir/totals_project"
  "${totals[@]}" --by model --json > "$dir/totals_model"
  "$TU" meters --json > "$dir/meters"
  if [ -n "$TW" ]; then "$TW" status --json > "$dir/incidents"; else echo null > "$dir/incidents"; fi
  python3 -c 'import json, os, sys
names = ("sessions", "totals_project", "totals_model", "meters", "incidents")
out = {}
for name in names:
    with open(os.path.join(sys.argv[1], name)) as stream:
        out[name] = json.load(stream)
print(json.dumps(out, indent=1))' "$dir"
  exit 0
fi

echo "== Sessions, last 60 min${project:+ (project $project)} =="
"${sessions[@]}"
echo
echo "== Totals by project${day:+ ($day)} =="
"${totals[@]}" --by project
echo
echo "== Totals by model${day:+ ($day)} =="
"${totals[@]}" --by model
echo
echo "== Quota meters (account-wide; note the observed time) =="
"$TU" meters
echo
if [ -n "$TW" ]; then
  echo "== Tripwire incidents =="
  "$TW" status
else
  echo "(token-tripwire not installed: no incident state)"
fi
