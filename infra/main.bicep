// The watch party on Azure (#14): one Container App behind HTTPS, Foundry models, keyless everywhere.
// Deploy into the existing resource group (see infra/README.md). Re-running it is safe: every resource
// is declared with the settings it should have; the Foundry account from #6 is referenced.

@description('Region for everything; Sweden Central has the models (docs/decisions/0001-models.md).')
param location string = resourceGroup().location

@description('The existing Foundry (AI Services) account; its name is also its endpoint subdomain.')
param foundryName string = 'watchparty-foundry-2af6c2'

@description('The existing container registry.')
param registryName string = 'watchpartyd4d84f'

@description('The app image, for example watchpartyd4d84f.azurecr.io/watchparty:<git sha>. CI updates it on each merge.')
param image string

@description('The recap job image (#15), built with --target recap. CI updates it on each merge.')
param recapImage string

@description('Region of the Foundry IQ search service (#18): Sweden Central takes no new search services (23 Oct).')
param searchLocation string = 'francecentral'

@description('Object id of the person who loads the knowledge base with tools/foundry_iq.py. Deploy-time only.')
param knowledgeAdminId string = ''

@description('Who gets the budget alerts. Passed at deploy time, never committed.')
param alertEmails array

@description('The OIDC subject GitHub presents for main of this repository. It carries the owner and repository IDs, so a renamed or re-created repository of the same name cannot match.')
param githubSubject string = 'repo:jadedm@7683271/mini-league-watch-party@1426084850:ref:refs/heads/main'

@description('Monthly budget amount, in the billing currency. Passed at deploy time, never committed.')
@minValue(1)
param budgetAmount int

@description('First day of the budget period (the first of a month).')
param budgetStart string = '2026-10-01'

@description('Create Azure Managed Redis for shared match state (#97). Creates the service only; see wireRedis and appReplicas.')
param enableRedis bool = false

@description('Create Azure Database for PostgreSQL for durable records (#97). Creates the service only; see wirePostgres.')
param enablePostgres bool = false

@description('PostgreSQL compute size. Sweden Central refused Standard_B1ms for capacity on 27 Oct.')
param postgresSku string = 'Standard_B1ms'

@description('The tier of postgresSku: Burstable for Standard_B*, GeneralPurpose for Standard_D*.')
@allowed(['Burstable', 'GeneralPurpose', 'MemoryOptimized'])
param postgresTier string = 'Burstable'

@description('PostgreSQL region, when the app\'s region has no capacity. Fixed once the server exists.')
param postgresLocation string = location

@description('Give the app REDIS_URL when Redis is on. A new setting restarts the one replica, so only in the release whose code reads it.')
param wireRedis bool = false

@description('Give the app DATABASE_URL when PostgreSQL is on. A new setting restarts the one replica, so only in the release whose code reads it.')
param wirePostgres bool = false

@description('App replicas, minimum and maximum. 2 only with wireRedis: without Redis each replica keeps its own matches.')
@minValue(1)
@maxValue(2)
param appReplicas int = 1

@description('Object id of a person to add as a second PostgreSQL Entra administrator, to create narrower roles by SQL. Optional.')
param postgresAdminId string = ''

@description('That person\'s user principal name, which becomes their database role name. Needed with postgresAdminId.')
param postgresAdminName string = ''

@description('Object id of a person to give the Redis access policy too, to run tools/check_redis_publish.py and other checks with az login. Optional.')
param redisUserId string = ''

@description('A host name to serve the app on besides its default one, such as couchgaffer.com (#66). Empty, the default, adds none.')
param customDomain string = ''
// Once a domain is on the app, every apply must pass it again, or the apply removes it; scripts/apply-infra.sh does.

@description('Bind customDomain to a managed certificate. Azure issues the certificate only for a host name already on the app, so the first apply adds it with binding Disabled and the second sets this; scripts/apply-infra.sh --domain decides from what is live.')
param customDomainBound bool = false

