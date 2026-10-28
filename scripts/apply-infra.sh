#!/usr/bin/env bash
# Apply infra/main.bicep by hand, as an owner. Infrastructure changes need role-assignment rights, so
# they never run in CI. The script reads what the template must be given from what is live, so an apply
# never rolls the app or the recap job back, never changes the app's scale or settings by accident, and never
# changes the budget's alert emails or amount:
#   - the app's and the recap job's running images, read again just before an apply creates
#   - the app's replica count (appReplicas), and whether it already has REDIS_URL (wireRedis) and
#     DATABASE_URL (wirePostgres), each read on its own
#   - whether Redis and PostgreSQL already exist, so a run without their switch keeps managing them
#   - the budget's current alert emails and amount
#   - the User who holds Search Service Contributor on the search service, for the knowledge admin roles,
#     so an apply never grants them to whoever runs it (#130); the signed-in user only when nobody holds it.
#     With --postgres, the same person becomes the server's second Entra administrator, only when it has no
#     person as one yet
#   - the app's custom domain, if it has one (#66), and with a domain the environment's static IP and custom domain
#     verification id, for the DNS check under --domain
#   - with Redis on, the signed-in user, who gets the Redis access policy (redisUserId) to run
#     scripts/check-redis-publish.sh: the person running the checks, not necessarily the knowledge admin
# It stops if any of them is empty or cannot be read. It also stops, naming what to remove, if the search role
# is held by more than one principal, by anything but a User, or from above the service, or if the admin holds
# either knowledge role under an assignment the template did not name (the create would fail).
#
# Usage: scripts/apply-infra.sh [--redis] [--postgres] [--wire] [--domain NAME] [--apply | --dry-run]
#   (none)      print the state before, then the what-if (nothing changes in Azure)
#   --redis     also create Azure Managed Redis (enableRedis=true); it does not touch the app
#   --postgres  also create Azure Database for PostgreSQL (enablePostgres=true); it does not touch the app
#   --postgres-sku NAME, --postgres-location REGION
#               the server's size and region when creating it (default Standard_B1ms in the group's region), for a
#               region out of capacity. Once the server exists the script passes its live size and region and
#               refuses a different value
#   --wire      wire the app to both (#180): REDIS_URL, DATABASE_URL and DATABASE_AUTH, and two replicas
#               (wireRedis=true wirePostgres=true appReplicas=2). Only once both services exist; the after
#               state then checks the three settings and the two replicas
#   --domain NAME
#               also serve the app on NAME (#66), an apex domain such as couchgaffer.com, with a free managed
#               certificate. It takes two applies: the first adds the host name with its binding disabled, the
#               second, once Azure sees the host name on the app, issues the certificate and binds it (SniEnabled).
#               Before the what-if it checks with dig that NAME has an A record to the environment's static IP
#               and asuid.NAME a TXT record with the environment's verification id, as read from Azure, and stops
#               printing the records to create if not. Once the host name is on the app, a run without the flag
#               keeps it (and checks its records), so a later apply never drops it. One custom domain only
#   --apply     the same, then create the deployment and print the state after; exits non-zero if the
#               Application Insights setup, or a service asked for, is not what the template says
#   --dry-run   read what is live and print the what-if and create commands without running them
#
# Applying a change to the app's environment variables or scale starts a new revision and restarts the one
# replica: every live match ends. Apply only when no match is running. Without --wire this script never changes
# wireRedis, wirePostgres or appReplicas: it passes them back as they are live.
#
# Needs: az signed in (az login) as an owner of rg-watchparty, and AZURE_SUBSCRIPTION_ID in the environment naming
# the subscription; it refuses to run if az is on another one. Prints no secrets: there are none here (the
# Application Insights connection string cannot send anything with local auth off; Redis and PostgreSQL have
# no keys or passwords). Bash 3.2 compatible.
set -euo pipefail

