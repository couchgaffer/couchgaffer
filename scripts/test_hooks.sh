#!/usr/bin/env bash
# Tests for .githooks/pre-push (#93). Two kinds of case, all in throwaway directories:
# - the hook alone, fed the lines git writes on stdin, under /bin/bash (3.2 on macOS) and under the bash on
#   PATH when that is another one;
# - real pushes from a throwaway clone with core.hooksPath pointed at the hook, to a throwaway bare origin.
# Nothing here changes this checkout's git config.
#
# Usage: scripts/test_hooks.sh [hook]
#   The default is .githooks/pre-push; pass a changed copy to check that a case can fail.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
hook="$(cd "$(dirname "${1:-$here/../.githooks/pre-push}")" && pwd)/$(basename "${1:-$here/../.githooks/pre-push}")"
[[ -f "$hook" ]] || { echo "no hook at $hook" >&2; exit 2; }

tmp="$(mktemp -d "${TMPDIR:-/tmp}/test_hooks.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT

passed=0
failed=0
failures=()
SHA=1111111111111111111111111111111111111111
OTHER=2222222222222222222222222222222222222222
ZERO=0000000000000000000000000000000000000000

result() {  # result NAME EXPECT(accept|refuse) STATUS OUTPUT
  local name=$1 expect=$2 status=$3 output=$4 ok=0
  if [[ "$expect" == accept && "$status" == 0 ]] || [[ "$expect" == refuse && "$status" != 0 ]]; then ok=1; fi
  if (( ok )); then
    passed=$((passed + 1)); echo "ok    $name"
  else
    failed=$((failed + 1)); failures+=("$name"); echo "FAIL  $name"
    echo "        expected: $expect; exit status $status; output:"
    while IFS= read -r line; do echo "          | $line"; done <<< "$output"
  fi
}

# --- the hook alone -----------------------------------------------------------------------------------