var tags = { project: 'watchparty', until: '2026-11-10' }
var managerModel = 'gpt-5.4-mini'
var roles = {
  openAiUser: '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
  acrPull: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
  blobDataContributor: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
  queueMessageSender: 'c6a89b2d-59bc-44d0-9896-0f6e12d7b80a'
  queueDataContributor: '974c5e8b-45b9-4653-ba55-5f855dd0fb88'
  blobDelegator: 'db58b8e5-c6ad-4a2a-8342-4190687cbf4a'
  acrPush: '8311e382-0749-4cb8-b61a-304f252e45ec'
  cognitiveServicesUser: 'a97b65f3-24c7-4388-baec-2e87135dc908'
  searchIndexDataReader: '1407120a-92aa-4202-b7e9-c0e197c71c8f'
  searchIndexDataContributor: '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
  searchServiceContributor: '7ca78c08-252a-4471-8644-bb5ff32d4ba0'
  contributor: 'b24988ac-6180-42a0-ab88-20f7382dd24c'
  monitoringMetricsPublisher: '3913510d-42f4-4e42-8a64-420c390055eb'
}

// --- identity: the app reaches Foundry, storage and the registry as this, with no keys -----------------

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-watchparty'
  location: location
  tags: tags
}

// --- Foundry: the account, project and two model deployments from #6 --------------------------------
// Referenced, not redeclared: Foundry refuses a re-PUT of this account or its deployments as "unusual
// activity" (23 Oct). Their settings are recorded in infra/README.md: Entra ID only (no keys),
// gpt-5.4-mini Data Zone Standard 200K TPM, gpt-4.1-mini Global Standard 100K TPM.

resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryName
}

// --- registry, storage, logs -------------------------------------------------------------------------

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: registryName
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'stwp${uniqueString(resourceGroup().id)}'
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    allowSharedKeyAccess: false // Entra ID only
    allowBlobPublicAccess: false // recaps are served through the app (#15)
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    defaultToOAuthAuthentication: true
  }
}

resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource recaps 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobs
  name: 'recaps'
  properties: { publicAccess: 'None' }
}

// Each match leaves an input bundle and five videos. Deleting them after 30 days means storage stops growing
// with every match, abusive ones included.
resource recapLifecycle 'Microsoft.Storage/storageAccounts/managementPolicies@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    policy: {
      rules: [
        {
          name: 'delete-recaps-after-30-days'
          enabled: true
          type: 'Lifecycle'
          definition: {
            filters: { blobTypes: ['blockBlob'], prefixMatch: ['${recaps.name}/'] }
            actions: { baseBlob: { delete: { daysAfterModificationGreaterThan: 30 } } }
          }
        }
      ]
    }
  }
}

resource queues 'Microsoft.Storage/storageAccounts/queueServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource recapQueue 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01' = {
  parent: queues
  name: 'recaps' // the app writes one message at full time; the recap job (#15) is triggered by it
}

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-watchparty'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    workspaceCapping: { dailyQuotaGb: 1 } // a runaway log loop cannot run up the bill
  }
}

// Traces from the app and the recap job. Workspace-based: they land in log-watchparty, under its cap and
// retention, and are billed through it. Local auth off: the instrumentation key in the connection string cannot
// send anything; an Entra identity with Monitoring Metrics Publisher must. The two workload identities are granted
// it explicitly below.
resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-watchparty'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
    IngestionMode: 'LogAnalytics'
    DisableLocalAuth: true
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

resource appCanSendTraces 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: appInsights
  name: guid(appInsights.id, identity.id, roles.monitoringMetricsPublisher)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.monitoringMetricsPublisher)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// --- role assignments: only what the app needs -------------------------------------------------------

resource canUseModels 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: foundry
  name: guid(foundry.id, identity.id, roles.openAiUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.openAiUser)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource canPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, identity.id, roles.acrPull)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.acrPull)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource canWriteRecaps 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: recaps
  name: guid(recaps.id, identity.id, roles.blobDataContributor)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobDataContributor)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource canQueueRecaps 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: recapQueue
  name: guid(recapQueue.id, identity.id, roles.queueMessageSender)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.queueMessageSender)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource canSignRecapLinks 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage // a user delegation key is account-wide; what a link may read is still limited by its signature
  name: guid(storage.id, identity.id, roles.blobDelegator)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobDelegator)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// --- Foundry IQ (#18): the league rulebook as a knowledge base, keyless both ways ---------------------
// The search service plans queries with our model through its own identity; the app retrieves with its
// own. Basic is the lowest tier that can use a managed identity.

