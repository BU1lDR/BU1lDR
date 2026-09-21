#!/usr/bin/env bash
# Refresh fixtures/api from the live account: the answers tools/build_readme.py would
# get today, saved so the acceptance checks and the tests can run offline.
#
#   scripts/snapshot-fixtures.sh            # uses gh's login; the account is profile.config.json's login
#
# What is saved, in the layout tools/build_readme.py's Fixtures class reads:
#   fixtures/api/repos.json             GET /users/<login>/repos?type=owner (all pages)
#   fixtures/api/user.json              GET /users/<login>, trimmed to what the builder reads
#   fixtures/api/releases/<name>.json   GET /repos/<login>/<name>/releases (every public repository)
#   fixtures/api/profiles/<name>.md     .github/PROFILE.md, raw, where one exists
#
# Review the diff before committing: this is the inventory the offline checks trust.

set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PYTHON:-$(command -v python3 || command -v python)}
# tr -d '\r': a Windows Python prints CRLF into a pipe, and read -r would keep the CR.
LOGIN=$("$PY" -c 'import json; print(json.load(open("profile.config.json", encoding="utf-8"))["login"])' | tr -d '\r')
PROFILE_PATH=$("$PY" -c 'import json; print(json.load(open("profile.config.json", encoding="utf-8")).get("profile_path", ".github/PROFILE.md"))' | tr -d '\r')
OUT=fixtures/api

mkdir -p "$OUT/repos" "$OUT/releases" "$OUT/profiles"

# Fetch into a staging directory first: a failure part-way must not leave the
# committed fixture half-emptied.
STAGE=$(mktemp -d)
mkdir -p "$STAGE/releases" "$STAGE/profiles"
trap 'rm -rf "$STAGE"' EXIT

gh api "users/$LOGIN/repos?type=owner&per_page=100&sort=full_name" --paginate --slurp \
  | "$PY" -c 'import json,sys; pages=json.load(sys.stdin); repos=[r for page in pages for r in page]; print(json.dumps(repos, indent=2, ensure_ascii=False))' \
  > "$STAGE/repos.json"
gh api "users/$LOGIN" --jq '{login, id, public_repos, type}' > "$STAGE/user.json"

count=0
while IFS= read -r name; do
  [ -n "$name" ] || continue
  gh api "repos/$LOGIN/$name/releases?per_page=100" > "$STAGE/releases/$name.json"
  if gh api -H 'Accept: application/vnd.github.raw+json' "repos/$LOGIN/$name/contents/$PROFILE_PATH" > "$STAGE/profiles/$name.md" 2>/dev/null; then
    :
  else
    rm -f "$STAGE/profiles/$name.md"
  fi
  count=$((count + 1))
done < <("$PY" -c 'import json,sys; [print(r["name"]) for r in json.load(open(sys.argv[1], encoding="utf-8")) if not r["private"] and r.get("visibility", "public") == "public"]' "$STAGE/repos.json" | tr -d '\r')

rm -f "$OUT"/releases/*.json "$OUT"/profiles/*.md
cp "$STAGE/repos.json" "$STAGE/user.json" "$OUT/"
cp "$STAGE"/releases/*.json "$OUT/releases/"
if ls "$STAGE"/profiles/*.md > /dev/null 2>&1; then cp "$STAGE"/profiles/*.md "$OUT/profiles/"; fi

echo "snapshot of $LOGIN: $count public repositories, $(ls "$OUT/profiles" | wc -l | tr -d ' ') PROFILE.md files"
git --no-pager status --short "$OUT"