push_line() {  # push_line REF [new|delete|move]: the line git writes; REF is a branch name or refs/tags/NAME
  local ref=$1 kind=${2:-new}
  [[ "$ref" == refs/* ]] || ref="refs/heads/$ref"
  case "$kind" in
    new) echo "$ref $SHA $ref $ZERO" ;;
    delete) echo "(delete) $ZERO $ref $SHA" ;;
    move) echo "$ref $SHA $ref $OTHER" ;;
  esac
}

alone() {  # alone NAME EXPECT [VAR=value ...] -- LINE...: each LINE is "REF [new|delete|move]"
  local name=$1 expect=$2 output status line; shift 2
  local -a vars=()
  while (( $# )) && [[ "$1" != -- ]]; do vars+=("$1"); shift; done
  shift
  local input=""
  for line in "$@"; do
    # shellcheck disable=SC2086  # "REF KIND" splits into its two words by design
    input+="$(push_line $line)"$'\n'
  done
  output="$(printf '%s' "$input" | env -u ALLOW_PROTECTED_PUSH ${vars[@]+"${vars[@]}"} "$shell" "$hook" origin "$tmp/origin.git" 2>&1)"
  status=$?
  result "$name" "$expect" "$status" "$output"
}

alone_cases() {
  # #93 case 3: direct pushes to develop and main
  alone "93.3 push to develop" refuse -- develop
  alone "93.3b push to main" refuse -- main
  alone "93.3c develop with ALLOW_PROTECTED_PUSH=1" accept ALLOW_PROTECTED_PUSH=1 -- develop
  alone "93.3d main with ALLOW_PROTECTED_PUSH=1" accept ALLOW_PROTECTED_PUSH=1 -- main
  alone "93.3e ALLOW_PROTECTED_PUSH=yes is not 1" refuse ALLOW_PROTECTED_PUSH=yes -- develop
  alone "93.3f ALLOW_PROTECTED_PUSH=0" refuse ALLOW_PROTECTED_PUSH=0 -- main
  alone "93.3g delete develop" refuse -- "develop delete"
  alone "93.3h a good branch and develop in one push" refuse -- feature/12-x develop
  # #93 case 4: branch names
  alone "93.4 feat/x" refuse -- feat/x
  alone "93.4b feature/12-x" accept -- feature/12-x
  for prefix in bugfix hotfix chore fix ci infra refactor docs; do
    alone "93.4c $prefix/93-some-slug" accept -- "$prefix/93-some-slug"
  done
  alone "93.4d feature/x without an issue" refuse -- feature/x
  alone "93.4e feature/12 without a slug" refuse -- feature/12
  alone "93.4f feature/12- with an empty slug" refuse -- feature/12-
  alone "93.4g Feature/12-x" refuse -- Feature/12-x
  alone "93.4h feature/12-X uppercase slug" refuse -- feature/12-X
  alone "93.4i worktree-agent-abc" refuse -- worktree-agent-abc
  alone "93.4j feature/12-x/y" refuse -- feature/12-x/y
  alone "93.4k fix/22-25-33-session-lifecycle (existing)" accept -- fix/22-25-33-session-lifecycle
  alone "93.4l release/1.2.0" accept -- release/1.2.0
  alone "93.4m release/1.2" refuse -- release/1.2
  alone "93.4n release/v1.2.0" refuse -- release/v1.2.0
  alone "93.4o release/01.2.0" refuse -- release/01.2.0
  alone "93.4p delete a badly named branch" accept -- "worktree-agent-abc delete"
  alone "93.4q a good and a bad branch in one push" refuse -- feature/12-x feat/x
  alone "93.4r feature/0-x, issue 0" refuse -- feature/0-x
  alone "93.4s feature/012-x, leading zero" refuse -- feature/012-x
  alone "93.4t feature/10-x" accept -- feature/10-x
  # Tags
  alone "93.10 push v1.0.0" refuse -- refs/tags/v1.0.0
  alone "93.10b push v1.0.0 with ALLOW_PROTECTED_PUSH=1" accept ALLOW_PROTECTED_PUSH=1 -- refs/tags/v1.0.0
  alone "93.10c push v1.0.0 with ALLOW_PROTECTED_PUSH=yes" refuse ALLOW_PROTECTED_PUSH=yes -- refs/tags/v1.0.0
  alone "93.10d delete v1.0.0" refuse -- "refs/tags/v1.0.0 delete"
  alone "93.10e move v1.0.0, even with ALLOW_PROTECTED_PUSH=1" refuse ALLOW_PROTECTED_PUSH=1 -- "refs/tags/v1.0.0 move"
  alone "93.10f move a non-release tag" refuse -- "refs/tags/note move"
  alone "93.10g push a non-release tag" accept -- refs/tags/note
  alone "93.10h a feature branch and v1.0.0 in one push" refuse -- feature/12-x refs/tags/v1.0.0
}

# --- real pushes --------------------------------------------------------------------------------------

push_case() {  # push_case NAME EXPECT [VAR=value ...] -- git push args...
  local name=$1 expect=$2 output status; shift 2
  local -a vars=()
  while (( $# )) && [[ "$1" != -- ]]; do vars+=("$1"); shift; done
  shift
  output="$(env -u ALLOW_PROTECTED_PUSH ${vars[@]+"${vars[@]}"} git -C "$clone" push --quiet origin "$@" 2>&1)"
  status=$?
  result "$name" "$expect" "$status" "$output"
}
on_origin() { git --git-dir="$tmp/origin.git" rev-parse --verify --quiet "$1" > /dev/null; }
develop_is() { [[ "$(git --git-dir="$tmp/origin.git" rev-parse develop)" == "$1" ]]; }

push_cases() {
  git init --quiet --bare "$tmp/origin.git"
  clone="$tmp/clone"
  git clone --quiet "$tmp/origin.git" "$clone" 2> /dev/null
  git -C "$clone" config user.name test
  git -C "$clone" config user.email test@example.invalid
  git -C "$clone" commit --quiet --allow-empty -m init
  git -C "$clone" branch -M main
  git -C "$clone" push --quiet --no-verify origin main main:develop
  git -C "$clone" config core.hooksPath "$(dirname "$hook")"
  git -C "$clone" commit --quiet --allow-empty -m change
  local before
  before="$(git --git-dir="$tmp/origin.git" rev-parse develop)"

  push_case "push: 93.3 git push origin HEAD:develop" refuse -- HEAD:develop
  develop_is "$before"
  result "push: 93.3 develop unchanged on origin" accept "$?" "develop moved"
  push_case "push: 93.3c develop with ALLOW_PROTECTED_PUSH=1" accept ALLOW_PROTECTED_PUSH=1 -- HEAD:develop
  develop_is "$(git -C "$clone" rev-parse HEAD)"
  result "push: 93.3c develop moved on origin" accept "$?" "develop did not move"
  push_case "push: 93.4 feat/x" refuse -- HEAD:feat/x
  ! on_origin refs/heads/feat/x
  result "push: 93.4 feat/x not on origin" accept "$?" "feat/x reached origin"
  push_case "push: 93.4b feature/12-x" accept -- HEAD:feature/12-x
  git -C "$clone" tag v1.0.0
  push_case "push: 93.10 tag v1.0.0" refuse -- v1.0.0
  ! on_origin refs/tags/v1.0.0
  result "push: 93.10 v1.0.0 not on origin" accept "$?" "v1.0.0 reached origin"
  push_case "push: 93.10b tag v1.0.0 with ALLOW_PROTECTED_PUSH=1" accept ALLOW_PROTECTED_PUSH=1 -- v1.0.0
  local tagged
  tagged="$(git --git-dir="$tmp/origin.git" rev-parse v1.0.0)"
  git -C "$clone" commit --quiet --allow-empty -m after-release
  git -C "$clone" tag -f v1.0.0 > /dev/null 2>&1
  push_case "push: 93.10e force-move v1.0.0 with ALLOW_PROTECTED_PUSH=1" refuse ALLOW_PROTECTED_PUSH=1 -- --force v1.0.0
  [[ "$(git --git-dir="$tmp/origin.git" rev-parse v1.0.0)" == "$tagged" ]]
  result "push: 93.10e v1.0.0 unchanged on origin" accept "$?" "v1.0.0 moved"
}

shells=(/bin/bash)
path_bash="$(command -v bash)"
if [[ "$(realpath "$path_bash")" != "$(realpath /bin/bash)" ]]; then shells+=("$path_bash"); fi
for shell in "${shells[@]}"; do
  echo "== hook alone, $shell ($("$shell" --version | head -1))"
  alone_cases
done
echo "== real pushes"
push_cases

if command -v shellcheck > /dev/null; then
  result "shellcheck on the hook and this script" accept "$(shellcheck "$hook" "$here/test_hooks.sh" > /dev/null 2>&1; echo $?)" ""
else
  echo "skip  shellcheck: not installed"
fi

echo "== $passed passed, $failed failed"
(( failed == 0 )) || { printf '   %s\n' "${failures[@]}"; exit 1; }
