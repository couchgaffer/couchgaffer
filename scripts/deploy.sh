#!/usr/bin/env bash
# Deploy a release of main by hand while GitHub Actions cannot run (#60, #62). The same steps as
# .github/workflows/deploy.yml, in the same order, with the CI gate run locally first: build the app and
# recap images, push them, roll the recap job and then the app onto them, and smoke-test the live URL.
# If anything after the roll fails, both go back to the images they ran before (#24).
# make check is a subset of CI: it skips the codegen drift checks, npm ci and audit, the container
# health check and the secret scan.
#
# Usage: scripts/deploy.sh [--dry-run] [--remote-build]
#   --dry-run       print every Azure and Docker command instead of running it; skips make check. The git
#                   guards still run, so it works only from a clean checkout of a release tag on origin/main.
#   --remote-build  build both images in the registry with ACR Tasks (az acr build) instead of on this
#                   machine (#173), for a slow or congested connection: only the commit's source is uploaded.
#                   The builds do not use the local Docker, so its check is skipped; make check's Redis
#                   tests still start a container.
#
# Deploys only a release (#93): HEAD is the tip of main on origin and has one vX.Y.Z tag there
# (scripts/release_tag.sh). Both images are tagged with the commit and the version.
#
# Needs: az signed in (az login) with AcrPush on the registry and Contributor on the resource group,
# Docker running (for make check and the local build), uv, npm. AZURE_SUBSCRIPTION_ID in the environment names the subscription to deploy to, the
# same value as the repository variable deploy.yml reads; the script refuses to run if az is on another one.
#
# The smoke runs only once the new revision is Healthy and is the app's latest ready revision (#68). In
# single revision mode Azure keeps traffic on the old revision until the new one is ready, and /health is
# the same on both, so without the wait a broken image would pass the smoke against the old revision.
set -euo pipefail

REGISTRY=watchpartyd4d84f
RESOURCE_GROUP=rg-watchparty
APP=ca-watchparty
RECAP_JOB=caj-watchparty-recap
# The smoke's target: APP_URL in the environment (https://couchgaffer.com once it is bound, #66), else the
# app's default host, which keeps working beside a custom domain.
APP_URL="${APP_URL:-https://ca-watchparty.delightfulsand-8cf751ef.swedencentral.azurecontainerapps.io}"
IMAGE_QUERY='properties.template.containers[0].image'
BUILD_TIMEOUT=1800  # seconds each az acr build (--remote-build) may run, half the az default of 3600

dry_run=0
remote_build=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) dry_run=1 ;;
    --remote-build) remote_build=1 ;;
    *) echo "usage: $0 [--dry-run] [--remote-build]" >&2; exit 2 ;;
  esac
done

