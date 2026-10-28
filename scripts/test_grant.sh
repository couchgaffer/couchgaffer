#!/usr/bin/env bash
# Tests for scripts/grant-local-agents.sh against a stub az: nothing here calls Azure.
# The stub answers `az account show` with AZ_SUBSCRIPTION, `az ad signed-in-user show` with a fixed id and
# `az role assignment list` with AZ_ROLES, and logs every call.
#
# Usage: scripts/test_grant.sh [grant script]
#   The default is scripts/grant-local-agents.sh beside this file; pass a changed copy to check that a case can fail.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
grant="${1:-$here/grant-local-agents.sh}"
[[ -f "$grant" ]] || { echo "no grant script at $grant" >&2; exit 2; }

SUBSCRIPTION=11111111-2222-3333-4444-555555555555  # made up
USER_ID=66666666-7777-8888-9999-000000000000
tmp="$(mktemp -d "${TMPDIR:-/tmp}/test_grant.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"
cat > "$tmp/bin/az" << 'EOF'
#!/usr/bin/env bash
echo "az $*" >> "$STUB_LOG"
case "$*" in
  "account show"*) echo "$AZ_SUBSCRIPTION" ;;
  "ad signed-in-user show"*) echo "$USER_ID" ;;
  "role assignment list"*) [[ -z "${AZ_ROLES:-}" ]] || printf '%s\n' "$AZ_ROLES" ;;
esac
exit 0
EOF
chmod +x "$tmp/bin/az"

passed=0
failed=0
errors=()
log="$tmp/log"
out="$tmp/out"

run() {  # run [VAR=value ...] [-- grant args]; sets status
  local -a vars=() args=()
  while (( $# )); do
    if [[ "$1" == -- ]]; then shift; args=("$@"); break; fi
    vars+=("$1"); shift
  done
  : > "$log"
  env "PATH=$tmp/bin:$PATH" "STUB_LOG=$log" "USER_ID=$USER_ID" "AZ_SUBSCRIPTION=$SUBSCRIPTION" \
    "AZURE_SUBSCRIPTION_ID=$SUBSCRIPTION" ${vars[@]+"${vars[@]}"} bash "$grant" ${args[@]+"${args[@]}"} > "$out" 2>&1
  status=$?
}

check() { local what=$1; shift; "$@" || errors+=("$what"); }
status_is() { [[ "$status" == "$1" ]]; }
logged() { grep -qF -- "$1" "$log"; }
not_logged() { ! grep -qF -- "$1" "$log"; }
output_has() { grep -qF -- "$1" "$out"; }
finish() {  # finish NAME
  if (( ${#errors[@]} == 0 )); then
    passed=$((passed + 1)); echo "ok    $1"
  else
    failed=$((failed + 1)); echo "FAIL  $1"
    for error in "${errors[@]}"; do echo "        expected: $error"; done
    echo "        exit status $status; output:"; sed 's/^/          | /' "$out"
  fi
  errors=()
}

run AZURE_SUBSCRIPTION_ID=
check "exit 1" status_is 1; check "says to set it" output_has "set AZURE_SUBSCRIPTION_ID"
check "az not asked" not_logged "account show"; check "no role call" not_logged "role assignment"
finish "no expected subscription"

run AZ_SUBSCRIPTION=00000000-0000-0000-0000-000000000000
check "exit 1" status_is 1; check "says az account set" output_has "az account set --subscription $SUBSCRIPTION"
check "no role call" not_logged "role assignment"
finish "wrong subscription"

run -- --dry-run
check "exit 0" status_is 0; check "lists roles" logged "role assignment list"
check "assigns nothing" not_logged "role assignment create"
check "prints the scope on the signed-in subscription" output_has "/subscriptions/$SUBSCRIPTION/resourceGroups/rg-watchparty/"
finish "dry run on the right subscription"

run
check "exit 0" status_is 0
check "assigns to the signed-in user" logged "role assignment create --assignee-object-id $USER_ID"
check "scope on the signed-in subscription" logged "--scope /subscriptions/$SUBSCRIPTION/resourceGroups/rg-watchparty/"
finish "grant on the right subscription"

run AZ_ROLES="Cognitive Services OpenAI User"
check "exit 0" status_is 0; check "assigns nothing" not_logged "role assignment create"
finish "already granted"

if command -v shellcheck > /dev/null; then
  status=""; : > "$out"
  check "shellcheck clean on the grant script" shellcheck "$grant"
  check "shellcheck clean on this script" shellcheck "$here/test_grant.sh"
  finish "shellcheck"
else
  echo "skip  shellcheck: not installed"
fi

echo "== $passed passed, $failed failed"
(( failed == 0 ))
