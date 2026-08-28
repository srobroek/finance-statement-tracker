export const NODE_PACKAGE = 'n8n-nodes-finance@0.1.0' as const;
export const PDF_SOCKET_PATH = '/run/finance-pdf/pdf.sock' as const;
export const ACTUAL_DATA_DIR = '/home/node/.n8n/finance-actual-cache' as const;

export type JsonObject = Record<string, unknown>;

export interface ActualCredential {
  serverUrl: string;
  password: string;
  syncId: string;
  encryptionPassword?: string;
  mutationEnabled?: boolean;
}

export interface PreparedActualOutbox {
  schema_version: 1;
  outbox_id: string;
  state: 'PREPARED';
  account_id: string;
  execution_context: {
    trigger: 'SCHEDULE' | 'SUBWORKFLOW' | 'RECOVERY';
    manual: false;
    mcp: false;
  };
  writer_lease: {
    lease_id: string;
    fencing_token: number;
    expires_at: string;
  };
  transactions: ActualImportTransaction[];
}

export interface ActualImportTransaction {
  imported_id: string;
  date: string;
  amount: number;
  imported_payee: string;
  notes?: string;
  category?: string;
  cleared?: boolean;
}

export function assertActualImportTransactions(value: unknown, label = 'transactions'): ActualImportTransaction[] {
  if (!Array.isArray(value) || value.length === 0 || value.length > 5000) throw new Error(`${label} must contain 1..5000 rows`);
  const ids = new Set<string>();
  return value.map((row, index) => {
    assertObject(row, `${label}[${index}]`);
    const importedId = requiredString(row.imported_id, `${label}[${index}].imported_id`, 256);
    if (ids.has(importedId)) throw new Error(`duplicate imported_id in ${label}: ${importedId}`);
    ids.add(importedId);
    const amount = row.amount;
    if (!Number.isSafeInteger(amount)) throw new Error(`${label}[${index}].amount must be integer minor units`);
    const cleared = row.cleared;
    if (cleared !== undefined && typeof cleared !== 'boolean') {
      throw new Error(`${label}[${index}].cleared must be a boolean`);
    }
    return {
      imported_id: importedId,
      date: assertIsoDate(row.date, `${label}[${index}].date`),
      amount: Number(amount),
      imported_payee: requiredString(row.imported_payee, `${label}[${index}].imported_payee`, 512),
      ...(row.notes === undefined ? {} : { notes: requiredString(row.notes, `${label}[${index}].notes`, 4000) }),
      ...(row.category === undefined ? {} : { category: requiredString(row.category, `${label}[${index}].category`, 128) }),
      ...(cleared === undefined ? {} : { cleared }),
    };
  });
}

export function assertObject(value: unknown, label: string): asserts value is JsonObject {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
}

export function requiredString(value: unknown, label: string, max = 512): string {
  if (typeof value !== 'string' || value.trim() === '' || value.length > max) {
    throw new Error(`${label} must be a non-empty string no longer than ${max}`);
  }
  return value;
}

export function assertIsoDate(value: unknown, label: string): string {
  const result = requiredString(value, label, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(result) || Number.isNaN(Date.parse(`${result}T00:00:00Z`))) {
    throw new Error(`${label} must be YYYY-MM-DD`);
  }
  return result;
}

export function assertActualMutationMode(mode: string): void {
  if (!['trigger', 'integrated', 'retry'].includes(mode)) {
    throw new Error(`Actual mutation is forbidden in n8n execution mode: ${mode}`);
  }
}

export function assertPreparedOutbox(value: unknown): PreparedActualOutbox {
  assertObject(value, 'outbox');
  if (value.schema_version !== 1 || value.state !== 'PREPARED') throw new Error('outbox must be schema v1 in PREPARED state');
  const outboxId = requiredString(value.outbox_id, 'outbox.outbox_id', 128);
  const accountId = requiredString(value.account_id, 'outbox.account_id', 128);
  assertObject(value.execution_context, 'outbox.execution_context');
  const context = value.execution_context;
  if (!['SCHEDULE', 'SUBWORKFLOW', 'RECOVERY'].includes(String(context.trigger)) || context.manual !== false || context.mcp !== false) {
    throw new Error('Actual import is forbidden for manual or MCP execution contexts');
  }
  assertObject(value.writer_lease, 'outbox.writer_lease');
  const leaseId = requiredString(value.writer_lease.lease_id, 'outbox.writer_lease.lease_id', 128);
  const fencingToken = value.writer_lease.fencing_token;
  const expiresAt = requiredString(value.writer_lease.expires_at, 'outbox.writer_lease.expires_at', 64);
  if (!Number.isSafeInteger(fencingToken) || Number(fencingToken) <= 0) throw new Error('writer lease fencing token must be a positive integer');
  const expiry = Date.parse(expiresAt);
  if (!Number.isFinite(expiry) || expiry <= Date.now()) throw new Error('writer lease is expired or invalid');
  const transactions = assertActualImportTransactions(value.transactions, 'outbox.transactions');
  return {
    schema_version: 1,
    outbox_id: outboxId,
    state: 'PREPARED',
    account_id: accountId,
    execution_context: context as PreparedActualOutbox['execution_context'],
    writer_lease: { lease_id: leaseId, fencing_token: Number(fencingToken), expires_at: expiresAt },
    transactions,
  };
}
