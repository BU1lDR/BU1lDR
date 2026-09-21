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
# The schedule in update-readme.yml is hourly, but GitHub runs a public repository's
# schedule late and unevenly -- gaps of two to five hours between ticks are normal
# here -- so a two-interval limit would cry wolf most Mondays. Freshness is judged
# over a day: a day with no successful run is wrong whatever the scheduler does, and
# the hazard that matters is the 60-day shutdown. A late scheduler is a warning.
FRESH_LIMIT=$((26 * 3600))
LATE_SCHEDULE=$((6 * 3600))
# Beyond this the scheduler has not been late, it has stopped. The freshness check above
# counts any successful run, so pushes and dispatches were able to mask a dead cron
# indefinitely -- and the cron is the only thing that notices a release or a description
# edit in a repository that has not pushed.
STALE_SCHEDULE=$((48 * 3600))
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
      bad "last recorded run finished ${age}s ago ($finished); the limit is ${FRESH_LIMIT}s -- no run has committed a record in a day"
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

newest_sched=$(gh api "repos/$REPO/actions/workflows/$WORKFLOW/runs?event=schedule&per_page=1" \
                 --jq '.workflow_runs[0].run_started_at // empty' 2>/dev/null) || newest_sched=""
if [ -z "$newest_sched" ]; then
  warn "no scheduled run of $WORKFLOW appears in the run history; pushes and dispatches are doing all the work"
else
  age=$(( now - $(to_epoch "$newest_sched") ))
  if [ "$age" -le "$LATE_SCHEDULE" ]; then
    ok "newest scheduled run started ${age}s ago ($newest_sched)"
  elif [ "$age" -le "$STALE_SCHEDULE" ]; then
    warn "newest scheduled run started ${age}s ago ($newest_sched); GitHub's scheduler is running late (gaps of two to eight hours are normal on this account). Nothing to do unless the limits above fail"
  else
    bad "no scheduled run of $WORKFLOW in ${age}s ($newest_sched); the limit is ${STALE_SCHEDULE}s. The cron has stopped, and pushes or dispatches have been hiding it -- without the cron, a release or a description edit in a repository that has not pushed never reaches the profile. Check the Actions tab for a disabled schedule."
  fi
fi

# ---------------------------------------------------- README.md and the managed block
# Run to a file and check the status, never through a process substitution: the exit
# status of `done < <(cmd)` is the status of `done`, so every way this block could die
# -- README.md gone, profile.config.json gone, a renamed function in build_readme.py,
# a SyntaxError from a bad edit -- used to print nothing, count nothing, and let the
# audit report "audit passed: 0 failures". The audit's whole subject is this file; an
# audit that inspected nothing must not be able to say it looked.
readme_report=$(mktemp)
if ! "$PY" - "$README_LIMIT" > "$readme_report" 2>&1 <<'PYEOF'
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
# Strings that are never English: a failure anywhere in the file.
never_english = (
    (r"\[object Object\]", "[object Object]"), (r"Maximum retries", "Maximum retries"),
    (r"Bad credentials", "Bad credentials"), (r"Traceback \(most recent", "a Python traceback"),
    (r"<class '", "a Python repr"),
)
hits = [label for pattern, label in never_english if re.search(pattern, text)]
for label in hits:
    print(f"problem: README.md contains {label}")
if not hits:
    print("ok: none of [object Object] / Maximum retries / Bad credentials / a traceback / a repr is present")
counts = (text.count(start), text.count(end))
if counts != (1, 1):
    # Everything below needs the block. The scan above did not, which is why it ran first.
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
# Words that are English as often as they are a bug ("Dependencies: None"): a warning,
# and only inside the block, where API text lands. An unfilled placeholder is not
# checked for: substitute() is single-pass and the digest above proves the block is
# exactly what the builder wrote, so a "{version}" there is the documented
# {{version}} literal, not a miss.
soft = [word for word in ("None", "undefined", "NaN") if re.search(rf"\b{word}\b", inner)]
if soft:
    print(f"warning: the block contains the word(s) {', '.join(soft)}; check on the page that they are prose, not a rendered null")
