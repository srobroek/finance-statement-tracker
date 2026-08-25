import type { ICredentialType, INodeProperties } from 'n8n-workflow';

export class ActualBudgetApi implements ICredentialType {
  name = 'actualBudgetApi';
  displayName = 'Finance Actual';
  documentationUrl = 'https://actualbudget.org/docs/api/';
  properties: INodeProperties[] = [
    { displayName: 'Server Origin', name: 'serverUrl', type: 'string', default: '', placeholder: 'http://actual:5006', required: true },
    { displayName: 'Server Password', name: 'password', type: 'string', typeOptions: { password: true }, default: '', required: true },
    { displayName: 'Sync ID', name: 'syncId', type: 'string', typeOptions: { password: true }, default: '', required: true },
    { displayName: 'Budget Encryption Password', name: 'encryptionPassword', type: 'string', typeOptions: { password: true }, default: '' },
    { displayName: 'Enable Mutation', name: 'mutationEnabled', type: 'boolean', default: false, description: 'Must remain disabled outside the fenced writer credential' },
  ];
}
