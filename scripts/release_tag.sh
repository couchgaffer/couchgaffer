#!/usr/bin/env bash
# The release check (#93), shared by scripts/deploy.sh and .github/workflows/deploy.yml: prints HEAD's
# version, or says why HEAD is not a release and exits 1.
#
# HEAD is a release when origin's main is HEAD now and origin has exactly one strict vX.Y.Z tag on HEAD.
# Both are read live from origin with ls-remote, not from origin/main or local tags: a remote-tracking ref
# can be stale (a narrowed fetch refspec, no fetch since), and a local tag may never have been pushed or may
# have been moved after it was. ls-remote prints an annotated tag twice, the tag object and then its commit
# as ^{}; the commit is what counts.
#
# Usage: scripts/release_tag.sh   (from inside the repository; needs read access to origin)
set -euo pipefail

pattern='^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'
fail() { echo "release: $*" >&2; exit 1; }

head="$(git rev-parse HEAD)"
listing="$(git ls-remote --heads --tags origin)" || fail "could not list origin's branches and tags"

main="$(awk '$2 == "refs/heads/main" { print $1 }' <<< "$listing")"
[[ -n "$main" ]] || fail "origin has no main branch"
[[ "$head" == "$main" ]] || fail "HEAD $head is not origin/main ($main); check out the release tag on main"

# Tag names on origin whose commit is HEAD, then the strict release ones among them.
on_head="$(awk -v head="$head" '
  $2 ~ /^refs\/tags\/.*\^\{\}$/ { peeled[substr($2, 11, length($2) - 13)] = $1; next }
  $2 ~ /^refs\/tags\// { plain[substr($2, 11)] = $1 }
  END { for (name in plain) if (((name in peeled) ? peeled[name] : plain[name]) == head) print name }
' <<< "$listing")"
if ! tags="$(grep -E "$pattern" <<< "$on_head" | sort)"; then
  hint=""
  if local_tags="$(git tag --points-at HEAD | grep -E "$pattern")"; then
    hint="; the local tag $(echo "$local_tags" | tr '\n' ' ')is not on origin at this commit"
  fi
  fail "HEAD $head has no vX.Y.Z release tag on origin$hint. Cut a release (docs/releasing.md)"
fi
[[ "$tags" != *$'\n'* ]] || fail "HEAD $head has more than one release tag on origin: $(echo "$tags" | tr '\n' ' ')"
echo "$tags"
