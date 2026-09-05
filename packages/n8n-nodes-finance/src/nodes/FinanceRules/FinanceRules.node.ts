import type { IExecuteFunctions, INodeExecutionData, INodeType, INodeTypeDescription } from 'n8n-workflow';
import { applyNonRepresentableRules, normalizeTransaction } from '../../lib/rules';
import { projectStatementToActual } from '../../lib/statements';
import { loadPackagedLedgerRules, PACKAGED_RULE_SOURCE_SHA256 } from '../../lib/runtime-rules';
import { assertObject } from '../../lib/contracts';

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
      if (!['normalize', 'projectActualImport', 'applyNonRepresentableRules'].includes(operation)) throw new Error('UNKNOWN_FINANCE_RULES_OPERATION');
      if (operation === 'projectActualImport') {
        const statement = items[index].json.statement ?? items[index].json;
        result.push({ json: { ...items[index].json, actual_transactions: projectStatementToActual(statement as never, items[index].json.actual as never, String(items[index].json.actual_file_id || '')) }, pairedItem: index });
        continue;
      }
      const source = items[index].json.transactions ?? items[index].json.transaction;
      const rows = Array.isArray(source) ? source : [source];
      if (rows.length === 0 || rows[0] === undefined) throw new Error('Finance Rules requires json.transaction or json.transactions');
      if (items[index].json.non_representable_rules !== undefined) throw new Error('UNTRUSTED_LEDGER_RULE_OVERRIDE_FORBIDDEN');
      const rules = operation === 'applyNonRepresentableRules' ? loadPackagedLedgerRules() : [];
      const transformed = rows.map(row => {
        assertObject(row, 'transaction');
        const normalized = normalizeTransaction({
          ...row,
          merchant_raw: row.merchant_raw ?? row.description,
          transaction_at: row.transaction_at ?? row.transaction_date,
          source_direction: row.source_direction ?? row.direction,
          currency: row.currency ?? row.currency_original ?? 'AED',
          card: row.card ?? items[index].json.card_code,
          account: row.account ?? items[index].json.account_id,
          account_last4: row.account_last4 ?? row.card_last4,
          source_type: row.source_type ?? 'statement',
          channel: row.channel ?? 'UNKNOWN',
        });
        return operation === 'normalize' ? normalized : applyNonRepresentableRules(normalized, rules);
      });
      result.push({ json: { ...items[index].json, transactions: transformed,
        ...(operation === 'applyNonRepresentableRules' ? { rules_source_sha256: PACKAGED_RULE_SOURCE_SHA256 } : {}) }, pairedItem: index });
    }
    return [result];
  }
}
