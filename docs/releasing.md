# Releasing

`develop` is the default and integration branch. `main` holds only releases, and only a release tag on `main` deploys.

Install the hooks once per clone with `make hooks` (`git config core.hooksPath .githooks`). The pre-push hook refuses branch names other than `<prefix>/<issue-number>-<slug>` (prefixes `feature`, `bugfix`, `hotfix`, `chore`, `fix`, `ci`, `infra`, `refactor`, `docs`; the issue number has no leading zero) and `release/X.Y.Z`, and refuses any push to `main` or `develop`, and any push or delete of a `v*` tag, unless `ALLOW_PROTECTED_PUSH=1` is set, which only the integrator's steps below do. It refuses to move a tag the remote already has in one push; deleting a tag and pushing it again, both with the variable set, is still possible, so never do that to a release. It makes mistakes less likely, not impossible: it runs only where installed, and `git push --no-verify` skips it. `make hooks-test` tests it.

Ticket work: branch from `develop`, open a pull request into `develop`, and add a line under `Unreleased` in `CHANGELOG.md`. The integrator squash-merges locally:

```sh
git switch develop && git pull --ff-only
git merge --squash feature/12-x && git commit   # subject: the PR title with (#PR)
ALLOW_PROTECTED_PUSH=1 git push origin develop
```

A release:

1. `git switch -c release/1.2.0 origin/develop`. In `CHANGELOG.md`, rename `Unreleased` to `1.2.0 - <date>` and start a new empty `Unreleased`. Commit, push, open a pull request into `main`. Only fixes for the release go on this branch.
2. Merge it into `main` with a merge commit, tag it, and push both:
   ```sh
   git switch main && git pull --ff-only
   git merge --no-ff release/1.2.0 -m "release: 1.2.0"
   git tag -a v1.2.0 -m "v1.2.0"
   ALLOW_PROTECTED_PUSH=1 git push origin main v1.2.0
   ```
3. Merge it back into `develop`, then delete the release branch:
   ```sh
   git switch develop && git pull --ff-only
   git merge --no-ff release/1.2.0 -m "chore: merge release 1.2.0 back into develop"
   ALLOW_PROTECTED_PUSH=1 git push origin develop
   git push origin --delete release/1.2.0
   ```
4. Deploy from the tag: `git switch --detach v1.2.0 && scripts/deploy.sh` (infra/README.md, Deploying). The script, and `.github/workflows/deploy.yml`, refuse a commit that is not the tip of `main` on origin with exactly one `vX.Y.Z` tag there (`scripts/release_tag.sh`).

A hotfix branches from `main`, and its release branch is cut from the hotfix branch rather than from `develop`. For issue 120 on top of `v1.2.0`:

```sh
git fetch origin && git switch -c hotfix/120-x origin/main
# fix, commit, push, open a pull request into main for review
git switch -c release/1.2.1 hotfix/120-x
# CHANGELOG.md: add a "1.2.1 - <date>" section above Unreleased with the fix; commit
git push -u origin release/1.2.1

git switch main && git pull --ff-only
git merge --no-ff release/1.2.1 -m "release: 1.2.1"
git tag -a v1.2.1 -m "v1.2.1"
ALLOW_PROTECTED_PUSH=1 git push origin main v1.2.1

git switch develop && git pull --ff-only
git merge --no-ff release/1.2.1 -m "chore: merge release 1.2.1 back into develop"   # keep develop's Unreleased lines
ALLOW_PROTECTED_PUSH=1 git push origin develop
git push origin --delete release/1.2.1 hotfix/120-x

git switch --detach v1.2.1 && scripts/deploy.sh
```
