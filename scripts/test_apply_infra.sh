#!/usr/bin/env bash
# Tests for scripts/apply-infra.sh against a stub az and a stub dig: nothing here calls Azure or DNS.
# The stub logs every call and answers from the variables below; a deployment create flips it to the state
# after the apply, so the script's after-check reads what the template would have made.
#
# Usage: scripts/test_apply_infra.sh [apply script]
#   The default is scripts/apply-infra.sh beside this file; pass a changed copy to check that a case can fail.
#   Every case runs under /bin/bash (3.2 on macOS) and under the bash on PATH when that is another one.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
script="$(cd "$(dirname "${1:-$here/apply-infra.sh}")" && pwd)/$(basename "${1:-$here/apply-infra.sh}")"
[[ -f "$script" ]] || { echo "no apply script at $script" >&2; exit 2; }

tmp="$(mktemp -d "${TMPDIR:-/tmp}/test_apply_infra.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT
passed=0
failed=0
failures=()

mkdir -p "$tmp/bin"
cat > "$tmp/bin/az" << 'EOF'
#!/usr/bin/env bash
echo "az $*" >> "$STUB_LOG"
SEARCH_ID=/subscriptions/s/resourceGroups/rg-watchparty/providers/Microsoft.Search/searchServices/srch-watchparty-x
# Azure reports the scope of an assignment with a lower-case resourcegroups, as the live service does.
SEARCH_SCOPE=/subscriptions/s/resourcegroups/rg-watchparty/providers/Microsoft.Search/searchServices/srch-watchparty-x
ASSIGNMENTS=providers/Microsoft.Authorization/roleAssignments
created() { [[ -f "$STUB_STATE/created" ]]; }
setting() { created && echo APPLICATIONINSIGHTS_CONNECTION_STRING; return 0; }
redis_on() { [[ -n "${AZ_REDIS_LIVE:-}" || -f "$STUB_STATE/redis" ]]; }
postgres_on() { [[ -n "${AZ_POSTGRES_LIVE:-}" || -f "$STUB_STATE/postgres" ]]; }
# Azure Managed Redis children answer a missing resource without the ResourceNotFound code (#191).
not_found() {
  [[ -z "${AZ_PLAIN_NOT_FOUND:-}" ]] || { echo "ERROR: Operation returned an invalid status 'Not Found'" >&2; exit 1; }
  echo "ERROR: (ResourceNotFound) The Resource was not found." >&2; exit 3
}
G=/subscriptions/s/resourceGroups/rg-watchparty/providers
args="$*"
case "$args" in
  "containerapp show"*"scale.maxReplicas"*) if [[ -n "${AZ_MAX:-}" ]]; then echo "$AZ_MAX"; elif [[ -f "$STUB_STATE/replicas" ]]; then cat "$STUB_STATE/replicas"; else echo "${AZ_REPLICAS-1}"; fi ;;
  "containerapp show"*"name=='DATABASE_AUTH'"*) [[ -f "$STUB_STATE/wired" ]] && grep -qx DATABASE_AUTH "$STUB_STATE/wired" && echo "${AZ_AUTH_VALUE-entra}"; exit 0 ;;
  "containerapp show"*"scale.minReplicas"*) if [[ -f "$STUB_STATE/replicas" ]]; then cat "$STUB_STATE/replicas"; else echo "${AZ_REPLICAS-1}"; fi ;;
  "containerapp show"*"env[].name"*) [[ -z "${AZ_SETTINGS_FAILS:-}" ]] || { echo "ERROR: (TooManyRequests) throttled" >&2; exit 1; }
    echo FOUNDRY_ENDPOINT; setting; [[ -z "${AZ_EXTRA_SETTING:-}" ]] || printf '%s\n' $AZ_EXTRA_SETTING
    [[ ! -f "$STUB_STATE/wired" ]] || cat "$STUB_STATE/wired" ;;
  "resource list"*redisEnterprise* | "resource list"*flexibleServers*) [[ -z "${AZ_LIST_FAILS:-}" ]] || { echo "ERROR: (TooManyRequests) throttled" >&2; exit 1; }
    case "$args" in
      *redisEnterprise*) redis_on && echo "$G/Microsoft.Cache/redisEnterprise/redis-watchparty-x" ;;
      *flexibleServers*) postgres_on && echo "$G/Microsoft.DBforPostgreSQL/flexibleServers/psql-watchparty-x" ;;
    esac; exit 0 ;;
  "resource show --ids"*accessPolicyAssignments/app*) [[ -z "${AZ_POLICY_MISSING:-}" ]] || not_found; echo principal-app ;;
  "resource show --ids"*accessPolicyAssignments/person*) [[ -f "$STUB_STATE/person" ]] || not_found; cat "$STUB_STATE/person" ;;
  "resource show --ids"*databases/default*) echo "${AZ_REDIS_KEYS-Disabled}" ;;
  "resource show --ids"*redisEnterprise*) echo Disabled ;;
  "resource show --ids"*administrators*) [[ -z "${AZ_PG_ADMIN_FAILS:-}" ]] || { echo "ERROR: (TooManyRequests) throttled" >&2; exit 1; }
    echo id-watchparty ;;
  "resource show --ids"*flexibleServers*"--query sku.name"*) [[ -z "${AZ_PG_SKU_FAILS:-}" ]] || { echo "ERROR: throttled" >&2; exit 1; }
    echo "${AZ_PG_SKU-Standard_B2s}" ;;
  "resource show --ids"*flexibleServers*"--query location"*) echo "${AZ_PG_LOCATION-North Europe}" ;;
  "resource show --ids"*passwordAuth*) echo "${AZ_PG_PASSWORD-Disabled}" ;;
  "resource show --ids"*activeDirectoryAuth*) echo Enabled ;;
  "provider show -n "*) [[ -z "${AZ_PROVIDER_FAILS:-}" ]] || { echo "ERROR: (AuthorizationFailed) no access" >&2; exit 1; }
    ns="${args#provider show -n }"; ns="${ns%% *}"
    if [[ " ${AZ_UNREGISTERED:-} " == *" $ns "* ]]; then echo NotRegistered; else echo Registered; fi ;;
  "account show"*) [[ -z "${AZ_SIGNED_OUT:-}" ]] || exit 1; echo "${AZ_SUBSCRIPTION-11111111-2222-3333-4444-555555555555}" ;;
  "containerapp show"*"env[?"*) setting ;;
  # #66: the custom domain, live (AZ_LIVE_DOMAINS, AZ_LIVE_BINDING) or as the last create left it ("name binding")
  "containerapp show"*"customDomains[].name"*) [[ -z "${AZ_DOMAINS_FAILS:-}" ]] || { echo "ERROR: (TooManyRequests) throttled" >&2; exit 1; }
    if [[ -f "$STUB_STATE/domain" ]]; then cut -d' ' -f1 "$STUB_STATE/domain"
    elif [[ -n "${AZ_LIVE_DOMAINS:-}" ]]; then printf '%s\n' $AZ_LIVE_DOMAINS; fi; exit 0 ;;
  "containerapp show"*"customDomains[?name=="*)
    if [[ -f "$STUB_STATE/domain" ]]; then cut -d' ' -f2 "$STUB_STATE/domain"
    elif [[ -n "${AZ_LIVE_DOMAINS:-}" ]]; then echo "${AZ_LIVE_BINDING-Disabled}"; fi; exit 0 ;;
  "containerapp env show"*staticIp*) [[ -z "${AZ_IP_FAILS:-}" ]] || { echo "ERROR: (AuthorizationFailed) no access" >&2; exit 1; }
    echo "${AZ_STATIC_IP-203.0.113.7}" ;;
  "containerapp env show"*customDomainVerificationId*) echo "${AZ_VERIFICATION-ABC123DEF}" ;;
  "containerapp show"*) echo x >> "$STUB_STATE/image-reads"
    if [[ -n "${AZ_IMAGE_MOVES:-}" && "$(wc -l < "$STUB_STATE/image-reads")" -gt 1 ]]; then
      echo watchpartyd4d84f.azurecr.io/watchparty:newer
    else
      echo "${AZ_APP_IMAGE-watchpartyd4d84f.azurecr.io/watchparty:live}"
    fi ;;
  "rest --method get --url "*administrators*) [[ -z "${AZ_REST_FAILS:-}" ]] || { echo "ERROR: (TooManyRequests) throttled" >&2; exit 1; }
    echo "${AZ_PG_PEOPLE-0}" ;;
  "containerapp job show"*"env[?"*) setting ;;
  "containerapp job show"*) echo "${AZ_RECAP_IMAGE-watchpartyd4d84f.azurecr.io/watchparty-recap:live}" ;;
  "consumption budget show"*"--query amount"*) [[ -z "${AZ_BUDGET_FAILS:-}${AZ_AMOUNT_FAILS:-}" ]] ||
    { echo "ERROR: (AuthorizationFailed) no access" >&2; exit 1; }; echo "${AZ_AMOUNT-100.0}" ;;
  "consumption budget show"*) [[ -z "${AZ_BUDGET_FAILS:-}" ]] || { echo "ERROR: (AuthorizationFailed) no access" >&2; exit 1; }; printf '%s\n' "${AZ_EMAILS-[
  \"alerts@example.com\"
]}" ;;
  "resource list -g rg-watchparty --resource-type Microsoft.Search/searchServices"*)
    [[ -z "${AZ_SEARCH_LIST_FAILS:-}" ]] || { echo "ERROR: (AuthorizationFailed) no access" >&2; exit 1; }
    for name in ${AZ_SEARCH_NAMES-srch-watchparty-x}; do  # honours the script's name filter, if it sends one
      [[ "$args" == *"starts_with(name, 'srch-watchparty-')"* && "$name" != srch-watchparty-* ]] && continue
      echo "/subscriptions/s/resourceGroups/rg-watchparty/providers/Microsoft.Search/searchServices/$name"
    done ;;
  "role assignment list --scope $SEARCH_ID --role "*" --assignee-object-id "*)
    [[ -z "${AZ_ASSIGNEE_FAILS:-}" ]] || { echo "ERROR: (AuthorizationFailed) no access" >&2; exit 1; }
    assignee="${args##*--assignee-object-id }"; assignee="${assignee%% *}"
    if [[ "$args" == *"--role Search Index Data Contributor "* ]]; then
      [[ -z "${AZ_DATA_NAME:-}" ]] || echo "$SEARCH_SCOPE/$ASSIGNMENTS/$AZ_DATA_NAME"
      exit 0
    fi
    for holder in ${AZ_HOLDERS:-}; do  # principal:type:service|sub:assignment name
      IFS=: read -r principal type where name <<< "$holder"
      [[ "$principal" == "$assignee" && "$where" == service ]] && echo "$SEARCH_SCOPE/$ASSIGNMENTS/$name"
    done
    exit 0 ;;
  "role assignment list --scope $SEARCH_ID --role Search Service Contributor "*)
    [[ -z "${AZ_HOLDER_FAILS:-}" ]] || { echo "ERROR: (AuthorizationFailed) no access" >&2; exit 1; }
    for holder in ${AZ_HOLDERS:-}; do  # principal:type:service|sub:assignment name; sub only with --include-inherited
      IFS=: read -r principal type where name <<< "$holder"
      scope="$SEARCH_SCOPE"
      [[ "$where" == service ]] || scope=/subscriptions/s
      [[ "$where" == service || "$args" == *--include-inherited* ]] || continue
      printf '%s\t%s\t%s\t%s\n' "$principal" "$type" "$scope" "$scope/$ASSIGNMENTS/$name"
    done ;;
  "ad user show --id "*) [[ -z "${AZ_UPN_FAILS:-}" ]] || { echo "ERROR: (Request_ResourceNotFound)" >&2; exit 1; }
    echo "upn-${args##*--id }" | cut -d' ' -f1 ;;
  "ad signed-in-user show"*) echo "${AZ_ADMIN-00000000-0000-0000-0000-0000000000ad}" ;;
  "resource show -g"*) [[ -z "${AZ_COMPONENT_FAILS:-}" ]] || { echo "ERROR: (TooManyRequests) throttled" >&2; exit 1; }
    created || { echo "ERROR: (ResourceNotFound) The Resource was not found." >&2; exit 3; }; echo "/subscriptions/s/resourceGroups/rg-watchparty/providers/Microsoft.Insights/components/appi-watchparty" ;;
  "resource show --ids"*DisableLocalAuth*) echo "${AZ_LOCAL_AUTH_DISABLED:-true}" ;;
  "resource show --ids"*WorkspaceResourceId*) echo "/subscriptions/s/resourceGroups/rg-watchparty/providers/Microsoft.OperationalInsights/workspaces/log-watchparty" ;;
  "role assignment list"*) echo "principal-app"; [[ -n "${AZ_ONE_ROLE:-}" ]] || echo "principal-recap" ;;
  "identity show -n id-watchparty-recap "*) echo "principal-recap" ;;
  "identity show -n id-watchparty "*) echo "principal-app" ;;
  "deployment group what-if"*) echo "what-if: + appi-watchparty" ;;
  "deployment group create"*) touch "$STUB_STATE/created"
    [[ "$args" != *enableRedis=true* ]] || touch "$STUB_STATE/redis"
    [[ "$args" != *enablePostgres=true* ]] || touch "$STUB_STATE/postgres"
    # The person's Redis access, unless a case drops it; Azure may report the object id in another case.
    if [[ "$args" == *redisUserId=* && -z "${AZ_PERSON_DROPPED:-}" ]]; then
      person="${args##*redisUserId=}"; tr '[:lower:]' '[:upper:]' <<< "${person%% *}" > "$STUB_STATE/person"
    fi
    # What a wiring apply leaves on the app; AZ_WIRE_DROPS names a setting the case leaves off.
    : > "$STUB_STATE/wired"
    [[ "$args" != *wireRedis=true* ]] || echo REDIS_URL >> "$STUB_STATE/wired"
    if [[ "$args" == *wirePostgres=true* ]]; then printf '%s\n' DATABASE_URL DATABASE_AUTH >> "$STUB_STATE/wired"; fi
    [[ -z "${AZ_WIRE_DROPS:-}" ]] || { grep -vx "$AZ_WIRE_DROPS" "$STUB_STATE/wired" > "$STUB_STATE/w" || true; mv "$STUB_STATE/w" "$STUB_STATE/wired"; }
    if [[ "$args" == *appReplicas=2* && -z "${AZ_REPLICAS_STAY:-}" ]]; then echo 2 > "$STUB_STATE/replicas"; fi
    # The host name, and its certificate on the second apply, unless a case drops one.
    if [[ "$args" == *customDomain=* && -z "${AZ_DOMAIN_DROPPED:-}" ]]; then
      name="${args##*customDomain=}"; binding=Disabled
      [[ "$args" != *customDomainBound=true* || -n "${AZ_BIND_FAILS:-}" ]] || binding=SniEnabled
      echo "${name%% *} $binding" > "$STUB_STATE/domain"
    fi
    echo "Succeeded" ;;
  *) echo "unexpected az call: $args" >&2; exit 99 ;;