resource search 'Microsoft.Search/searchServices@2025-05-01' = {
  name: 'srch-watchparty-${uniqueString(resourceGroup().id)}'
  location: searchLocation
  tags: tags
  sku: { name: 'basic' }
  identity: { type: 'SystemAssigned' }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'Default'
    disableLocalAuth: true // Entra ID only: no admin or query keys
    semanticSearch: 'standard' // agentic retrieval reranks with it
    publicNetworkAccess: 'enabled'
  }
}

resource searchCanPlanWithModels 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: foundry
  name: guid(foundry.id, search.id, roles.cognitiveServicesUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.cognitiveServicesUser)
    principalId: search.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource appCanRetrieve 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: search
  name: guid(search.id, identity.id, roles.searchIndexDataReader)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.searchIndexDataReader)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource adminCanManageKnowledge 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(knowledgeAdminId)) {
  scope: search
  name: guid(search.id, knowledgeAdminId, roles.searchServiceContributor)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.searchServiceContributor)
    principalId: knowledgeAdminId
    principalType: 'User'
  }
}

resource adminCanLoadKnowledge 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(knowledgeAdminId)) {
  scope: search
  name: guid(search.id, knowledgeAdminId, roles.searchIndexDataContributor)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.searchIndexDataContributor)
    principalId: knowledgeAdminId
    principalType: 'User'
  }
}

// --- shared state (#97): Redis for live matches across replicas, PostgreSQL for records -------------------
// Both are off by default, so a deploy with no new parameters changes nothing, and switching one on creates the
// service without touching the app: its settings (wireRedis, wirePostgres) and replicas (appReplicas) are
// separate. Both are keyless: the app signs in as its managed identity and sends an Entra token as the password;
// neither service has a key or password that could appear in a setting or an output.

var redisPort = 10000 // the Azure Managed Redis database endpoint

resource redis 'Microsoft.Cache/redisEnterprise@2025-07-01' = if (enableRedis) {
  name: 'redis-watchparty-${uniqueString(resourceGroup().id)}'
  location: location
  tags: tags
  sku: { name: 'Balanced_B0' } // the smallest size
  properties: {
    highAvailability: 'Disabled' // one node, no replica, no SLA; a lost node loses live matches only
    minimumTlsVersion: '1.2'
    // Reachable from the internet, with no IP firewall: Entra sign-in is the only control. Container Apps on
    // Consumption has no fixed outbound address to allow, and private access needs a VNet.
    publicNetworkAccess: 'Enabled'
  }
}

resource redisDatabase 'Microsoft.Cache/redisEnterprise/databases@2025-07-01' = if (enableRedis) {
  parent: redis
  name: 'default' // the only name the service accepts
  properties: {
    accessKeysAuthentication: 'Disabled' // Entra ID only: no access keys
    clientProtocol: 'Encrypted'
    clusteringPolicy: 'EnterpriseCluster' // one endpoint, so a client that is not cluster-aware works
    evictionPolicy: 'NoEviction' // a full cache refuses writes rather than silently dropping a live match
    port: redisPort
  }
}

resource appCanUseRedis 'Microsoft.Cache/redisEnterprise/databases/accessPolicyAssignments@2025-07-01' = if (enableRedis) {
  parent: redisDatabase
  name: 'app' // letters and digits only
  properties: {
    accessPolicyName: 'default' // the only policy the service supports: all commands and keys
    user: { objectId: identity.properties.principalId } // the token's object id is the user name; the URL has none
  }
}

// A person, for the checks run from a laptop (#144, #180). After the app's: the database takes one change at a time.
resource personCanUseRedis 'Microsoft.Cache/redisEnterprise/databases/accessPolicyAssignments@2025-07-01' = if (enableRedis && !empty(redisUserId)) {
  parent: redisDatabase
  name: 'person'
  properties: {
    accessPolicyName: 'default'
    user: { objectId: redisUserId }
  }
  dependsOn: [appCanUseRedis]
}

