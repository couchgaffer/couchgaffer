// The app's identity as the PostgreSQL server's Entra administrator (#97). A module only because ARM needs the
// administrator's name, the principal's object id, at the start of a deployment, and main.bicep learns the
// identity's object id only once the identity exists; a module parameter may take it at run time.

@description('The existing PostgreSQL flexible server.')
param serverName string

@description('Object id of the managed identity that becomes the administrator.')
param principalId string

@description('Its display name, which is also its database role name.')
param principalName string

resource server 'Microsoft.DBforPostgreSQL/flexibleServers@2025-08-01' existing = {
  name: serverName
}

resource admin 'Microsoft.DBforPostgreSQL/flexibleServers/administrators@2025-08-01' = {
  parent: server
  name: principalId
  properties: {
    principalName: principalName
    principalType: 'ServicePrincipal'
    tenantId: tenant().tenantId
  }
}