PYEOF
then
  bad "the README checks did not run: python exited non-zero. This script imports tools/build_readme.py (load_config, inner_block, digest, SECTION_MARK, BUILD_STAMP); if any of those moved, this script has to move with them. Its output follows."
fi
while IFS= read -r line; do
  case "$line" in
    "problem: "*) bad "${line#problem: }" ;;
    "warning: "*) warn "${line#warning: }" ;;
    "ok: "*)      ok "${line#ok: }" ;;
    *)            [ -n "$line" ] && echo "      $line" ;;
  esac
done < "$readme_report"
rm -f "$readme_report"

# --------------------------------------------- the block still matches the repositories
# The digest check above proves the block is exactly what the builder last wrote. It says
# nothing about whether that is still true of the repositories it describes, which is the
# one question a reader of the profile actually has. The builder already answers it:
# --check exits 1 when a rebuild would differ. A stale README here means the hourly run
# has stopped propagating even though it is green, which no other check in this file sees.
check_token=${GH_TOKEN:-${GITHUB_TOKEN:-$(gh auth token 2>/dev/null)}}
if [ -z "$check_token" ]; then
  info "no token available, so the block was not compared against the live repositories (--check skipped)"
else
  check_out=$(GITHUB_TOKEN="$check_token" "$PY" tools/build_readme.py --check --no-meta 2>&1)
  case $? in
    0) ok "the block still matches the repositories it describes (build_readme.py --check)" ;;
    1) bad "the block is out of date against the live repositories:"$'\n'"$(printf '%s\n' "$check_out" | sed 's/^/      /')"$'\n'"      If one of those repositories changed in the last hour or two, the next scheduled run carries it over and a re-run of this audit passes. If it does not, the hourly run is green and not propagating, which no other check here would notice." ;;
    *) warn "build_readme.py --check could not answer (exit 2); the hourly run is the authority on build health and is checked above:"$'\n'"$(printf '%s\n' "$check_out" | tail -3 | sed 's/^/      /')" ;;
  esac
fi

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
          # A 404 or 410 from GitHub is a real dead link. A 429 or a 5xx is GitHub rate-
          # limiting or wobbling, which it does to Actions runner IPs, and failing the
          # weekly audit for it trains the owner to ignore a red audit -- the same damage
          # as a silent pass, from the other direction.
          case "$code" in
            429|5??|000) warn "link $url -> $code (GitHub-hosted, but a rate limit or a wobble rather than a dead link; re-run the audit)" ;;
            *) bad "link $url -> $code (GitHub-hosted; this should never fail)" ;;
          esac ;;
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
# seen is its effect, per repository: the hook's own last run (skipped step = the
# secret is missing there), and whether a repository_dispatch reached this
# repository within fifteen minutes of it. The hook's step is soft-fail by design,
# so a dead token still shows a green hook run; the arrival is the truth. GitHub
# also revokes any token unused for a year.
shift_iso() {  # ISO instant, minutes -> ISO instant
  "$PY" -c 'import sys, datetime as d
t = d.datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00")) + d.timedelta(minutes=int(sys.argv[2]))
print(t.strftime("%Y-%m-%dT%H:%M:%SZ"))' "$1" "$2"
}
newest_dispatch=$(gh api "repos/$REPO/actions/runs?event=repository_dispatch&per_page=1" \
                    --jq '.workflow_runs[0].created_at // empty' 2>/dev/null | tr -d '\r') || newest_dispatch=""
dispatch_epoch=0
[ -n "$newest_dispatch" ] && dispatch_epoch=$(to_epoch "$newest_dispatch")
hooked=0
listing_failed=0
hook_repos=$(mktemp)
if ! gh api "users/$OWNER/repos?type=owner&per_page=100" --paginate \
       --jq '.[] | select(.fork == false and .archived == false and .private == false and .name != "'"$OWNER"'") | [.name, .default_branch] | @tsv' 2>/dev/null \
     | tr -d '\r' > "$hook_repos"; then
  listing_failed=1
