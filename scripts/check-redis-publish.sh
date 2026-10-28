#!/usr/bin/env bash
# The #144 check on the live Azure Managed Redis: does PUBLISH (and PUBSUB NUMSUB) through the proxy count
# subscribers on every shard? Runs backend/tools/check_redis_publish.py signed in as you (az login), which needs
# the Redis access policy that scripts/apply-infra.sh gives the signed-in user who runs it (redisUserId).
#
# Usage: scripts/check-redis-publish.sh [--replicas]
#   (none)       the #144 check: backend/tools/check_redis_publish.py
#   --replicas   the #180 check, once the app is wired: backend/tools/check_replicas.py against the live URL, a
#                match created on one replica watched and pressed through the other. It starts a match, which
#                calls the agents as the smoke test does. If it says every screen landed on the owner, run it again
#
# Reads only: the subscription, the Redis host name and the app's address. Writes nothing in
# Azure; on Redis the #144 check only subscribes and publishes on throwaway channels. Exits 0 only when the tool
# prints PASS; 1 on any short count, missing message, sign-in failure, or a read that fails. Needs
# AZURE_SUBSCRIPTION_ID, as for apply-infra.sh.
set -euo pipefail

RESOURCE_GROUP=rg-watchparty
REDIS_TYPE=Microsoft.Cache/redisEnterprise
REDIS_API=2025-07-01
PORT=10000  # infra/main.bicep, redisPort

fail() { echo "check-redis-publish: $*" >&2; exit 1; }

APP=ca-watchparty
mode=publish
[[ $# -eq 0 || ( $# -eq 1 && "$1" == --replicas ) ]] || { echo "usage: $0 [--replicas]" >&2; exit 2; }
[[ $# -eq 0 ]] || mode=replicas
cd "$(dirname "$0")/.."

expected="${AZURE_SUBSCRIPTION_ID:-}"
[[ -n "$expected" ]] || fail "set AZURE_SUBSCRIPTION_ID to the subscription that holds $RESOURCE_GROUP"
current="$(az account show --query id --output tsv 2> /dev/null)" || fail "az is not signed in; run az login"
[[ "$current" == "$expected" ]] || fail "az is on subscription $current; run az account set --subscription $expected"

redis_id="$(az resource list -g "$RESOURCE_GROUP" --resource-type "$REDIS_TYPE" --query "[0].id" -o tsv)" ||
  fail "could not list Redis in $RESOURCE_GROUP"
[[ -n "$redis_id" ]] || fail "no Redis in $RESOURCE_GROUP; create it with scripts/apply-infra.sh --redis --apply"
host="$(az resource show --ids "$redis_id" --api-version "$REDIS_API" --query properties.hostName -o tsv)" ||
  fail "could not read the host name of ${redis_id##*/}"
[[ -n "$host" ]] || fail "could not read the host name of ${redis_id##*/}"

# No user name: redis-py refuses one beside the Entra credential provider, which takes it from the token.
url="rediss://$host:$PORT"
if [[ "$mode" == publish ]]; then
  echo "checking $host:$PORT as your az login"
  cd backend
  uv run python -m tools.check_redis_publish "$url"
  exit
fi
fqdn="$(az containerapp show -n "$APP" -g "$RESOURCE_GROUP" --query properties.configuration.ingress.fqdn -o tsv)" ||
  fail "could not read the address of $APP"
[[ -n "$fqdn" ]] || fail "could not read the address of $APP"
echo "checking https://$fqdn through $host:$PORT as your az login"
cd backend
uv run python -m tools.check_replicas "$url" "https://$fqdn"