RESOURCE_GROUP=rg-watchparty
APP=ca-watchparty
RECAP_JOB=caj-watchparty-recap
BUDGET=budget-watchparty
COMPONENT=appi-watchparty
SETTING=APPLICATIONINSIGHTS_CONNECTION_STRING
PUBLISHER="Monitoring Metrics Publisher"
SEARCH_ROLE="Search Service Contributor"
SEARCH_ROLE_ID=7ca78c08-252a-4471-8644-bb5ff32d4ba0
DATA_ROLE="Search Index Data Contributor"
DATA_ROLE_ID=8ebe5a00-799e-43f5-93ac-243d3dce84a7
IMAGE_QUERY='properties.template.containers[0].image'
SETTING_QUERY="properties.template.containers[0].env[?name=='$SETTING'].name | [0]"
SETTINGS_QUERY='properties.template.containers[0].env[].name'
REPLICAS_QUERY='properties.template.scale.minReplicas'
MAX_REPLICAS_QUERY='properties.template.scale.maxReplicas'
AUTH_QUERY="properties.template.containers[0].env[?name=='DATABASE_AUTH'].value | [0]"
REDIS_TYPE=Microsoft.Cache/redisEnterprise
POSTGRES_TYPE=Microsoft.DBforPostgreSQL/flexibleServers
REDIS_API=2025-07-01
POSTGRES_API=2025-08-01
ENVIRONMENT=cae-watchparty
DOMAINS_QUERY='properties.configuration.ingress.customDomains[].name'
RESOLVER=1.1.1.1  # Cloudflare's resolver, beside the zone's own servers: a laptop's resolver may cache a record's absence longer
USAGE="usage: $0 [--redis] [--postgres] [--postgres-sku NAME] [--postgres-location REGION] [--wire] [--domain NAME] [--apply | --dry-run]"

mode=whatif
redis=false
postgres=false
wire=false
pg_sku=""
pg_location=""
domain=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --postgres-sku | --postgres-location)
      [[ $# -gt 1 && "$2" != --* ]] || { echo "$USAGE" >&2; exit 2; }
      [[ "$1" == --postgres-sku ]] && pg_sku="$2" || pg_location="$2"
      shift ;;
    --domain)
      [[ $# -gt 1 && "$2" != --* ]] || { echo "$USAGE" >&2; exit 2; }
      domain="$2"
      shift ;;
    --redis) redis=true ;;
    --postgres) postgres=true ;;
    --wire) wire=true ;;
    --apply | --dry-run)
      [[ "$mode" == whatif ]] || { echo "$USAGE" >&2; exit 2; }
      [[ "$1" == --apply ]] && mode=apply || mode=dry ;;
    *) echo "$USAGE" >&2; exit 2 ;;
  esac
  shift
done

fail() { echo "apply-infra: $*" >&2; exit 1; }

template_guid() {  # The template's guid(a, b, c): a version 5 UUID of the arguments joined by '-', in ARM's namespace
  local IFS=- hash
  hash="$({ printf '\x11\xfb\x06\xfb\x71\x2d\x4d\xdd\x98\xc7\xe7\x1b\xbd\x58\x88\x30'; printf '%s' "$*"; } | shasum -a 1)"
  printf '%s-%s-5%s-%x%s-%s\n' "${hash:0:8}" "${hash:8:4}" "${hash:13:3}" "$(((0x${hash:16:1} & 3) | 8))" \
    "${hash:17:3}" "${hash:20:12}"
}

lower() { tr '[:upper:]' '[:lower:]' <<< "$1"; }

named_by_template() {  # $1 role name, $2 role id: stops if the admin holds the role on the service under another name
  local found
  found="$(az role assignment list --scope "$search" --role "$1" --assignee-object-id "$admin" --query "[].id" -o tsv)" ||
    fail "could not read $1 for $admin on ${search##*/}"
  [[ -z "$found" || "${found##*/}" == "$(template_guid "$search" "$admin" "$2")" ]] && return 0
  fail "$upn holds $1 on ${search##*/} through an assignment the template did not name, so the apply would fail" \
    "with RoleAssignmentExists. Remove it with: az role assignment delete --ids $found ; then run again signed in as $upn"
}

cd "$(dirname "$0")/.."

