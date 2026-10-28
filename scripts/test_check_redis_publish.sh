#!/usr/bin/env bash
# Tests for scripts/check-redis-publish.sh against a stub az and a stub uv: nothing here calls Azure or Redis.
#
# Usage: scripts/test_check_redis_publish.sh [script]
#   The default is scripts/check-redis-publish.sh beside this file; pass a changed copy to check a case can fail.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
script="$(cd "$(dirname "${1:-$here/check-redis-publish.sh}")" && pwd)/$(basename "${1:-$here/check-redis-publish.sh}")"
[[ -f "$script" ]] || { echo "no script at $script" >&2; exit 2; }

tmp="$(mktemp -d "${TMPDIR:-/tmp}/test_check_redis.XXXXXX")"
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
  "account show"*) [[ -z "${AZ_SIGNED_OUT:-}" ]] || exit 1; echo "${AZ_SUBSCRIPTION-11111111-2222-3333-4444-555555555555}" ;;
  "resource list"*redisEnterprise*) [[ -z "${AZ_LIST_FAILS:-}" ]] || { echo "ERROR: throttled" >&2; exit 1; }
    [[ -n "${AZ_NO_REDIS:-}" ]] || echo "$G/Microsoft.Cache/redisEnterprise/redis-watchparty-x" ;;
  "resource show --ids"*hostName*) [[ -z "${AZ_HOST_FAILS:-}" ]] || { echo "ERROR: throttled" >&2; exit 1; }
    echo "${AZ_HOST-redis-watchparty-x.swedencentral.redis.azure.net}" ;;
  "containerapp show -n ca-watchparty"*fqdn*) [[ -z "${AZ_FQDN_FAILS:-}" ]] || { echo "ERROR: throttled" >&2; exit 1; }
    echo "${AZ_FQDN-ca-watchparty.example.azurecontainerapps.io}" ;;
  *) echo "unexpected az call: $*" >&2; exit 99 ;;
esac
EOF
cat > "$tmp/bin/uv" << 'EOF'
#!/usr/bin/env bash
echo "uv $* (in $(basename "$PWD"))" >> "$STUB_LOG"
exit "${UV_EXIT-0}"
EOF
chmod +x "$tmp/bin/az" "$tmp/bin/uv"

run_case() {  # run_case NAME EXPECTED_STATUS CHECK [VAR=value ...] -- [script args]
  local name=$1 expected=$2 check=$3
  shift 3
  local -a env_vars=()
  while [[ $# -gt 0 && "$1" != -- ]]; do env_vars+=("$1"); shift; done
  shift
  local dir="$tmp/$name"
  mkdir -p "$dir"
  : > "$dir/log"
  env PATH="$tmp/bin:/usr/bin:/bin" STUB_LOG="$dir/log" AZURE_SUBSCRIPTION_ID=11111111-2222-3333-4444-555555555555 \
    ${env_vars[@]+"${env_vars[@]}"} /bin/bash "$script" "$@" > "$dir/out" 2>&1
  local status=$?
  if [[ "$status" == "$expected" ]] && "$check" "$dir"; then
    passed=$((passed + 1))
  else
    failed=$((failed + 1))
    failures+=("$name: exit $status (expected $expected)")
    sed 's/^/    /' "$dir/out" >&2
  fi
}

has() { grep -q -- "$2" "$1"; }
lacks() { ! grep -q -- "$2" "$1"; }

ran() {
  has "$1/log" "uv run python -m tools.check_redis_publish rediss://redis-watchparty-x.swedencentral.redis.azure.net:10000 (in backend)"
}
ran_replicas() {
  has "$1/log" "uv run python -m tools.check_replicas rediss://redis-watchparty-x.swedencentral.redis.azure.net:10000 https://ca-watchparty.example.azurecontainerapps.io (in backend)" &&
    lacks "$1/log" "tools.check_redis_publish"
}
publish_only() { ran "$1" && lacks "$1/log" "containerapp show" && lacks "$1/log" "check_replicas"; }
no_fqdn() { says "$1" "could not read the address of ca-watchparty"; }
not_run() { lacks "$1/log" "^uv "; }
says() { not_run "$1" && has "$1/out" "$2"; }
no_redis() { says "$1" "no Redis in rg-watchparty"; }
signed_out() { says "$1" "az is not signed in"; }
wrong_sub() { says "$1" "az is on subscription"; }
no_sub() { says "$1" "set AZURE_SUBSCRIPTION_ID" && lacks "$1/log" "account show"; }
list_fails() { says "$1" "could not list Redis"; }
host_fails() { says "$1" "could not read the host name"; }

run_case happy 0 publish_only --
run_case replicas 0 ran_replicas -- --replicas
run_case replicas-check-fails 1 ran_replicas UV_EXIT=1 -- --replicas
run_case replicas-no-fqdn 1 no_fqdn AZ_FQDN= -- --replicas
run_case replicas-fqdn-fails 1 no_fqdn AZ_FQDN_FAILS=1 -- --replicas
run_case replicas-no-redis 1 no_redis AZ_NO_REDIS=1 -- --replicas
run_case two-arguments 2 not_run -- --replicas --replicas
run_case check-fails 1 ran UV_EXIT=1 --
run_case no-redis 1 no_redis AZ_NO_REDIS=1 --
run_case signed-out 1 signed_out AZ_SIGNED_OUT=1 --
run_case wrong-subscription 1 wrong_sub AZ_SUBSCRIPTION=other --
run_case no-subscription 1 no_sub AZURE_SUBSCRIPTION_ID= --
run_case list-fails 1 list_fails AZ_LIST_FAILS=1 --
run_case host-fails 1 host_fails AZ_HOST_FAILS=1 --
run_case empty-host 1 host_fails AZ_HOST= --
run_case extra-argument 2 not_run -- --apply

echo "$passed passed, $failed failed"
for f in ${failures[@]+"${failures[@]}"}; do echo "  FAILED $f"; done
[[ $failed == 0 ]]
