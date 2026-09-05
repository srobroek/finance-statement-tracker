import { createHash } from 'node:crypto';
import { ActualImportTransaction, assertActualImportTransactions, assertObject, requiredString } from './contracts';
export type Row = Record<string, unknown>;
export type MaintenanceLease = {
    resource_key: string;
    lease_id: string;
    fencing_token: number;
    expires_at: string;
};
const BROWSER = 'browser:adcb-personal-internet-banking:';
const fields = ['id', 'account', 'imported_id', 'date', 'amount', 'imported_payee', 'payee', 'category', 'notes', 'cleared', 'reconciled', 'transfer_id', 'is_parent', 'is_child', 'parent_id', 'starting_balance_flag'] as const;
export function stableHash(value: unknown): string {
    const stable = (v: unknown): unknown => Array.isArray(v) ? v.map(stable) : v && typeof v === 'object' ? Object.fromEntries(Object.entries(v).sort(([a], [b]) => a.localeCompare(b)).map(([k, x]) => [k, stable(x)])) : v;
    return createHash('sha256').update(JSON.stringify(stable(value))).digest('hex');
}
export const rowHash = (row: Row): string => stableHash(Object.fromEntries(fields.map(k => [k, row[k] ?? null])));
export const rowsHash = (rows: Row[]): string => stableHash(rows.map(r => [String(r.id), rowHash(r)]).sort(([a], [b]) => a.localeCompare(b)));
const hash = (v: unknown, label: string): string => { const s = requiredString(v, label, 64); if (!/^[a-f0-9]{64}$/.test(s))
    throw Error(`${label} must be SHA256`); return s; };
const integer = (v: unknown, label: string): number => { if (!Number.isSafeInteger(v))
    throw Error(`${label} must be integer`); return v as number; };
const text = (v: unknown, label: string) => requiredString(v, label, 256);
const sameEconomics = (r: Row, s: ActualImportTransaction, a: string) => r.account === a && r.date === s.date && r.amount === s.amount;
function noLinks(row: Row): void { if (row.transfer_id || row.is_parent || row.is_child || row.parent_id || row.reconciled || row.starting_balance_flag)
    throw Error('MAINTENANCE_LINKED_OR_RECONCILED_ROW'); }