expected="${AZURE_SUBSCRIPTION_ID:-}"
[[ -n "$expected" ]] || fail "set AZURE_SUBSCRIPTION_ID to the subscription that holds $RESOURCE_GROUP"
current="$(az account show --query id --output tsv 2> /dev/null)" || fail "az is not signed in; run az login"
[[ "$current" == "$expected" ]] || fail "az is on subscription $current; run az account set --subscription $expected"

image="$(az containerapp show -n "$APP" -g "$RESOURCE_GROUP" --query "$IMAGE_QUERY" -o tsv)"
recap="$(az containerapp job show -n "$RECAP_JOB" -g "$RESOURCE_GROUP" --query "$IMAGE_QUERY" -o tsv)"
emails="$(az consumption budget show --budget-name "$BUDGET" -g "$RESOURCE_GROUP" \
  --query "notifications.*.contactEmails | [0]" -o json | tr -d ' \n')" || fail "could not read the budget's alert emails"
# Azure returns the amount as a decimal (100.0); the template takes a whole number.
amount="$(az consumption budget show --budget-name "$BUDGET" -g "$RESOURCE_GROUP" --query amount -o tsv)" ||
  fail "could not read the budget amount: az failed"
search="$(az resource list -g "$RESOURCE_GROUP" --resource-type Microsoft.Search/searchServices \
  --query "[?starts_with(name, 'srch-watchparty-')].id" -o tsv)" || fail "could not read the search service in $RESOURCE_GROUP"
[[ "$search" != *$'\n'* ]] ||
  fail "more than one srch-watchparty-* search service in $RESOURCE_GROUP: $(tr '\n' ' ' <<< "$search")"
# Everyone holding the search role on the service, inherited included: principal id, type, scope, assignment id.
rows=""
if [[ -n "$search" ]]; then
  rows="$(az role assignment list --scope "$search" --role "$SEARCH_ROLE" --include-inherited \
    --query "[].[principalId, principalType, scope, id]" -o tsv)" || fail "could not read who holds $SEARCH_ROLE on ${search##*/}"
fi
holders=""
count=0
while IFS=$'\t' read -r principal type scope assignment; do
  [[ -n "$principal" ]] || continue
  [[ "$type" == User && "$(lower "$scope")" == "$(lower "$search")" ]] ||
    fail "$type $principal holds $SEARCH_ROLE on ${search##*/} through $scope; this script handles only Users" \
      "holding it directly on the service. Give it to one User there, then run again. An assignment held from a" \
      "resource group or subscription may serve other resources: check before removing it"
  holders+="$principal $assignment"$'\n'
  count=$((count + 1))
done <<< "$rows"
if [[ $count -gt 1 ]]; then
  echo "apply-infra: $count Users hold $SEARCH_ROLE on ${search##*/}. Keep one, remove the others, then run again:" >&2
  while read -r principal assignment; do
    label="named by the template"
    [[ "${assignment##*/}" == "$(template_guid "$search" "$principal" "$SEARCH_ROLE_ID")" ]] ||
      label="not named by the template; remove this one"
    echo "  $principal ($label): az role assignment delete --ids $assignment" >&2
  done <<< "${holders%$'\n'}"
  exit 1
fi
admin="${holders%% *}"
admin_source="the current $SEARCH_ROLE"
if [[ -z "$admin" ]]; then
  admin="$(az ad signed-in-user show --query id -o tsv)"
  admin_source="the signed-in user; no User holds $SEARCH_ROLE on this service"
fi
[[ -n "$admin" ]] || fail "could not read the signed-in user's object id"
upn="$(az ad user show --id "$admin" --query userPrincipalName -o tsv)" && [[ -n "$upn" ]] ||
  fail "could not read the user name of $admin"
if [[ -n "$search" ]]; then
  named_by_template "$SEARCH_ROLE" "$SEARCH_ROLE_ID"
  named_by_template "$DATA_ROLE" "$DATA_ROLE_ID"
