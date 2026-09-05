#!/usr/bin/env bash
# Runs on the VPS via cron. Config/secrets are loaded from an env file that
# is NOT part of the git repo (see github-stats.env.example).
set -euo pipefail

ENV_FILE="${ENV_FILE:-$HOME/.config/github-stats/env}"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

: "${REPO_DIR:?set REPO_DIR in $ENV_FILE}"
: "${GH_TOKEN:?set GH_TOKEN in $ENV_FILE}"
: "${GH_USERNAME:?set GH_USERNAME in $ENV_FILE}"

export GH_TOKEN GH_USERNAME
export GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh}"

cd "$REPO_DIR"

git fetch origin main
git checkout main
git reset --hard origin/main

python3 scripts/update_stats.py

git config user.name "${GIT_COMMITTER_NAME:-github-stats-bot}"
git config user.email "${GIT_COMMITTER_EMAIL:-github-stats-bot@localhost}"

git add profile/github-stats.svg

if git diff --cached --quiet; then
  echo "No statistics changes."
  exit 0
fi

git commit -m 'chore: update GitHub development stats'
git push origin main