// A server in another region gets that region in its name: a create that failed for capacity keeps its name
// reserved in the first region for a while, and a name cannot move region (#191).
var postgresName = postgresLocation == location ? 'psql-watchparty-${uniqueString(resourceGroup().id)}' : 'psql-watchparty-${postgresLocation}-${uniqueString(resourceGroup().id)}'

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2025-08-01' = if (enablePostgres) {
  name: postgresName
  location: postgresLocation
  tags: tags
  sku: { name: postgresSku, tier: postgresTier } // Standard_B1ms by default: 1 vCore, 2 GiB, the smallest size
  properties: {
    version: '16'
    createMode: 'Create'
    authConfig: {
      activeDirectoryAuth: 'Enabled'
      passwordAuth: 'Disabled' // so no administrator login or password exists
      tenantId: tenant().tenantId
    }
    storage: { storageSizeGB: 32, autoGrow: 'Disabled' } // the smallest disk; it cannot shrink later
    backup: { backupRetentionDays: 7, geoRedundantBackup: 'Disabled' }
    highAvailability: { mode: 'Disabled' }
    network: { publicNetworkAccess: 'Enabled' }
  }
}

// The app's identity is the Entra administrator, the one role ARM can create; a narrower role needs SQL
// (pgaadauth_create_principal) run by an administrator. Its database role name is the identity's name.
// A module, because the administrator's name is the identity's object id, known only once it exists.
module appIsPostgresAdmin 'postgres-admin.bicep' = if (enablePostgres) {
  name: 'watchparty-postgres-admin'
  params: {
    serverName: postgres.name
    principalId: identity.properties.principalId
    principalName: identity.name
  }
}

// The server takes one administrator change at a time, so the person follows the app's identity.
resource personIsPostgresAdmin 'Microsoft.DBforPostgreSQL/flexibleServers/administrators@2025-08-01' = if (enablePostgres && !empty(postgresAdminId)) {
  parent: postgres
  name: postgresAdminId
  properties: {
    principalName: postgresAdminName
    principalType: 'User'
    tenantId: tenant().tenantId
  }
  dependsOn: [appIsPostgresAdmin]
}

// 0.0.0.0 admits every address inside Azure, any customer's tenant included, so the network is not the guard:
// Entra-only sign-in is. The app's outbound addresses on Consumption are a shared pool of about 170 that can
// change, so listing them would admit no less, and a VNet cannot be added to the existing environment.
resource postgresFromAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2025-08-01' = if (enablePostgres) {
  parent: postgres
  name: 'AllowAllAzureServicesAndResourcesWithinAzureIps'
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '0.0.0.0' }
  dependsOn: [appIsPostgresAdmin, personIsPostgresAdmin] // the server takes one change at a time
}

resource postgresDatabase 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2025-08-01' = if (enablePostgres) {
  parent: postgres
  name: 'watchparty'
  properties: { charset: 'UTF8', collation: 'en_US.utf8' }
  dependsOn: [postgresFromAzure]
}

// No password in either URL, and no user name in REDIS_URL: redis-py refuses a user name beside the credential
// provider (DataError on every connection), which takes the user name from the token. The app adds an Entra token for https://redis.azure.com and
// https://ossrdbms-aad.database.windows.net at connect time, and refreshes it before it expires. For PostgreSQL
// it does so only with DATABASE_AUTH=entra (store/db.py); without it the app sends no password and cannot start.
// Each URL has its own switch, wireRedis or wirePostgres: a new setting starts a new revision and restarts the one
// replica, ending every live match, so a URL reaches the app only in the release whose code reads it, and wiring
// one service never adds the other's.
var redisEnv = wireRedis && enableRedis
  ? [{ name: 'REDIS_URL', value: 'rediss://${redis!.properties.hostName}:${redisPort}' }]
  : []
var postgresEnv = wirePostgres && enablePostgres
  ? [
      {
        name: 'DATABASE_URL'
        value: 'postgresql://${identity.name}@${postgres!.properties.fullyQualifiedDomainName}:5432/${postgresDatabase!.name}?sslmode=require'
      }
      { name: 'DATABASE_AUTH', value: 'entra' }
    ]
  : []

// --- the app -----------------------------------------------------------------------------------------

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-watchparty'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: { destination: 'azure-monitor' } // sent on by the diagnostic setting: no workspace key
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
  }
}

// The free certificate for customDomain, checked over HTTP: an apex domain points at the environment's static IP
// with an A record. Created only on the second apply, once the host name is on the app (#66).
resource certificate 'Microsoft.App/managedEnvironments/managedCertificates@2024-03-01' = if (!empty(customDomain) && customDomainBound) {
  parent: environment
  name: 'mc-${replace(customDomain, '.', '-')}'
  location: location
  tags: tags
  properties: { subjectName: customDomain, domainControlValidation: 'HTTP' }
}