fi
[[ -n "$image" ]] || fail "could not read the app's image"
[[ -n "$recap" ]] || fail "could not read the recap job's image"
[[ -n "$emails" && "$emails" != "null" && "$emails" != "[]" ]] || fail "could not read the budget's alert emails"
[[ -n "$amount" ]] || fail "could not read the budget amount"
[[ "$amount" =~ ^[1-9][0-9]*(\.0+)?$ ]] || fail "the budget amount $amount is not a whole number above 0"
amount="${amount%%.*}"

# What the app runs now, passed back unchanged: its scale and whether it is wired to Redis or PostgreSQL.
replicas="$(az containerapp show -n "$APP" -g "$RESOURCE_GROUP" --query "$REPLICAS_QUERY" -o tsv)" ||
  fail "could not read the app's replicas"
[[ "$replicas" == 1 || "$replicas" == 2 ]] || fail "could not read the app's replicas (got '$replicas')"
settings="$(az containerapp show -n "$APP" -g "$RESOURCE_GROUP" --query "$SETTINGS_QUERY" -o tsv)" ||
  fail "could not read the app's settings"
[[ -n "$settings" ]] || fail "could not read the app's settings"
# One switch per URL, so wiring one service never adds the other's setting and restarts the replica.
wired_redis=false
wired_postgres=false
if grep -qx REDIS_URL <<< "$settings"; then wired_redis=true; fi
if grep -qx DATABASE_URL <<< "$settings"; then wired_postgres=true; fi

# A service that exists stays managed, so leaving its switch off never drops it from the template.
service_id() {  # $1: resource type. The first such resource's id, empty if there is none.
  az resource list -g "$RESOURCE_GROUP" --resource-type "$1" --query "[0].id" -o tsv
}
redis_id="$(service_id "$REDIS_TYPE")" || fail "could not list Redis in $RESOURCE_GROUP"
postgres_id="$(service_id "$POSTGRES_TYPE")" || fail "could not list PostgreSQL in $RESOURCE_GROUP"
[[ -z "$redis_id" ]] || redis=true
[[ -z "$postgres_id" ]] || postgres=true

# PostgreSQL's size and region: as live once the server exists, so a later run never resizes or moves it;
# otherwise the flags, or the template's defaults.
[[ -z "$pg_sku$pg_location" || "$postgres" == true ]] || fail "--postgres-sku and --postgres-location go with --postgres"
[[ -z "$pg_sku" || "$pg_sku" =~ ^Standard_[A-Za-z0-9_]+$ ]] || fail "--postgres-sku $pg_sku is not a size like Standard_B2s"
[[ -z "$pg_location" || "$pg_location" =~ ^[a-z0-9]+$ ]] || fail "--postgres-location $pg_location is not a region like northeurope"
live() {  # live QUERY: a property of the live server, stopping if it cannot be read
  local out
  out="$(az resource show --ids "$postgres_id" --api-version "$POSTGRES_API" --query "$1" -o tsv)" &&
    [[ -n "$out" ]] || fail "could not read $1 of ${postgres_id##*/}"
  echo "$out"
}
if [[ -n "$postgres_id" ]]; then
  live_sku="$(live sku.name)" || exit 1
  live_location="$(live location | tr -d ' ' | tr '[:upper:]' '[:lower:]')" || exit 1
  [[ -z "$pg_sku" || "$pg_sku" == "$live_sku" ]] || fail "${postgres_id##*/} exists as $live_sku; this script does not resize it"
  [[ -z "$pg_location" || "$pg_location" == "$live_location" ]] ||
    fail "${postgres_id##*/} exists in $live_location; a server cannot move region"
  pg_sku="$live_sku"
  pg_location="$live_location"
fi
pg_sku="${pg_sku:-Standard_B1ms}"
case "$pg_sku" in
  Standard_B*) pg_tier=Burstable ;;
  Standard_D*) pg_tier=GeneralPurpose ;;
  Standard_E*) pg_tier=MemoryOptimized ;;
  *) fail "no tier known for $pg_sku" ;;
esac

