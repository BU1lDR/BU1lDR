#!/usr/bin/env bash
# Weekly audit of the README automation: exits non-zero on anything that means the
# profile is decaying without a red run to say so. Runs from .github/workflows/audit.yml
# and by hand:
#
#   scripts/audit.sh                  # uses gh's own login
#   GH_TOKEN=... scripts/audit.sh     # or a token; Actions passes github.token
#
# Needs gh, curl and python3, and a checkout of this repository. A depth-1 checkout
# is fine: nothing here reads git history. Freshness comes from meta/last-run.json,
# which the Update README workflow commits after every run, and from the Actions
# API -- never from `git log`, which under fetch-depth 1 has one commit to show.
#
# FAIL lines fail the audit. warn lines are printed and counted but do not; they are
# things a person should look at (a bot-blocked link, a secret not yet filled in).

set -uo pipefail
cd "$(dirname "$0")/.."

PY=${PYTHON:-$(command -v python3 || command -v python)}
REPO=${GITHUB_REPOSITORY:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}
OWNER=${REPO%%/*}
WORKFLOW=update-readme.yml
AUDIT_WORKFLOW=audit.yml
CRON_SECONDS=3600                     # the schedule in update-readme.yml is hourly
FRESH_LIMIT=$((2 * CRON_SECONDS + 900))   # two intervals plus a run's worth of slack
README_LIMIT=$((100 * 1024))
UA="profile-readme-audit (+https://github.com/$REPO)"

failures=0
warnings=0
ok()   { printf 'ok    %s\n' "$*"; }
info() { printf 'info  %s\n' "$*"; }   # could not be checked from here; not counted
warn() { printf 'warn  %s\n' "$*"; warnings=$((warnings + 1)); }
bad()  { printf 'FAIL  %s\n' "$*"; failures=$((failures + 1)); }

now_epoch() { "$PY" -c 'import time; print(int(time.time()))'; }
to_epoch() {  # ISO-8601 UTC instant -> epoch seconds
  "$PY" -c 'import sys, datetime as d
t = d.datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00"))
print(int(t.timestamp()))' "$1"
}

echo "audit of $REPO"

# ------------------------------------------------------------- workflows are alive
default_branch=$(gh api "repos/$REPO" --jq .default_branch 2>/dev/null) || default_branch=""
if [ -z "$default_branch" ]; then
  bad "cannot read the repository ($REPO) through the API"
else
  ok "default branch is $default_branch"
fi

for wf in "$WORKFLOW" "$AUDIT_WORKFLOW"; do
  state=$(gh api "repos/$REPO/actions/workflows/$wf" --jq .state 2>/dev/null) || state="unreadable"
  if [ "$state" = "active" ]; then
    ok "workflow $wf is active"
  else
    bad "workflow $wf state is '$state' (disabled_inactivity means 60 days without a commit; re-enable it in the Actions tab)"
  fi
  if [ -n "$default_branch" ]; then
    if gh api "repos/$REPO/contents/.github/workflows/$wf?ref=$default_branch" --jq .sha > /dev/null 2>&1; then
      ok "workflow $wf exists on $default_branch (schedules register from the default branch only)"
    else
      bad "workflow $wf is not on $default_branch; its schedule is not registered"
    fi
  fi
done

# ------------------------------------------------------------- the last run record
META=meta/last-run.json
now=$(now_epoch)
if [ ! -s "$META" ]; then
  bad "$META is missing or empty: no run has been recorded, so freshness cannot be shown"
else
  finished=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("finished_at") or "")' "$META")
  status=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("status") or "")' "$META")
  error=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("error") or "")' "$META")
  if [ -z "$finished" ]; then
    bad "$META has no finished_at"
  else
    age=$(( now - $(to_epoch "$finished") ))
    if [ "$age" -le "$FRESH_LIMIT" ]; then
      ok "last recorded run finished ${age}s ago ($finished)"
    else
      bad "last recorded run finished ${age}s ago ($finished); the limit is ${FRESH_LIMIT}s -- the hourly run is not committing"
    fi
  fi
  if [ "$status" = "ok" ]; then
    ok "last recorded run status: ok"
  else
    bad "last recorded run status: '$status' -- $error"
  fi
fi

newest=$(gh api "repos/$REPO/actions/workflows/$WORKFLOW/runs?status=success&per_page=1" \
           --jq '.workflow_runs[0].run_started_at // empty' 2>/dev/null) || newest=""
if [ -z "$newest" ]; then
  bad "the Actions API shows no successful run of $WORKFLOW"
else
  age=$(( now - $(to_epoch "$newest") ))
  if [ "$age" -le "$FRESH_LIMIT" ]; then
    ok "newest successful run started ${age}s ago ($newest)"
  else
    bad "newest successful run started ${age}s ago ($newest); the limit is ${FRESH_LIMIT}s"
  fi
fi

# ---------------------------------------------------- README.md and the managed block
while IFS= read -r line; do
  case "$line" in
    "problem: "*) bad "${line#problem: }" ;;
    "warning: "*) warn "${line#warning: }" ;;
    "ok: "*)      ok "${line#ok: }" ;;
    *)            [ -n "$line" ] && echo "$line" ;;
  esac
done < <("$PY" - "$README_LIMIT" <<'PYEOF'
import json, re, sys
from pathlib import Path
sys.path.insert(0, "tools")
import build_readme as br

limit = int(sys.argv[1])
config = br.load_config(Path("."))
start, end = config.get("markers", ["<!-- work:start -->", "<!-- work:end -->"])
raw = Path("README.md").read_bytes()
text = raw.decode("utf-8")
if len(raw) > limit:
    print(f"problem: README.md is {len(raw):,} bytes; the limit is {limit:,}")
else:
    print(f"ok: README.md is {len(raw):,} bytes")
if b"\r" in raw:
    print("problem: README.md has CRLF line endings; the builder will refuse it")
counts = (text.count(start), text.count(end))
if counts != (1, 1):
    print(f"problem: marker counts are {counts}; expected one START and one END")
    sys.exit(0)
print("ok: one START and one END marker")
inner = br.inner_block(text, start, end)
marks = sum(1 for line in inner.split("\n") if br.SECTION_MARK.match(line))
stamps = [br.BUILD_STAMP.match(line) for line in text.split("\n") if br.BUILD_STAMP.match(line)]
if len(stamps) != 1:
    print(f"problem: {len(stamps)} build stamp lines; expected exactly one")
else:
    stamp = stamps[0]
    if int(stamp.group("sections")) != marks:
        print(f"problem: the build stamp says {stamp.group('sections')} sections but the block has {marks} section marks")
    elif stamp.group("digest") != br.digest(inner):
        print("problem: the block's digest does not match its stamp: it was edited by hand since the last build "
              "(the next run overwrites the edit; make it in the repository's PROFILE.md instead)")
    else:
        print(f"ok: build stamp agrees with the block ({marks} sections, digest {stamp.group('digest')})")
meta_path = Path("meta/last-run.json")
if meta_path.exists():
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("sections") is not None and meta["sections"] != marks:
        print(f"problem: meta/last-run.json recorded {meta['sections']} sections but README.md has {marks}")
    before, after = meta.get("block_bytes_before") or 0, meta.get("block_bytes") or 0
    if before and after < 0.8 * before:
        removed = any(str(c).endswith(": removed") for c in meta.get("changes") or [])
        note = "explained by a removed repository" if removed else "allowed by --allow-shrink"
        print(f"warning: the last build shrank the block from {before:,} to {after:,} bytes ({note}); look at the page")
    if marks == 0:
        print("problem: the block has no sections")
for pattern, label in (
    (r"\bundefined\b", "undefined"), (r"\bNaN\b", "NaN"), (r"\[object Object\]", "[object Object]"),
    (r"Maximum retries", "Maximum retries"), (r"Bad credentials", "Bad credentials"),
    (r"Traceback \(most recent", "a Python traceback"), (r"<class '", "a Python repr"), (r"\bNone\b", "None"),
    (r"\{(?:version|license|live|description|name|url)\}", "an unfilled placeholder"),
):
    if re.search(pattern, text):
        print(f"problem: README.md contains {label}")
print("ok: none of undefined/NaN/[object Object]/None/Maximum retries/placeholder text is present")
PYEOF
)

# ------------------------------------------------------------------- images and links
probe() {  # url -> "code content-type"
  curl -sSL -o /dev/null -m 20 --retry 2 -A "$UA" -w '%{http_code} %{content_type}' "$1" 2>/dev/null || echo "000 unreachable"
}

images=$(grep -oE '<img[^>]+src="[^"]+"|!\[[^]]*\]\([^) ]+' README.md \
         | sed -E 's/.*src="([^"]+)".*/\1/; s/.*\]\(([^) ]+).*/\1/' | sort -u || true)