resource environmentLogs 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: environment
  name: 'to-log-analytics'
  properties: {
    workspaceId: logs.id
    logs: [{ categoryGroup: 'allLogs', enabled: true }]
  }
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-watchparty'
  location: location
  tags: tags
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identity.id}': {} } }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto' // HTTP/1.1 upgrades carry the WebSockets
        allowInsecure: false
        // null, not [], without a domain: an app with no custom domain is left as it was. An app that has one loses
        // it on an apply that does not pass it, as the ingress is replaced whole; scripts/apply-infra.sh passes it.
        customDomains: empty(customDomain)
          ? null
          : [
              customDomainBound
                ? { name: customDomain, bindingType: 'SniEnabled', certificateId: certificate.id }
                : { name: customDomain, bindingType: 'Disabled' }
            ]
      }
      registries: [{ server: registry.properties.loginServer, identity: identity.id }]
    }
    template: {
      containers: [
        {
          name: 'app'
          image: image
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: concat(
            [
              { name: 'FOUNDRY_ENDPOINT', value: 'https://${foundryName}.cognitiveservices.azure.com/' }
              { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId } // which managed identity to use
              { name: 'MANAGER_MODEL', value: managerModel }
              { name: 'HOST_MODEL', value: managerModel }
              { name: 'TRUST_PROXY', value: '1' } // the ingress appends the caller's address
              { name: 'RECAP_STORAGE_ACCOUNT', value: storage.name } // recaps at full time (#15)
              { name: 'FOUNDRY_IQ_ENDPOINT', value: 'https://${search.name}.search.windows.net' } // the rulebook (#18)
              { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString } // traces
            ],
            redisEnv, // shared match state (#97), only when wireRedis and Redis are on
            postgresEnv // durable records (#97), only when wirePostgres and PostgreSQL are on
          )
          // A replica's restart ends the matches it owns. Liveness gives a busy app 5 s an answer
          // and three misses before restarting it, not the 1 s default (#30).
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/health', port: 8000 }
              periodSeconds: 30
              timeoutSeconds: 5
              failureThreshold: 3
            }
            {
              type: 'Readiness' // /ready also asks Redis when REDIS_URL is set (#95); liveness never does
              httpGet: { path: '/ready', port: 8000 }
              periodSeconds: 10
              timeoutSeconds: 5
              failureThreshold: 3
            }
          ]
        }
      ]
      // A fixed count, always warm. Without Redis it must be 1: each replica would hold its own matches and
      // limits. With REDIS_URL (#95) a match runs on the replica that created it and any replica serves its
      // screens, so appReplicas=2 goes with wireRedis (scripts/apply-infra.sh --wire sets both).
      scale: { minReplicas: appReplicas, maxReplicas: appReplicas }
    }
  }
  // Every role the app uses, so a fresh deploy never starts it before it may (#38)
  dependsOn: [
    canPull
    canUseModels
    canWriteRecaps
    canQueueRecaps
    canSignRecapLinks
    appCanRetrieve
    searchCanPlanWithModels
    appCanSendTraces
    appCanUseRedis // skipped when Redis is off
    appIsPostgresAdmin // skipped when PostgreSQL is off
  ]
}

// --- the recap job (#15): one run per message on the recaps queue; nothing runs, or costs, in between ---

resource recapIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-watchparty-recap'
  location: location
  tags: tags
}

