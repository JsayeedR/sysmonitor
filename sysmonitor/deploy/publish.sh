#!/usr/bin/env bash
# MASTER: publish your finished SysMonitor code to GitHub (the remote server then
# picks it up by itself within 5 minutes).
#
#   ./deploy/publish.sh          shows what would be published, asks you to confirm
#   ./deploy/publish.sh --yes    no question (for scripts)
#
# It touches ONLY the sysmonitor project folder (+ the repository's .gitignore).
# Other projects in the same Git repository (e.g. ../coxcls-access-system) are never
# staged or committed. Their uncommitted (unstaged) work is left alone; if they have
# STAGED changes the script stops and tells you.
#
# It refuses to publish when:
#   • a secret or data file would be included (.env, *.sqlite3, media/, backups/)
#   • the pre-flight check fails (syntax of every .py file, every template, Django check)
set -u
PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(git -C "$PROJECT" rev-parse --show-toplevel)" || exit 1
REL="${PROJECT#"$REPO"/}"                       # e.g. "sysmonitor"
PY="${PYTHON:-$PROJECT/venv/bin/python}"; [ -x "$PY" ] || PY=python3
BRANCH="${GIT_BRANCH:-main}"
ASSUME_YES=0; [ "${1:-}" = "--yes" ] && ASSUME_YES=1
cd "$REPO" || exit 1

# Never rebase/autostash this repository automatically: its Git root also contains
# other projects. Fetching is read-only for the working tree/index. If origin/main
# contains work that this checkout does not have, stop and let an operator reconcile
# it deliberately before SysMonitor is published.
CURRENT_BRANCH="$(git symbolic-ref --short -q HEAD || true)"
if [ "$CURRENT_BRANCH" != "$BRANCH" ]; then
  echo "Not publishing: current branch is '${CURRENT_BRANCH:-detached}', expected '$BRANCH'."
  exit 1
fi
if ! git fetch -q origin "$BRANCH"; then
  echo "Not publishing: could not fetch origin/$BRANCH. Nothing was staged or committed."
  exit 1
fi
LOCAL_HEAD="$(git rev-parse HEAD)"
REMOTE_HEAD="$(git rev-parse "origin/$BRANCH")"
if [ "$LOCAL_HEAD" != "$REMOTE_HEAD" ] && ! git merge-base --is-ancestor "$REMOTE_HEAD" "$LOCAL_HEAD"; then
  echo "Not publishing: origin/$BRANCH has commits that are not in this checkout (or histories diverged)."
  echo "Local : $LOCAL_HEAD"
  echo "Remote: $REMOTE_HEAD"
  echo "Reconcile them manually first; this script will not pull, rebase or autostash."
  exit 1
fi

# keep secrets/data/mirror files out of git (adds missing lines to the repo's .gitignore)
for line in '.env' '.env.*' '!.env.*example' '*.sqlite3' 'media/' '**/backups/' \
            'mirror_current' 'mirror_meta.json' '.mirror_*' '.mirror-*' \
            '*.backup_*' '*.pre-patch*' '*.bak' '.sysmonitor-bad-commit'; do
  grep -qxF -- "$line" .gitignore 2>/dev/null || echo "$line" >> .gitignore
done

# Other projects in this repository must not have STAGED changes: we never touch
# another project's staging area, so if there are any, stop and let you decide.
OTHER_STAGED="$(git diff --cached --name-only | grep -v "^${REL}/" | grep -vx '\.gitignore' || true)"
if [ -n "$OTHER_STAGED" ]; then
  echo "Other projects already have STAGED changes in this repository:"
  echo "$OTHER_STAGED" | sed 's/^/   /'
  echo "Not publishing SysMonitor. Commit or unstage those first (git restore --staged <file>), then run again."
  exit 1
fi

# stage ONLY this project and .gitignore (never `git add -A` on the whole repository)
git add -A -- "$REL" .gitignore
CHANGED="$(git diff --cached --name-only -- "$REL" .gitignore)"
if [ -z "$CHANGED" ]; then echo "Nothing new to publish."; git reset -q -- "$REL" .gitignore; exit 0; fi

BAD="$(echo "$CHANGED" | grep -vE '\.example$' | grep -E '(^|/)\.env(\.|$)|\.sqlite3|(^|/)media/|(^|/)backups/' || true)"
if [ -n "$BAD" ]; then
  echo "REFUSING to publish secret/data files:"; echo "$BAD"; git reset -q -- "$REL" .gitignore; exit 1
fi
if ! OUT="$( cd "$PROJECT" && "$PY" deploy/preflight.py 2>&1 )"; then
  echo "$OUT"; echo "NOT publishing: fix the problem above first."
  git reset -q -- "$REL" .gitignore; exit 1
fi

echo "These changes will be published to GitHub ($BRANCH):"
git diff --cached --stat -- "$REL" .gitignore | tail -n 25
if [ "$ASSUME_YES" -ne 1 ]; then
  read -r -p "Publish now? [y/N] " ans
  case "$ans" in y|Y|yes|YES) ;; *) echo "Cancelled — nothing was published."; git reset -q -- "$REL" .gitignore; exit 0;; esac
fi

MSG="SysMonitor: $(date '+%F %T') — $(echo "$CHANGED" | head -5 | tr '\n' ' ')"
# `-- paths` commits only these paths, whatever else is staged in the repository
git commit -q -m "$MSG" -- "$REL" .gitignore || exit 1
if git push -q origin "HEAD:$BRANCH"; then
  echo "Published. The remote server updates itself within 5 minutes."
else
  echo "Push was refused or failed. Your SysMonitor commit is safe locally; no pull/rebase/autostash was attempted."
  echo "Inspect origin/$BRANCH and reconcile deliberately before trying again."
  exit 1
fi