fi
while IFS=$'\t' read -r name branch; do
  [ -n "$name" ] || continue
  if ! gh api "repos/$OWNER/$name/contents/.github/workflows/notify-profile.yml?ref=$branch" --jq .sha > /dev/null 2>&1; then
    continue
  fi
  hooked=$((hooked + 1))
  # run_started_at is the start of the latest attempt (a re-run moves it; created_at
  # does not), which is what the arrival window below must be anchored on. Every field
  # gets a placeholder: tab is IFS whitespace, and an empty field would shift the rest.
  last=$(gh api "repos/$OWNER/$name/actions/workflows/notify-profile.yml/runs?branch=$branch&per_page=1" \
           --jq '.workflow_runs[0] | select(. != null) | [.id, (.run_started_at // .created_at), (.conclusion // "-"), (.head_sha // "-")] | @tsv' 2>/dev/null | tr -d '\r') || last=""
  if [ -z "$last" ]; then
    info "$OWNER/$name: hook present; its runs cannot be read from here, or it has not run yet"
    continue
  fi
  IFS=$'\t' read -r run_id run_created run_conclusion run_sha <<< "$last"
  if [ "$run_conclusion" = "-" ]; then
    info "$OWNER/$name: the hook's latest run ($run_created, ${run_sha:0:7}) has not finished yet"
    continue
  fi
  step=$(gh api "repos/$OWNER/$name/actions/runs/$run_id/jobs" \
           --jq '[.jobs[].steps[] | select(.name | startswith("Tell "))][0].conclusion // "absent"' 2>/dev/null | tr -d '\r') || step="unreadable"
  case "$step" in
    success) ;;
    skipped)
      # The step skips when the secret is absent. Whether it is absent *now* cannot
      # be read from here; what can is whether this run predates the newest dispatch
      # any repository delivered -- if so the secret was probably installed after it.
      if [ "$(to_epoch "$run_created")" -lt "$dispatch_epoch" ]; then
        info "$OWNER/$name: its last hook run ($run_created, ${run_sha:0:7}) skipped the dispatch step, but that run predates the newest dispatch from another repository ($newest_dispatch); the next push here will show whether the secret is set"
      else
        warn "$OWNER/$name: the hook ran for ${run_sha:0:7} at $run_created but its dispatch step was skipped -- PROFILE_DISPATCH_TOKEN is not set there (run docs/install-dispatch-secret.sh)"
      fi
      continue ;;
    *)
      warn "$OWNER/$name: the hook's last run ($run_conclusion, $run_created) has dispatch step state '$step'"
      continue ;;
  esac
  from=$(shift_iso "$run_created" -1)
  to=$(shift_iso "$run_created" 15)
  arrived=$(gh api "repos/$REPO/actions/runs?event=repository_dispatch&created=${from}..${to}&per_page=5" \
              --jq '.workflow_runs | length' 2>/dev/null | tr -d '\r') || arrived="unreadable"
  if [ "$arrived" = "unreadable" ]; then
    info "$OWNER/$name: hook ran at $run_created; this repository's run history cannot be read from here to confirm arrival"
  elif [ "$arrived" -gt 0 ]; then
    ok "$OWNER/$name: hook run at $run_created (${run_sha:0:7}) was followed by a repository_dispatch here"
  else
    bad "$OWNER/$name: the hook ran at $run_created (${run_sha:0:7}) and its dispatch step reported success, but no repository_dispatch reached $REPO within fifteen minutes of it. The dispatch is failing -- PROFILE_DISPATCH_TOKEN revoked, expired, unused for a year, or scoped wrong. The hourly schedule still covers content; re-run docs/install-dispatch-secret.sh with a new token."
  fi
done < "$hook_repos"
rm -f "$hook_repos"
if [ "$listing_failed" = 1 ]; then
  bad "could not list $OWNER's repositories, so no hook was checked (the same silent-skip shape as the README block above: a process substitution's failure is invisible, so this is read from a file)"
elif [ "$hooked" -eq 0 ]; then
  warn "no project repository carries .github/workflows/notify-profile.yml; the profile updates on the hour only"
fi

# ----------------------------------------------------------------------- verdict
echo
if [ "$failures" -eq 0 ]; then
  echo "audit passed: 0 failures, $warnings warnings"
  exit 0
fi
echo "audit FAILED: $failures failures, $warnings warnings"
exit 1
