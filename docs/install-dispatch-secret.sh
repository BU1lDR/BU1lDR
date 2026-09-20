#!/usr/bin/env bash
# Store the profile dispatch token as a secret in every project repository that
# carries docs/notify-profile.yml, then prove the token works by sending one ping.
#
#   gh auth status                                  # must be logged in as the owner
#   ./docs/install-dispatch-secret.sh               # prompts for the token, hidden
#   ./docs/install-dispatch-secret.sh < token.txt   # or read it from a file / pipe
#
# The token itself has to come from a browser -- GitHub has no API for creating a
# personal access token. Fine-grained, repository access: only BU1lDR/BU1lDR,
# permission Contents: Read and write, nothing else, with an expiry. Steps are in
# docs/notify-profile.yml.
#
# Idempotent: re-running with a new token replaces the secret everywhere, which is
# how the expiry gets handled. The test dispatch at the end is what tells you the
# token is scoped correctly; `gh secret set` accepts any string.

set -euo pipefail

PROFILE_REPO="BU1lDR/BU1lDR"
PROJECT_REPOS=(
  BU1lDR/security-scanner
  BU1lDR/file-integrity-checker
  BU1lDR/retail-customer-segmentation
  BU1lDR/Portfolio
)
SECRET_NAME="PROFILE_DISPATCH_TOKEN"

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
if ! GH_TOKEN="$token" gh api -X POST "repos/${PROFILE_REPO}/dispatches" \
      -f event_type=project-updated \
      -f "client_payload[repository]=install-dispatch-secret" \
      -f "client_payload[sha]=test" 2> /tmp/dispatch.err; then
  echo "the token cannot send a repository_dispatch to ${PROFILE_REPO}:" >&2
  cat /tmp/dispatch.err >&2
  echo "It needs repository access to ${PROFILE_REPO} and Contents: Read and write." >&2
  exit 1
fi
echo "  ok -- ${PROFILE_REPO} received a test ping (its Update README workflow will run once)."

for repo in "${PROJECT_REPOS[@]}"; do
  printf '%s: ' "$repo"
  printf '%s' "$token" | gh secret set "$SECRET_NAME" -R "$repo"
  echo "secret ${SECRET_NAME} set"
done

echo
echo "Done. The next push to main in any of those repositories notifies the profile."
echo "Confirm from that repository's Actions tab: job 'Notify profile', step 'Tell BU1lDR/BU1lDR to rebuild its README' prints 'Profile notified.'"
