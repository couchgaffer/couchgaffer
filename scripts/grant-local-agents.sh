#!/usr/bin/env bash
# Lets one person run the AI agents on their laptop: grants "Cognitive Services OpenAI User" on the
# Foundry account, so DefaultAzureCredential (backend/watchparty/agents/foundry.py) can call the models through
# `az login`. Owner on the resource group does not include the data-plane permission to call the models.
#
# Usage: AZURE_SUBSCRIPTION_ID=<id> scripts/grant-local-agents.sh [--user-object-id <id>] [--dry-run]
#   AZURE_SUBSCRIPTION_ID  the subscription that holds the Foundry account; az must be on it
#   --user-object-id  whose access to grant; defaults to the user signed in to az
#   --dry-run         print the roles and the planned command; assign nothing
set -euo pipefail

RESOURCE_GROUP=rg-watchparty
ACCOUNT=watchparty-foundry-2af6c2
ROLE="Cognitive Services OpenAI User"

user=""
dry_run=0
while (( $# )); do
  case "$1" in
    --user-object-id)
      [[ "${2:-}" =~ ^[0-9a-fA-F-]{36}$ ]] || { echo "grant: --user-object-id needs an Entra object id (a GUID)" >&2; exit 2; }
      user="$2"; shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    *) echo "usage: $0 [--user-object-id <id>] [--dry-run]" >&2; exit 2 ;;
  esac
done

fail() { echo "grant: $*" >&2; exit 1; }

expected="${AZURE_SUBSCRIPTION_ID:-}"
[[ -n "$expected" ]] || fail "set AZURE_SUBSCRIPTION_ID to the subscription that holds $ACCOUNT"
current="$(az account show --query id --output tsv 2>/dev/null)" || fail "az is not signed in; run az login"
[[ "$current" == "$expected" ]] || fail "az is on subscription $current; run az account set --subscription $expected"
[[ -n "$user" ]] || user="$(az ad signed-in-user show --query id --output tsv)"
scope="/subscriptions/$current/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.CognitiveServices/accounts/$ACCOUNT"

roles() {
  az role assignment list --assignee "$user" --scope "$scope" --include-inherited --include-groups --only-show-errors \
    --query '[].roleDefinitionName' --output tsv | sort | paste -sd, -
}

before="$(roles)" || fail "could not list role assignments for $user on $ACCOUNT"
echo "before: user $user on $ACCOUNT: ${before:-none}"
if [[ ",$before," == *",$ROLE,"* ]]; then
  echo "already assigned: $ROLE; nothing to do"
  exit 0
fi

command=(az role assignment create --assignee-object-id "$user" --assignee-principal-type User
         --role "$ROLE" --scope "$scope" --only-show-errors --output none)
if (( dry_run )); then
  printf '+'; printf ' %q' "${command[@]}"; echo
  exit 0
fi
"${command[@]}"
after="$(roles)" || fail "assigned, but could not list role assignments afterwards; check in the portal"
echo "after:  user $user on $ACCOUNT: $after"
echo "Role assignments can take a few minutes to apply. Then start the backend as infra/README.md shows."