fail() { echo "deploy: $*" >&2; exit 1; }
# APP_URL is a common name in other projects' shells (http://localhost:3000): a stale one would smoke the wrong host.
[[ "$APP_URL" =~ ^https://[a-z0-9.-]+$ ]] || fail "APP_URL $APP_URL is not an https:// host such as https://couchgaffer.com"
run() {
  if (( dry_run )); then echo "+ $*"; return 0; fi
  "$@"
}

cd "$(dirname "$0")/.."

# Guards: deploy exactly a release (#93), from a clean tree, to the right subscription. A release is HEAD
# being origin's main, read live, with one vX.Y.Z tag on origin; scripts/release_tag.sh, which the workflow
# runs too, checks it and prints the version.
on_release() {
  [[ -z "$(git status --porcelain)" ]] || fail "the working tree has changes; commit or stash them first"
  head="$(git rev-parse HEAD)"
  version="$(scripts/release_tag.sh)" || exit 1
}
on_release

if (( ! dry_run )); then
  expected="${AZURE_SUBSCRIPTION_ID:-}"
  [[ -n "$expected" ]] || fail "set AZURE_SUBSCRIPTION_ID to the subscription to deploy to"
  current="$(az account show --query id --output tsv 2>/dev/null)" || fail "az is not signed in; run az login"
  [[ "$current" == "$expected" ]] || fail "az is on subscription $current; run az account set --subscription $expected"
  (( remote_build )) || docker info > /dev/null 2>&1 || fail "Docker is not running"
  echo "== make check (what CI runs)"
  make check
  # Again: make check can rewrite a lock file, and the tree, main or the tag may have moved while it ran.
  on_release
fi

app_image="$REGISTRY.azurecr.io/watchparty:$head"
recap_image="$REGISTRY.azurecr.io/watchparty-recap:$head"
# The version tag names the same image for people; the roll, the check and the rollback use the commit tag.
app_version_image="$REGISTRY.azurecr.io/watchparty:$version"
recap_version_image="$REGISTRY.azurecr.io/watchparty-recap:$version"

# Built from the commit itself (git archive), not the working tree, so the image is exactly $head.
build() {
  if (( dry_run )); then echo "+ git archive $head | docker buildx build $* -"; return 0; fi
  git archive --format=tar "$head" | docker buildx build --platform linux/amd64 --pull --provenance=false "$@" -
}
# --remote-build: the same commit's tree, extracted to a temp dir and built by ACR Tasks. az acr build
# uploads the dir (honouring .dockerignore) and takes --image as repo:tag in this registry, so it pushes the
# images named above.
remote_build() {
  local context
  if (( dry_run )); then
    context="<temp dir>"
    echo "+ git archive $head | tar -x -C $context"
  else
    context="$(mktemp -d "${TMPDIR:-/tmp}/watchparty-build.XXXXXX")"
    # shellcheck disable=SC2064  # expand now: context is local to this function
    trap "rm -rf '$context'" EXIT  # until the builds end; on_exit replaces it below
    git archive --format=tar "$head" | tar -x -C "$context"
  fi
  # --file by its full path: az reads a relative one from the current directory, the working tree.
  run az acr build --registry "$REGISTRY" --platform linux/amd64 --timeout "$BUILD_TIMEOUT" \
    --file "$context/Dockerfile" --image "watchparty:$head" --image "watchparty:$version" "$context"
  check_pushed watchparty
  run az acr build --registry "$REGISTRY" --platform linux/amd64 --timeout "$BUILD_TIMEOUT" \
    --file "$context/Dockerfile" --target recap --image "watchparty-recap:$head" --image "watchparty-recap:$version" \
    "$context"
  check_pushed watchparty-recap
  if (( ! dry_run )); then rm -rf "$context"; trap - EXIT; fi
}
# az acr build can exit 0 before the run has pushed anything: it catches Ctrl-C, and it stops following the
# run's log after --timeout seconds with no new line. So both tags are read back from the registry, and must
# name one image, before anything rolls onto them.
pushed() {  # pushed REPO
  local by_commit by_version
  by_commit="$(az acr repository show --name "$REGISTRY" --image "$1:$head" --query digest --output tsv)" || return 1
  by_version="$(az acr repository show --name "$REGISTRY" --image "$1:$version" --query digest --output tsv)" ||
    return 1
  [[ -n "$by_commit" && "$by_commit" == "$by_version" ]]
}
check_pushed() {  # check_pushed REPO
  if (( dry_run )); then
    run az acr repository show --name "$REGISTRY" --image "$1:$head" --query digest --output tsv
    run az acr repository show --name "$REGISTRY" --image "$1:$version" --query digest --output tsv
    return 0
  fi
  pushed "$1" || fail "the registry has no $1:$head and $1:$version naming one image; nothing rolled"
}
if (( remote_build )); then
  echo "== build $version ($head) for linux/amd64 in the registry $REGISTRY"
  remote_build
else
  echo "== build and push $version ($head) for linux/amd64"
  run az acr login --name "$REGISTRY"
  build --tag "$app_image" --tag "$app_version_image" --push
  build --target recap --tag "$recap_image" --tag "$recap_version_image" --push
fi

run az config set extension.use_dynamic_install=yes_without_prompt --only-show-errors
if (( dry_run )); then
  previous_app="<current app image>"
  previous_recap="<current recap image>"
else
  previous_app="$(az containerapp show --name "$APP" --resource-group "$RESOURCE_GROUP" \
    --query "$IMAGE_QUERY" --output tsv)"
  previous_recap="$(az containerapp job show --name "$RECAP_JOB" --resource-group "$RESOURCE_GROUP" \
    --query "$IMAGE_QUERY" --output tsv)"
  [[ -n "$previous_app" && -n "$previous_recap" ]] || fail "could not read the images running now; nothing rolled"
fi
echo "== before: app $previous_app"
echo "           recap $previous_recap"

roll_back() {
  set +e  # try both, whatever the first does
  echo "== rolling back to $previous_app and $previous_recap" >&2
  run az containerapp job update --name "$RECAP_JOB" --resource-group "$RESOURCE_GROUP" \
    --image "$previous_recap" --only-show-errors --output none
  local recap_status=$?
  run az containerapp update --name "$APP" --resource-group "$RESOURCE_GROUP" --image "$previous_app" \
    --only-show-errors --query properties.latestRevisionName --output tsv
  local app_status=$?
  (( recap_status == 0 )) || echo "deploy: the recap job did NOT roll back; set it to $previous_recap by hand" >&2
  (( app_status == 0 )) || echo "deploy: the app did NOT roll back; set it to $previous_app by hand" >&2
  (( recap_status == 0 && app_status == 0 )) && echo "deploy: failed and rolled back" >&2
}
on_exit() {
  local status=$?
  # Ignore further signals while rolling back, so a second Ctrl-C or kill cannot stop it halfway. The az
  # children inherit the ignore. After status, which trap would overwrite.
  trap '' HUP TERM INT
  trap - EXIT
  (( status == 0 )) || roll_back
  exit "$status"
}
trap on_exit EXIT
# Under bash 3.2 the EXIT trap sees status 0 after TERM or HUP, so on_exit would not roll back (#68). INT
# already arrives as 130. The traps run once the foreground command has ended: a closed terminal or a
# kill of the process group ends the smoke too, a kill of this process alone waits for it.
trap 'exit 129' HUP
trap 'exit 143' TERM

# Wait for the new revision to be Healthy and the app's latest ready revision; give up on Failed or after
# 5 minutes. Until then the old revision is still taking the traffic.
# The poll's commands, shared with --dry-run, which prints them; the revision name and --query go last.
revision_show=(az containerapp revision show --name "$APP" --resource-group "$RESOURCE_GROUP" --output tsv --revision)
latest_ready=(az containerapp show --name "$APP" --resource-group "$RESOURCE_GROUP" --output tsv
  --query properties.latestReadyRevisionName)
wait_for_revision() {
  local revision=$1 running health ready
  for _ in $(seq 1 30); do
    running="$("${revision_show[@]}" "$revision" --query properties.runningState)" ||
      fail "could not read revision $revision"
    [[ "$running" != Failed ]] || fail "revision $revision failed to start"
    health="$("${revision_show[@]}" "$revision" --query properties.healthState)" ||
      fail "could not read revision $revision"
    ready="$("${latest_ready[@]}")" || fail "could not read the latest ready revision"
    if [[ "$health" == Healthy && "$ready" == "$revision" ]]; then return 0; fi
    echo "   $revision: $running, $health; latest ready $ready"
    sleep 10
  done
  fail "revision $revision was not healthy and ready within 5 minutes"
}

echo "== roll the recap job, then the app"
run az containerapp job update --name "$RECAP_JOB" --resource-group "$RESOURCE_GROUP" --image "$recap_image" \
  --only-show-errors --output none
if (( dry_run )); then
  run az containerapp update --name "$APP" --resource-group "$RESOURCE_GROUP" --image "$app_image" \
    --only-show-errors --query properties.latestRevisionName --output tsv
  new_revision="<new revision>"
else
  new_revision="$(az containerapp update --name "$APP" --resource-group "$RESOURCE_GROUP" --image "$app_image" \
    --only-show-errors --query properties.latestRevisionName --output tsv)"
  [[ -n "$new_revision" ]] || fail "az containerapp update did not name the new revision"
fi

echo "== wait for $new_revision to be healthy and ready, and to run $app_image"
if (( dry_run )); then
  run "${revision_show[@]}" "$new_revision" --query properties.runningState
  run "${revision_show[@]}" "$new_revision" --query properties.healthState
  run "${latest_ready[@]}"
  run "${revision_show[@]}" "$new_revision" --query "$IMAGE_QUERY"
else
  wait_for_revision "$new_revision"
  revision_image="$("${revision_show[@]}" "$new_revision" --query "$IMAGE_QUERY")"
  [[ "$revision_image" == "$app_image" ]] || fail "revision $new_revision runs $revision_image, not $app_image"
fi

echo "== smoke test $APP_URL"
if (( ! dry_run )); then
  healthy=0
  for _ in $(seq 1 12); do  # the new revision may still be starting
    if curl --fail --silent --max-time 10 "$APP_URL/health" > /dev/null; then healthy=1; break; fi
    sleep 10
  done
  (( healthy )) || fail "$APP_URL/health did not answer within 2 minutes"
fi
# 40 minutes, as deploy.yml's timeout: a hung smoke must fail and roll back. macOS has no timeout(1).
# The venv's python runs under the alarm itself, so the signal reaches the smoke, not a uv parent.
(cd backend && run uv sync --locked --quiet && run perl -e 'alarm shift; exec @ARGV' 2400 .venv/bin/python -m tools.smoke "$APP_URL" --full)
trap - EXIT  # deployed and smoke-tested: nothing below rolls back

if (( ! dry_run )); then
  echo "== after: app $(az containerapp show --name "$APP" --resource-group "$RESOURCE_GROUP" \
    --query "$IMAGE_QUERY" --output tsv)"
  echo "          recap $(az containerapp job show --name "$RECAP_JOB" --resource-group "$RESOURCE_GROUP" \
    --query "$IMAGE_QUERY" --output tsv)"
fi
echo "== deployed $version ($head)"