esac
EOF
chmod +x "$tmp/bin/az"

# dig +short: DIG_A and DIG_TXT list the answers, space-separated; each TXT answer comes back quoted, as dig prints it.
cat > "$tmp/bin/dig" << 'EOF'
#!/usr/bin/env bash
echo "dig $*" >> "$STUB_LOG"
[[ -z "${DIG_FAILS:-}" ]] || { echo ";; connection timed out; no servers could be reached" >&2; exit 9; }
case "$*" in
  *" A "*) for a in ${DIG_A-203.0.113.7}; do echo "$a"; done ;;
  *" TXT "*) for t in ${DIG_TXT-ABC123DEF}; do echo "\"$t\""; done ;;
  *) echo "unexpected dig call: $*" >&2; exit 99 ;;
esac
EOF
chmod +x "$tmp/bin/dig"

shells=(/bin/bash)
[[ "$(command -v bash)" == /bin/bash ]] || shells+=("$(command -v bash)")

run_case() {  # run_case NAME EXPECTED_STATUS CHECK [VAR=value ...] -- [script args]
  local name=$1 expected=$2 check=$3 shell
  shift 3
  local -a env_vars=()
  while [[ $# -gt 0 && "$1" != -- ]]; do env_vars+=("$1"); shift; done
  shift
  for shell in "${shells[@]}"; do
    local dir="$tmp/$name-${shell//\//_}"
    mkdir -p "$dir/state"
    : > "$dir/log"
    env PATH="$tmp/bin:/usr/bin:/bin" STUB_LOG="$dir/log" STUB_STATE="$dir/state" \
      AZURE_SUBSCRIPTION_ID=11111111-2222-3333-4444-555555555555 ${env_vars[@]+"${env_vars[@]}"} \
      "$shell" "$script" "$@" > "$dir/out" 2>&1
    local status=$?
    if [[ "$status" == "$expected" ]] && "$check" "$dir"; then
      passed=$((passed + 1))
    else
      failed=$((failed + 1))
      failures+=("$name under $shell: exit $status (expected $expected)")
      sed 's/^/    /' "$dir/out" >&2
    fi
  done
}

has() { grep -q -- "$2" "$1"; }
lacks() { ! grep -q -- "$2" "$1"; }

# --- checks -------------------------------------------------------------------------------------------

whatif_only() {
  has "$1/log" "deployment group what-if" && lacks "$1/log" "deployment group create" &&
    has "$1/out" "== before" && has "$1/out" "component appi-watchparty: none" &&
    has "$1/out" "APPLICATIONINSIGHTS_CONNECTION_STRING on ca-watchparty: not set$" &&
    has "$1/out" "nothing applied" && lacks "$1/out" "== after"
}
applied() {
  has "$1/log" "deployment group what-if" && has "$1/log" "deployment group create" &&
    [[ "$(grep -n 'what-if' "$1/log" | head -1 | cut -d: -f1)" -lt "$(grep -n 'group create' "$1/log" | cut -d: -f1)" ]] &&
    has "$1/log" "image=watchpartyd4d84f.azurecr.io/watchparty:live" &&
    has "$1/log" "recapImage=watchpartyd4d84f.azurecr.io/watchparty-recap:live" &&
    has "$1/log" 'alertEmails=\["alerts@example.com"\]' && has "$1/log" "budgetAmount=100 " &&
    has "$1/log" "knowledgeAdminId=00000000-0000-0000-0000-0000000000ad" &&
    has "$1/out" "== before" && has "$1/out" "== after" &&
    has "$1/out" "local auth disabled: true; workspace: log-watchparty" &&
    has "$1/out" "Monitoring Metrics Publisher for id-watchparty: yes" &&
    has "$1/out" "Monitoring Metrics Publisher for id-watchparty-recap: yes" &&
    has "$1/out" "APPLICATIONINSIGHTS_CONNECTION_STRING on ca-watchparty: set$" &&
    has "$1/out" "APPLICATIONINSIGHTS_CONNECTION_STRING on caj-watchparty-recap: set$" && has "$1/out" "^applied"
}
dry() {
  lacks "$1/log" "deployment group" && has "$1/out" "+ az deployment group what-if" &&
    has "$1/out" "+ az deployment group create"
}
nothing_deployed() { lacks "$1/log" "deployment group"; }
says_why() { nothing_deployed "$1" && has "$1/out" "could not read" && lacks "$1/out" "component appi-watchparty: none"; }
no_subscription() { nothing_deployed "$1" && lacks "$1/log" "account show" && has "$1/out" "set AZURE_SUBSCRIPTION_ID"; }
amount_read_failed() { says_why "$1" && has "$1/out" "budget amount: az failed"; }
no_amount() { nothing_deployed "$1" && has "$1/out" "budget amount"; }
HOLDER=00000000-0000-0000-0000-00000000b01d
SIGNED_IN=00000000-0000-0000-0000-0000000000ad
# The template's guid(search.id, HOLDER, role) for each knowledge role, computed independently of the script
# with Python's uuid5 in ARM's namespace 11fb06fb-712d-4ddd-98c7-e71bbd588830.
HOLDER_SEARCH_NAME=25769d99-5081-5a3e-abd2-c4e7ac0fa2aa
HOLDER_DATA_NAME=347261ca-a035-5e16-8b5a-df76177e340e
HOLDS="$HOLDER:User:service:$HOLDER_SEARCH_NAME"
SEARCH_SCOPE=/subscriptions/s/resourcegroups/rg-watchparty/providers/Microsoft.Search/searchServices/srch-watchparty-x
ASSIGNMENTS=providers/Microsoft.Authorization/roleAssignments
holder_passed() {
  has "$1/log" "deployment group create.*knowledgeAdminId=$HOLDER" && lacks "$1/log" "knowledgeAdminId=$SIGNED_IN" &&
    has "$1/out" "knowledge admin $HOLDER upn-$HOLDER (the current Search Service Contributor)" &&
    has "$1/out" "^applied"
}
holder_passed_without_signed_in_read() { holder_passed "$1" && lacks "$1/log" "ad signed-in-user"; }
signed_in_passed() {
  has "$1/log" "deployment group create.*knowledgeAdminId=$SIGNED_IN" && has "$1/out" "^applied" &&
    has "$1/out" "knowledge admin $SIGNED_IN upn-$SIGNED_IN (the signed-in user; no User holds Search Service Contributor on this service)"
}
two_services() { nothing_deployed "$1" && has "$1/out" "more than one srch-watchparty-.* search service in rg-watchparty"; }
not_a_direct_user() {
  nothing_deployed "$1" && has "$1/out" "holds Search Service Contributor on srch-watchparty-x through" &&
    has "$1/out" "handles only Users"
}
group_holder() { not_a_direct_user "$1" && has "$1/out" "Group group-1 holds"; }
inherited_holder() { not_a_direct_user "$1" && has "$1/out" "User $HOLDER holds .* through /subscriptions/s;"; }
hand_made() {  # hand_made DIR ROLE NAME
  nothing_deployed "$1" &&
    has "$1/out" "upn-$HOLDER holds $2 on srch-watchparty-x through an assignment the template did not name" &&
    has "$1/out" "az role assignment delete --ids $SEARCH_SCOPE/$ASSIGNMENTS/$3 ; then run again signed in as upn-$HOLDER"
}
hand_made_search() { hand_made "$1" "Search Service Contributor" hand-made; }
hand_made_data() { hand_made "$1" "Search Index Data Contributor" hand-data; }
upn_read_failed() { nothing_deployed "$1" && has "$1/out" "could not read the user name of $HOLDER"; }
assignee_read_failed() {
  nothing_deployed "$1" && has "$1/out" "could not read Search Service Contributor for $HOLDER on srch-watchparty-x"
}
no_search_service() { signed_in_passed "$1" && lacks "$1/log" "role assignment list.*Search Service Contributor"; }
search_read_failed() { nothing_deployed "$1" && has "$1/out" "could not read the search service in rg-watchparty"; }
holder_read_failed() {
  nothing_deployed "$1" && has "$1/out" "could not read who holds Search Service Contributor on srch-watchparty-x"
}
two_holders() {
  nothing_deployed "$1" && has "$1/out" "2 Users hold Search Service Contributor on srch-watchparty-x" &&
    has "$1/out" "  $HOLDER (named by the template): az role assignment delete --ids $SEARCH_SCOPE/$ASSIGNMENTS/$HOLDER_SEARCH_NAME$" &&
    has "$1/out" "  other-user (not named by the template; remove this one): az role assignment delete --ids $SEARCH_SCOPE/$ASSIGNMENTS/hand-made$"
}
created_but_incomplete() { has "$1/log" "deployment group create" && lacks "$1/out" "^applied"; }

# #97: the switches, and what the script keeps from what is live
passes() {  # passes DIR PARAM...: the what-if, and the create if there is one, carry every PARAM
  local dir=$1 p; shift
  for p in "$@"; do grep "deployment group" "$dir/log" | grep -q -- "-p $p\( \|$\)" || return 1; done
}
defaults_kept() {
  whatif_only "$1" && passes "$1" enableRedis=false enablePostgres=false wireRedis=false wirePostgres=false appReplicas=1 &&
    lacks "$1/log" postgresAdminId && has "$1/out" "redis: none" && has "$1/out" "postgres: none" &&
    has "$1/out" "ca-watchparty replicas: 1$"
}
redis_whatif() {
  whatif_only "$1" && passes "$1" enableRedis=true enablePostgres=false wireRedis=false wirePostgres=false appReplicas=1 &&
    has "$1/out" "redis: none"
}
both_whatif() {
  whatif_only "$1" && passes "$1" enableRedis=true enablePostgres=true wireRedis=false wirePostgres=false appReplicas=1 \
    postgresAdminId=00000000-0000-0000-0000-0000000000ad postgresAdminName=upn-00000000-0000-0000-0000-0000000000ad
}
redis_applied() {
  applied "$1" && passes "$1" enableRedis=true wireRedis=false wirePostgres=false appReplicas=1 &&
    has "$1/out" "high availability: Disabled; access keys: Disabled; app access: yes" &&
    has "$1/out" "== applying with app image watchpartyd4d84f.azurecr.io/watchparty:live and recap image watchpartyd4d84f.azurecr.io/watchparty-recap:live" &&
    has "$1/out" "restarts the app's replicas" && [[ "$(grep -c 'ca-watchparty replicas: 1$' "$1/out")" == 2 ]]
}
postgres_applied() {
  applied "$1" && passes "$1" enablePostgres=true postgresAdminId=00000000-0000-0000-0000-0000000000ad \
    postgresAdminName=upn-00000000-0000-0000-0000-0000000000ad &&
    has "$1/out" "password auth: Disabled; Entra auth: Enabled; app administrator: yes"
}
keeps_replicas() { whatif_only "$1" && passes "$1" appReplicas=2; }
keeps_postgres_wiring() { whatif_only "$1" && passes "$1" wireRedis=false wirePostgres=true; }
keeps_redis_wiring() { whatif_only "$1" && passes "$1" wireRedis=true wirePostgres=false; }
keeps_both_wirings() { whatif_only "$1" && passes "$1" wireRedis=true wirePostgres=true; }
redis_not_wired_by_postgres_url() { whatif_only "$1" && passes "$1" enableRedis=true wireRedis=false wirePostgres=true; }
keeps_live_postgres() {
  whatif_only "$1" && passes "$1" enablePostgres=true postgresAdminId=00000000-0000-0000-0000-0000000000ad &&
    has "$1/out" "postgres psql-watchparty-x"
}
person_admin_kept() { whatif_only "$1" && passes "$1" enablePostgres=true && lacks "$1/log" postgresAdminId; }
images_moved() {
  has "$1/log" "deployment group what-if" && lacks "$1/log" "deployment group create" &&
    has "$1/out" "the images changed since they were read" && lacks "$1/out" "^applied"
}
keeps_live_redis() { whatif_only "$1" && passes "$1" enableRedis=true && has "$1/out" "redis redis-watchparty-x"; }
stops_at_read() { says_why "$1"; }
# #180: --wire, and the person's Redis access
ADMIN_ID=00000000-0000-0000-0000-0000000000ad
person_access() { has "$1/out" "signed-in user's access: yes"; }
redis_user_passed() { passes "$1" redisUserId=$ADMIN_ID; }
no_redis_user() { whatif_only "$1" && lacks "$1/log" redisUserId; }
wire_whatif() {
  whatif_only "$1" && passes "$1" enableRedis=true enablePostgres=true wireRedis=true wirePostgres=true appReplicas=2 &&
    redis_user_passed "$1" && has "$1/out" "wiring the app: REDIS_URL, DATABASE_URL and DATABASE_AUTH on, replicas 2"
}
wire_applied() {
  applied "$1" && passes "$1" wireRedis=true wirePostgres=true appReplicas=2 && person_access "$1" &&
    has "$1/out" "ca-watchparty replicas: 2$" && has "$1/out" "ca-watchparty settings: .*REDIS_URL" &&
    has "$1/out" "ca-watchparty settings: .*DATABASE_URL DATABASE_AUTH" &&
    has "$1/out" "ca-watchparty maximum replicas: 2; DATABASE_AUTH: entra$"
}
wire_refused() { nothing_deployed "$1" && has "$1/out" "--wire needs Redis and PostgreSQL to exist"; }
wire_incomplete() { created_but_incomplete "$1" && has "$1/out" "the setup is not complete"; }
wire_setting_missing() { wire_incomplete "$1" && has "$1/out" "$2 on ca-watchparty: not set"; }
wire_auth_missing() { wire_incomplete "$1" && has "$1/out" "DATABASE_AUTH: not set$"; }
wire_redis_url_missing() { wire_setting_missing "$1" REDIS_URL; }
wire_auth_password() { wire_incomplete "$1" && has "$1/out" "DATABASE_AUTH: password$"; }
wire_max_short() { wire_incomplete "$1" && has "$1/out" "maximum replicas: 1;"; }
wire_replicas_short() { wire_incomplete "$1" && has "$1/out" "ca-watchparty replicas: 1$"; }
redis_applied_with_person() { redis_applied "$1" && redis_user_passed "$1" && person_access "$1"; }
unregistered() {  # unregistered DIR NAMESPACE
  nothing_deployed "$1" && has "$1/out" "not registered for $2 (state: NotRegistered); run: az provider register --namespace $2 --wait"
}
cache_unregistered() { unregistered "$1" Microsoft.Cache; }
postgres_unregistered() { unregistered "$1" Microsoft.DBforPostgreSQL; }
provider_read_failed() { nothing_deployed "$1" && has "$1/out" "could not read the registration of Microsoft.Cache"; }
no_provider_check() { whatif_only "$1" && lacks "$1/log" "provider show"; }
pg_defaults() { whatif_only "$1" && passes "$1" postgresSku=Standard_B1ms postgresTier=Burstable && lacks "$1/log" postgresLocation; }
pg_flags() { applied "$1" && passes "$1" postgresSku=Standard_B2s postgresTier=Burstable postgresLocation=northeurope; }
pg_sku_only() { whatif_only "$1" && passes "$1" postgresSku=Standard_D2ds_v5 postgresTier=GeneralPurpose && lacks "$1/log" postgresLocation; }
pg_live_kept() { whatif_only "$1" && passes "$1" postgresSku=Standard_B2s postgresTier=Burstable postgresLocation=northeurope; }
pg_no_resize() { nothing_deployed "$1" && has "$1/out" "exists as Standard_B2s; this script does not resize it"; }
pg_no_move() { nothing_deployed "$1" && has "$1/out" "exists in northeurope; a server cannot move region"; }
pg_unreadable() { nothing_deployed "$1" && has "$1/out" "could not read sku.name"; }
pg_needs_postgres() { nothing_deployed "$1" && has "$1/out" "go with --postgres"; }
pg_bad_sku() { nothing_deployed "$1" && has "$1/out" "is not a size like Standard_B2s"; }
pg_bad_region() { nothing_deployed "$1" && has "$1/out" "is not a region like northeurope"; }
pg_unknown_tier() { nothing_deployed "$1" && has "$1/out" "no tier known for Standard_L8s"; }
no_pg_size() { whatif_only "$1" && lacks "$1/log" postgresSku && lacks "$1/log" postgresLocation; }
person_dropped() { wire_incomplete "$1" && has "$1/out" "signed-in user's access: no"; }
# The search role's holder is someone else: Redis access still goes to whoever runs the script.
redis_user_is_signed_in() {
  has "$1/out" "^applied" && passes "$1" redisUserId=$ADMIN_ID knowledgeAdminId=$HOLDER && lacks "$1/log" "redisUserId=$HOLDER" &&
    person_access "$1"
}
signed_in_unreadable() { nothing_deployed "$1" && has "$1/out" "could not read the signed-in user's object id"; }
list_stops() { nothing_deployed "$1" && has "$1/out" "could not list Redis"; }
read_fails_after() { created_but_incomplete "$1" && has "$1/out" "could not read"; }
# #66: the custom domain
D=couchgaffer.com
looked_up() { has "$1/log" "dig +short @1.1.1.1 A $D" && has "$1/log" "dig +short @1.1.1.1 TXT asuid.$D"; }
no_domain() {
  whatif_only "$1" && lacks "$1/log" "^dig" && lacks "$1/log" "customDomain=" && has "$1/out" "custom domain: none"
}
domain_first() {
  whatif_only "$1" && looked_up "$1" && passes "$1" customDomain=$D customDomainBound=false &&
    has "$1/out" "dns: $D A 203.0.113.7; asuid.$D TXT matches cae-watchparty's verification id" &&
    has "$1/out" "custom domain $D: this run adds it with binding Disabled; run again"
}
domain_bound() {
  whatif_only "$1" && looked_up "$1" && passes "$1" customDomain=$D customDomainBound=true &&
    has "$1/out" "custom domain $D: on the app; this run binds it" && has "$1/out" "^custom domain $D: Disabled$"
}
domain_dry() { dry "$1" && looked_up "$1" && has "$1/out" "+ az deployment group create.* -p customDomain=$D -p customDomainBound=false"; }
records_refused() {  # records_refused DIR A_NOW TXT_NOW: stops before the what-if, naming both records to create
  nothing_deployed "$1" && looked_up "$1" && has "$1/out" "the DNS records for $D are not in place" &&
    has "$1/out" "DNS only (grey cloud)" &&
    has "$1/out" "^  A    $D    203.0.113.7    (now: $2)$" &&
    has "$1/out" "^  TXT  asuid.$D    ABC123DEF    (now: $3)$"
}
a_missing() { records_refused "$1" none '"ABC123DEF"'; }
a_proxied() { records_refused "$1" "104.21.48.1 172.67.140.2" '"ABC123DEF"'; }
a_extra() { records_refused "$1" "203.0.113.7 198.51.100.9" '"ABC123DEF"'; }
txt_missing() { records_refused "$1" 203.0.113.7 none; }
txt_wrong() { records_refused "$1" 203.0.113.7 '"OTHER"'; }
ip_unreadable() { nothing_deployed "$1" && lacks "$1/log" "^dig" && has "$1/out" "could not read the static IP of cae-watchparty"; }
id_unreadable() {
  nothing_deployed "$1" && lacks "$1/log" "^dig" && has "$1/out" "could not read the custom domain verification id of cae-watchparty"
}
dig_failed() { nothing_deployed "$1" && has "$1/out" "could not look up $D with dig"; }
domains_unreadable() { nothing_deployed "$1" && has "$1/out" "could not read ca-watchparty's custom domains"; }
other_domain_live() { nothing_deployed "$1" && lacks "$1/log" "^dig" && has "$1/out" "already serves play.example.com"; }
two_domains_live() { nothing_deployed "$1" && has "$1/out" "has more than one custom domain"; }
bad_domain() { nothing_deployed "$1" && has "$1/out" "is not a lower-case host name like couchgaffer.com"; }
domain_added() {
  applied "$1" && passes "$1" customDomain=$D customDomainBound=false && has "$1/out" "^custom domain $D: Disabled$" &&
    has "$1/out" "$D is on the app without a certificate; run the same command again"
}
domain_bound_applied() {
  applied "$1" && passes "$1" customDomain=$D customDomainBound=true && has "$1/out" "^custom domain $D: SniEnabled$" &&
    lacks "$1/out" "without a certificate"
}
domain_steady() { domain_bound_applied "$1" && [[ "$(grep -c "^custom domain $D: SniEnabled$" "$1/out")" == 2 ]]; }
bind_failed() { created_but_incomplete "$1" && has "$1/out" "^custom domain $D: Disabled$" && has "$1/out" "not complete"; }
domain_dropped() { created_but_incomplete "$1" && has "$1/out" "^custom domain $D: not on the app$"; }

# --- cases --------------------------------------------------------------------------------------------

run_case whatif-by-default 0 whatif_only --
run_case apply 0 applied -- --apply
run_case dry-run 0 dry -- --dry-run
run_case unknown-flag 2 nothing_deployed -- --force
run_case two-flags 2 nothing_deployed -- --apply --dry-run
run_case signed-out 1 nothing_deployed AZ_SIGNED_OUT=1 -- --apply
run_case wrong-subscription 1 nothing_deployed AZ_SUBSCRIPTION=11111111-1111-1111-1111-111111111111 -- --apply
run_case no-expected-subscription 1 no_subscription AZURE_SUBSCRIPTION_ID= -- --apply
run_case no-app-image 1 nothing_deployed AZ_APP_IMAGE= -- --apply
run_case no-recap-image 1 nothing_deployed AZ_RECAP_IMAGE= -- --apply
run_case no-budget-emails 1 nothing_deployed AZ_EMAILS= -- --apply
run_case empty-budget-emails 1 nothing_deployed "AZ_EMAILS=[]" -- --apply
run_case no-admin 1 nothing_deployed AZ_ADMIN= -- --apply
run_case no-budget-amount 1 says_why AZ_AMOUNT= -- --apply
run_case fractional-budget-amount 1 no_amount AZ_AMOUNT=100.5 -- --apply
run_case budget-amount-read-fails 1 amount_read_failed AZ_AMOUNT_FAILS=1 -- --apply
run_case budget-read-fails 1 says_why AZ_BUDGET_FAILS=1 -- --apply
run_case component-read-fails 1 says_why AZ_COMPONENT_FAILS=1 -- --apply
run_case holder-exists 0 holder_passed AZ_HOLDERS=$HOLDS -- --apply
run_case holder-exists-signed-in-unreadable 0 holder_passed_without_signed_in_read AZ_HOLDERS=$HOLDS AZ_ADMIN= -- --apply
run_case holder-data-role-named-by-template 0 holder_passed AZ_HOLDERS=$HOLDS AZ_DATA_NAME=$HOLDER_DATA_NAME -- --apply
run_case no-holder 0 signed_in_passed -- --apply
run_case no-search-service 0 no_search_service AZ_SEARCH_NAMES= -- --apply
run_case other-search-service-ignored 0 holder_passed "AZ_SEARCH_NAMES=srch-other srch-watchparty-x" AZ_HOLDERS=$HOLDS -- --apply
run_case two-search-services 1 two_services "AZ_SEARCH_NAMES=srch-watchparty-x srch-watchparty-y" -- --apply
run_case search-read-fails 1 search_read_failed AZ_SEARCH_LIST_FAILS=1 -- --apply
run_case holder-read-fails 1 holder_read_failed AZ_HOLDER_FAILS=1 -- --apply
run_case two-holders 1 two_holders "AZ_HOLDERS=$HOLDS other-user:User:service:hand-made" -- --apply
run_case group-holder 1 group_holder AZ_HOLDERS=group-1:Group:service:g -- --apply
run_case inherited-holder 1 inherited_holder AZ_HOLDERS=$HOLDER:User:sub:inherited -- --apply
run_case hand-made-search-assignment 1 hand_made_search AZ_HOLDERS=$HOLDER:User:service:hand-made -- --apply
run_case hand-made-data-assignment 1 hand_made_data AZ_HOLDERS=$HOLDS AZ_DATA_NAME=hand-data -- --apply
run_case upn-read-fails 1 upn_read_failed AZ_HOLDERS=$HOLDS AZ_UPN_FAILS=1 -- --apply
run_case assignee-read-fails 1 assignee_read_failed AZ_HOLDERS=$HOLDS AZ_ASSIGNEE_FAILS=1 -- --apply
run_case local-auth-left-on 1 created_but_incomplete AZ_LOCAL_AUTH_DISABLED=false -- --apply
run_case one-role-missing 1 created_but_incomplete AZ_ONE_ROLE=1 -- --apply

# #97 cases
run_case defaults-off 0 defaults_kept --
run_case redis-whatif-only 0 redis_whatif -- --redis
run_case both-whatif-only 0 both_whatif -- --postgres --redis
run_case redis-apply 0 redis_applied -- --redis --apply
run_case postgres-apply 0 postgres_applied -- --apply --postgres
run_case keeps-two-replicas 0 keeps_replicas AZ_REPLICAS=2 --
run_case keeps-postgres-wiring 0 keeps_postgres_wiring AZ_EXTRA_SETTING=DATABASE_URL --
run_case keeps-redis-wiring 0 keeps_redis_wiring AZ_EXTRA_SETTING=REDIS_URL --
run_case keeps-both-wirings 0 keeps_both_wirings "AZ_EXTRA_SETTING=REDIS_URL DATABASE_URL" --
run_case postgres-url-does-not-wire-redis 0 redis_not_wired_by_postgres_url AZ_EXTRA_SETTING=DATABASE_URL -- --redis
run_case keeps-live-postgres 0 keeps_live_postgres AZ_POSTGRES_LIVE=1 --
run_case postgres-has-a-person 0 person_admin_kept AZ_POSTGRES_LIVE=1 AZ_PG_PEOPLE=1 -- --postgres
run_case postgres-admins-read-fails 1 nothing_deployed AZ_POSTGRES_LIVE=1 AZ_REST_FAILS=1 -- --postgres --apply
run_case postgres-admins-garbled 1 nothing_deployed AZ_POSTGRES_LIVE=1 AZ_PG_PEOPLE=none -- --postgres --apply
run_case images-moved 1 images_moved AZ_IMAGE_MOVES=1 -- --redis --apply
run_case keeps-live-redis 0 keeps_live_redis AZ_REDIS_LIVE=1 --
run_case apply-twice 2 nothing_deployed -- --redis --apply --apply
run_case apply-and-dry-run 2 nothing_deployed -- --postgres --apply --dry-run
run_case no-replicas 1 nothing_deployed AZ_REPLICAS= -- --redis --apply
run_case three-replicas 1 nothing_deployed AZ_REPLICAS=3 -- --redis --apply
run_case settings-read-fails 1 stops_at_read AZ_SETTINGS_FAILS=1 -- --redis --apply
run_case service-list-fails 1 list_stops AZ_LIST_FAILS=1 -- --redis --apply
run_case redis-keys-left-on 1 created_but_incomplete AZ_REDIS_KEYS=Enabled -- --redis --apply
run_case redis-policy-missing 1 created_but_incomplete AZ_POLICY_MISSING=1 -- --redis --apply
run_case postgres-password-left-on 1 created_but_incomplete AZ_PG_PASSWORD=Enabled -- --postgres --apply
run_case postgres-admin-read-fails 1 read_fails_after AZ_PG_ADMIN_FAILS=1 -- --postgres --apply

# #180 cases
run_case wire-whatif 0 wire_whatif AZ_REDIS_LIVE=1 AZ_POSTGRES_LIVE=1 -- --wire
run_case wire-apply 0 wire_applied AZ_REDIS_LIVE=1 AZ_POSTGRES_LIVE=1 -- --wire --apply
run_case wire-without-redis 1 wire_refused AZ_POSTGRES_LIVE=1 -- --wire --apply
run_case wire-without-postgres 1 wire_refused AZ_REDIS_LIVE=1 -- --wire --apply
run_case wire-in-the-creating-run 1 wire_refused -- --redis --postgres --wire --apply
run_case wire-auth-left-off 1 wire_auth_missing AZ_REDIS_LIVE=1 AZ_POSTGRES_LIVE=1 AZ_WIRE_DROPS=DATABASE_AUTH -- --wire --apply
run_case wire-redis-url-left-off 1 wire_redis_url_missing AZ_REDIS_LIVE=1 AZ_POSTGRES_LIVE=1 AZ_WIRE_DROPS=REDIS_URL -- --wire --apply
run_case wire-auth-password 1 wire_auth_password AZ_REDIS_LIVE=1 AZ_POSTGRES_LIVE=1 AZ_AUTH_VALUE=password -- --wire --apply
run_case wire-max-one 1 wire_max_short AZ_REDIS_LIVE=1 AZ_POSTGRES_LIVE=1 AZ_MAX=1 -- --wire --apply
run_case wire-replicas-stay-one 1 wire_replicas_short AZ_REDIS_LIVE=1 AZ_POSTGRES_LIVE=1 AZ_REPLICAS_STAY=1 -- --wire --apply
run_case redis-person-access 0 redis_applied_with_person -- --redis --apply
run_case redis-person-dropped 1 person_dropped AZ_PERSON_DROPPED=1 -- --redis --apply
run_case redis-live-person-plain-404 0 redis_applied_with_person AZ_REDIS_LIVE=1 AZ_PLAIN_NOT_FOUND=1 -- --redis --apply
run_case no-redis-no-person 0 no_redis_user -- --postgres
run_case redis-user-is-signed-in 0 redis_user_is_signed_in AZ_HOLDERS=$HOLDS -- --redis --apply
run_case redis-signed-in-unreadable 1 signed_in_unreadable AZ_HOLDERS=$HOLDS AZ_ADMIN= -- --redis --apply
# Postgres size and region (#180: Sweden Central had no capacity for Standard_B1ms)
run_case pg-defaults 0 pg_defaults -- --postgres
run_case pg-sku-and-region 0 pg_flags -- --postgres --postgres-sku Standard_B2s --postgres-location northeurope --apply
run_case pg-sku-only 0 pg_sku_only -- --postgres --postgres-sku Standard_D2ds_v5
run_case pg-live-kept 0 pg_live_kept AZ_POSTGRES_LIVE=1 --
run_case pg-live-same-flags 0 pg_live_kept AZ_POSTGRES_LIVE=1 -- --postgres-sku Standard_B2s --postgres-location northeurope
run_case pg-live-other-sku 1 pg_no_resize AZ_POSTGRES_LIVE=1 -- --postgres-sku Standard_B1ms
run_case pg-live-other-region 1 pg_no_move AZ_POSTGRES_LIVE=1 -- --postgres-location westeurope
run_case pg-live-sku-unreadable 1 pg_unreadable AZ_POSTGRES_LIVE=1 AZ_PG_SKU_FAILS=1 --
run_case pg-flags-without-postgres 1 pg_needs_postgres -- --postgres-sku Standard_B2s
run_case pg-bad-sku 1 pg_bad_sku -- --postgres --postgres-sku "B2s;rm"
run_case pg-bad-region 1 pg_bad_region -- --postgres --postgres-location "North Europe"
run_case pg-sku-missing-value 2 nothing_deployed -- --postgres --postgres-sku --apply
run_case pg-unknown-tier 1 pg_unknown_tier -- --postgres --postgres-sku Standard_L8s
run_case no-postgres-no-size 0 no_pg_size --
run_case cache-not-registered 1 cache_unregistered AZ_UNREGISTERED=Microsoft.Cache -- --redis --postgres --apply
run_case postgres-not-registered 1 postgres_unregistered AZ_UNREGISTERED=Microsoft.DBforPostgreSQL -- --redis --postgres --apply
run_case postgres-not-registered-whatif 1 postgres_unregistered AZ_UNREGISTERED=Microsoft.DBforPostgreSQL -- --postgres
run_case provider-read-fails 1 provider_read_failed AZ_PROVIDER_FAILS=1 -- --redis --apply
run_case unregistered-ignored-without-services 0 no_provider_check "AZ_UNREGISTERED=Microsoft.Cache Microsoft.DBforPostgreSQL" --

# #66 cases: the custom domain
run_case no-domain 0 no_domain --
run_case domain-first-whatif 0 domain_first -- --domain $D
run_case domain-live-binds 0 domain_bound AZ_LIVE_DOMAINS=$D -- --domain $D
run_case domain-live-kept-without-flag 0 domain_bound AZ_LIVE_DOMAINS=$D --
run_case domain-dry-run 0 domain_dry -- --dry-run --domain $D
run_case domain-txt-among-others 0 domain_first "DIG_TXT=v=spf1 ABC123DEF" -- --domain $D
run_case domain-a-missing 1 a_missing DIG_A= -- --domain $D --apply
run_case domain-a-proxied 1 a_proxied "DIG_A=104.21.48.1 172.67.140.2" -- --domain $D --apply
run_case domain-a-extra 1 a_extra "DIG_A=203.0.113.7 198.51.100.9" -- --domain $D --apply
run_case domain-txt-missing 1 txt_missing DIG_TXT= -- --domain $D --apply
run_case domain-txt-wrong 1 txt_wrong DIG_TXT=OTHER -- --domain $D --apply
run_case domain-live-records-gone 1 a_missing AZ_LIVE_DOMAINS=$D DIG_A= -- --apply
run_case domain-ip-unreadable 1 ip_unreadable AZ_IP_FAILS=1 -- --domain $D
run_case domain-id-unreadable 1 id_unreadable AZ_VERIFICATION= -- --domain $D
run_case domain-dig-fails 1 dig_failed DIG_FAILS=1 -- --domain $D
run_case domain-list-fails 1 domains_unreadable AZ_DOMAINS_FAILS=1 -- --domain $D
run_case domain-other-live 1 other_domain_live AZ_LIVE_DOMAINS=play.example.com -- --domain $D
run_case domain-two-live 1 two_domains_live "AZ_LIVE_DOMAINS=$D www.$D" --
run_case domain-upper-case 1 bad_domain -- --domain CouchGaffer.com
run_case domain-not-a-host 1 bad_domain -- --domain "couchgaffer.com;rm"
run_case domain-single-label 1 bad_domain -- --domain couchgaffer
run_case domain-missing-value 2 nothing_deployed -- --domain --apply
run_case domain-apply-adds 0 domain_added -- --domain $D --apply
run_case domain-apply-binds 0 domain_bound_applied AZ_LIVE_DOMAINS=$D -- --apply
run_case domain-bound-stays-bound 0 domain_steady AZ_LIVE_DOMAINS=$D AZ_LIVE_BINDING=SniEnabled -- --apply
run_case domain-bind-fails 1 bind_failed AZ_LIVE_DOMAINS=$D AZ_BIND_FAILS=1 -- --apply
run_case domain-add-dropped 1 domain_dropped AZ_DOMAIN_DROPPED=1 -- --domain $D --apply

echo "$passed passed, $failed failed"
for f in ${failures[@]+"${failures[@]}"}; do echo "  FAILED $f"; done
[[ $failed == 0 ]]
