#!/usr/bin/env bash
# Let the app's identity (role id-watchparty) create its tables in database watchparty (#180), once, after
# scripts/apply-infra.sh --postgres --apply and before --wire. The app migrates on start, so without CREATE on schema
# public it starts without leagues. An Entra administrator that does not own the database is not proven to have it.
#
# Usage: scripts/grant-postgres-app.sh
#
# Run it signed in (az login) as a person who is the server's Entra administrator; it stops, naming the command to
# add yourself, if you are not. The server admits only Azure addresses, so it adds a firewall rule for this machine's
# public address and removes it again on any exit. It prints the role's privileges before and after, grants
# CONNECT and CREATE on the database and USAGE and CREATE on schema public only when CREATE on public is missing,
# and exits non-zero unless the role can create there at the end. Needs psql, curl and AZURE_SUBSCRIPTION_ID.
# The sign-in token is passed to psql in PGPASSWORD at the moment of use and never printed.
set -euo pipefail

RESOURCE_GROUP=rg-watchparty
POSTGRES_TYPE=Microsoft.DBforPostgreSQL/flexibleServers
POSTGRES_API=2025-08-01
DATABASE=watchparty
ROLE=id-watchparty

fail() { echo "grant-postgres-app: $*" >&2; exit 1; }

[[ $# -eq 0 ]] || { echo "usage: $0" >&2; exit 2; }
command -v psql > /dev/null || fail "psql is not installed (brew install libpq)"

expected="${AZURE_SUBSCRIPTION_ID:-}"
[[ -n "$expected" ]] || fail "set AZURE_SUBSCRIPTION_ID to the subscription that holds $RESOURCE_GROUP"
current="$(az account show --query id --output tsv 2> /dev/null)" || fail "az is not signed in; run az login"
[[ "$current" == "$expected" ]] || fail "az is on subscription $current; run az account set --subscription $expected"

server_id="$(az resource list -g "$RESOURCE_GROUP" --resource-type "$POSTGRES_TYPE" --query "[0].id" -o tsv)" ||
  fail "could not list PostgreSQL in $RESOURCE_GROUP"
[[ -n "$server_id" ]] || fail "no PostgreSQL in $RESOURCE_GROUP; create it with scripts/apply-infra.sh --postgres --apply"
server="${server_id##*/}"
host="$(az resource show --ids "$server_id" --api-version "$POSTGRES_API" --query properties.fullyQualifiedDomainName -o tsv)" ||
  fail "could not read the host name of $server"
[[ -n "$host" ]] || fail "could not read the host name of $server"

me="$(az ad signed-in-user show --query id -o tsv)" || fail "could not read your object id"
[[ -n "$me" ]] || fail "could not read your object id"
# Your role name is your administrator principal name; empty when you are not an administrator.
user="$(az rest --method get --url "https://management.azure.com$server_id/administrators?api-version=$POSTGRES_API" \
  --query "value[?properties.objectId=='$me' && properties.principalType=='User'].properties.principalName | [0]" -o tsv)" ||
  fail "could not list the administrators of $server"
[[ -n "$user" && "$user" != None ]] || fail "you ($me) are not an Entra administrator of $server. Run this signed in as" \
  "the person administrator, or add yourself: az postgres flexible-server microsoft-entra-admin create -g $RESOURCE_GROUP" \
  "-s $server -i $me -u <your user principal name> -t User"

ip="$(curl -sf --max-time 10 https://api.ipify.org)" || fail "could not read this machine's public address"
[[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "could not read this machine's public address (got '$ip')"
rule="grant-app-$(date +%s)"
remove_rule() {
  az postgres flexible-server firewall-rule delete -g "$RESOURCE_GROUP" -s "$server" -n "$rule" --yes --output none ||
    echo "grant-postgres-app: could not remove firewall rule $rule; remove it with: az postgres flexible-server firewall-rule delete -g $RESOURCE_GROUP -s $server -n $rule --yes" >&2
}
echo "adding firewall rule $rule for $ip on $server (removed on exit)"
trap remove_rule EXIT  # before the create: an interrupted create can still leave the rule behind
az postgres flexible-server firewall-rule create -g "$RESOURCE_GROUP" -s "$server" -n "$rule" \
  --start-ip-address "$ip" --end-ip-address "$ip" --output none || fail "could not add firewall rule $rule"

sql() {  # sql STATEMENT: runs as you, stops on the first error
  PGPASSWORD="$(az account get-access-token --resource-type oss-rdbms --query accessToken -o tsv)" \
    psql "host=$host port=5432 dbname=$DATABASE user=$user sslmode=require" -X -v ON_ERROR_STOP=1 -At -c "$1"
}
privileges() {  # one line: whether the role exists and what it may do
  sql "SELECT 'role exists: ' || EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$ROLE')
    || '; database owner: ' || (SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database())
    || '; schema public owner: ' || (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'public')
    || '; CREATE on public: ' || COALESCE((SELECT has_schema_privilege('$ROLE', 'public', 'CREATE')
       WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$ROLE'))::text, 'no role')"
}
can_create() {
  [[ "$(sql "SELECT has_schema_privilege('$ROLE', 'public', 'CREATE')")" == t ]]
}

before="$(privileges)" || fail "could not read the privileges of $ROLE"
echo "before: $before"
exists="$(sql "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$ROLE')")" || fail "could not read the roles"
[[ "$exists" == t ]] ||
  fail "role $ROLE does not exist; scripts/apply-infra.sh --postgres --apply makes the app's identity an administrator"
if can_create; then
  echo "$ROLE can already create tables in schema public; nothing granted"
else
  sql "GRANT CONNECT ON DATABASE $DATABASE TO \"$ROLE\"; GRANT USAGE, CREATE ON SCHEMA public TO \"$ROLE\""
  echo "granted CONNECT on database $DATABASE and USAGE, CREATE on schema public to $ROLE"
fi
after="$(privileges)" || fail "could not read the privileges of $ROLE"
echo "after: $after"
can_create || fail "$ROLE still cannot create tables in schema public"
echo "ok"
