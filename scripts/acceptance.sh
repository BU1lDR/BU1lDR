#!/usr/bin/env bash
# The acceptance checks for tools/build_readme.py, lettered as in the hardening brief.
#
#   scripts/acceptance.sh             # all of them; (c) and (h) need the network and gh auth
#   scripts/acceptance.sh --offline   # skips (c) and (h); this is what the pull_request CI runs
#
#   a. two runs from the same fixture: the second leaves `git diff --exit-code README.md` clean
#   b. the network black-holed: non-zero exit and README.md byte-identical
#   c. an invalid credential: non-zero exit and README.md byte-identical
#   d. the hostile fixture renders byte-for-byte to fixtures/hostile/expected/README.md
#   e. the hostile fixture, shuffled, renders to the same sha256
#   f. the marker counts are 1 and 1 before and after every run above
#   g. every `uses:` in .github/workflows is pinned to a 40-hex commit
#   h. scripts/audit.sh exits 0 against the live repository
#   i. the unit tests pass with the network black-holed
#
# Every builder run happens in a throwaway git repository, never in this checkout.

set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PYTHON:-$(command -v python3 || command -v python)}
OFFLINE=0
[ "${1:-}" = "--offline" ] && OFFLINE=1
BLACKHOLE=http://127.0.0.1:1
START='<!-- work:start -->'
END='<!-- work:end -->'
export NOW=2026-09-21T12:00:00Z   # the run record is stamped from this, not the clock

pass() { printf 'PASS  %s\n' "$*"; }
skip() { printf 'SKIP  %s\n' "$*"; }
fail() { printf 'FAIL  %s\n' "$*" >&2; exit 1; }

markers() {  # file -> "starts ends"
  local s e
  s=$(grep -c -F -- "$START" "$1" || true)
  e=$(grep -c -F -- "$END" "$1" || true)
  printf '%s %s' "$s" "$e"
}

assert_markers() {  # (f) after every run
  [ "$(markers "$1")" = "1 1" ] || fail "(f) marker counts in $1 are '$(markers "$1")', expected '1 1'"
}

fresh_root() {  # readme config -> path of a committed throwaway repository
  local d
  d=$(mktemp -d)
  cp "$1" "$d/README.md"
  cp "$2" "$d/profile.config.json"
  git -C "$d" init -q
  git -C "$d" config core.autocrlf false   # the builder writes LF; a Windows global autocrlf must not rewrite it
  git -C "$d" config user.email acceptance@example.invalid
  git -C "$d" config user.name acceptance
  git -C "$d" add -A
  git -C "$d" commit -q -m base
  printf '%s' "$d"
}

build() {  # root fixtures [flags...]
  local root=$1 fixtures=$2
  shift 2
  "$PY" tools/build_readme.py --root "$root" --fixtures "$fixtures" --no-meta "$@"
}

# ---------------------------------------------------------------- (a) idempotent
root=$(fresh_root README.md profile.config.json)
assert_markers "$root/README.md"
build "$root" fixtures/api > /dev/null
assert_markers "$root/README.md"
if cmp -s "$root/README.md" README.md; then
  echo "note  fixtures/api reproduces the committed README.md exactly"
else
  echo "note  fixtures/api is a snapshot; the committed README.md has moved on since it was taken (not a failure)"
fi
git -C "$root" add README.md
git -C "$root" commit -q -m "first run" --allow-empty
build "$root" fixtures/api > /dev/null
assert_markers "$root/README.md"
git -C "$root" diff --exit-code -- README.md > /dev/null || fail "(a) the second run changed README.md"
pass "(a) two consecutive fixture runs; the second left README.md untouched"

# ------------------------------------------------------- (b) black-holed network
before=$(sha256sum "$root/README.md")
set +e
env -u NO_PROXY -u no_proxy HTTPS_PROXY=$BLACKHOLE HTTP_PROXY=$BLACKHOLE https_proxy=$BLACKHOLE http_proxy=$BLACKHOLE \
  GITHUB_TOKEN=not-used-offline "$PY" tools/build_readme.py --root "$root" --no-meta > "$root/b.out" 2> "$root/b.err"
code=$?
set -e
[ "$code" -ne 0 ] || fail "(b) the builder exited 0 with the network black-holed"
grep -q 'left untouched' "$root/b.err" || fail "(b) the builder did not say it left README.md untouched: $(cat "$root/b.err")"
[ "$(sha256sum "$root/README.md")" = "$before" ] || fail "(b) README.md changed"
git -C "$root" diff --exit-code -- README.md > /dev/null || fail "(b) git diff is not clean"
assert_markers "$root/README.md"
pass "(b) black-holed network: exit $code, README.md byte-identical"

# ------------------------------------------------------------ (c) bad credential
if [ "$OFFLINE" = 1 ]; then
  skip "(c) invalid credential needs the network"
