# Azure

The watch party runs as one Container App in Sweden Central, with Foundry models, and no keys anywhere. Everything is in `main.bicep`, written by us (rules 4.11, 4.12).

- **Live URL:** the `APP_URL` repository variable. The `url` output is the app's default Azure host, which keeps working beside the custom domain (below).
- **Resource group:** `rg-watchparty`.

## What is in it

| Resource | Name | Notes |
|---|---|---|
| Container App | `ca-watchparty` | <ul><li>external HTTPS; WebSockets over HTTP/1.1</li><li>0.5 vCPU and 1 GiB</li><li>a fixed replica count, always warm: **one** without Redis, where sessions live in one server's memory; **two** once wired to Redis (`appReplicas`, below)</li><li>`/health` probes</li></ul> |
| Recap job | `caj-watchparty-recap` | <ul><li>a Container Apps Job, triggered by each message on the `recaps` queue (a keyless KEDA trigger)</li><li>renders the five recaps with 4 vCPU and 8 GiB, then exits</li><li>nothing runs or costs in between</li></ul> |
| Container Apps environment | `cae-watchparty` | Consumption profile; its logs go to Log Analytics through a diagnostic setting, with no workspace key |
| Log Analytics | `log-watchparty` | 30-day retention; a 1 GB daily cap, so a log loop cannot run up the bill |
| Application Insights | `appi-watchparty` | <ul><li>traces from the app and the recap job, stored in `log-watchparty`, so its cap and retention apply</li><li>local auth off, so the instrumentation key in the connection string sends nothing; the two workload identities below are granted Monitoring Metrics Publisher explicitly (an owner of the group could still grant others)</li><li>traces only: logs already arrive through the environment's diagnostic setting (`backend/watchparty/telemetry.py` lists what is and is not sent)</li></ul> |
| Storage | `stwp<unique>` | <ul><li>`recaps` blob container and `recaps` queue, for the recap videos</li><li>shared keys and public access off</li></ul> |
| App identity | `id-watchparty` | <ul><li>OpenAI User on Foundry</li><li>AcrPull on the registry</li><li>Blob Data Contributor on `recaps`</li><li>Queue Message Sender on `recaps`</li><li>Blob Delegator, to sign read-only recap links with a user delegation key; there are no account keys</li><li>Monitoring Metrics Publisher on `appi-watchparty`, to send traces</li></ul> |
| Recap identity | `id-watchparty-recap` | AcrPull; Blob Data Contributor on `recaps`; Queue Data Contributor on the `recaps` queue; Monitoring Metrics Publisher on `appi-watchparty` |
| Deploy identity | `id-watchparty-deploy` | <ul><li>GitHub OIDC from `main` only</li><li>AcrPush on the registry; Contributor on the app and the recap job; a custom role to read and join their environment</li><li>cannot assign roles or touch Foundry or storage</li></ul> |
| Foundry IQ search service | `srch-watchparty-<unique>` | <ul><li>Azure AI Search **Basic in France Central**: Sweden Central takes no new search services (Microsoft's region table, 23 Oct); France Central keeps the data in the EU</li><li>keyless: `disableLocalAuth`; its own identity has Cognitive Services User on Foundry, to plan queries with `gpt-5.4-mini`</li><li>the app has Search Index Data Reader</li><li>loaded by `backend/tools/foundry_iq.py` from `watchparty/knowledge.py` (index `league-knowledge`, knowledge source `league-knowledge-ks`, knowledge base `league-rulebook`)</li></ul> |
| Registry | `watchpartyd4d84f` | Basic; admin user off |
| Budget | `budget-watchparty` | a monthly budget on the resource group, alerting at 50% and 80% of actual spend and 100% of forecast. Budgets only alert; they stop nothing |

**Off until switched on:** these parameters default to off, so a deploy that does not pass them changes nothing. Creating a service and wiring it into the app are separate steps, because any change to the app's settings or scale restarts the one replica and ends every live match.

| Parameter | Default | Does | Notes |
|---|---|---|---|
| `enableRedis` | `false` | creates Azure Managed Redis `redis-watchparty-<unique>` | <ul><li>Balanced B0, one node (high availability off: no replica and no SLA), Sweden Central</li><li>access keys off; the app's identity has the `default` access policy and signs in with an Entra token, whose object id is its user name; `REDIS_URL` carries no user name, which redis-py would refuse beside the token provider</li><li>one TLS endpoint on port 10000 (`EnterpriseCluster`), no eviction</li><li>does not touch the app</li></ul> |
| `enablePostgres` | `false` | creates Azure Database for PostgreSQL flexible server `psql-watchparty-<unique>` and database `watchparty` | <ul><li>Burstable B1ms, PostgreSQL 16, 32 GiB (the smallest; a disk cannot shrink), no high availability, 7-day backups</li><li>password authentication off; Entra only, with the app's identity as the Entra administrator (role name `id-watchparty`)</li><li>does not touch the app</li></ul> |
| `redisUserId` | empty | gives a person the Redis `default` access policy too (assignment `person`), after the app's | for `scripts/check-redis-publish.sh` from a laptop. With Redis on, the script passes the signed-in user, who runs the checks. `default` is the only policy: all commands and keys, so that person's Entra account can read and change live match state from anywhere |
| `postgresSku`, `postgresTier`, `postgresLocation` | `Standard_B1ms`, `Burstable`, the group's region | the server's size and region | Sweden Central refused `Standard_B1ms` for lack of capacity on 27 Oct. `--postgres-sku` and `--postgres-location` set them on the create (the tier follows the size); once the server exists the script passes its live size and region and refuses others. `az postgres flexible-server list-skus` listed B1ms, B2s and B2ms in Sweden Central, North Europe and West Europe alike, so it does not show capacity. Another region puts the database a region away from the app |
| `postgresAdminId`, `postgresAdminName` | empty | adds a person as a second Entra administrator, after the app's identity | the object id and user principal name (the role name); needed to create narrower roles by SQL. With `--postgres`, the script passes the signed-in user only when the server has no person as an administrator yet |
| `wireRedis` | `false` | gives the app `REDIS_URL` when Redis is on | **restarts the replica**; set it only in the release whose code reads it |
| `wirePostgres` | `false` | gives the app `DATABASE_URL` and `DATABASE_AUTH=entra` when PostgreSQL is on | **restarts the replica**; set it only in the release whose code reads it. One switch per URL, so wiring one service never adds the other's setting |
| `appReplicas` | `1` | the app's minimum and maximum replicas (1 or 2) | **1 without `wireRedis`**: each replica would keep its own matches and limits. 2 with it: a match runs on the replica that created it and either replica serves its screens (#95). `--wire` sets both. The league API's per-client limit (10 creates and joins a minute, `store/api.py`) is kept by each replica, so with two it is up to twice that |

Neither URL carries a password; the app adds a token for `https://redis.azure.com/.default` or `https://ossrdbms-aad.database.windows.net/.default`. Setting `enableRedis` or `enablePostgres` back to `false` does not delete the service; the template stops managing it and it keeps billing until deleted with `az redisenterprise delete` or `az postgres flexible-server delete`.

**Network exposure, plainly:**
- **Redis** has public network access on and no IP firewall. Anyone on the internet can reach the endpoint; Entra sign-in, with access keys off, is the only control.
- **PostgreSQL** has the firewall rule `0.0.0.0`, which admits every address inside Azure, including other customers' tenants, not just ours. Entra-only sign-in, with password authentication off, is the guard. The app's outbound addresses are a shared Consumption pool of about 170 that can change, so listing them would admit no less, and a VNet cannot be added to the existing environment.

**Referenced, not managed:** the Foundry account `watchparty-foundry-2af6c2` and its project and deployments, created before the template. Foundry refused a re-PUT of the account and of a deployment as "unusual activity" (23 Oct), so the template references the account instead. Its settings:

- Entra ID only (`disableLocalAuth: true`); public network access on
- project `watchparty`
- `gpt-5.4-mini` 2026-03-17, Data Zone Standard, 200K TPM
- `gpt-4.1-mini` 2025-04-14, Global Standard, 100K TPM

**To do:** set both deployments' version upgrade option to "No auto-upgrade" (in the portal, or by CLI once Foundry allows it), so a new default version cannot change tested behaviour.

## Deploying

**What deploys:** a release, never a branch. A release is a `release/X.Y.Z` branch cut from `develop`, merged into `main` with a merge commit and tagged `vX.Y.Z` (docs/releasing.md). Deploy from that tag:

```sh
git fetch origin --tags && git switch --detach v1.2.0
scripts/deploy.sh --dry-run   # the git checks, then the Azure and Docker commands it would run
scripts/deploy.sh
```

**Building in the registry:** `scripts/deploy.sh --remote-build` (with or without `--dry-run`) builds both images with ACR Tasks (`az acr build`) in `watchpartyd4d84f` instead of on the laptop. Use it when the connection is slow or congested: a local build downloads every npm and Python package and then pushes the images, and on a poor connection those downloads time out before anything reaches Azure. With the flag the script extracts the release commit's tree (`git archive`, never the working tree) into a temp dir, removed when the builds end, and uploads only that source; `az acr build` honours `.dockerignore`. The `--file` given is the Dockerfile in that dir, since az reads a relative `--file` from the current directory. It builds the app and the `recap` target for `linux/amd64` with the same commit and version tags as a local build, so the roll, the revision check and the rollback are unchanged. The builds do not use the local Docker, so the `docker info` check and `az acr login` are skipped, only in this mode. `make check` still runs first, as without the flag, and its Redis tests start a Redis container, so in practice Docker still has to be running for them. A failed `az acr build` stops the deploy before anything rolls. Each build has a 1800 second `--timeout`, and because `az acr build` can exit 0 without the run having pushed (it catches Ctrl-C, and stops following the log after `--timeout` seconds with no new line), the script then reads both tags back with `az acr repository show` and stops before the roll unless both exist and name the same image. Running ACR Tasks on the registry is covered by the Contributor role on `rg-watchparty` the deploy already needs.

`scripts/release_tag.sh` decides what is a release, for both `scripts/deploy.sh` and the workflow below, which runs it before signing in to Azure. HEAD must be the tip of `main` on origin and carry exactly one strict `vX.Y.Z` tag on origin, both read live with `git ls-remote` rather than from `origin/main` or local tags. An untagged commit, a release tag that `main` has moved past, a malformed tag (`v1.0`, `v1.0.0-rc1`), a tag never pushed, or a tag moved locally after it was pushed all stop the deploy before anything is built. `scripts/deploy.sh` checks again after `make check`. Its images are tagged with both the commit and the version (`watchparty:<commit>` and `watchparty:v1.2.0`); the roll, the image check and the rollback use the commit tag. The workflow tags its images with the commit only.

**Code:** merging to `main` does not deploy: a deploy restarts the single replica and ends every live match, so `.github/workflows/deploy.yml` runs only when started by hand (`workflow_dispatch`), and only on `main`. It does not check that CI passed on that commit and runs no tests itself, so start it only after CI is green on `main` (`scripts/deploy.sh` runs `make check` first). It:
1. builds the app and recap images, tagged with the commit
2. pushes them
3. updates the recap job, then the app
4. waits up to 5 minutes for the new revision to be `Healthy` and the app's latest ready revision, and fails at once if it is `Failed`. Until then Azure keeps the traffic on the old revision, so a smoke run earlier would test the old image. It then checks the revision runs the image just pushed
5. plays a whole match on the live URL (`tools.smoke --full`, 25 minutes at most); if any step after the roll fails or the run is cancelled, it puts both back on the images they ran before and stays red

It logs in through OIDC with the repository variables `AZURE_CLIENT_ID`, `AZURE_TENANT_ID` and `AZURE_SUBSCRIPTION_ID`, which are IDs, not secrets.

**Deploy by hand:** when Actions cannot run, `scripts/deploy.sh` does the same steps from a laptop, after running `make check`. `make check` is a subset of CI: it skips the codegen drift checks, `npm ci` and `npm audit`, the container health check and the secret scan. The script deploys only a clean checkout of a release tag on `origin/main`, checks both again after `make check`, and builds the images from the commit itself (`git archive`) for `linux/amd64`. It waits for the new revision and rolls back the same way as the workflow, and it also rolls back if `/health` does not answer within 2 minutes, the smoke runs past 40, or the script is stopped with Ctrl-C, a closed terminal or `kill` (a `kill` of the script's process alone takes effect when the current step ends). Once the rollback starts, further signals are ignored until it ends. `scripts/test_deploy.sh` (`make deploy-test`) runs it against stubs; it calls nothing in Azure. It needs `az login` with AcrPush on the registry and Contributor on `rg-watchparty`, and `AZURE_SUBSCRIPTION_ID` set in the environment to the subscription to deploy to (the same value as the repository variable); it refuses to run when the variable is unset or `az account show` names another subscription. `--dry-run` prints the Azure and Docker commands without running them, but the git checks still apply, so it too needs a clean checkout of a release tag on `origin/main`.

**Rollback by hand:** run `az containerapp update -n ca-watchparty -g rg-watchparty --image watchpartyd4d84f.azurecr.io/watchparty:<earlier commit>`. Images deployed from a release also carry the version tag (`watchparty:v1.1.0`).

**Infrastructure:** changes are rare and need role-assignment rights, so they run by hand, as an owner, with `scripts/apply-infra.sh`. Set `AZURE_SUBSCRIPTION_ID` as for a deploy; the script refuses any other subscription. It reads the live app and recap images, the app's replica count, whether the app already has `REDIS_URL` or `DATABASE_URL`, the budget's alert emails and amount, and your object id itself (so an apply never rolls the app back or changes the budget), and stops if any is empty or cannot be read:

```sh
scripts/apply-infra.sh            # the state before, then the what-if; changes nothing
scripts/apply-infra.sh --apply    # the same, then create the deployment and print the state after
scripts/apply-infra.sh --dry-run  # print the what-if and create commands without running them
scripts/apply-infra.sh --redis --postgres          # the what-if with both #97 services created
scripts/apply-infra.sh --redis --postgres --apply  # create them; the app keeps its settings and replicas
scripts/apply-infra.sh --wire                      # the what-if of wiring the app to both, on two replicas
scripts/apply-infra.sh --wire --apply              # wire it; restarts the app (#180)
```

With `--redis` or `--postgres` (or a service that already exists), the script first checks the subscription is registered for `Microsoft.Cache` and `Microsoft.DBforPostgreSQL` and stops with `az provider register --namespace <namespace> --wait` if not: an unregistered namespace otherwise fails the deployment after minutes of other work (27 Oct).

Between the two, run `scripts/grant-postgres-app.sh` once, signed in as a person who is the server's Entra administrator: it gives `id-watchparty` CONNECT and CREATE on `watchparty` and USAGE and CREATE on schema `public` if it cannot already create tables there (an Entra administrator that does not own the database is not proven to), printing the privileges before and after. The server admits only Azure addresses, so it adds a firewall rule for this machine's address and removes it on exit. `make infra-test` runs it against stubs.

`--wire` passes `wireRedis=true wirePostgres=true appReplicas=2`. It refuses unless both services exist before the run, so the app never starts before its access is in place. Its after state also needs `REDIS_URL`, `DATABASE_URL`, `DATABASE_AUTH` set to `entra`, and two replicas as both minimum and maximum. A later code deploy changes only the image (`az containerapp update --image`), so it keeps the settings and the replicas, and every run of this script without `--wire` passes them back as they are live.

`--redis` and `--postgres` set `enableRedis` and `enablePostgres`; a service that already exists stays on without its switch. `--postgres` also makes the knowledge admin (the person the script finds holding the search role, or the signed-in user when nobody does) the server's second Entra administrator, when the server has no person as one yet. The script passes `wireRedis`, `wirePostgres` and `appReplicas` as they are live (`wireRedis` is on when the app already has `REDIS_URL`, `wirePostgres` when it has `DATABASE_URL`), without `--wire`, so it never wires the app or changes its scale unasked. Before it creates, it reads the app and recap images again and stops if either changed since the what-if. Wiring is `--wire`, above, run when no match is running.

**Not proven yet:** a guest account's user principal name (the form `name_domain#EXT#@tenant.onmicrosoft.com`) as `postgresAdminName`. Nothing has created a PostgreSQL Entra administrator from one. The first `--postgres` what-if must show the administrator with that name and id, and the first apply must be checked in the after state and by signing in with `az login` and a token for `https://ossrdbms-aad.database.windows.net`.

The before and after states also print the app's replicas and setting names, and whether Redis exists with high availability off, access keys off and the app's access policy, and whether PostgreSQL exists with password auth off, Entra auth on and the app's identity as administrator. Each value is read before it is printed, and a read that fails stops the script; only a ResourceNotFound from az reads as none. They cover the Application Insights setup: the component, local auth off on the `log-watchparty` workspace, Monitoring Metrics Publisher for both identities, and the connection string on the app and the job. `--apply` exits non-zero when the after state is incomplete. `scripts/test_apply_infra.sh` (`make infra-test`) runs it against a stub `az`.

**Any apply that changes the app's environment variables starts a new revision and restarts the app's replicas: every live match ends.** If the league store cannot start (sign-in, permissions, network), the app now starts without the league routes and `/ready` says `"leagues": "unavailable: <error type>"` while staying 200; before, the process exited and every replica restarted in a loop. Apply only when no match is running. For the telemetry setup, the order against the code deploy does not matter: an image without telemetry ignores the setting, and an image with it and no setting sends nothing.

A read-only what-if against the live group, before the telemetry setup was applied, showed these real changes for it: create `appi-watchparty` and its two Monitoring Metrics Publisher assignments, and add `APPLICATIONINSIGHTS_CONNECTION_STRING` to the app and the job. It also showed changes that were there before: two Create lines for the knowledge admin's roles on the search service (not assigned live; an apply creates them), the budget's dates and the diagnostic setting's `audit` group (format only), and the usual noise (`reference()` values and read-only properties shown as Modify or Delete).

After the first match on the new revision, traces show under `appi-watchparty`, Transaction search:
- one `key_moment` trace per key moment, with `moment`, then `manager_bid` and `host_decide`, under it
- one `summary` trace per manager at half time and full time, with the summary agent's spans under it
- a `recap_render` from the `watchparty-recap` role about a minute after full time

A bid or host fallback has a `fallback` attribute saying why. A summary has a `source` attribute (`manager` or `fallback`). A timeout, an error, an invalid answer, a failed check, a moment that throws or a failed render marks its span failed, with the exception's type and never its text.

Then load the rulebook into Foundry IQ. Without it the search service is empty and every lookup is a 404. Run it once after a deploy into a new resource group or search region; it is idempotent and exits non-zero if any document fails:

```sh
SEARCH=$(az search service list -g rg-watchparty --query "[0].name" -o tsv)
FOUNDRY=$(az cognitiveservices account list -g rg-watchparty --query "[0].properties.customSubDomainName" -o tsv)
cd backend && uv run python -m tools.foundry_iq "https://$SEARCH.search.windows.net" "https://$FOUNDRY.openai.azure.com"
```

**Checks:**
- **Smoke test:** `uv run python -m tools.smoke <url>` from `backend/`: health, then one explanation a manager agent wrote. About a minute.
- **Whole match:** add `--full` to play a whole match, print how the agents did, and fail without the summaries, a rulebook citation, Spanish text and the five recaps. It runs after every deploy and daily (`.github/workflows/smoke.yml`).

**Agents on a laptop:** the backend signs in to Foundry with `DefaultAzureCredential`, so a local run can use your `az login`. Owner on the resource group does not include calling the models; grant yourself the role once with `scripts/grant-local-agents.sh` (`--dry-run` first, with `AZURE_SUBSCRIPTION_ID` set as for a deploy), wait a few minutes, then:

```sh
cd backend && FOUNDRY_ENDPOINT=https://watchparty-foundry-2af6c2.cognitiveservices.azure.com/ \
  MANAGER_MODEL=gpt-5.4-mini HOST_MODEL=gpt-5.4-mini uv run uvicorn watchparty.app.main:app --port 8000 --reload
```

`/health` then reports `"agents": true` (and `"recaps"`, whether a finished match queues recap videos). Leave `RECAP_STORAGE_ACCOUNT` unset so a local match never queues a render in Azure, and `FOUNDRY_IQ_ENDPOINT` unset because your login has no role on the search service; local matches then run without rulebook lookups. Every local match calls the paid models, the same as the live site.

This grant is a role assignment made by hand, outside `main.bicep`. To remove it: `az role assignment delete --assignee <object id> --role "Cognitive Services OpenAI User" --scope <Foundry account id>`.

## Live end-to-end check

After a deploy, and before recording, `backend/tools/e2e_live.py` drives the deployed site in Chromium as a judge would: the landing page, Watch now, one match through to its recap (the group screen, the drawer, the phones, a knock-out, Behind the scenes, a close call if one happens, the phone page at 375 px, the summaries), the ended page, a league with two members through squads, its gameweek and the season table, For judges, and on `https://couchgaffer.com` the certificate and the hosts of the QR and invite links (#204). From `backend/`:

```sh
uv run --with playwright playwright install chromium     # once
uv run --with playwright python -m tools.e2e_live https://couchgaffer.com
uv run --with playwright python -m tools.e2e_live http://127.0.0.1:8000 --allow-agents-off --allow-no-recaps  # local
uv run --with playwright python -m tools.e2e_live https://couchgaffer.com --cases 1,2,14,20   # no match started
```

It prints one line per case (`ok`, `skip <reason>` or `FAIL <reason>`) and a summary, exits 1 on any failure, and saves failure screenshots to `--out` (by default a `couch-gaffer-e2e` folder in the system temp folder) with the QR code hidden; it never prints or keeps a phone or member token. It fails closed: a server without agents, or without a recap job, fails the cases that need them, and a setup that fails (the Watch now start, the league) fails every selected case that depends on it. A local run passes `--allow-agents-off` and `--allow-no-recaps` to accept a server without them; the summary line says which mode ran. A whole run took about 7 minutes locally, and can take up to 10 more when it waits for a recap. Each run costs two matches with the agents and one league: from one address that is half the hourly limit of 4 matches, so run it at most twice an hour, and not just before recording from the same network. A close call may not happen in a run; that case then reports skip. The recap case reads `"recaps"` from `/health` to know whether a recap job exists.

## Custom domain

The app is served on `couchgaffer.com` as well as its default host, which keeps working (#66). The certificate is an Azure Container Apps managed certificate: free, renewed by Azure, checked over HTTP. The zone is on Cloudflare.

**Records.** Two, both **DNS only** (grey cloud). A proxied record answers with Cloudflare's addresses, and the certificate check fails.

| Type | Name | Value |
|---|---|---|
| A | `couchgaffer.com` (`@`) | the environment's static IP: `az containerapp env show -n cae-watchparty -g rg-watchparty --query properties.staticIp -o tsv` |
| TXT | `asuid` (`asuid.couchgaffer.com`) | the environment's verification id: `az containerapp env show -n cae-watchparty -g rg-watchparty --query properties.customDomainConfiguration.customDomainVerificationId -o tsv` |

An apex domain takes an A record, not a CNAME. Both values belong to the environment, so they change only if `cae-watchparty` is created again. `scripts/apply-infra.sh` reads them from Azure on every run and prints the two records if DNS does not answer with them.

**Apply, twice.** Azure issues a managed certificate only for a host name already on the app, so it takes two applies, the same command each time, when no match is running:

```sh
AZURE_SUBSCRIPTION_ID=<id> scripts/apply-infra.sh --domain couchgaffer.com           # what-if only
AZURE_SUBSCRIPTION_ID=<id> scripts/apply-infra.sh --domain couchgaffer.com --apply   # adds the host name, binding Disabled
AZURE_SUBSCRIPTION_ID=<id> scripts/apply-infra.sh --domain couchgaffer.com --apply   # issues the certificate, binding SniEnabled
```

Before the what-if the script checks with `dig @1.1.1.1` that `couchgaffer.com` has exactly one A record, the static IP, and that `asuid.couchgaffer.com` has a TXT record with the verification id, and stops if not. It does not check AAAA or CAA records: the zone should have no AAAA record on the apex, and no CAA record that leaves out the issuer Azure uses. Once the domain is live every run checks the records, so a record switched to proxied stops unrelated applies too, until it is put back. The template's `customDomain` parameter is empty by default, which adds no host name; `customDomainBound` is false on the first apply and true once the host name is live, which adds the certificate (`mc-couchgaffer-com`) and binds it. The script decides it from the app. Once the host name is on the app, a run without `--domain` keeps it and checks its records, so a later apply never removes it. An apply of the template by any other means must pass `customDomain` and `customDomainBound=true`, or it removes the domain.

**Check:**

```sh
dig +short couchgaffer.com A            # the static IP, and nothing else
dig +short asuid.couchgaffer.com TXT    # the verification id
curl -sI https://couchgaffer.com/health | head -1    # HTTP/2 200
curl -sI http://couchgaffer.com/health | head -3     # a redirect to https, never the app over http
echo | openssl s_client -connect couchgaffer.com:443 -servername couchgaffer.com 2>/dev/null | openssl x509 -noout -subject -issuer -dates
(cd backend && uv run python -m tools.smoke https://couchgaffer.com --full)    # the full smoke, WebSockets included
```

The subject must be `CN=couchgaffer.com`. The page builds its content security policy from the request's host and the QR code, the phone links and the league share link from the page's own origin, so nothing in the app names a host. The recap links point at the storage account. The smoke and the deploy's health poll take `APP_URL`; the repository variable of the same name sets it for `.github/workflows/smoke.yml` and `deploy.yml`.

`www.couchgaffer.com` is not bound. To send it to the apex, add in Cloudflare a proxied (orange cloud) `AAAA` record `www` to `100::` and a redirect rule from `www.couchgaffer.com/*` to `https://couchgaffer.com/${1}`; Cloudflare's own certificate covers it. The app sends `Strict-Transport-Security` with `includeSubDomains`, so every subdomain of `couchgaffer.com` must serve HTTPS once a browser has seen the apex.

## Sizing

| Item | Size and use |
|---|---|
| Container App | 0.5 vCPU and 1 GiB, one replica (two once wired, below), always on and mostly idle; a 3-minute match is about 3 minutes of active time |
| Registry | Basic |
| Log Analytics | under the 1 GB daily cap, usually far under |
| Application Insights traces | stored in Log Analytics, under the same cap; one demo match is about 380 spans on code alone (340 KB as OpenTelemetry JSON, measured locally) and about 500 with agents; the stored size in Application Insights is not measured yet |
| Recap job | 4 vCPU and 8 GiB for about a minute a match, and nothing in between |
| Storage | a few hundred MB of recaps |
| Models | about 19K input and 4K output tokens a match (0001-models.md), before the half-time and full-time summaries |
| Foundry IQ search service | Basic, always on; semantic ranker on the Standard plan; a few lookups a match |
| Azure Managed Redis, once `enableRedis` is on | Balanced B0, one node, always on from creation until deleted |
| PostgreSQL, once `enablePostgres` is on | Burstable B1ms and 32 GiB of storage, always on from creation until deleted |
| Second app replica, once `appReplicas` is 2 | the Container App line above, twice |
