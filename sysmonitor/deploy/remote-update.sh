#!/usr/bin/env bash
# REMOTE: pull new code from GitHub and restart the mirror website
# (run by sysmonitor-remote-update.timer every 5 minutes).
# The remote server never has its own code edits — it always matches GitHub.
# If new code fails `manage.py check` it is rolled back and not retried until
# GitHub has a newer commit.
set -u
REPO_DIR="${REPO_DIR:-/home/app-admin/sysmonitor/repo}"
VENV="${VENV:-/home/app-admin/sysmonitor/venv}"
BRANCH="${GIT_BRANCH:-main}"
cd "$REPO_DIR" || exit 1

git fetch -q origin "$BRANCH" || exit 0        # GitHub unreachable → try next time
OLD="$(git rev-parse HEAD)"; NEW="$(git rev-parse "origin/$BRANCH")"
[ "$OLD" = "$NEW" ] && exit 0
[ -f .sysmonitor-bad-commit ] && [ "$(cat .sysmonitor-bad-commit)" = "$NEW" ] && exit 0

git reset -q --hard "$NEW"
if git diff --name-only "$OLD" "$NEW" | grep -q 'requirements-mirror.txt'; then
  if ! "$VENV/bin/pip" install -q -r sysmonitor/requirements-mirror.txt; then
    echo "installing the new Python packages FAILED — rolled back to $OLD"
    git reset -q --hard "$OLD"; echo "$NEW" > .sysmonitor-bad-commit; exit 1
  fi
fi
if ! OUT="$( cd sysmonitor && "$VENV/bin/python" deploy/preflight.py 2>&1 )"; then
  echo "$OUT"; echo "new code ($NEW) FAILED the pre-flight check — rolled back to $OLD"
  git reset -q --hard "$OLD"; echo "$NEW" > .sysmonitor-bad-commit; exit 1
fi
sudo systemctl restart sysmonitor-remote-web && echo "updated $OLD -> $NEW"
