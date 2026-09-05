import assert from 'node:assert/strict';
import test from 'node:test';
import { applyMaintenancePlan, buildMaintenancePlan, MaintenanceApi, MaintenanceLease, MaintenanceRequest, Row, rowsHash, validateMaintenancePlan } from './actual-maintenance';
const id = (n: number) => `statement:adcb_v1:${String(n).padStart(24, '0')}`;
const digest = (n: number) => String(n).repeat(64), account = { id: 'adcb', closed: true, offbudget: false };
const lease = (): MaintenanceLease => ({ resource_key: 'actual:budget', lease_id: 'lease', fencing_token: 3, expires_at: new Date(Date.now() + 120000).toISOString() });
function fixture() {
    const source = [{ imported_id: id(1), date: '2026-01-05', amount: -100, imported_payee: 'Original', cleared: true, category: 'rule', notes: 'rule note' },
        { imported_id: id(2), date: '2026-02-05', amount: 70, imported_payee: 'Payment', cleared: true },
        { imported_id: id(3), date: '2026-02-07', amount: -30, imported_payee: 'New', cleared: true, category: 'rule' }];
    const rows: Row[] = [{ id: 'canonical', account: 'adcb', ...source[0], category: 'manual', notes: 'keep' },
        { id: 'duplicate', account: 'adcb', imported_id: 'browser:adcb-personal-internet-banking:duplicate', date: source[0].date, amount: -100, imported_payee: 'Original suffix', category: 'old' },
        { id: 'alias', account: 'adcb', imported_id: 'browser:adcb-personal-internet-banking:alias', date: source[1].date, amount: 70, imported_payee: 'PAYMENT', category: 'manual', notes: 'user note', payee: 'user-payee', cleared: false },
        { id: 'reconcile', account: 'adcb', imported_id: 'reconcile:adcb:closed', date: '2026-02-10', amount: 130, imported_payee: '', cleared: true }];
    const request: MaintenanceRequest = { schema_version: 1, account_id: 'adcb', actual_file_id: 'budget', source_documents: [
            { sha256: digest(1), archive_receipt_sha256: digest(2), statement_date: '2026-01-15', opening_minor: 0, closing_minor: 100, transactions: [source[0]] },
            { sha256: digest(3), archive_receipt_sha256: digest(4), statement_date: '2026-02-15', opening_minor: 100, closing_minor: 60, transactions: source.slice(1) }
        ],
        aliases: [{ transaction_id: 'alias', source_imported_id: id(2), proof_sha256: digest(5) }], deletions: [
            { transaction_id: 'duplicate', kind: 'DUPLICATE_BROWSER_PROJECTION', source_imported_ids: [id(1)], proof_sha256: digest(6) },
            { transaction_id: 'reconcile', kind: 'OBSOLETE_RECONCILIATION', source_imported_ids: [], proof_sha256: digest(6) }
        ],
        backup: { receipt_sha256: digest(7), restore_reference: 'verified-backup-receipt' }, expected_before: { count: 4, balance: 0, rows_sha256: rowsHash(rows) }, expected_after: { count: 3, balance: -60 } };
    return { rows, request, plan: buildMaintenancePlan(request, account, rows, 'budget') };
}
function fake(initial: Row[], opts: {
    failDelete?: number;
    skipDeletedSource?: boolean;
} = {}) {
    const rows = structuredClone(initial), calls: string[] = [];
    let deletes = 0;
    const api: MaintenanceApi = { async getAccounts() { return [account]; }, async getTransactions() { return structuredClone(rows); }, async getAccountBalance() { return rows.reduce((n, r) => n + Number(r.amount), 0); },
        async updateTransaction(id, fields) { calls.push('update'); Object.assign(rows.find(r => r.id === id)!, fields); },
        async deleteTransaction(id) { calls.push('delete'); if (++deletes === opts.failDelete)
            throw Error('injected restart'); rows.splice(rows.findIndex(r => r.id === id), 1); },
        async importTransactions(account, batch, options) { if (!options.dryRun) calls.push('import'); assert.equal(options.reimportDeleted, false); if (!options.dryRun && !opts.skipDeletedSource)
            for (const row of batch)
                rows.push({ ...row, id: `new-${rows.length}`, account }); return { errors: [], added: opts.skipDeletedSource ? [] : batch.map((_, i) => 'added-' + i), updated: [], updatedPreview: [] }; }, async sync() { calls.push('sync'); } };
    return { api, rows, calls };
}
test('reconstruction preserves canonical/manual alias fields; replay has zero mutation', async () => {
    const f = fixture(), m = fake(f.rows);
    const result = await applyMaintenancePlan(m.api, validateMaintenancePlan(f.plan, f.plan.plan_sha256), lease(), 'budget');
    assert.equal(result.count, 3);
    assert.equal(result.balance_minor, -60);
    assert.equal(result.deleted, 2);
    assert.equal(result.updated, 1);
    assert.equal(result.added, 1);
    assert.equal(m.rows.find(r => r.id === 'canonical')!.category, 'manual');
    const a = m.rows.find(r => r.id === 'alias')!;
    assert.equal(a.imported_id, id(2));
    assert.equal(a.imported_payee, 'Payment');
    assert.equal(a.category, 'manual');
    assert.equal(a.notes, 'user note');
    assert.equal(a.payee, 'user-payee');
    assert.equal(a.cleared, false);
    assert.equal((await applyMaintenancePlan(m.api, f.plan, lease(), 'budget')).replay, true);
    assert.equal(m.rows.length, 3);
});
test('restart after partial mutation safely resumes immutable per-action states', async () => {
    const f = fixture(), m = fake(f.rows, { failDelete: 2 });
    await assert.rejects(applyMaintenancePlan(m.api, f.plan, lease(), 'budget'), /restart/);
    const resumed = await applyMaintenancePlan(m.api, f.plan, lease(), 'budget');
    assert.equal(resumed.updated, 0);
    assert.equal(resumed.deleted, 1);
    assert.equal(resumed.added, 1);
});
test('manual drift or unplanned rows fail before any mutation', async () => {
    for (const change of [(r: Row[]) => { r[0].notes = 'human changed'; }, (r: Row[]) => { r.push({ ...r[0], id: 'unexpected', imported_id: 'manual' }); }]) {
        const f = fixture();
        change(f.rows);
        const m = fake(f.rows);
        await assert.rejects(applyMaintenancePlan(m.api, f.plan, lease(), 'budget'), /DRIFT|UNPLANNED/);
        assert.deepEqual(m.calls, []);
    }
});
test('expired, near-expiry and wrong-budget fence cannot mutate', async () => {
    const f = fixture();
    for (const l of [{ ...lease(), expires_at: new Date(Date.now() - 1).toISOString() }, { ...lease(), expires_at: new Date(Date.now() + 1000).toISOString() }, { ...lease(), resource_key: 'actual:other' }]) {
        const m = fake(f.rows);
        await assert.rejects(applyMaintenancePlan(m.api, f.plan, l, 'budget'), /LEASE/);
        assert.deepEqual(m.calls, []);
    }
});
test('lease is rechecked between mutations; partial state remains resumable', async () => {
    const f = fixture(), m = fake(f.rows), l = lease(), update = m.api.updateTransaction!;
    m.api.updateTransaction = async (id, fields) => { await update(id, fields); l.expires_at = new Date(Date.now() + 1000).toISOString(); };
    await assert.rejects(applyMaintenancePlan(m.api, f.plan, l, 'budget'), /LEASE/);
    assert.deepEqual(m.calls, ['update']);
    m.api.updateTransaction = update;
    assert.equal((await applyMaintenancePlan(m.api, f.plan, lease(), 'budget')).count, 3);
});
test('deleted source identity is not resurrected or falsely verified', async () => {
    const f = fixture(), m = fake(f.rows, { skipDeletedSource: true });
    await assert.rejects(applyMaintenancePlan(m.api, f.plan, lease(), 'budget'), /EXACT_ADDITIONS/);
    assert.equal(m.rows.some(r => r.imported_id === id(3)), false);
});
test('complete plan hash and explicit approved digest reject tampering', () => {
    const { plan } = fixture();
    assert.throws(() => validateMaintenancePlan({ ...plan, after: { count: 3, balance: 0 } }, plan.plan_sha256), /HASH/);
    assert.throws(() => validateMaintenancePlan(plan, digest(9)), /HASH/);
});
test('source arithmetic, opening, continuity, archive hash and duplicate IDs fail closed', () => {
    for (const mutate of [(r: MaintenanceRequest) => { r.source_documents[0].opening_minor = 1; }, (r: MaintenanceRequest) => { r.source_documents[1].opening_minor = 99; }, (r: MaintenanceRequest) => { r.source_documents[1].closing_minor = 0; }, (r: MaintenanceRequest) => { r.source_documents[1].archive_receipt_sha256 = 'missing'; }, (r: MaintenanceRequest) => { r.source_documents[1].transactions[0].imported_id = id(1); }, (r: MaintenanceRequest) => { r.source_documents[1].statement_date = '2026-03-15'; }]) {
        const f = fixture();
        mutate(f.request);
        assert.throws(() => buildMaintenancePlan(f.request, account, f.rows, 'budget'));
    }
});
test('foreign account, unexplained row, linked rows and arbitrary deletion are rejected', () => {
    for (const mutate of [(r: MaintenanceRequest, rows: Row[]) => { rows[1].transfer_id = 'peer'; }, (r: MaintenanceRequest, rows: Row[]) => { rows[1].imported_id = 'user-entered'; }, (r: MaintenanceRequest) => { r.deletions[0].source_imported_ids = [id(3)]; }, (r: MaintenanceRequest) => { r.deletions.pop(); }, (r: MaintenanceRequest) => { r.account_id = 'other'; }]) {
        const f = fixture();
        mutate(f.request, f.rows);
        f.request.expected_before.rows_sha256 = rowsHash(f.rows);
        assert.throws(() => buildMaintenancePlan(f.request, account, f.rows, 'budget'));
    }
});
test('large source reconstruction returns verified bounded chunks and resumes to exact final state', async () => {
    const f = fixture();
    for (let n = 4; n < 224; n++)
        f.request.source_documents[1].transactions.push({ imported_id: id(n), date: '2026-02-08', amount: -1, imported_payee: 'Source ' + n, cleared: true, category: 'rule' });
    f.request.source_documents[1].closing_minor = 280;
    f.request.expected_after = { count: 223, balance: -280 };
    const plan = buildMaintenancePlan(f.request, account, f.rows, 'budget'), m = fake(f.rows);
    let rounds = 0, result;
    do {
        result = await applyMaintenancePlan(m.api, plan, lease(), 'budget');
        rounds++;
        assert.ok(Number(result.updated) + Number(result.deleted) + Number(result.added) <= 100);
    } while (result.state === 'PARTIAL');
    assert.equal(rounds, 3);
    assert.equal(result.count, 223);
    assert.equal(result.balance_minor, -280);
});

test('fuzzy match preview prevents all maintenance mutations', async () => {
    const f = fixture(), m = fake(f.rows);
    m.api.importTransactions = async (_account, _rows, options) => {
        assert.equal(options.dryRun, true);
        return { errors: [], added: [], updated: ['canonical'], updatedPreview: [{ existing: { id: 'canonical' } }] };
    };
    await assert.rejects(applyMaintenancePlan(m.api, f.plan, lease(), 'budget'), /EXACT_ADDITIONS/);
    assert.deepEqual(m.calls, []);
    assert.deepEqual(m.rows, f.rows);
});
