import type { IExecuteFunctions } from 'n8n-workflow';
import type { INodeExecutionData, INodeType, INodeTypeDescription } from 'n8n-workflow';
import { detectAndParseStatement } from '../../lib/statements';

export class FinanceStatement implements INodeType {
  description: INodeTypeDescription = {
    displayName: 'Finance Statement', name: 'financeStatement', group: ['transform'], version: 1,
    description: 'Parse extracted text with a uniquely detected, verified issuer profile',
    defaults: { name: 'Finance Statement' }, inputs: ['main'], outputs: ['main'],
    properties: [{ displayName: 'Operation', name: 'operation', type: 'options', noDataExpression: true, default: 'parse', options: [{ name: 'Parse', value: 'parse' }] }],
  };

  async execute(this: IExecuteFunctions): Promise<INodeExecutionData[][]> {
    const output: INodeExecutionData[] = [];
    const items = this.getInputData();
    for (let index = 0; index < items.length; index += 1) {
      const text = items[index].json.extracted_text;
      if (typeof text !== 'string') throw new Error('Finance Statement requires json.extracted_text');
      const sourceFile = typeof items[index].json.source_file === 'string' ? String(items[index].json.source_file) : '';
      output.push({ json: detectAndParseStatement(text, sourceFile) as unknown as INodeExecutionData['json'], pairedItem: index });
    }
    return [output];
  }
}
