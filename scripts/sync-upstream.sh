#!/usr/bin/env bash
# Merge in a disposable clone. Only publish sync branches, PRs and conflict issues.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
FORK=brucetoo/DigUp
ORIGIN=https://github.com/brucetoo/DigUp.git
UPSTREAM=https://github.com/ARahim3/DigUp.git
PUBLISH=1
REPORT=

usage() {
  echo "Usage: $0 [--origin URL_OR_LOCAL_PATH] [--upstream URL_OR_LOCAL_PATH]"
  echo "          [--dry-run | --no-publish] [--report PATH]"
  echo "Dry-run/no-publish still fetch, merge and check in a temporary clone."
}
while [[ $# -gt 0 ]]; do
  case "$1" in
    --origin|--upstream|--report)
      [[ $# -ge 2 && -n "$2" ]] || { usage >&2; exit 2; }
      case "$1" in
        --origin) ORIGIN=$2 ;;
        --upstream) UPSTREAM=$2 ;;
        --report) REPORT=$2 ;;
      esac
      shift 2 ;;
    --dry-run|--no-publish) PUBLISH=0; shift ;;
    --help|-h) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done
command -v git >/dev/null
command -v python3 >/dev/null
if [[ $PUBLISH == 1 ]]; then command -v gh >/dev/null; fi
# Convert explicit local remotes before changing directories.
[[ ! -d "$ORIGIN" ]] || ORIGIN=$(cd "$ORIGIN" && pwd)
[[ ! -d "$UPSTREAM" ]] || UPSTREAM=$(cd "$UPSTREAM" && pwd)
if [[ -n "$REPORT" ]]; then
  REPORT=$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$REPORT")