if [ -z "$images" ]; then
  ok "README.md embeds no images (nothing to check for 2xx image/*)"
else
  while IFS= read -r url; do
    [ -n "$url" ] || continue
    case "$url" in
      http*) ;;
      *) url="https://github.com/$REPO/raw/${default_branch:-main}/${url#./}" ;;  # a relative image resolves here on github.com
    esac
    result=$(probe "$url")
    code=${result%% *}
    type=${result#* }
    case "$code" in
      2*) case "$type" in
            image/*) ok "image $url -> $code $type" ;;
            *) bad "image $url -> $code but content-type '$type' is not image/* (a card service returning an error page?)" ;;
          esac ;;
      *) bad "image $url -> $code $type" ;;
    esac
  done <<< "$images"
fi

links=$(grep -oE 'https?://[^][:space:])<>"'"'"'`]+' README.md | sed -E 's/[.,;:]+$//' | sort -u || true)
while IFS= read -r url; do
  [ -n "$url" ] || continue
  result=$(probe "$url")
  code=${result%% *}
  host=$(printf '%s' "$url" | sed -E 's#^https?://([^/]+).*#\1#')
  case "$code" in
    2*) ok "link $url -> $code" ;;
    *)
      case "$host" in
        github.com|*.github.io|raw.githubusercontent.com|*.githubusercontent.com)
          bad "link $url -> $code (GitHub-hosted; this should never fail)" ;;
        *)
          warn "link $url -> $code (third-party host; 999/403/503 from a datacenter IP usually means bot protection, not a dead link -- check it in a browser)" ;;
      esac ;;
  esac
done <<< "$links"

# ---------------------------------------------------------------- supply chain
unpinned=$(grep -rnE 'uses:' .github/workflows/ | grep -vE '@[0-9a-f]{40}' || true)
if [ -z "$unpinned" ]; then
  ok "every uses: in .github/workflows is pinned to a commit"
else
  bad "unpinned actions:"$'\n'"$unpinned"
fi
if [ -f .github/dependabot.yml ] && grep -q 'github-actions' .github/dependabot.yml; then
  ok "Dependabot watches github-actions"
else
  bad ".github/dependabot.yml does not cover github-actions"
fi

# ----------------------------------------------------------- secrets and variables
referenced=$(grep -rhoE 'secrets\.[A-Za-z0-9_]+' .github/workflows/ | sed 's/^secrets\.//' | sort -u || true)
have=$(gh api "repos/$REPO/actions/secrets" --jq '.secrets[].name' 2>/dev/null | sort -u) || have="__unreadable__"
if [ "$have" = "__unreadable__" ]; then
  info "secrets cannot be listed with this token (GITHUB_TOKEN never can); run scripts/audit.sh locally for the inventory"
else
  for name in $referenced; do
    if printf '%s\n' "$have" | grep -qx "$name"; then
      ok "secret $name is referenced and set"
    else
      case "$name" in
        HEALTHCHECK_URL) warn "secret HEALTHCHECK_URL is referenced but not set: no external monitor is pinged yet (see the header of update-readme.yml)" ;;
        *) warn "secret $name is referenced by a workflow but not set (an unset secret expands to an empty string)" ;;
      esac
    fi
  done
  for name in $have; do
    printf '%s\n' "$referenced" | grep -qx "$name" || warn "secret $name is set but no workflow references it (orphan)"
  done
fi
vars=$(gh api "repos/$REPO/actions/variables" --jq '.variables[].name' 2>/dev/null | sort -u) || vars=""
for name in $vars; do
  grep -rqE "vars\.$name\b" .github/workflows/ || warn "variable $name is set but no workflow references it (orphan)"
done

perm=$(gh api "repos/$REPO/actions/permissions/workflow" --jq .default_workflow_permissions 2>/dev/null) || perm="unreadable"
if [ "$perm" = "read" ]; then
  ok "default GITHUB_TOKEN permissions are read-only; the workflow asks for contents: write itself"
elif [ "$perm" = "unreadable" ]; then
  info "default GITHUB_TOKEN permissions need an admin token to read; run scripts/audit.sh locally to check them"
else
  warn "default GITHUB_TOKEN permissions are '$perm'; set them to read in Settings > Actions > General"
fi

# --------------------------------------------- the dispatch token, seen by its effect
# The PROFILE_DISPATCH_TOKEN lives in the project repositories, not here, and a
# fine-grained token's expiry is not queryable by anyone but its owner. What can be
# seen is its effect: a project repository that carries docs/notify-profile.yml and
# whose default branch moved after the newest repository_dispatch reached this
# repository has a hook that is not delivering -- a revoked or expired token, or
# the secret gone. GitHub also revokes any token unused for a year.
newest_dispatch=$(gh api "repos/$REPO/actions/runs?event=repository_dispatch&per_page=1" \
                    --jq '.workflow_runs[0].created_at // empty' 2>/dev/null) || newest_dispatch=""
dispatch_epoch=0
[ -n "$newest_dispatch" ] && dispatch_epoch=$(to_epoch "$newest_dispatch")
hooked=0
while IFS=$'\t' read -r name branch; do
  [ -n "$name" ] || continue
  if ! gh api "repos/$OWNER/$name/contents/.github/workflows/notify-profile.yml?ref=$branch" --jq .sha > /dev/null 2>&1; then
    continue
  fi
  hooked=$((hooked + 1))
  tip=$(gh api "repos/$OWNER/$name/commits/$branch" --jq '.commit.committer.date' 2>/dev/null) || tip=""
  [ -n "$tip" ] || { warn "cannot read the tip of $OWNER/$name@$branch"; continue; }
  tip_epoch=$(to_epoch "$tip")
  if [ "$tip_epoch" -gt $((dispatch_epoch + 1800)) ]; then
    bad "$OWNER/$name carries the notify-profile hook and its $branch moved at $tip, but no repository_dispatch has reached $REPO since ${newest_dispatch:-ever}: the hook is not delivering (PROFILE_DISPATCH_TOKEN revoked, expired, unused for a year, or the secret removed). The hourly schedule still covers content; re-run docs/install-dispatch-secret.sh with a new token."
  else
    ok "$OWNER/$name: hook present; last push to $branch ($tip) was followed by a dispatch"
  fi
done < <(gh api "users/$OWNER/repos?type=owner&per_page=100" \
           --jq '.[] | select(.fork == false and .archived == false and .private == false and .name != "'"$OWNER"'") | [.name, .default_branch] | @tsv' 2>/dev/null || true)
[ "$hooked" -gt 0 ] || warn "no project repository carries .github/workflows/notify-profile.yml; the profile updates on the hour only"

# ----------------------------------------------------------------------- verdict
echo
if [ "$failures" -eq 0 ]; then
  echo "audit passed: 0 failures, $warnings warnings"
  exit 0
fi
echo "audit FAILED: $failures failures, $warnings warnings"
exit 1
