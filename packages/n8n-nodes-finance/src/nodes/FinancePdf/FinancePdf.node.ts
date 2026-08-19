import type { IExecuteFunctions, INodeExecutionData, INodeType, INodeTypeDescription } from 'n8n-workflow';
import { callPdfUtility, PdfOperation } from '../../lib/pdf-client';

export class FinancePdf implements INodeType {
  description: INodeTypeDescription = {
    displayName: 'Finance PDF', name: 'financePdf', group: ['transform'], version: 1,
    description: 'Validate, unlock, or profile a PDF through the networkless utility socket',
    defaults: { name: 'Finance PDF' }, inputs: ['main'], outputs: ['main'],
    credentials: [{ name: 'financeStatementPassword', required: true, displayOptions: { show: { operation: ['unlock'] } } }],
    properties: [{ displayName: 'Operation', name: 'operation', type: 'options', noDataExpression: true, default: 'validate', options: [
      { name: 'Validate', value: 'validate' }, { name: 'Unlock', value: 'unlock' }, { name: 'Profile', value: 'profile' },
    ] }],
  };

  async execute(this: IExecuteFunctions): Promise<INodeExecutionData[][]> {
    const items = this.getInputData();
    const output: INodeExecutionData[] = [];
    for (let index = 0; index < items.length; index += 1) {
      const operation = this.getNodeParameter('operation', index) as PdfOperation;
      const pdf = await this.helpers.getBinaryDataBuffer(index, 'data');
      const credential = operation === 'unlock' ? await this.getCredentials('financeStatementPassword', index) : undefined;
      const response = await callPdfUtility(operation, pdf, credential ? String(credential.password) : undefined);
      if (operation === 'unlock') {
        const unlocked = await this.helpers.prepareBinaryData(response.body, 'unlocked.pdf', 'application/pdf');
        output.push({
          json: { ...items[index].json, pdf_status: 'unlocked', source_sha256: response.headers['x-source-sha256'] ?? null },
          binary: { ...items[index].binary, data: unlocked, extractable: unlocked }, pairedItem: index,
        });
      } else {
        const metadata = JSON.parse(response.body.toString('utf8')) as Record<string, unknown>;
        const extractedText = metadata.extracted_text;
        if (operation === 'profile' && typeof extractedText !== 'string') {
          throw new Error('PDF utility profile response is missing extracted_text');
        }
        delete metadata.extracted_text;
        output.push({ json: { ...items[index].json, pdf: metadata, ...(operation === 'profile' ? { extracted_text: extractedText as string } : {}) }, binary: items[index].binary, pairedItem: index });
      }
    }
    return [output];
  }
}