fi
WORK=$(mktemp -d "${TMPDIR:-/tmp}/digup-sync.XXXXXXXX")
cleanup() {
  if [[ -n "$REPORT" && -f "$WORK/report.md" ]]; then
    cp "$WORK/report.md" "$REPORT" || true
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
finish_report() {
  cat "$WORK/report.md"
  if [[ -n "$REPORT" ]]; then cp "$WORK/report.md" "$REPORT"; fi
}
# No checkout/fetch/reset touches the caller's repository, even if it is dirty.
git clone --no-checkout --no-local -- "$ORIGIN" "$WORK/repo"
cd "$WORK/repo"
git config user.name "DigUp upstream sync"
git config user.email "digup-sync@users.noreply.github.com"
git config core.hooksPath /dev/null
git config commit.gpgsign false
git fetch --no-tags origin refs/heads/main:refs/remotes/origin/main
git fetch --no-tags -- "$UPSTREAM" refs/heads/main:refs/remotes/upstream/main
TARGET=$(git rev-parse refs/remotes/upstream/main)
MAIN=$(git rev-parse refs/remotes/origin/main)
BRANCH="sync/upstream-$TARGET"
BASE=$(git merge-base "$MAIN" "$TARGET") || {
  echo "No shared history; refusing unrelated-history merge." >&2
  exit 1
}
if git show-ref --verify --quiet refs/remotes/origin/upstream-main; then
  BASE=$(git rev-parse refs/remotes/origin/upstream-main)
  git merge-base --is-ancestor "$BASE" "$TARGET" || {
    echo "upstream-main is not pristine upstream history; refusing rewind/divergence." >&2
    exit 1
  }
  git merge-base --is-ancestor "$BASE" "$MAIN" || {
    echo "upstream-main is ahead of fork main; review the baseline manually." >&2
    exit 1
  }
fi
{
  printf '# Upstream sync %s\n\n' "$TARGET"
  printf 'Fork: `%s`, main: `%s`\n\n' "$FORK" "$MAIN"
  printf 'Upstream: `ARahim3/DigUp main`, target: `%s`\n\n' "$TARGET"
  printf 'Baseline: `%s`, branch: `%s`\n\n' "$BASE" "$BRANCH"
  echo '## Upstream commits'
  echo
  echo '```text'
  git log --format='%h %s' "$BASE..$TARGET"
  echo '```'
} > "$WORK/report.md"
push_ref() {
  # Normal push only: server-side fast-forward checks protect concurrent changes.
  git -c credential.helper='!gh auth git-credential' push origin "$1"
}
if git merge-base --is-ancestor "$TARGET" "$MAIN"; then
  echo "Already integrated; main is unchanged." >> "$WORK/report.md"
  if [[ $PUBLISH == 1 ]]; then
    push_ref "$TARGET:refs/heads/upstream-main"
  fi
  finish_report
  exit 0
fi

# Full SHA makes repeated scheduled/manual runs address exactly the same PR.
if [[ $PUBLISH == 1 ]]; then
  EXISTING=$(gh pr list --repo "$FORK" --base main --head "$BRANCH" --state all \
    --json number,state,url --limit 100)
  STATE=$(python3 -c 'import json,sys; p=json.load(sys.stdin); print(p[0]["state"] if p else "")' <<< "$EXISTING")
  if [[ -n "$STATE" ]]; then
    printf '\nExisting PR (reused, not overwritten): %s\n' "$EXISTING" >> "$WORK/report.md"
    finish_report
    if [[ "$STATE" == OPEN ]]; then
      gh workflow run fork-checks.yml --repo "$FORK" --ref "$BRANCH"
      exit 0
    fi
    echo "PR is closed/merged but target is not in main; human review required." >&2
    exit 1
  fi
fi
git checkout --detach "$MAIN"
if ! git merge --no-ff --no-commit "$TARGET"; then
  if [[ ! -f .git/MERGE_HEAD ]]; then
    echo "Merge failed without a merge state; no issue or branch published." >&2
    exit 1
  fi
  {
    echo
    echo '## Merge conflicts'
    echo
    echo '```text'
    git diff --name-only --diff-filter=U
    echo '```'
    echo
    echo 'Merge aborted. Fork main and upstream-main were not changed.'
    echo 'Resolve on a separate branch, run fork checks and platform tests, then review a PR.'
  } >> "$WORK/report.md"
  git merge --abort
  if [[ $PUBLISH == 1 ]]; then
    TITLE="Upstream sync conflict $TARGET"
    ISSUES=$(gh issue list --repo "$FORK" --state all --search "$TARGET in:title" \
      --json title,url --limit 100)
    FOUND=$(python3 -c 'import json,sys; print(next((p["url"] for p in json.load(sys.stdin) if p["title"] == sys.argv[1]), ""))' "$TITLE" <<< "$ISSUES")
    if [[ -n "$FOUND" ]]; then
      printf '\nExisting conflict issue: %s\n' "$FOUND" >> "$WORK/report.md"
    else
      gh issue create --repo "$FORK" --title "$TITLE" --body-file "$WORK/report.md"
    fi
  fi
  finish_report
  exit 1
fi
git commit --no-verify -m "Merge ARahim3/DigUp main at $TARGET"
if [[ $PUBLISH == 1 ]]; then
  # Recover a previous successful push followed by failed PR creation. Check the
  # branch that the PR will actually contain, not a freshly generated candidate.
  REMOTE=$(git ls-remote --heads origin "refs/heads/$BRANCH")
  if [[ -n "$REMOTE" ]]; then
    git fetch origin "refs/heads/$BRANCH"
    git merge-base --is-ancestor "$TARGET" FETCH_HEAD || {
      echo "Existing sync branch does not contain target; refusing overwrite." >&2
      exit 1
    }
    git checkout --detach FETCH_HEAD
    printf '\nRecovered existing remote branch `%s`; no overwrite.\n' "$BRANCH" >> "$WORK/report.md"
  fi
fi
CHECK=0
# Run trusted checkout tooling against the candidate's actual on-disk resources.
# Do not execute scripts/workflows supplied by the upstream merge with write tokens.
python3 "$SCRIPT_DIR/check-fork.py" --root "$WORK/repo" --base "$MAIN" \
  > "$WORK/check.txt" 2>&1 || CHECK=$?
{
  echo
  echo '## Fork checks'
  echo
  echo '```text'
  cat "$WORK/check.txt"
  echo '```'
  echo
  echo 'No automatic merge or release. Review model changes and translation gaps.'
  echo 'Swift tests, macOS app build, and Windows installer validation remain release gates.'
} >> "$WORK/report.md"
if [[ $PUBLISH == 1 ]]; then
  if [[ -z "$REMOTE" ]]; then
    push_ref "HEAD:refs/heads/$BRANCH"
  fi
  # GITHUB_TOKEN-created PRs do not trigger pull_request workflows. A draft plus
  # inline checks still exposes failures; explicitly run Fork checks on this ref.
  gh pr create --repo "$FORK" --base main --head "$BRANCH" --draft \
    --title "Sync upstream ${TARGET:0:12}" --body-file "$WORK/report.md"
  gh workflow run fork-checks.yml --repo "$FORK" --ref "$BRANCH"
fi
finish_report
exit "$CHECK"