# A deployment into a namespace the subscription is not registered for fails only after minutes of other work
# (#180: Microsoft.Cache and Microsoft.DBforPostgreSQL, 27 Oct), so check before the what-if.
registered() {  # $1: provider namespace
  local got
  got="$(az provider show -n "$1" --query registrationState -o tsv)" || fail "could not read the registration of $1"
  [[ "$got" == Registered ]] ||
    fail "the subscription is not registered for $1 (state: ${got:-none}); run: az provider register --namespace $1 --wait"
}
[[ "$redis" != true ]] || registered Microsoft.Cache
[[ "$postgres" != true ]] || registered Microsoft.DBforPostgreSQL

# The custom domain (#66): the flag's, or the one already on the app, so an apply without the flag keeps it.
[[ -z "$domain" || "$domain" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$ ]] ||
  fail "--domain $domain is not a lower-case host name like couchgaffer.com"
live_domains="$(az containerapp show -n "$APP" -g "$RESOURCE_GROUP" --query "$DOMAINS_QUERY" -o tsv)" ||
  fail "could not read $APP's custom domains"
[[ "$live_domains" != *$'\n'* ]] ||
  fail "$APP has more than one custom domain ($(tr '\n' ' ' <<< "$live_domains")); this script handles one"
[[ -z "$domain" || -z "$live_domains" || "$domain" == "$live_domains" ]] ||
  fail "$APP already serves $live_domains; this script handles one custom domain and does not replace it"
domain="${domain:-$live_domains}"
bound=false
[[ -z "$domain" || "$domain" != "$live_domains" ]] || bound=true

# The records Azure checks before it issues the certificate, compared with what public DNS answers now.
dns_records() {
  local ip verification a txt ok=0
  ip="$(az containerapp env show -n "$ENVIRONMENT" -g "$RESOURCE_GROUP" --query properties.staticIp -o tsv)" &&
    [[ -n "$ip" ]] || fail "could not read the static IP of $ENVIRONMENT"
  verification="$(az containerapp env show -n "$ENVIRONMENT" -g "$RESOURCE_GROUP" \
    --query properties.customDomainConfiguration.customDomainVerificationId -o tsv)" && [[ -n "$verification" ]] ||
    fail "could not read the custom domain verification id of $ENVIRONMENT"
  a="$(dig +short "@$RESOLVER" A "$domain")" || fail "could not look up $domain with dig"
  txt="$(dig +short "@$RESOLVER" TXT "asuid.$domain")" || fail "could not look up asuid.$domain with dig"
  # Exactly the one address: a second one, or a proxied record's Cloudflare addresses, fail the certificate's check.
  [[ "$a" == "$ip" ]] || ok=1
  grep -qx "\"$verification\"" <<< "$txt" || ok=1
  if [[ $ok != 0 ]]; then
    echo "apply-infra: the DNS records for $domain are not in place. In Cloudflare, create these, DNS only (grey cloud)," >&2
    echo "then run again:" >&2
    echo "  A    $domain    $ip    (now: $(paste -sd' ' - <<< "${a:-none}"))" >&2
    echo "  TXT  asuid.$domain    $verification    (now: $(paste -sd' ' - <<< "${txt:-none}"))" >&2
    exit 1
  fi
  echo "dns: $domain A $ip; asuid.$domain TXT matches $ENVIRONMENT's verification id"
}

# --wire points the app at both services and runs it on two replicas. Both must exist before this run: wiring in
# the same deployment that creates a service would start the app before its access is in place.
if [[ "$wire" == true ]]; then
  [[ -n "$redis_id" && -n "$postgres_id" ]] ||
    fail "--wire needs Redis and PostgreSQL to exist; create them first with: $0 --redis --postgres --apply"
  wired_redis=true
  wired_postgres=true
  replicas=2
fi

args=(-g "$RESOURCE_GROUP" -f infra/main.bicep -p "image=$image" -p "recapImage=$recap" -p "alertEmails=$emails"
      -p "budgetAmount=$amount" -p "knowledgeAdminId=$admin" -p "enableRedis=$redis" -p "enablePostgres=$postgres"
      -p "wireRedis=$wired_redis" -p "wirePostgres=$wired_postgres" -p "appReplicas=$replicas")
