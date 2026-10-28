#!/usr/bin/env bash
# Tests for scripts/deploy.sh (#62, #68, #93) against stubs: nothing here calls Azure, Docker or the network.
# Each case builds a throwaway origin and clone holding the script under test, puts stub az, docker, make,
# uv, curl, sleep and a venv python on PATH that log their calls, runs the deploy, and checks the exit
# status and the calls. The stub az answers the revision polls from AZ_RUNNING, AZ_HEALTH and AZ_READY:
# space separated answers, one per poll, the last one repeating.
# Only deploy.sh runs here. .github/workflows/deploy.yml is checked by grep and actionlint, not run.
#
# Usage: scripts/test_deploy.sh [deploy script]
#   The default is scripts/deploy.sh beside this file; pass a changed copy to check that a case can fail.
#   RELEASE_TAG_SCRIPT=<path> does the same for scripts/release_tag.sh, which the deploy calls.
#   Every case runs under /bin/bash (3.2 on macOS) and under the bash on PATH when that is another one.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
deploy="$(cd "$(dirname "${1:-$here/deploy.sh}")" && pwd)/$(basename "${1:-$here/deploy.sh}")"
workflow="$here/../.github/workflows/deploy.yml"
[[ -f "$deploy" ]] || { echo "no deploy script at $deploy" >&2; exit 2; }
release_script="${RELEASE_TAG_SCRIPT:-$here/release_tag.sh}"
[[ -x "$release_script" ]] || { echo "no executable release check at $release_script" >&2; exit 2; }

SUBSCRIPTION=11111111-2222-3333-4444-555555555555  # a made-up id: the stub az and the deploy both get it
OLD_APP=watchpartyd4d84f.azurecr.io/watchparty:old
OLD_RECAP=watchpartyd4d84f.azurecr.io/watchparty-recap:old
NEW_REVISION=ca-watchparty--new
OLD_REVISION=ca-watchparty--old

tmp="$(mktemp -d "${TMPDIR:-/tmp}/test_deploy.XXXXXX")"
running_pid=""
cleanup() {
  if [[ -n "$running_pid" ]]; then kill -KILL -- "-$running_pid" 2> /dev/null; fi
  rm -rf "$tmp"
}
trap cleanup EXIT

passed=0
failed=0
failures=()

# --- stubs --------------------------------------------------------------------------------------------

write_stubs() {  # $1: the bin directory
  local bin=$1
  mkdir -p "$bin"
  cat > "$bin/az" << 'EOF'
#!/usr/bin/env bash
echo "az $*" >> "$STUB_LOG"
next() {  # the next answer from a space separated list, the last one repeating
  local file="$STUB_STATE/$1" n=0
  [[ -f "$file" ]] && n="$(< "$file")"
  echo $((n + 1)) > "$file"
  local -a items
  read -r -a items <<< "$2"
  (( n < ${#items[@]} )) || n=$((${#items[@]} - 1))
  echo "${items[$n]}"
}
args="$*"
case "$args" in
  "account show"*)
    [[ -z "${AZ_SIGNED_OUT:-}" ]] || exit 1
    echo "${AZ_SUBSCRIPTION:?}" ;;
  "acr build"*)  # keeps the source dir's path and a copy of what it held, then fails if asked to
    context="${*: -1}"
    target=app
    [[ "$args" != *"--target recap"* ]] || target=recap
    echo "$context" > "$STUB_STATE/acr-dir-$target"
    [[ "$args" =~ --file\ ([^ ]+) ]] && echo "${BASH_REMATCH[1]}" > "$STUB_STATE/acr-file-$target"
    cp -R "$context" "$STUB_STATE/acr-copy-$target"
    [[ "${AZ_ACR_BUILD_FAILS:-}" != "$target" ]] || { echo "ERROR: build failed" >&2; exit 1; } ;;
  "acr repository show"*)  # a digest per repository; AZ_ACR_MISSING or AZ_ACR_OTHER_DIGEST name one repo:tag
    [[ "$args" =~ --image\ ([^ ]+) ]] || exit 2
    image="${BASH_REMATCH[1]}"
    [[ "$image" != "${AZ_ACR_MISSING:-}" ]] || { echo "ERROR: manifest unknown" >&2; exit 3; }
    [[ "$image" != "${AZ_ACR_OTHER_DIGEST:-}" ]] || { echo "sha256:other"; exit 0; }
    echo "sha256:${image%%:*}" ;;
  "containerapp revision show"*containers*) echo "${AZ_REVISION_IMAGE:-$NEW_APP_IMAGE}" ;;
  "containerapp revision show"*properties.runningState*) next running "${AZ_RUNNING:-Running}" ;;
  "containerapp revision show"*properties.healthState*) next health "${AZ_HEALTH:-Healthy}" ;;
  "containerapp show"*latestReadyRevisionName*) next ready "${AZ_READY:-$NEW_REVISION}" ;;
  "containerapp show"*) [[ -n "${AZ_EMPTY_PREVIOUS_APP:-}" ]] || echo "$OLD_APP" ;;
  "containerapp job show"*) echo "$OLD_RECAP" ;;
  "containerapp job update"*"--image $OLD_RECAP"*)
    if [[ -n "${ROLLBACK_SECONDS:-}" ]]; then  # a slow rollback, to signal the script during it
      touch "$STUB_STATE/rollback-started"
      /bin/sleep "$ROLLBACK_SECONDS"
    fi
    echo "az finished the recap rollback" >> "$STUB_LOG"
    exit "${AZ_RECAP_ROLLBACK_STATUS:-0}" ;;
  "containerapp job update"*) exit 0 ;;
  "containerapp update"*"--image $OLD_APP"*)
    [[ -z "${AZ_APP_ROLLBACK_FAILS:-}" ]] || { echo "ERROR: rollback failed" >&2; exit 1; }
    echo "$OLD_REVISION-2" ;;
  "containerapp update"*)
    [[ -z "${AZ_APP_UPDATE_FAILS:-}" ]] || { echo "ERROR: update failed" >&2; exit 1; }
    echo "$NEW_REVISION" ;;
