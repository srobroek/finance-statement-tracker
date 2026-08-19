import type { ICredentialType, INodeProperties } from 'n8n-workflow';

export class FinanceStatementPassword implements ICredentialType {
  name = 'financeStatementPassword';
  displayName = 'Issuer Statement Password';
  properties: INodeProperties[] = [
    { displayName: 'Password', name: 'password', type: 'string', typeOptions: { password: true }, default: '', required: true },
  ];
}