resource recapCanPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, recapIdentity.id, roles.acrPull)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.acrPull)
    principalId: recapIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource recapCanUseBlobs 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: recaps
  name: guid(recaps.id, recapIdentity.id, roles.blobDataContributor)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobDataContributor)
    principalId: recapIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource recapCanUseQueue 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: recapQueue // read the length (the trigger), take a message, delete it
  name: guid(recapQueue.id, recapIdentity.id, roles.queueDataContributor)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.queueDataContributor)
    principalId: recapIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource recapCanSendTraces 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: appInsights
  name: guid(appInsights.id, recapIdentity.id, roles.monitoringMetricsPublisher)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.monitoringMetricsPublisher)
    principalId: recapIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource recapJob 'Microsoft.App/jobs@2025-01-01' = {
  name: 'caj-watchparty-recap'
  location: location
  tags: tags
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${recapIdentity.id}': {} } }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Event'
      replicaTimeout: 240 // as the queue message's visibility (worker.py): a hung run stops as its retry can start (#29)
      replicaRetryLimit: 1
      eventTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
        scale: {
          minExecutions: 0
          maxExecutions: 5
          pollingInterval: 5 // seconds: the recap should start soon after full time
          rules: [
            {
              name: 'recaps-queue'
              type: 'azure-queue'
              metadata: { accountName: storage.name, queueName: recapQueue.name, queueLength: '1' }
              identity: recapIdentity.id // keyless trigger
            }
          ]
        }
      }
      registries: [{ server: registry.properties.loginServer, identity: recapIdentity.id }]
    }
    template: {
      containers: [
        {
          name: 'recap'
          image: recapImage
          resources: { cpu: json('4'), memory: '8Gi' } // the five render in parallel; billed only while running
          env: [
            { name: 'RECAP_STORAGE_ACCOUNT', value: storage.name }
            { name: 'AZURE_CLIENT_ID', value: recapIdentity.properties.clientId }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString } // traces
          ]
        }
      ]
    }
  }
  dependsOn: [recapCanPull, recapCanUseBlobs, recapCanUseQueue, recapCanSendTraces]
}

// --- CI deploys as its own identity: GitHub OIDC tokens from main only, no secrets anywhere -----------
// It may push images and update this one app; it cannot assign roles or touch Foundry or storage.

resource deployer 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-watchparty-deploy'
  location: location
  tags: tags
}

resource fromGitHub 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = {
  parent: deployer
  name: 'github-main'
  properties: {
    issuer: 'https://token.actions.githubusercontent.com'
    subject: githubSubject
    audiences: ['api://AzureADTokenExchange']
  }
}

resource deployerCanPush 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, deployer.id, roles.acrPush)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.acrPush)
    principalId: deployer.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource deployerCanUpdateApp 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: app
  name: guid(app.id, deployer.id, roles.contributor)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.contributor)
    principalId: deployer.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource deployerCanUpdateRecapJob 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: recapJob
  name: guid(recapJob.id, deployer.id, roles.contributor)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.contributor)
    principalId: deployer.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// A new revision only needs to join the environment. Contributor here would also let a pushed workflow change
// the environment's log destination, storage mounts and Dapr components (#50).
resource joinEnvironment 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(resourceGroup().id, 'watchparty-join-environment')
  properties: {
    roleName: 'Watch party: join the Container Apps environment'
    description: 'Read the environment and place a container app revision in it. Nothing else.'
    type: 'CustomRole'
    permissions: [
      { actions: ['Microsoft.App/managedEnvironments/read', 'Microsoft.App/managedEnvironments/join/action'] }
    ]
    assignableScopes: [resourceGroup().id]
  }
}

resource deployerCanUseEnvironment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: environment
  name: guid(environment.id, deployer.id, 'join')
  properties: {
    roleDefinitionId: joinEnvironment.id
    principalId: deployer.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// --- budget: alerts at half, most and all of a small monthly amount ----------------------------------

resource budget 'Microsoft.Consumption/budgets@2023-11-01' = {
  name: 'budget-watchparty'
  properties: {
    category: 'Cost'
    amount: budgetAmount
    timeGrain: 'Monthly'
    timePeriod: { startDate: budgetStart, endDate: '2026-12-31' }
    notifications: {
      half: { enabled: true, operator: 'GreaterThan', threshold: 50, contactEmails: alertEmails }
      most: { enabled: true, operator: 'GreaterThan', threshold: 80, contactEmails: alertEmails }
      forecast: { enabled: true, operator: 'GreaterThan', threshold: 100, thresholdType: 'Forecasted', contactEmails: alertEmails }
    }
  }
}

output url string = 'https://${app.properties.configuration.ingress.fqdn}'
output appName string = app.name
output registry string = registry.properties.loginServer
output storageAccount string = storage.name
output identityClientId string = identity.properties.clientId
output deployerClientId string = deployer.properties.clientId
output recapJobName string = recapJob.name
output searchEndpoint string = 'https://${search.name}.search.windows.net'
output redisHost string = enableRedis ? redis!.properties.hostName : ''
output postgresHost string = enablePostgres ? postgres!.properties.fullyQualifiedDomainName : ''