esac
exit 0
EOF
  cat > "$bin/docker" << 'EOF'
#!/usr/bin/env bash
echo "docker $*" >> "$STUB_LOG"
[[ "$1" == info ]] && exit "${DOCKER_INFO_STATUS:-0}"
[[ "$1" == buildx ]] && cat > /dev/null
exit 0
EOF
  cat > "$bin/make" << 'EOF'
#!/usr/bin/env bash
echo "make $*" >> "$STUB_LOG"
[[ -z "${MAKE_DIRTIES:-}" ]] || touch dirtied-by-make
# Deletes a tag on origin while make check runs, as someone else could
[[ -z "${MAKE_DELETES_TAG:-}" ]] || git push --quiet origin ":refs/tags/$MAKE_DELETES_TAG" 2> /dev/null
exit "${MAKE_STATUS:-0}"
EOF
  cat > "$bin/uv" << 'EOF'
#!/usr/bin/env bash
echo "uv $*" >> "$STUB_LOG"
EOF
  cat > "$bin/curl" << 'EOF'
#!/usr/bin/env bash
echo "curl $*" >> "$STUB_LOG"
exit "${CURL_STATUS:-0}"
EOF
  cat > "$bin/sleep" << 'EOF'
#!/usr/bin/env bash
echo "sleep $*" >> "$STUB_LOG"
EOF
  chmod +x "$bin"/*
}

write_python() {  # $1: the venv python path. The smoke: logs, optionally lasts SMOKE_SECONDS, exits SMOKE_STATUS.
  mkdir -p "$(dirname "$1")"
  cat > "$1" << 'EOF'
#!/usr/bin/env bash
echo "python $*" >> "$STUB_LOG"
touch "$STUB_STATE/smoke-started"
[[ -z "${SMOKE_SECONDS:-}" ]] || /bin/sleep "$SMOKE_SECONDS"
exit "${SMOKE_STATUS:-0}"
EOF
  chmod +x "$1"
}

# --- one throwaway origin and clone per case ----------------------------------------------------------

new_case() {  # new_case [untagged]: sets work, clone, log, out, head. Tags HEAD v1.0.0 on origin unless untagged.
  work="$(mktemp -d "$tmp/case.XXXXXX")"
  clone="$work/clone"
  log="$work/log"
  out="$work/out"
  : > "$log"
  mkdir -p "$work/state"
  git init --quiet --bare "$work/origin.git"
  git clone --quiet "$work/origin.git" "$clone" 2> /dev/null
  mkdir -p "$clone/scripts" "$clone/backend"
  cp "$deploy" "$clone/scripts/deploy.sh"
  cp "$release_script" "$clone/scripts/release_tag.sh"
  echo ".venv/" > "$clone/.gitignore"
  echo "placeholder" > "$clone/backend/README"
  git -C "$clone" checkout --quiet -b main
  git -C "$clone" add .
  git -C "$clone" -c user.name=test -c user.email=test@example.invalid commit --quiet -m init
  git -C "$clone" push --quiet origin main 2> /dev/null
  head="$(git -C "$clone" rev-parse HEAD)"
  if [[ "${1:-}" != untagged ]]; then tag_and_push v1.0.0; fi
  write_stubs "$work/bin"
  write_python "$clone/backend/.venv/bin/python"
}

git_test() { git -C "$clone" -c user.name=test -c user.email=test@example.invalid "$@"; }
tag_local() { git_test tag "$@" > /dev/null 2>&1; }  # tag_local [-a -m msg] [-f] NAME
tag_and_push() { tag_local "$@" && git -C "$clone" push --quiet origin "refs/tags/${*: -1}" 2> /dev/null; }
commit_on_main() {  # a new commit on main, pushed; sets head
  echo "$1" >> "$clone/backend/README"
  git_test commit --quiet -am "$1"
  git -C "$clone" push --quiet origin main 2> /dev/null
  head="$(git -C "$clone" rev-parse HEAD)"
}

stub_env() {  # the environment every run gets, before the case's own variables
  echo "PATH=$work/bin:$PATH" "STUB_LOG=$log" "STUB_STATE=$work/state" "AZ_SUBSCRIPTION=$SUBSCRIPTION" "AZURE_SUBSCRIPTION_ID=$SUBSCRIPTION" \
    "OLD_APP=$OLD_APP" "OLD_RECAP=$OLD_RECAP" "NEW_REVISION=$NEW_REVISION" "OLD_REVISION=$OLD_REVISION" \
    "NEW_APP_IMAGE=watchpartyd4d84f.azurecr.io/watchparty:$head"
}

run_deploy() {  # run_deploy [VAR=value ...] [-- deploy args]; sets status
  local -a vars=() args=()
  while (( $# )); do
    if [[ "$1" == -- ]]; then shift; args=("$@"); break; fi
    vars+=("$1"); shift
  done
  # shellcheck disable=SC2046  # stub_env is words by design
  env $(stub_env) ${vars[@]+"${vars[@]}"} "$shell" "$clone/scripts/deploy.sh" ${args[@]+"${args[@]}"} > "$out" 2>&1
  status=$?
}

wait_for_file() {  # up to 120 seconds; fails if the file never appears
  local waited=0
  until [[ -f "$1" ]] || (( waited >= 1200 )); do /bin/sleep 0.1; waited=$((waited + 1)); done
  [[ -f "$1" ]]
}

# A signal sent before the stage it is meant for proves nothing: before the roll there is nothing to roll
# back, so the deploy exits 143 or 129 with no rollback and looks like a broken trap. So the case fails as
# "never started" and no signal is sent. The note goes to case_errors, not to $out, which the background
# deploy opens with truncation and may open after the note was written.
stop_unsignalled() {  # stop_unsignalled WHAT
  case_errors+=("$1 within 120 s, so no signal was sent")
  kill -KILL -- "-$running_pid" 2> /dev/null
  wait "$running_pid"
  status="not signalled"
  running_pid=""
}

signal_again=""  # set it to a signal to send to the process group again once the rollback has started
run_and_signal() {  # run_and_signal SIGNAL TARGET [VAR=value ...]: signal the deploy mid-smoke
  # TARGET group: the whole process group, as a terminal does. process: the script alone, as kill PID does.
  local signal=$1 target=$2; shift 2
  set -m  # its own process group, as a terminal gives it, so the smoke gets the signal too
  # shellcheck disable=SC2046
  env $(stub_env) "$@" "$shell" "$clone/scripts/deploy.sh" > "$out" 2>&1 &
  running_pid=$!
  set +m
  # The stub smoke writes this marker itself, so perl has already exec'd it and the rollback trap is set.
  wait_for_file "$work/state/smoke-started" || { stop_unsignalled "the smoke to start"; return; }
  /bin/sleep 0.2  # let the stub smoke reach its sleep
  if [[ "$target" == group ]]; then kill "-$signal" -- "-$running_pid"; else kill "-$signal" "$running_pid"; fi
  if [[ -n "$signal_again" ]]; then
    wait_for_file "$work/state/rollback-started" || { stop_unsignalled "the rollback to start"; return; }
    kill "-$signal_again" -- "-$running_pid"
  fi
  wait "$running_pid"
  status=$?
  running_pid=""
}

# --- checks -------------------------------------------------------------------------------------------

case_errors=()
check() {  # check DESCRIPTION COMMAND...: record a failed expectation
  local what=$1; shift
  "$@" || case_errors+=("$what")
}
status_is() { [[ "$status" == "$1" ]]; }
status_not() { [[ "$status" != "$1" ]]; }
log_empty() { [[ ! -s "$log" ]]; }
logged() { grep -qF -- "$1" "$log"; }
not_logged() { ! grep -qF -- "$1" "$log"; }
count_logged() { grep -cF -- "$1" "$log"; }
logged_times() { [[ "$(count_logged "$2")" == "$1" ]]; }
line_of() { grep -nF -- "$1" "$log" | head -1 | cut -d: -f1; }
in_order() {  # every fragment logged, each after the one before
  local previous=0 line
  for fragment in "$@"; do
    line="$(line_of "$fragment")"
    [[ -n "$line" ]] && (( line > previous )) || return 1
    previous=$line
  done
}
nothing_built() { not_logged "docker buildx" && not_logged "containerapp job update" && not_logged "containerapp update"; }
rolled_back() { in_order "containerapp job update --name caj-watchparty-recap --resource-group rg-watchparty --image $OLD_RECAP" \
  "containerapp update --name ca-watchparty --resource-group rg-watchparty --image $OLD_APP"; }
not_rolled_back() { not_logged "--image $OLD_RECAP" && not_logged "--image $OLD_APP"; }
no_smoke() { not_logged "python " && not_logged "curl "; }
polls() { count_logged "properties.healthState"; }
polls_before_smoke() {  # health polls logged before the smoke started
  local smoke
  smoke="$(line_of "python -m tools.smoke")"
  [[ -n "$smoke" ]] || return 1
  head -n "$smoke" "$log" | grep -cF "properties.healthState"
}
smoke_after_poll() { [[ "$(polls_before_smoke)" == "$1" && "$(polls)" == "$1" ]]; }
output_has() { grep -qF -- "$1" "$out"; }
not_output_has() { ! grep -qF -- "$1" "$out"; }
no_workflow_run() { ! grep -q "workflow_run" "$workflow"; }
release_check_before_login() {  # the workflow runs the release check, before it signs in to Azure
  local check login
  check="$(grep -nF 'scripts/release_tag.sh' "$workflow" | head -1 | cut -d: -f1)"
  login="$(grep -nF "uses: azure/login@" "$workflow" | head -1 | cut -d: -f1)"
  [[ -n "$check" && -n "$login" ]] && (( check < login ))
}
no_docker() { ! grep -q '^docker ' "$log"; }
built_from() {  # built_from TARGET: the source dir az acr build got for app or recap, as the stub kept it
  [[ -f "$work/state/acr-dir-$1" ]] && cat "$work/state/acr-dir-$1"
}
context_gone() { local dir; dir="$(built_from app)" && [[ -n "$dir" && ! -e "$dir" ]]; }
in_context() { [[ -f "$work/state/acr-copy-$1/$2" ]]; }  # in_context TARGET PATH
not_in_context() { [[ -d "$work/state/acr-copy-$1" && ! -e "$work/state/acr-copy-$1/$2" ]]; }
dockerfile_in_context() {  # az reads a relative --file from the working directory, so it must be the dir's own
  local dir; dir="$(built_from "$1")" && [[ "$(cat "$work/state/acr-file-$1")" == "$dir/Dockerfile" ]]
}
context_file_is() { [[ "$(cat "$work/state/acr-copy-$1/$2")" == "$3" ]]; }  # TARGET PATH CONTENT
rollback_set_plus_e() { sed -n '/name: Roll back to the images/,$p' "$workflow" | grep -q '^ *set +e'; }

finish() {  # finish NAME
  if (( ${#case_errors[@]} == 0 )); then
    passed=$((passed + 1)); echo "ok    $1"
  else
    failed=$((failed + 1)); failures+=("$1"); echo "FAIL  $1"
    for error in "${case_errors[@]}"; do echo "        expected: $error"; done
    echo "        exit status $status; output:"; sed 's/^/          | /' "$out"
  fi
  case_errors=()
}

# --- cases --------------------------------------------------------------------------------------------

run_cases() {
  # #62 case 1: dirty tree
  new_case; echo change >> "$clone/backend/README"; run_deploy
  check "exit 1" status_is 1; check "nothing built" nothing_built; check "no make check" not_logged "make check"
  finish "62.1 dirty tree"

  # #62 case 2: HEAD ahead of origin/main
  new_case; echo change >> "$clone/backend/README"
  git -C "$clone" -c user.name=test -c user.email=test@example.invalid commit --quiet -am ahead
  run_deploy
  check "exit 1" status_is 1; check "nothing built" nothing_built; check "says not origin/main" output_has "is not origin/main"
  finish "62.2 HEAD not origin/main"

  # #62 case 3a: az not signed in
  new_case; run_deploy AZ_SIGNED_OUT=1
  check "exit 1" status_is 1; check "nothing built" nothing_built; check "says az login" output_has "run az login"
  finish "62.3a az not signed in"

  # #62 case 3b: wrong subscription
  new_case; run_deploy AZ_SUBSCRIPTION=00000000-0000-0000-0000-000000000000
  check "exit 1" status_is 1; check "nothing built" nothing_built; check "says az account set" output_has "az account set"
  finish "62.3b wrong subscription"

  # #121: no expected subscription in the environment
  new_case; run_deploy AZURE_SUBSCRIPTION_ID=
  check "exit 1" status_is 1; check "nothing built" nothing_built; check "no make check" not_logged "make check"
  check "says to set it" output_has "set AZURE_SUBSCRIPTION_ID"; check "az not asked" not_logged "account show"
  finish "121.1 no expected subscription"

  # #62 case 4: make check fails
  new_case; run_deploy MAKE_STATUS=2
  check "non-zero exit" status_not 0; check "nothing built" nothing_built
  finish "62.4 make check fails"

  # #62 case 4b: make check dirties the tree
  new_case; run_deploy MAKE_DIRTIES=1
  check "exit 1" status_is 1; check "nothing built" nothing_built
  finish "62.4b make check dirties the tree"

  # #62 case 5: happy path, in workflow order
  new_case; run_deploy
  check "exit 0" status_is 0
  check "calls in workflow order" in_order "make check" "az acr login" "docker buildx build" "--target recap" \
    "az containerapp show --name ca-watchparty" "az containerapp job show" \
    "az containerapp job update --name caj-watchparty-recap --resource-group rg-watchparty --image watchpartyd4d84f.azurecr.io/watchparty-recap:$head" \
    "az containerapp update --name ca-watchparty --resource-group rg-watchparty --image watchpartyd4d84f.azurecr.io/watchparty:$head" \
    "--revision $NEW_REVISION --query properties.healthState" "curl " "uv sync --locked" "python -m tools.smoke"
  check "full-SHA and version app tags, amd64, context on stdin" \
    logged "docker buildx build --platform linux/amd64 --pull --provenance=false --tag watchpartyd4d84f.azurecr.io/watchparty:$head --tag watchpartyd4d84f.azurecr.io/watchparty:v1.0.0 --push -"
  check "full-SHA and version recap tags" \
    logged "--target recap --tag watchpartyd4d84f.azurecr.io/watchparty-recap:$head --tag watchpartyd4d84f.azurecr.io/watchparty-recap:v1.0.0 --push -"
  check "rolls the full-SHA image, not the version" not_logged "--image watchpartyd4d84f.azurecr.io/watchparty:v1.0.0"
  check "not rolled back" not_rolled_back
  check "says deployed" output_has "== deployed v1.0.0 ($head)"
  finish "62.5 / 93.2 happy path on v1.0.0"

  # #66: APP_URL points the health poll and the smoke at another host; without it, the default host
  new_case; run_deploy APP_URL=https://couchgaffer.com
  check "exit 0" status_is 0
  check "polls the given host" logged "curl --fail --silent --max-time 10 https://couchgaffer.com/health"
  check "smokes the given host" logged "python -m tools.smoke https://couchgaffer.com --full"
  check "not the default host" not_logged "azurecontainerapps.io"
  finish "66.1 APP_URL from the environment"
  new_case; run_deploy
  check "smokes the default host" \
    logged "python -m tools.smoke https://ca-watchparty.delightfulsand-8cf751ef.swedencentral.azurecontainerapps.io --full"
  finish "66.2 default APP_URL"
  new_case; run_deploy APP_URL=http://localhost:3000
  check "exit 1" status_is 1; check "nothing built" nothing_built; check "says why" output_has "is not an https:// host"
  finish "66.3 APP_URL not https refused"

  # #93 case 1: untagged main
  new_case untagged; run_deploy
  check "exit 1" status_is 1; check "nothing built" nothing_built; check "no make check" not_logged "make check"
  check "says no release tag" output_has "has no vX.Y.Z release tag"
  finish "93.1 untagged main"

  new_case untagged; run_deploy -- --dry-run
  check "exit 1" status_is 1; check "calls nothing" log_empty; check "prints no build" not_output_has "docker buildx"
  finish "93.1b untagged main, dry run"

  # main moved on past the release: the tag is on an older commit
  new_case; commit_on_main after-release; run_deploy
  check "exit 1" status_is 1; check "nothing built" nothing_built; check "says no release tag" output_has "has no vX.Y.Z release tag"
  finish "93.1c release tag on an older commit"

  # An annotated tag deploys too: origin lists the tag object and the commit, and the commit is compared
  new_case untagged; tag_and_push -a -m "release 1.2.3" v1.2.3; run_deploy
  check "exit 0" status_is 0; check "version app tag" logged "--tag watchpartyd4d84f.azurecr.io/watchparty:v1.2.3 --push -"
  check "says deployed" output_has "== deployed v1.2.3 ($head)"
  finish "93.2b annotated tag v1.2.3"

  # #93 case 3: malformed tags only
  new_case untagged
  for bad in v1.0 v1.0.0-rc1 release-1.0.0 1.0.0 v01.0.0 V1.0.0 v1.0.0.1; do tag_and_push "$bad"; done
  run_deploy
  check "exit 1" status_is 1; check "nothing built" nothing_built; check "says no release tag" output_has "has no vX.Y.Z release tag"
  finish "93.3 malformed tags only"

  # #93 case 4: the tag exists only locally
  new_case untagged; tag_local v1.0.0; run_deploy
  check "exit 1" status_is 1; check "nothing built" nothing_built
  check "says the local tag is not on origin" output_has "the local tag v1.0.0 is not on origin at this commit"
  finish "93.4 local-only tag"

  # #93 case 5: stale; origin's v1.0.0 is an older commit, and the local tag was moved onto HEAD
  new_case; commit_on_main after-release; tag_local -f v1.0.0; run_deploy
  check "exit 1" status_is 1; check "nothing built" nothing_built
  check "says the local tag is not on origin" output_has "the local tag v1.0.0 is not on origin at this commit"
  finish "93.5 stale tag moved locally"

  # A tagged, pushed release that main has moved past: HEAD is the tag, not the tip of main
  new_case; commit_on_main after-release; git -C "$clone" checkout --quiet --detach v1.0.0; run_deploy
  check "exit 1" status_is 1; check "nothing built" nothing_built; check "no make check" not_logged "make check"
  check "says not origin/main" output_has "is not origin/main"
  finish "93.7 release tag behind the tip of main"

  # origin/main is stale (a fetch refspec without main) while origin's main has moved on: HEAD equals the
  # stale origin/main and carries the pushed tag, but is no longer the tip of main on origin
  new_case
  git -C "$clone" config remote.origin.fetch "+refs/heads/develop:refs/remotes/origin/develop"
  git -C "$clone" checkout --quiet -b side
  echo moved >> "$clone/backend/README"; git_test commit --quiet -am moved
  git -C "$clone" push --quiet origin side:main 2> /dev/null
  git -C "$clone" checkout --quiet main; git -C "$clone" branch --quiet -D side
  git -C "$clone" update-ref refs/remotes/origin/main "$head"
  run_deploy
  check "exit 1" status_is 1; check "nothing built" nothing_built; check "says not origin/main" output_has "is not origin/main"
  finish "93.8 origin/main stale, main moved on origin"

  # The tag is deleted on origin while make check runs: the second check refuses
  new_case; run_deploy MAKE_DELETES_TAG=v1.0.0
  check "exit 1" status_is 1; check "make check ran" logged "make check"; check "nothing built" nothing_built
  check "nothing pushed" not_logged "az acr login"
  check "says no release tag" output_has "has no vX.Y.Z release tag on origin"
  finish "93.9 tag deleted on origin during make check"

  # #93 case 6: two release tags on HEAD
  new_case; tag_and_push v1.0.1; run_deploy
  check "exit 1" status_is 1; check "nothing built" nothing_built; check "says more than one" output_has "more than one release tag"
  finish "93.6 two release tags"

  # #62 case 6: smoke fails
  new_case; run_deploy SMOKE_STATUS=1
  check "exit 1" status_is 1; check "recap then app rolled back" rolled_back
  finish "62.6 smoke fails"

  # #62 case 6b: smoke fails and the recap rollback fails
  new_case; run_deploy SMOKE_STATUS=1 AZ_RECAP_ROLLBACK_STATUS=1
  check "exit 1" status_is 1; check "app still rolled back" rolled_back
  check "names the recap job for a manual fix" output_has "the recap job did NOT roll back"
  finish "62.6b smoke and recap rollback fail"

  # The app rollback fails
  new_case; run_deploy SMOKE_STATUS=1 AZ_APP_ROLLBACK_FAILS=1
  check "exit 1" status_is 1; check "recap rolled back first" rolled_back
  check "names the app for a manual fix" output_has "the app did NOT roll back"
  check "does not say rolled back" not_output_has "failed and rolled back"
  finish "62.6c smoke and app rollback fail"

  # The running app image reads empty
  new_case; run_deploy AZ_EMPTY_PREVIOUS_APP=1
  check "exit 1" status_is 1; check "says it could not read the images" output_has "could not read the images running now"
  check "nothing rolled" not_logged "containerapp job update"; check "app not updated" not_logged "containerapp update"
  finish "62.7b previous app image empty"

  # #62 case 7: the app update fails
  new_case; run_deploy AZ_APP_UPDATE_FAILS=1
  check "exit 1" status_is 1; check "both rolled back" rolled_back; check "no smoke" no_smoke
  check "no revision poll" logged_times 0 "containerapp revision show"
  finish "62.7 app update fails"

  # #62 case 8: dry run
  new_case; run_deploy -- --dry-run
  check "exit 0" status_is 0
  check "calls nothing" log_empty
  check "prints the app update" output_has "+ az containerapp update --name ca-watchparty"
  check "prints the running state poll" output_has "--revision <new revision> --query properties.runningState"
  check "prints the health poll" output_has "--revision <new revision> --query properties.healthState"
  check "prints the latest ready poll" output_has "--query properties.latestReadyRevisionName"
  check "prints the image check" output_has "--revision <new revision> --query properties.template.containers[0].image"
  finish "62.8 dry run"

  # #68 case 1: the new revision stays Unhealthy until the limit
  new_case; run_deploy AZ_HEALTH=Unhealthy AZ_READY=$OLD_REVISION
  check "exit 1" status_is 1; check "no smoke" no_smoke; check "both rolled back" rolled_back
  check "30 polls" logged_times 30 "properties.healthState"
  check "says not healthy within 5 minutes" output_has "was not healthy and ready within 5 minutes"
  finish "68.1 Unhealthy until the limit"

  # #68 case 2: Healthy and latest ready on the third poll
  new_case; run_deploy AZ_HEALTH="Unhealthy Unhealthy Healthy" AZ_READY="$OLD_REVISION $OLD_REVISION $NEW_REVISION"
  check "exit 0" status_is 0; check "smoke after the third poll, no poll after" smoke_after_poll 3
  check "not rolled back" not_rolled_back
  finish "68.2 ready on the third poll"

  # #68 case 2b: Healthy at once, but the old revision stays latest ready until the third poll
  new_case; run_deploy AZ_HEALTH=Healthy AZ_READY="$OLD_REVISION $OLD_REVISION $NEW_REVISION"
  check "exit 0" status_is 0; check "smoke after the third poll, no poll after" smoke_after_poll 3
  finish "68.2b latest ready on the third poll"

  # #68 case 2c: latest ready at once, but Unhealthy until the third poll
  new_case; run_deploy AZ_HEALTH="Unhealthy Unhealthy Healthy" AZ_READY=$NEW_REVISION
  check "exit 0" status_is 0; check "smoke after the third poll, no poll after" smoke_after_poll 3
  finish "68.2c Healthy on the third poll"

  # #68 case 3: runningState Failed
  new_case; run_deploy AZ_RUNNING=Failed AZ_HEALTH=Unhealthy AZ_READY=$OLD_REVISION
  check "exit 1" status_is 1; check "no smoke" no_smoke; check "both rolled back" rolled_back
  check "one poll" logged_times 1 "properties.runningState"
  check "no sleep" not_logged "sleep "
  check "says failed to start" output_has "failed to start"
  finish "68.3 runningState Failed"

  # #68 case 4: TERM and HUP during the smoke
  new_case; run_and_signal TERM group SMOKE_SECONDS=30
  check "exit 143" status_is 143; check "both rolled back" rolled_back
  finish "68.4a TERM during the smoke"
  new_case; run_and_signal HUP group SMOKE_SECONDS=30
  check "exit 129" status_is 129; check "both rolled back" rolled_back
  finish "68.4b HUP during the smoke"
  # The script alone: bash runs the trap once the smoke ends, here after 3 seconds, and still rolls back.
  new_case; run_and_signal TERM process SMOKE_SECONDS=3
  check "exit 143" status_is 143; check "both rolled back" rolled_back
  finish "68.4c TERM to the script alone during the smoke"

  # #68 case 5: INT during the smoke (regression)
  new_case; run_and_signal INT group SMOKE_SECONDS=30
  check "exit 130" status_is 130; check "both rolled back" rolled_back
  finish "68.5 INT during the smoke"

  # A second TERM while the rollback runs must not stop it halfway
  new_case; signal_again=TERM; run_and_signal TERM group SMOKE_SECONDS=30 ROLLBACK_SECONDS=2; signal_again=""
  check "exit 143" status_is 143
  check "recap rollback finished, then the app rolled back" in_order "az finished the recap rollback" \
    "containerapp update --name ca-watchparty --resource-group rg-watchparty --image $OLD_APP"
  check "says rolled back" output_has "failed and rolled back"
  finish "68.6 second TERM during the rollback"

  # The revision that got ready runs some other image
  new_case; run_deploy AZ_REVISION_IMAGE=watchpartyd4d84f.azurecr.io/watchparty:other
  check "exit 1" status_is 1; check "no smoke" no_smoke; check "both rolled back" rolled_back
  check "names the image" output_has "runs watchpartyd4d84f.azurecr.io/watchparty:other"
  finish "68.8 revision runs another image"

  # #173: --remote-build builds both images in the registry with az acr build
  for order in "--remote-build --dry-run" "--dry-run --remote-build"; do
    new_case
    # shellcheck disable=SC2086  # two flags, split on purpose
    run_deploy -- $order
    check "exit 0" status_is 0; check "calls nothing" log_empty
    check "prints the archive of the commit" output_has "+ git archive $head | tar -x -C <temp dir>"
    check "prints the app build, both tags, from the archive" \
      output_has "+ az acr build --registry watchpartyd4d84f --platform linux/amd64 --timeout 1800 --file <temp dir>/Dockerfile --image watchparty:$head --image watchparty:v1.0.0 <temp dir>"
    check "prints the recap build, both tags, from the archive" \
      output_has "+ az acr build --registry watchpartyd4d84f --platform linux/amd64 --timeout 1800 --file <temp dir>/Dockerfile --target recap --image watchparty-recap:$head --image watchparty-recap:v1.0.0 <temp dir>"
    check "prints no docker" not_output_has "docker"; check "prints no acr login" not_output_has "az acr login"
    check "prints the roll" output_has "+ az containerapp update --name ca-watchparty"
    check "prints the image checks" output_has "+ az acr repository show --name watchpartyd4d84f --image watchparty-recap:v1.0.0 --query digest"
    finish "173.1 dry run ($order)"
  done

  new_case; run_deploy DOCKER_INFO_STATUS=1
  check "exit 1" status_is 1; check "nothing built" nothing_built; check "says Docker" output_has "Docker is not running"
  finish "173.2b no flag, Docker not running"

  new_case; run_deploy AZ_ACR_BUILD_FAILS=app -- --remote-build
  check "exit 1" status_is 1; check "app build ran" logged "az acr build"
  check "recap not built" not_logged "--target recap"
  check "nothing rolled" not_logged "containerapp job update"; check "app not updated" not_logged "containerapp update"
  check "no smoke" no_smoke; check "build dir removed" context_gone
  finish "173.3 app az acr build fails"

  new_case; run_deploy AZ_ACR_BUILD_FAILS=recap -- --remote-build
  check "exit 1" status_is 1; check "recap build ran" logged "--target recap"
  check "nothing rolled" not_logged "containerapp job update"; check "app not updated" not_logged "containerapp update"
  check "no smoke" no_smoke; check "build dir removed" context_gone
  finish "173.3b recap az acr build fails"

  # A file git ignores, and a tracked file changed where git status cannot see it, must not reach the build
  new_case
  echo planted > "$clone/backend/.venv/planted"
  git -C "$clone" update-index --assume-unchanged backend/README
  echo uncommitted >> "$clone/backend/README"
  run_deploy DOCKER_INFO_STATUS=1 -- --remote-build
  check "exit 0" status_is 0
  for target in app recap; do
    check "$target: built from a dir" built_from "$target"
    check "$target: holds the committed script" in_context "$target" scripts/deploy.sh
    check "$target: holds the committed README" context_file_is "$target" backend/README placeholder
    check "$target: no ignored file" not_in_context "$target" backend/.venv
    check "$target: the Dockerfile from the build dir, not the working tree" dockerfile_in_context "$target"
  done
  check "both from the same dir" [ "$(built_from app)" == "$(built_from recap)" ]
  check "build dir removed" context_gone
  finish "173.4 the build holds the commit's tree"

  # Happy path with Docker stopped: no docker call at all, the rest as without the flag
  new_case; run_deploy DOCKER_INFO_STATUS=1 -- --remote-build
  check "exit 0" status_is 0; check "no docker call" no_docker; check "no acr login" not_logged "az acr login"
  check "calls in order" in_order "make check" \
    "--image watchparty:$head --image watchparty:v1.0.0 " \
    "--target recap --image watchparty-recap:$head --image watchparty-recap:v1.0.0 " \
    "az containerapp show --name ca-watchparty" "az containerapp job show" \
    "az containerapp job update --name caj-watchparty-recap --resource-group rg-watchparty --image watchpartyd4d84f.azurecr.io/watchparty-recap:$head" \
    "az containerapp update --name ca-watchparty --resource-group rg-watchparty --image watchpartyd4d84f.azurecr.io/watchparty:$head" \
    "--revision $NEW_REVISION --query properties.healthState" "curl " "python -m tools.smoke"
  check "not rolled back" not_rolled_back; check "says deployed" output_has "== deployed v1.0.0 ($head)"
  finish "173.5 remote build happy path, Docker stopped"

  new_case; run_deploy SMOKE_STATUS=1 -- --remote-build
  check "exit 1" status_is 1; check "recap then app rolled back" rolled_back
  finish "173.6 remote build, smoke fails"

  # az acr build exits 0 but a tag is missing, or the two tags name different images: stop before the roll
  for repo in watchparty watchparty-recap; do
    new_case; run_deploy "AZ_ACR_MISSING=$repo:$head" -- --remote-build
    check "exit 1" status_is 1; check "says which image" output_has "the registry has no $repo:$head"
    check "nothing rolled" not_logged "containerapp job update"; check "app not updated" not_logged "containerapp update"
    check "build dir removed" context_gone
    finish "173.8 built but $repo:<commit> missing"
  done
  new_case; run_deploy AZ_ACR_OTHER_DIGEST=watchparty:v1.0.0 -- --remote-build
  check "exit 1" status_is 1; check "recap not built" not_logged "--target recap"
  check "nothing rolled" not_logged "containerapp job update"; check "app not updated" not_logged "containerapp update"
  finish "173.8b tags name different images"

  new_case; run_deploy -- --remote
  check "exit 2" status_is 2; check "calls nothing" log_empty; check "prints usage" output_has "usage:"
  finish "173.7 unknown flag"
}

# #62 case 9 and #68 case 7: lint
lint() {
  status=""
  : > "${out:=$tmp/lint.out}"
  log="$tmp/lint.log"; : > "$log"
  if command -v shellcheck > /dev/null; then
    check "shellcheck clean on the deploy script" shellcheck "$deploy"
    check "shellcheck clean on this script" shellcheck "$here/test_deploy.sh"
  else
    echo "skip  shellcheck: not installed"
  fi
  finish "62.9 shellcheck"

  check "no workflow_run trigger" no_workflow_run
  check "workflow_dispatch trigger" grep -q "^  workflow_dispatch:" "$workflow"
  check "rollback step runs on failure or cancel" grep -qF "if: (failure() || cancelled())" "$workflow"
  check "rollback step has set +e" rollback_set_plus_e
  check "waits on the new revision" grep -qF -- "--revision \"\$NEW_REVISION\" --query properties.healthState" "$workflow"
  # shellcheck disable=SC2016  # the workflow's shell text, matched as written
  check "waits for the new revision to be latest ready" grep -qF -- '[ "$ready" = "$NEW_REVISION" ]' "$workflow"
  # shellcheck disable=SC2016  # the workflow's shell text, matched as written
  check "checks the revision runs the pushed image" grep -qF -- '[ "$image" != "$IMAGE" ]' "$workflow"
  # shellcheck disable=SC2016  # the workflow's shell text, matched as written
  check "fails on an unread running image" grep -qF -- '[ -z "$app" ] || [ -z "$recap" ]' "$workflow"
  check "smoke step has its own limit" grep -qF "timeout-minutes: 25" "$workflow"
  check "runs the release check before the Azure login" release_check_before_login
  check "deploys only main, and survives a cancel" grep -qF "if: always() && github.ref == 'refs/heads/main'" "$workflow"
  if command -v actionlint > /dev/null; then
    check "actionlint clean" actionlint "$workflow"
  else
    echo "skip  actionlint: not installed"
  fi
  finish "68.7 deploy.yml lint"
}

shells=(/bin/bash)
path_bash="$(command -v bash)"
if [[ "$(realpath "$path_bash")" != "$(realpath /bin/bash)" ]]; then shells+=("$path_bash"); fi
for shell in "${shells[@]}"; do
  echo "== $shell ($("$shell" --version | head -1))"
  run_cases
done
lint

echo "== $passed passed, $failed failed"
(( failed == 0 )) || { printf '   %s\n' "${failures[@]}"; exit 1; }
