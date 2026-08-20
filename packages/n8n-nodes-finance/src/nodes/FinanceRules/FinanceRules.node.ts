import type { IExecuteFunctions, INodeExecutionData, INodeType, INodeTypeDescription } from 'n8n-workflow';
import { applyNonRepresentableRules, normalizeTransaction } from '../../lib/rules';
import { projectStatementToActual } from '../../lib/statements';

export class FinanceRules implements INodeType {
  description: INodeTypeDescription = {
    displayName: 'Finance Rules', name: 'financeRules', group: ['transform'], version: 1,
    description: 'Run deterministic normalization or explicitly N8N_ONLY rules',
    defaults: { name: 'Finance Rules' }, inputs: ['main'], outputs: ['main'],
    properties: [{ displayName: 'Operation', name: 'operation', type: 'options', noDataExpression: true, default: 'normalize', options: [
      { name: 'Normalize', value: 'normalize' }, { name: 'Project Actual Import', value: 'projectActualImport' }, { name: 'Apply Non-Representable Rules', value: 'applyNonRepresentableRules' },
    ] }],
  };

  async execute(this: IExecuteFunctions): Promise<INodeExecutionData[][]> {
    const items = this.getInputData();
    const result: INodeExecutionData[] = [];
    for (let index = 0; index < items.length; index += 1) {
      const operation = this.getNodeParameter('operation', index) as string;
      if (operation === 'projectActualImport') {
        const statement = items[index].json.statement ?? items[index].json;
        result.push({ json: { ...items[index].json, actual_transactions: projectStatementToActual(statement as never) }, pairedItem: index });
        continue;
      }
      const source = items[index].json.transactions ?? items[index].json.transaction;
      const rows = Array.isArray(source) ? source : [source];
      if (rows.length === 0 || rows[0] === undefined) throw new Error('Finance Rules requires json.transaction or json.transactions');
      const rules = operation === 'applyNonRepresentableRules' ? items[index].json.non_representable_rules : [];
      if (!Array.isArray(rules)) throw new Error('non_representable_rules must be an array');
      const transformed = rows.map(row => operation === 'normalize' ? normalizeTransaction(row) : applyNonRepresentableRules(row, rules));
      result.push({ json: { ...items[index].json, transactions: transformed }, pairedItem: index });
    }
    return [result];
  }
}