if [[ -n "$domain" ]]; then
  args+=(-p "customDomain=$domain" -p "customDomainBound=$bound")
fi
if [[ "$postgres" == true ]]; then
  args+=(-p "postgresSku=$pg_sku" -p "postgresTier=$pg_tier")
  [[ -z "$pg_location" ]] || args+=(-p "postgresLocation=$pg_location")
fi
# The signed-in user also gets Redis access, for the checks they run next from this laptop (#144, #180).
me=""
if [[ "$redis" == true ]]; then
  me="$(az ad signed-in-user show --query id -o tsv)" || fail "could not read the signed-in user's object id"
  [[ -n "$me" ]] || fail "could not read the signed-in user's object id"
  args+=(-p "redisUserId=$me")
fi

# The knowledge admin also becomes the server's second Entra administrator, only when it has no person as one
# yet, so a run by someone else never adds them.
people=0
if [[ -n "$postgres_id" ]]; then
  people="$(az rest --method get --url "https://management.azure.com$postgres_id/administrators?api-version=$POSTGRES_API" \
    --query "length(value[?properties.principalType=='User'])" -o tsv)" || fail "could not list PostgreSQL's administrators"
  [[ "$people" =~ ^[0-9]+$ ]] || fail "could not list PostgreSQL's administrators (got '$people')"
fi
if [[ "$postgres" == true && "$people" == 0 ]]; then
  args+=(-p "postgresAdminId=$admin" -p "postgresAdminName=$upn")
fi

component_id() {  # The component's id, empty if it does not exist; stops if it could not be read at all
  local out
  out="$(az resource show -g "$RESOURCE_GROUP" -n "$COMPONENT" --resource-type Microsoft.Insights/components \
    --query id -o tsv 2>&1)" && { echo "$out"; return 0; }
  grep -q "ResourceNotFound" <<< "$out" && return 0
  fail "could not read component $COMPONENT: ${out%%$'\n'*}"
}

child() {  # child ID API QUERY: a child resource's property, empty only when az says the child does not exist
  local out
  out="$(az resource show --ids "$1" --api-version "$2" --query "$3" -o tsv 2>&1)" && { echo "$out"; return 0; }
  # Redis children say "invalid status 'Not Found'" without the ResourceNotFound code (#191).
  grep -qE "ResourceNotFound|invalid status 'Not Found'" <<< "$out" && return 0
  fail "could not read ${1##*/}: ${out%%$'\n'*}"
}

set_or_not() { [[ -n "$1" ]] && echo set || echo "not set"; }

