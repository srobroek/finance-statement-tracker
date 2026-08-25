import type { ICredentialDataDecryptedObject, IExecuteFunctions, INodeExecutionData, INodeType, INodeTypeDescription } from 'n8n-workflow';
import { ActualSession } from '../../lib/actual-session';
import { ActualCredential, assertActualMutationMode } from '../../lib/contracts';

function credential(value: ICredentialDataDecryptedObject): ActualCredential {
  return {
    serverUrl: String(value.serverUrl ?? ''), password: String(value.password ?? ''), syncId: String(value.syncId ?? ''),
    ...(value.encryptionPassword ? { encryptionPassword: String(value.encryptionPassword) } : {}),
    mutationEnabled: value.mutationEnabled === true || value.mutationEnabled === 'true',
  };
}

export class ActualBudget implements INodeType {
  description: INodeTypeDescription = {
    displayName: 'Actual Budget', name: 'actualBudget', group: ['transform'], version: 1,
    description: 'Fixed-purpose direct Actual doctor, read, preflight, import, and verify operations',
    defaults: { name: 'Actual Budget' }, inputs: ['main'], outputs: ['main'],
    credentials: [{ name: 'actualBudgetApi', required: true }],
    properties: [
      { displayName: 'Operation', name: 'operation', type: 'options', noDataExpression: true, default: 'doctor', options: [
        { name: 'Doctor', value: 'doctor' }, { name: 'Read', value: 'read' }, { name: 'Preflight', value: 'preflight' },
        { name: 'Import', value: 'import' }, { name: 'Verify', value: 'verify' },
      ] },
      { displayName: 'Read Shape', name: 'readShape', type: 'options', noDataExpression: true, default: 'accounts', displayOptions: { show: { operation: ['read'] } }, options: [
        { name: 'Accounts', value: 'accounts' }, { name: 'Categories', value: 'categories' }, { name: 'Transactions by Imported IDs', value: 'transactionsByImportedIds' },
      ] },
    ],
  };

  async execute(this: IExecuteFunctions): Promise<INodeExecutionData[][]> {
    const items = this.getInputData();
    const output: INodeExecutionData[] = [];
    for (let index = 0; index < items.length; index += 1) {
      const operation = this.getNodeParameter('operation', index) as string;
      const auth = credential(await this.getCredentials('actualBudgetApi', index));
      const session = new ActualSession();
      let result;
      if (operation === 'doctor') result = await session.doctor(auth);
      else if (operation === 'read') {
        const shape = this.getNodeParameter('readShape', index) as 'accounts' | 'categories' | 'transactionsByImportedIds';
        const verification = items[index].json.verification;
        if (shape === 'transactionsByImportedIds' && (verification === null || typeof verification !== 'object' || Array.isArray(verification))) throw new Error('read transactionsByImportedIds requires json.verification');
        result = await session.read(auth, shape === 'transactionsByImportedIds' ? { shape, ...(verification as Record<string, unknown>) } as never : { shape });
      } else if (operation === 'preflight') result = await session.preflight(auth, items[index].json.outbox);
      else if (operation === 'import') {
        assertActualMutationMode(this.getMode());
        result = await session.import(auth, items[index].json.outbox);
      }
      else if (operation === 'verify') result = await session.verify(auth, items[index].json.verification);
      else throw new Error(`Unsupported Actual operation: ${operation}`);
      output.push({ json: { ...items[index].json, actual: result }, pairedItem: index });
    }
    return [output];
  }
}
