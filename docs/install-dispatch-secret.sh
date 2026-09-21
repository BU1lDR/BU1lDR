#!/usr/bin/env bash
# Store the profile dispatch token as a secret in every project repository that
# carries .github/workflows/notify-profile.yml, then prove the token works by sending
# one ping. The owner is whoever gh is logged in as; the profile repository is
# <owner>/<owner>; the project repositories are found by looking for the hook file,
# so a new repository that copied docs/notify-profile.yml is picked up without an
# edit here.
#
#   gh auth status                                  # must be logged in as the owner
#   ./docs/install-dispatch-secret.sh               # prompts for the token, hidden
#   ./docs/install-dispatch-secret.sh < token.txt   # or read it from a file / pipe
#
# The token itself has to come from a browser -- GitHub has no API for creating a
# personal access token. Fine-grained, repository access: only the profile
# repository, permission Contents: Read and write, nothing else. Steps are in
# docs/notify-profile.yml. Never paste the token into a chat, a ticket or a shell
# history; the hidden prompt and the pipe exist so it does not have to appear.
#
# Idempotent: re-running with a new token replaces the secret everywhere, which is
# how a rotation is done. The test dispatch is what tells you the token is scoped
# correctly; `gh secret set` accepts any string.

set -euo pipefail

OWNER=$(gh api user --jq .login)
PROFILE_REPO="${OWNER}/${OWNER}"
SECRET_NAME="PROFILE_DISPATCH_TOKEN"
HOOK_PATH=".github/workflows/notify-profile.yml"

if [ -t 0 ]; then
  read -r -s -p "Fine-grained token for ${PROFILE_REPO} (input hidden): " token
  echo
else
  token=$(cat)
fi
token=${token//[$'\r\n']/}
if [ -z "$token" ]; then
  echo "no token given" >&2
  exit 2
fi

echo "Checking the token can dispatch to ${PROFILE_REPO} ..."
err=$(mktemp)
if ! GH_TOKEN="$token" gh api -X POST "repos/${PROFILE_REPO}/dispatches" \
      -f event_type=project-updated \
      -f "client_payload[repository]=install-dispatch-secret" \
      -f "client_payload[sha]=test" 2> "$err"; then
  echo "the token cannot send a repository_dispatch to ${PROFILE_REPO}:" >&2
  cat "$err" >&2
  echo "It needs repository access to ${PROFILE_REPO} and Contents: Read and write." >&2
  rm -f "$err"
  exit 1
fi
rm -f "$err"
echo "  ok -- ${PROFILE_REPO} received a test ping (its Update README workflow will run once)."

echo "Looking for repositories that carry ${HOOK_PATH} ..."
found=0
while IFS=$'\t' read -r repo branch; do
  [ -n "$repo" ] || continue
  if gh api "repos/${OWNER}/${repo}/contents/${HOOK_PATH}?ref=${branch}" --jq .sha > /dev/null 2>&1; then
    found=$((found + 1))
    printf '%s/%s: ' "$OWNER" "$repo"
    printf '%s' "$token" | gh secret set "$SECRET_NAME" -R "${OWNER}/${repo}"
    echo "secret ${SECRET_NAME} set"
  fi
done < <(gh api "users/${OWNER}/repos?type=owner&per_page=100" --paginate \
           --jq '.[] | select(.fork == false and .archived == false and .name != "'"${OWNER}"'") | [.name, .default_branch] | @tsv')

if [ "$found" -eq 0 ]; then
  echo "No repository carries ${HOOK_PATH}. Copy docs/notify-profile.yml there first, then re-run." >&2
  exit 1
fi

echo
echo "Done: ${found} repositories. The next push to the default branch in any of them notifies the profile."
echo "Confirm from that repository's Actions tab: job 'Notify profile' prints 'Profile notified.'"