state() {  # $1: before or after. Prints the setup; returns 1 if a part this run asks for is not complete.
  local complete=0 id local_auth workspace roles identity principal app_setting job_setting app_principal
  local now_replicas now_settings binding rid ha keys policy person pid password entra app_admin name
  echo "== $1"
  id="$(component_id)" || exit 1
  if [[ -z "$id" ]]; then
    echo "component $COMPONENT: none"
    complete=1
  else
    local_auth="$(az resource show --ids "$id" --query properties.DisableLocalAuth -o tsv)" ||
      fail "could not read $COMPONENT's local auth"
    workspace="$(az resource show --ids "$id" --query properties.WorkspaceResourceId -o tsv)" ||
      fail "could not read $COMPONENT's workspace"
    echo "component $COMPONENT: local auth disabled: $local_auth; workspace: ${workspace##*/}"
    [[ "$local_auth" == "true" && "${workspace##*/}" == "log-watchparty" ]] || complete=1
    roles="$(az role assignment list --scope "$id" --query "[?roleDefinitionName=='$PUBLISHER'].principalId" -o tsv)" ||
      fail "could not list the roles on $COMPONENT"
    for identity in id-watchparty id-watchparty-recap; do
      principal="$(az identity show -n "$identity" -g "$RESOURCE_GROUP" --query principalId -o tsv)" ||
        fail "could not read $identity"
      if [[ -n "$principal" ]] && printf '%s\n' "$roles" | grep -qx "$principal"; then
        echo "$PUBLISHER for $identity: yes"
      else
        echo "$PUBLISHER for $identity: no"
        complete=1
      fi
    done
  fi
  app_setting="$(az containerapp show -n "$APP" -g "$RESOURCE_GROUP" --query "$SETTING_QUERY" -o tsv)" ||
    fail "could not read $APP's settings"
  job_setting="$(az containerapp job show -n "$RECAP_JOB" -g "$RESOURCE_GROUP" --query "$SETTING_QUERY" -o tsv)" ||
    fail "could not read $RECAP_JOB's settings"
  echo "$SETTING on $APP: $(set_or_not "$app_setting")"
  echo "$SETTING on $RECAP_JOB: $(set_or_not "$job_setting")"
  [[ -n "$app_setting" && -n "$job_setting" ]] || complete=1

  # The app's scale and setting names: an apply from this script must leave both as they were.
  now_replicas="$(az containerapp show -n "$APP" -g "$RESOURCE_GROUP" --query "$REPLICAS_QUERY" -o tsv)" ||
    fail "could not read $APP's replicas"
  now_settings="$(az containerapp show -n "$APP" -g "$RESOURCE_GROUP" --query "$SETTINGS_QUERY" -o tsv)" ||
    fail "could not read $APP's settings"
  echo "$APP replicas: $now_replicas"
  echo "$APP settings: $(printf '%s\n' "$now_settings" | paste -sd' ' -)"
  app_principal="$(az identity show -n id-watchparty -g "$RESOURCE_GROUP" --query principalId -o tsv)" ||
    fail "could not read id-watchparty"
  [[ -n "$app_principal" ]] || fail "could not read id-watchparty"
  # With --wire, the after state must show all three settings, Entra sign-in for PostgreSQL, and two replicas
  # both ways (minimum and maximum).
  if [[ "$wire" == true && "$1" == after ]]; then
    local max auth
    max="$(az containerapp show -n "$APP" -g "$RESOURCE_GROUP" --query "$MAX_REPLICAS_QUERY" -o tsv)" ||
      fail "could not read $APP's maximum replicas"
    auth="$(az containerapp show -n "$APP" -g "$RESOURCE_GROUP" --query "$AUTH_QUERY" -o tsv)" ||
      fail "could not read $APP's DATABASE_AUTH"
    echo "$APP maximum replicas: $max; DATABASE_AUTH: ${auth:-not set}"
    [[ "$now_replicas" == 2 && "$max" == 2 && "$auth" == entra ]] || complete=1
    for name in REDIS_URL DATABASE_URL; do
      grep -qx "$name" <<< "$now_settings" || { echo "$name on $APP: not set"; complete=1; }
    done
  fi

  if [[ -n "$domain" ]]; then
    binding="$(az containerapp show -n "$APP" -g "$RESOURCE_GROUP" \
      --query "properties.configuration.ingress.customDomains[?name=='$domain'].bindingType | [0]" -o tsv)" ||
      fail "could not read $APP's binding of $domain"
    echo "custom domain $domain: ${binding:-not on the app}"
    # After the first apply the host name must be there; after the second, bound to its certificate.
    [[ -n "$binding" ]] || complete=1
    [[ "$bound" != true || "$binding" == SniEnabled ]] || complete=1
  fi

  rid="$(service_id "$REDIS_TYPE")" || fail "could not list Redis in $RESOURCE_GROUP"
  if [[ -z "$rid" ]]; then
    echo "redis: none"
    [[ "$redis" != true ]] || complete=1
  else
    ha="$(child "$rid" "$REDIS_API" properties.highAvailability)" || exit 1
    keys="$(child "$rid/databases/default" "$REDIS_API" properties.accessKeysAuthentication)" || exit 1
    policy="$(child "$rid/databases/default/accessPolicyAssignments/app" "$REDIS_API" properties.user.objectId)" || exit 1
    person="$(child "$rid/databases/default/accessPolicyAssignments/person" "$REDIS_API" properties.user.objectId)" || exit 1
    echo "redis ${rid##*/}: high availability: ${ha:-none}; access keys: ${keys:-none}; app access: $([[ "$policy" == "$app_principal" ]] && echo yes || echo no); signed-in user's access: $([[ "$(lower "$person")" == "$(lower "$me")" ]] && echo yes || echo no)"
    [[ "$ha" == Disabled && "$keys" == Disabled && "$policy" == "$app_principal" ]] || complete=1
    [[ "$(lower "$person")" == "$(lower "$me")" ]] || complete=1
  fi

  pid="$(service_id "$POSTGRES_TYPE")" || fail "could not list PostgreSQL in $RESOURCE_GROUP"
  if [[ -z "$pid" ]]; then
    echo "postgres: none"
    [[ "$postgres" != true ]] || complete=1
  else
    password="$(child "$pid" "$POSTGRES_API" properties.authConfig.passwordAuth)" || exit 1
    entra="$(child "$pid" "$POSTGRES_API" properties.authConfig.activeDirectoryAuth)" || exit 1
    app_admin="$(child "$pid/administrators/$app_principal" "$POSTGRES_API" properties.principalName)" || exit 1
    echo "postgres ${pid##*/}: password auth: ${password:-none}; Entra auth: ${entra:-none}; app administrator: $([[ "$app_admin" == id-watchparty ]] && echo yes || echo no)"
    [[ "$password" == Disabled && "$entra" == Enabled && "$app_admin" == id-watchparty ]] || complete=1
  fi
  return $complete
}

