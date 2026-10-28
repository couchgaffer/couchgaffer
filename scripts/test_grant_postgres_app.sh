#!/usr/bin/env bash
# Tests for scripts/grant-postgres-app.sh against a stub az, curl and psql: nothing here calls Azure or a database.
#
# Usage: scripts/test_grant_postgres_app.sh [script]
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
script="$(cd "$(dirname "${1:-$here/grant-postgres-app.sh}")" && pwd)/$(basename "${1:-$here/grant-postgres-app.sh}")"
[[ -f "$script" ]] || { echo "no script at $script" >&2; exit 2; }

tmp="$(mktemp -d "${TMPDIR:-/tmp}/test_grant_pg.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT
passed=0
failed=0
failures=()

mkdir -p "$tmp/bin"
cat > "$tmp/bin/az" << 'EOF'
#!/usr/bin/env bash
echo "az $*" >> "$STUB_LOG"
G=/subscriptions/s/resourceGroups/rg-watchparty/providers
case "$*" in
  "account show"*) echo 11111111-2222-3333-4444-555555555555 ;;
  "resource list"*flexibleServers*) [[ -n "${AZ_NO_SERVER:-}" ]] || echo "$G/Microsoft.DBforPostgreSQL/flexibleServers/psql-watchparty-x" ;;
  "resource show --ids"*fullyQualifiedDomainName*) echo psql-watchparty-x.postgres.database.azure.com ;;
  "ad signed-in-user show"*) echo person-1 ;;
  "rest --method get"*administrators*) [[ -n "${AZ_NOT_ADMIN:-}" ]] || echo "person@example.test" ;;
  # az 2.91: -s names the server and -n the rule; the older -n server -r rule is refused (#191).
  "postgres flexible-server firewall-rule "*" -r "*) echo "ERROR: unrecognized arguments: -r" >&2; exit 2 ;;
  "postgres flexible-server firewall-rule create"*" -s "*" -n grant-app-"*) [[ -z "${AZ_RULE_FAILS:-}" ]] || exit 1; touch "$STUB_STATE/rule" ;;
  "postgres flexible-server firewall-rule delete"*" -s "*" -n grant-app-"*) rm -f "$STUB_STATE/rule" ;;
  "postgres flexible-server firewall-rule "*) echo "ERROR: the following arguments are required: --server-name/-s" >&2; exit 2 ;;
  "account get-access-token"*) echo SECRET-TOKEN-VALUE ;;
  *) echo "unexpected az call: $*" >&2; exit 99 ;;
esac
EOF
cat > "$tmp/bin/curl" << 'EOF'
#!/usr/bin/env bash
[[ -z "${CURL_FAILS:-}" ]] || exit 7
echo 203.0.113.7
EOF
cat > "$tmp/bin/psql" << 'EOF'
#!/usr/bin/env bash
echo "psql $* (password ${PGPASSWORD:+set})" >> "$STUB_LOG"
[[ -f "$STUB_STATE/rule" ]] || { echo "psql: no pg_hba.conf entry" >&2; exit 2; }
[[ -z "${PG_FAILS:-}" ]] || { echo "psql: FATAL: password authentication failed" >&2; exit 2; }
sql="${*: -1}"
can() { [[ -n "${PG_CAN:-}" || ( -f "$STUB_STATE/granted" && -z "${PG_GRANT_NOOP:-}" ) ]]; }
case "$sql" in
  GRANT*) touch "$STUB_STATE/granted" ;;
  "SELECT 'role exists"*) echo "role exists: true; CREATE on public: $(can && echo true || echo false)" ;;
  *"has_schema_privilege"*) can && echo t || echo f ;;
  *"EXISTS (SELECT 1 FROM pg_roles"*) [[ -n "${PG_NO_ROLE:-}" ]] && echo f || echo t ;;
  *) echo "unexpected sql: $sql" >&2; exit 3 ;;
esac
EOF
chmod +x "$tmp/bin/az" "$tmp/bin/curl" "$tmp/bin/psql"

run_case() {  # run_case NAME EXPECTED_STATUS CHECK [VAR=value ...]
  local name=$1 expected=$2 check=$3
  shift 3
  local dir="$tmp/$name"
  mkdir -p "$dir/state"
  : > "$dir/log"
  env PATH="$tmp/bin:/usr/bin:/bin" STUB_LOG="$dir/log" STUB_STATE="$dir/state" \
    AZURE_SUBSCRIPTION_ID=11111111-2222-3333-4444-555555555555 "$@" /bin/bash "$script" > "$dir/out" 2>&1
  local status=$?
  if [[ "$status" == "$expected" ]] && "$check" "$dir" && ! grep -q SECRET-TOKEN-VALUE "$dir/out"; then
    passed=$((passed + 1))
  else
    failed=$((failed + 1))
    failures+=("$name: exit $status (expected $expected)")
    sed 's/^/    /' "$dir/out" >&2
  fi
}

has() { grep -q -- "$2" "$1"; }
lacks() { ! grep -q -- "$2" "$1"; }
rule_removed() { has "$1/log" "firewall-rule create" && has "$1/log" "firewall-rule delete" && [[ ! -f "$1/state/rule" ]]; }
no_rule() { lacks "$1/log" "firewall-rule create" && lacks "$1/log" "^psql"; }

granted() {
  rule_removed "$1" && has "$1/log" "psql host=psql-watchparty-x.postgres.database.azure.com port=5432 dbname=watchparty user=person@example.test sslmode=require" &&
    has "$1/out" "before: role exists: true; CREATE on public: false" && has "$1/out" "after: role exists: true; CREATE on public: true" &&
    has "$1/out" "granted CONNECT on database watchparty and USAGE, CREATE on schema public to id-watchparty" &&
    has "$1/out" "^ok$" && has "$1/log" "(password set)"
}
already() { rule_removed "$1" && lacks "$1/out" "^granted" && has "$1/out" "can already create" && has "$1/out" "^ok$"; }
still_cannot() { rule_removed "$1" && has "$1/out" "still cannot create" && lacks "$1/out" "^ok$"; }
not_admin() { no_rule "$1" && has "$1/out" "are not an Entra administrator" && has "$1/out" "microsoft-entra-admin create"; }
psql_fails() { rule_removed "$1" && has "$1/out" "could not read the privileges" && lacks "$1/out" "^ok$"; }
no_role() { rule_removed "$1" && has "$1/out" "role id-watchparty does not exist" && lacks "$1/log" "GRANT"; }
no_server() { no_rule "$1" && has "$1/out" "no PostgreSQL in rg-watchparty"; }
no_ip() { no_rule "$1" && has "$1/out" "public address"; }
rule_fails() { lacks "$1/log" "^psql" && has "$1/out" "could not add firewall rule"; }

run_case grants 0 granted
run_case already-can 0 already PG_CAN=1
run_case grant-does-not-take 1 still_cannot PG_GRANT_NOOP=1
run_case not-an-admin 1 not_admin AZ_NOT_ADMIN=1
run_case psql-refused 1 psql_fails PG_FAILS=1
run_case no-role 1 no_role PG_NO_ROLE=1
run_case no-server 1 no_server AZ_NO_SERVER=1
run_case no-public-address 1 no_ip CURL_FAILS=1
run_case rule-fails 1 rule_fails AZ_RULE_FAILS=1

echo "$passed passed, $failed failed"
for f in ${failures[@]+"${failures[@]}"}; do echo "  FAILED $f"; done
[[ $failed == 0 ]]