else
  set +e
  env -u HTTPS_PROXY -u HTTP_PROXY GITHUB_TOKEN=github_pat_invalid_for_acceptance_0000 \
    "$PY" tools/build_readme.py --root "$root" --no-meta > "$root/c.out" 2> "$root/c.err"
  code=$?
  set -e
  [ "$code" -ne 0 ] || fail "(c) the builder exited 0 with an invalid token"
  grep -q 'HTTP 401' "$root/c.err" || fail "(c) expected a 401 to be named: $(cat "$root/c.err")"
  [ "$(sha256sum "$root/README.md")" = "$before" ] || fail "(c) README.md changed"
  git -C "$root" diff --exit-code -- README.md > /dev/null || fail "(c) git diff is not clean"
  assert_markers "$root/README.md"
  pass "(c) invalid credential: exit $code, 401 named, README.md byte-identical"
fi
rm -rf "$root"

# ------------------------------------------------------------ (d) hostile fixture
hostile=$(fresh_root fixtures/hostile/README.in.md fixtures/hostile/config.json)
assert_markers "$hostile/README.md"
build "$hostile" fixtures/hostile > "$hostile/d.out"
assert_markers "$hostile/README.md"
if ! cmp -s "$hostile/README.md" fixtures/hostile/expected/README.md; then
  diff -u fixtures/hostile/expected/README.md "$hostile/README.md" >&2 || true
  fail "(d) the hostile fixture did not render to fixtures/hostile/expected/README.md"
fi
for secret in "TOP SECRET" "INTERNAL ONLY" "FORK DESCRIPTION" "ARCHIVED DESCRIPTION" "SELF DESCRIPTION" \
              secret-project internal-tool forked-thing old-archive; do
  grep -q -F -- "$secret" "$hostile/README.md" && fail "(d) '$secret' leaked into README.md"
done
reference=$(sha256sum "$hostile/README.md" | cut -d' ' -f1)
pass "(d) hostile fixture matches the expected snapshot byte-for-byte; nothing non-public leaked"

# ------------------------------------------------------------ (e) shuffled fixture
for seed in 1 2 3; do
  shuffled=$(mktemp -d)
  "$PY" - "$seed" fixtures/hostile "$shuffled" <<'PYEOF'
import json, random, shutil, sys
from pathlib import Path
seed, src, dst = int(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
shutil.copytree(src, dst, dirs_exist_ok=True)
rng = random.Random(seed)
repos = json.loads((dst / "repos.json").read_text(encoding="utf-8"))
rng.shuffle(repos)
(dst / "repos.json").write_text(json.dumps(repos), encoding="utf-8")
for path in (dst / "releases").glob("*.json"):
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        rng.shuffle(data)
        path.write_text(json.dumps(data), encoding="utf-8")
PYEOF
  again=$(fresh_root fixtures/hostile/README.in.md fixtures/hostile/config.json)
  build "$again" "$shuffled" > /dev/null
  assert_markers "$again/README.md"
  got=$(sha256sum "$again/README.md" | cut -d' ' -f1)
  [ "$got" = "$reference" ] || fail "(e) shuffle seed $seed rendered $got, expected $reference"
  rm -rf "$shuffled" "$again"
done
rm -rf "$hostile"
pass "(e) three shuffles of the hostile fixture rendered sha256 $reference"
pass "(f) marker counts were 1 and 1 before and after every run above"

# ------------------------------------------------------------- (g) pinned actions
unpinned=$(grep -rnE 'uses:' .github/workflows/ | grep -vE '@[0-9a-f]{40}' || true)
[ -z "$unpinned" ] || fail "(g) unpinned actions:"$'\n'"$unpinned"
pass "(g) every uses: in .github/workflows is pinned to a commit"

# ----------------------------------------------------------------- (h) live audit
if [ "$OFFLINE" = 1 ]; then
  skip "(h) scripts/audit.sh needs the network and gh auth"
else
  scripts/audit.sh || fail "(h) scripts/audit.sh exited non-zero"
  pass "(h) scripts/audit.sh exited 0 against the live repository"
fi

# ------------------------------------------------------- (i) unit tests, offline
env -u NO_PROXY -u no_proxy HTTPS_PROXY=$BLACKHOLE HTTP_PROXY=$BLACKHOLE https_proxy=$BLACKHOLE http_proxy=$BLACKHOLE \
  "$PY" -m unittest discover -s tests -t . 2> "${TMPDIR:-/tmp}/acceptance-tests.log" \
  || { cat "${TMPDIR:-/tmp}/acceptance-tests.log" >&2; fail "(i) unit tests failed"; }
pass "(i) unit tests passed with the network black-holed: $(grep -E '^Ran ' "${TMPDIR:-/tmp}/acceptance-tests.log")"

echo
echo "All acceptance checks passed."