[[ -z "$domain" ]] || dns_records

echo "subscription $current, resource group $RESOURCE_GROUP, template infra/main.bicep at $(git rev-parse --short HEAD)"
echo "app image $image"
echo "recap image $recap"
echo "knowledge admin $admin $upn ($admin_source)"
if [[ -z "$domain" ]]; then
  echo "custom domain: none"
elif [[ "$bound" == true ]]; then
  echo "custom domain $domain: on the app; this run binds it to its managed certificate (SniEnabled)"
else
  echo "custom domain $domain: this run adds it with binding Disabled; run again after it to issue the certificate and bind it"
fi
if [[ "$wire" == true ]]; then
  echo "redis: $redis; postgres: $postgres; wiring the app: REDIS_URL, DATABASE_URL and DATABASE_AUTH on, replicas 2"
else
  echo "redis: $redis; postgres: $postgres; app replicas kept at $replicas; REDIS_URL kept $([[ "$wired_redis" == true ]] && echo on || echo off); DATABASE_URL kept $([[ "$wired_postgres" == true ]] && echo on || echo off)"
fi

if [[ "$mode" == dry ]]; then
  echo "+ az deployment group what-if ${args[*]}"
  echo "+ az deployment group create ${args[*]}"
  exit 0
fi

state before || true
echo "== the change (what-if)"
az deployment group what-if "${args[@]}"

if [[ "$mode" != apply ]]; then
  echo "nothing applied; run again with --apply, when no match is running"
  exit 0
fi

# The images may have moved while the what-if ran (a code deploy); applying the old ones would roll it back.
now_image="$(az containerapp show -n "$APP" -g "$RESOURCE_GROUP" --query "$IMAGE_QUERY" -o tsv)" ||
  fail "could not re-read the app's image"
now_recap="$(az containerapp job show -n "$RECAP_JOB" -g "$RESOURCE_GROUP" --query "$IMAGE_QUERY" -o tsv)" ||
  fail "could not re-read the recap job's image"
[[ "$now_image" == "$image" && "$now_recap" == "$recap" ]] ||
  fail "the images changed since they were read (app $now_image, recap $now_recap); nothing applied, run again"
echo "== applying with app image $image and recap image $recap"
echo "If the what-if above changes $APP's settings or scale, this restarts the app's replicas and ends every live match."
az deployment group create "${args[@]}" --query properties.provisioningState -o tsv
state after || fail "the setup is not complete; see above"
echo "applied"
if [[ -n "$domain" && "$bound" != true ]]; then
  echo "$domain is on the app without a certificate; run the same command again to issue it and bind it"
fi