export interface SourceDocument {
    sha256: string;
    archive_receipt_sha256: string;
    statement_date: string;
    opening_minor: number;
    closing_minor: number;
    transactions: ActualImportTransaction[];
}
export interface MaintenanceRequest {
    schema_version: 1;
    account_id: string;
    actual_file_id: string;
    source_documents: SourceDocument[];
    aliases: Array<{
        transaction_id: string;
        source_imported_id: string;
        proof_sha256: string;
    }>;
    deletions: Array<{
        transaction_id: string;
        kind: 'DUPLICATE_BROWSER_PROJECTION' | 'OBSOLETE_RECONCILIATION';
        source_imported_ids: string[];
        proof_sha256: string;
    }>;
    backup: {
        receipt_sha256: string;
        restore_reference: string;
    };
    expected_before: {
        rows_sha256: string;
        count: number;
        balance: number;
    };
    expected_after: {
        count: number;
        balance: number;
    };
}
export interface MaintenancePlan {
    schema_version: 'actual-adcb-reconstruction-v1';
    expected_server_version: '26.8.1';
    actual_file_id: string;
    account_id: string;
    backup: MaintenanceRequest['backup'];
    source_documents: Array<Omit<SourceDocument, 'transactions'> & {
        transaction_ids: string[];
    }>;
    sources: ActualImportTransaction[];
    preserved: Array<{
        id: string;
        imported_id: string;
        sha256: string;
    }>;
    aliases: Array<{
        id: string;
        old_imported_id: string;
        source_imported_id: string;
        before_sha256: string;
        after_sha256: string;
        proof_sha256: string;
    }>;
    deletions: Array<{
        id: string;
        imported_id: string;
        before_sha256: string;
        kind: string;
        source_imported_ids: string[];
        proof_sha256: string;
    }>;
    additions: string[];
    before: MaintenanceRequest['expected_before'];
    after: MaintenanceRequest['expected_after'];
    manual_conflict_policy: 'PRESERVE_CANONICAL_KEEP_LEGACY_IN_VERIFIED_BACKUP';
    plan_sha256: string;
}
export function buildMaintenancePlan(input: unknown, account: Row, rows: Row[], syncId: string): MaintenancePlan {
    assertObject(input, 'maintenance_request');
    const r = input as unknown as MaintenanceRequest;
    if (r.schema_version !== 1 || text(r.actual_file_id, 'actual_file_id') !== syncId)
        throw Error('MAINTENANCE_SYNC_BINDING');
    const accountId = text(r.account_id, 'account_id');
    if (account.id !== accountId || account.closed !== true || account.offbudget === true)
        throw Error('MAINTENANCE_CLOSED_ACCOUNT_REQUIRED');
    assertObject(r.backup, 'backup');
    hash(r.backup.receipt_sha256, 'backup.receipt_sha256');
    text(r.backup.restore_reference, 'restore_reference');
    assertObject(r.expected_before, 'expected_before');
    assertObject(r.expected_after, 'expected_after');
    if (rows.some(x => x.account !== accountId))
        throw Error('MAINTENANCE_ACCOUNT_DRIFT');
    if (new Set(rows.map(x => x.id)).size !== rows.length)
        throw Error('MAINTENANCE_DUPLICATE_ACTUAL_IDS');
    const sum = rows.reduce((n, x) => n + integer(x.amount, 'row.amount'), 0);
    if (integer(r.expected_before.count, 'before.count') !== rows.length || integer(r.expected_before.balance, 'before.balance') !== sum || hash(r.expected_before.rows_sha256, 'before.rows_sha256') !== rowsHash(rows))
        throw Error('MAINTENANCE_BEFORE_STATE_DRIFT');
    if (!Array.isArray(r.source_documents) || !r.source_documents.length || r.source_documents.length > 60)
        throw Error('MAINTENANCE_SOURCE_DOCUMENTS_REQUIRED');
    const documents = [...r.source_documents].sort((a, b) => String(a.statement_date).localeCompare(String(b.statement_date)));
    const sourceRows: ActualImportTransaction[] = [];
    const docHashes = new Set<string>();
    let previous: SourceDocument | undefined;
    for (const d of documents) {
        hash(d.sha256, 'source.sha256');
        hash(d.archive_receipt_sha256, 'archive_receipt_sha256');
        if (docHashes.has(d.sha256))
            throw Error('MAINTENANCE_DUPLICATE_SOURCE');
        docHashes.add(d.sha256);
        if (!/^\d{4}-\d{2}-\d{2}$/.test(d.statement_date) || !Number.isFinite(Date.parse(d.statement_date)))
            throw Error('MAINTENANCE_STATEMENT_DATE');
        integer(d.opening_minor, 'opening_minor');
        integer(d.closing_minor, 'closing_minor');
        if (previous) {
            const a = new Date(previous.statement_date), b = new Date(d.statement_date);
            if (b.getUTCFullYear() * 12 + b.getUTCMonth() !== a.getUTCFullYear() * 12 + a.getUTCMonth() + 1 || d.opening_minor !== previous.closing_minor)
                throw Error('MAINTENANCE_SOURCE_CONTINUITY');
        }
        else if (d.opening_minor !== 0)
            throw Error('MAINTENANCE_SOURCE_OPENING_NOT_ZERO');
        const tx = assertActualImportTransactions(d.transactions);
        if (tx.some(t => !/^statement:adcb_v1:[a-f0-9]{24}$/.test(t.imported_id) || t.cleared !== true || t.date > d.statement_date))
            throw Error('MAINTENANCE_SOURCE_ROW_INVALID');
        if (d.opening_minor - tx.reduce((n, t) => n + t.amount, 0) !== d.closing_minor)
            throw Error('MAINTENANCE_SOURCE_ARITHMETIC');
        sourceRows.push(...tx);
        previous = d;
    }
    if (sourceRows.length > 5000 || new Set(sourceRows.map(x => x.imported_id)).size !== sourceRows.length)
        throw Error('MAINTENANCE_SOURCE_ID_COLLISION');
    if (integer(r.expected_after.count, 'after.count') !== sourceRows.length || integer(r.expected_after.balance, 'after.balance') !== sourceRows.reduce((n, t) => n + t.amount, 0) || r.expected_after.balance !== -documents.at(-1)!.closing_minor)
        throw Error('MAINTENANCE_TARGET_MISMATCH');
    if (!Array.isArray(r.aliases) || !Array.isArray(r.deletions))
        throw Error('MAINTENANCE_ACTION_ARRAYS_REQUIRED');
    const sources = new Map(sourceRows.map(x => [x.imported_id, x]));
    const byId = new Map(rows.map(x => [String(x.id), x]));
    const claimed = new Set<string>();
    const represented = new Set<string>();
    const claim = (id: string) => { if (claimed.has(id))
        throw Error('MAINTENANCE_DUPLICATE_ACTION'); claimed.add(id); const row = byId.get(id); if (!row)
        throw Error('MAINTENANCE_ACTION_ROW_MISSING'); return row; };
    const preserved: MaintenancePlan['preserved'] = [];
    for (const row of rows) {
        const source = sources.get(String(row.imported_id));
        if (!source)
            continue;
        claim(String(row.id));
        if (represented.has(source.imported_id) || !sameEconomics(row, source, accountId) || row.imported_payee !== source.imported_payee)
            throw Error('MAINTENANCE_CANONICAL_DRIFT');
        represented.add(source.imported_id);
        preserved.push({ id: String(row.id), imported_id: source.imported_id, sha256: rowHash(row) });
    }
    const aliases: MaintenancePlan['aliases'] = r.aliases.map(a => {
        const row = claim(text(a.transaction_id, 'alias.transaction_id'));
        const source = sources.get(text(a.source_imported_id, 'alias.source_imported_id'));
        hash(a.proof_sha256, 'alias.proof_sha256');
        if (!String(row.imported_id).startsWith(BROWSER) || !source || represented.has(source.imported_id) || !sameEconomics(row, source, accountId))
            throw Error('MAINTENANCE_ALIAS_PROOF_MISMATCH');
        noLinks(row);
        represented.add(source.imported_id);
        return { id: String(row.id), old_imported_id: String(row.imported_id), source_imported_id: source.imported_id, before_sha256: rowHash(row), after_sha256: rowHash({ ...row, imported_id: source.imported_id, imported_payee: source.imported_payee }), proof_sha256: a.proof_sha256 };
    });
    let reconcile = 0;
    const deletions: MaintenancePlan['deletions'] = r.deletions.map(d => {
        const row = claim(text(d.transaction_id, 'deletion.transaction_id'));
        hash(d.proof_sha256, 'deletion.proof_sha256');
        noLinks(row);
        if (d.kind === 'DUPLICATE_BROWSER_PROJECTION') {
            if (!String(row.imported_id).startsWith(BROWSER) || !Array.isArray(d.source_imported_ids) || !d.source_imported_ids.length || new Set(d.source_imported_ids).size !== d.source_imported_ids.length)
                throw Error('MAINTENANCE_DUPLICATE_PROOF_REQUIRED');
            for (const id of d.source_imported_ids) {
                const source = sources.get(id);
                if (!source || !represented.has(id) || !sameEconomics(row, source, accountId))
                    throw Error('MAINTENANCE_DUPLICATE_SOURCE_MISMATCH');
            }
        }
        else if (d.kind === 'OBSOLETE_RECONCILIATION') {
            if (++reconcile > 1 || !String(row.imported_id).startsWith('reconcile:adcb:') || d.source_imported_ids.length !== 0)
                throw Error('MAINTENANCE_RECONCILIATION_SCOPE');
        }
        else
            throw Error('MAINTENANCE_UNKNOWN_DELETION_KIND');
        return { id: String(row.id), imported_id: String(row.imported_id), before_sha256: rowHash(row), kind: d.kind, source_imported_ids: d.source_imported_ids, proof_sha256: d.proof_sha256 };
    });
    if (claimed.size !== rows.length)
        throw Error('MAINTENANCE_UNEXPLAINED_EXISTING_ROWS');
    const plan: Omit<MaintenancePlan, 'plan_sha256'> = { schema_version: 'actual-adcb-reconstruction-v1', expected_server_version: '26.8.1', actual_file_id: syncId, account_id: accountId, backup: r.backup,
        source_documents: documents.map(({ transactions, ...d }) => ({ ...d, transaction_ids: transactions.map(x => x.imported_id) })), sources: sourceRows, preserved, aliases, deletions,
        additions: sourceRows.filter(x => !represented.has(x.imported_id)).map(x => x.imported_id), before: r.expected_before, after: r.expected_after,
        manual_conflict_policy: 'PRESERVE_CANONICAL_KEEP_LEGACY_IN_VERIFIED_BACKUP' };
    return { ...plan, plan_sha256: stableHash(plan) };
}
export function validateMaintenancePlan(value: unknown, approvedHash: unknown): MaintenancePlan {
    assertObject(value, 'maintenance_plan');
    const p = value as unknown as MaintenancePlan;
    const { plan_sha256, ...unsigned } = p;
    if (p.schema_version !== 'actual-adcb-reconstruction-v1' || p.expected_server_version !== '26.8.1' || hash(approvedHash, 'approved_plan_sha256') !== hash(plan_sha256, 'plan_sha256') || stableHash(unsigned) !== plan_sha256)
        throw Error('MAINTENANCE_PLAN_HASH_MISMATCH');
    // Approval is over the complete machine-generated plan, never a caller-chosen subset.
    if (p.manual_conflict_policy !== 'PRESERVE_CANONICAL_KEEP_LEGACY_IN_VERIFIED_BACKUP')
        throw Error('MAINTENANCE_POLICY_INVALID');
    assertActualImportTransactions(p.sources);
    for (const a of ['preserved', 'aliases', 'deletions', 'additions', 'source_documents'] as const)
        if (!Array.isArray(p[a]))
            throw Error('MAINTENANCE_PLAN_ARRAY_INVALID');
    return p;
}
export function assertMaintenanceLease(lease: MaintenanceLease, syncId: string): void {
    if (!lease || lease.resource_key !== `actual:${syncId}` || !lease.lease_id || !Number.isSafeInteger(lease.fencing_token) || lease.fencing_token <= 0 || !Number.isFinite(Date.parse(lease.expires_at)) || Date.parse(lease.expires_at) - Date.now() < 30000)
        throw Error('MAINTENANCE_WRITER_LEASE_INVALID_OR_NEAR_EXPIRY');
}
export interface MaintenanceApi {
    getAccounts(): Promise<Row[]>;
    getTransactions(account: string, start: string, end: string): Promise<Row[]>;
    getAccountBalance(account: string): Promise<number>;
    updateTransaction?(id: string, fields: Row): Promise<unknown>;
    deleteTransaction?(id: string): Promise<unknown>;
    importTransactions(account: string, rows: Row[], options: {
        defaultCleared: boolean;
        reimportDeleted: false;
        dryRun?: boolean;
    }): Promise<Row>;
    sync(): Promise<void>;
}
function inspectState(plan: MaintenancePlan, rows: Row[]): {
    aliases: MaintenancePlan['aliases'];
    deletions: MaintenancePlan['deletions'];
    additions: ActualImportTransaction[];
} {
    const byId = new Map(rows.map(r => [String(r.id), r]));
    const sources = new Map(plan.sources.map(s => [s.imported_id, s]));
    const ids = new Map<string, Row>();
    for (const r of rows) {
        if (r.account !== plan.account_id || ids.has(String(r.imported_id)))
            throw Error('MAINTENANCE_DUPLICATE_OR_FOREIGN_ROW');
        ids.set(String(r.imported_id), r);
    }
    const allowed = new Set<string>();
    const aliases: MaintenancePlan['aliases'] = [], deletions: MaintenancePlan['deletions'] = [];
    for (const p of plan.preserved) {
        const r = byId.get(p.id);
        if (!r || rowHash(r) !== p.sha256)
            throw Error('MAINTENANCE_PRESERVED_ROW_DRIFT');
        allowed.add(p.id);
    }
    for (const a of plan.aliases) {
        const r = byId.get(a.id);
        if (!r)
            throw Error('MAINTENANCE_ALIAS_ROW_DISAPPEARED');
        const h = rowHash(r);
        if (h === a.before_sha256)
            aliases.push(a);
        else if (h !== a.after_sha256)
            throw Error('MAINTENANCE_ALIAS_ROW_DRIFT');
        allowed.add(a.id);
    }
    for (const d of plan.deletions) {
        const r = byId.get(d.id);
        if (r) {
            if (rowHash(r) !== d.before_sha256)
                throw Error('MAINTENANCE_DELETION_ROW_DRIFT');
            deletions.push(d);
            allowed.add(d.id);
        }
    }
    const additions: ActualImportTransaction[] = [];
    for (const id of plan.additions) {
        const s = sources.get(id);
        if (!s)
            throw Error('MAINTENANCE_SOURCE_ID_UNKNOWN');
        const r = ids.get(id);
        if (!r) {
            additions.push(s);
            continue;
        }
        if (!sameEconomics(r, s, plan.account_id) || r.imported_payee !== s.imported_payee)
            throw Error('MAINTENANCE_ADDED_ROW_DRIFT');
        for (const k of ['category', 'notes', 'cleared', 'payee'] as const)
            if ((k !== 'payee' || k in s) && (r[k] ?? null) !== (s[k] ?? null))
                throw Error('MAINTENANCE_ADDED_CLASSIFICATION_DRIFT');
        allowed.add(String(r.id));
    }
    if (rows.some(r => !allowed.has(String(r.id))))
        throw Error('MAINTENANCE_UNPLANNED_ROW');
    return { aliases, deletions, additions };
}
export async function applyMaintenancePlan(api: MaintenanceApi, plan: MaintenancePlan, lease: MaintenanceLease, syncId: string): Promise<Row> {
    if (plan.actual_file_id !== syncId)
        throw Error('MAINTENANCE_SYNC_BINDING');
    if (!api.updateTransaction || !api.deleteTransaction)
        throw Error('MAINTENANCE_API_CAPABILITY_MISSING');
    const account = (await api.getAccounts()).find(a => a.id === plan.account_id);
    if (!account || account.closed !== true || account.offbudget === true)
        throw Error('MAINTENANCE_CLOSED_ACCOUNT_REQUIRED');
    const read = () => api.getTransactions(plan.account_id, '1900-01-01', '2100-12-31');
    const initial = await read();
    const pending = inspectState(plan, initial);
    const sources = new Map(plan.sources.map(s => [s.imported_id, s]));
    const before = rowsHash(initial);
    const assertAddOnly = (result: Row, count: number): void => {
        if (!Array.isArray(result.errors) || result.errors.length || !Array.isArray(result.added) || result.added.length !== count || new Set(result.added).size !== count || !Array.isArray(result.updated) || result.updated.length || !Array.isArray(result.updatedPreview) || result.updatedPreview.length)
            throw Error('MAINTENANCE_IMPORT_NOT_EXACT_ADDITIONS');
    };
    // Actual reconciles fuzzy matches and deleted IDs. Preview the complete
    // missing set before touching any existing row, then each bounded batch.
    // Never force-add a tombstoned source or permit a matched-row update.
    if (pending.additions.length) {
        assertMaintenanceLease(lease, syncId);
        assertAddOnly(await api.importTransactions(plan.account_id, pending.additions as unknown as Row[], { defaultCleared: true, reimportDeleted: false, dryRun: true }), pending.additions.length);
    }
    let updated = 0, deleted = 0, added = 0, budget = 100;
    // Every mutation is independently resumable against immutable before/after facts.
    for (const a of pending.aliases) {
        if (budget === 0)
            break;
        budget--;
        assertMaintenanceLease(lease, syncId);
        const s = sources.get(a.source_imported_id)!;
        await api.updateTransaction(a.id, { imported_id: s.imported_id, imported_payee: s.imported_payee });
        updated++;
    }
    for (const d of pending.deletions) {
        if (budget === 0)
            break;
        budget--;
        assertMaintenanceLease(lease, syncId);
        await api.deleteTransaction(d.id);
        deleted++;
    }
    for (let i = 0; i < pending.additions.length && budget > 0; i += 100) {
        assertMaintenanceLease(lease, syncId);
        const batch = pending.additions.slice(i, i + Math.min(100, budget));
        budget -= batch.length;
        assertAddOnly(await api.importTransactions(plan.account_id, batch as unknown as Row[], { defaultCleared: true, reimportDeleted: false, dryRun: true }), batch.length);
        assertMaintenanceLease(lease, syncId);
        const result = await api.importTransactions(plan.account_id, batch as unknown as Row[], { defaultCleared: true, reimportDeleted: false });
        assertAddOnly(result, batch.length);
        added += batch.length;
    }
    assertMaintenanceLease(lease, syncId);
    await api.sync();
    const final = await read();
    const outstanding = inspectState(plan, final);
    const remaining = outstanding.aliases.length + outstanding.deletions.length + outstanding.additions.length;
    const observedBalance = final.reduce((n, r) => n + Number(r.amount), 0);
    if (await api.getAccountBalance(plan.account_id) !== observedBalance)
        throw Error('MAINTENANCE_BALANCE_READBACK_MISMATCH');
    if (remaining === 0 && (final.length !== plan.after.count || observedBalance !== plan.after.balance))
        throw Error('MAINTENANCE_FINAL_READBACK_MISMATCH');
    if (remaining > 0 && budget > 0)
        throw Error('MAINTENANCE_INCOMPLETE_MUTATION_READBACK');
    const finalAccount = (await api.getAccounts()).find(a => a.id === plan.account_id);
    if (!finalAccount || finalAccount.closed !== true || finalAccount.offbudget === true)
        throw Error('MAINTENANCE_ACCOUNT_STATE_DRIFT');
    assertMaintenanceLease(lease, syncId);
    return { status: remaining ? 'ACTUAL_MAINTENANCE_PARTIAL' : 'ACTUAL_MAINTENANCE_VERIFIED', state: remaining ? 'PARTIAL' : 'VERIFIED', remaining_actions: remaining, plan_sha256: plan.plan_sha256, account_id: plan.account_id, actual_file_id: syncId, before_rows_sha256: before, after_rows_sha256: rowsHash(final), count: final.length, balance_minor: observedBalance, target_count: plan.after.count, target_balance_minor: plan.after.balance, updated, deleted, added, replay: updated + deleted + added === 0, writer_fencing_token: lease.fencing_token, backup_receipt_sha256: plan.backup.receipt_sha256 };
}
