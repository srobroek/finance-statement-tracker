import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  captureRebuildState,
  compareRebuildStates,
  isVerifiedEmptyManifest,
  loadFullRebuildManifests,
  applyDisposableManualCorrections,
  summarizeRebuildState,
  validateRebuildManifestCorpus,
  validateRebuildManifestPayload,
} from "./full-rebuild.mjs";

test("full rebuild loads browser sources before statements with stable ordering", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "full-rebuild-manifest-test-"));
  try {
    await fs.mkdir(path.join(root, "browser"));
    await fs.mkdir(path.join(root, "statements"));
    await fs.writeFile(path.join(root, "browser", "capture.json"), "{}\n");
    await fs.writeFile(path.join(root, "statements", "b.json"), "{}\n");
    await fs.writeFile(path.join(root, "statements", "a.json"), "{}\n");
    const rows = await loadFullRebuildManifests(root, {
      manifest_sources: [
        { id: "statements", import_order: 20, globs: ["statements/*.json"] },
        { id: "browser", import_order: 10, files: ["browser/capture.json"] },
      ],
    });
    assert.deepEqual(rows.map(row => row.source_id), ["browser", "statements", "statements"]);
    assert.deepEqual(rows.slice(1).map(row => path.basename(row.filename)), ["a.json", "b.json"]);
  } finally {
    await fs.rm(root, { recursive: true, force: true });
  }
});

test("full rebuild rejects manifests outside the repository root", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "full-rebuild-root-test-"));
  try {
    await assert.rejects(
      loadFullRebuildManifests(root, {
        manifest_sources: [{ id: "unsafe", files: ["../outside.json"] }],
      }),
      /escapes repository root/,
    );
  } finally {
    await fs.rm(root, { recursive: true, force: true });
  }
});

test("full rebuild accepts only balance-tied review-free empty statements", () => {
  const valid = {
    envelopes: [],
    review_count: 0,
    statement: { transaction_count: 0, balance_tied: true },
  };
  assert.equal(isVerifiedEmptyManifest(valid), true);
  assert.equal(isVerifiedEmptyManifest({ ...valid, review_count: 1 }), false);
  assert.equal(
    isVerifiedEmptyManifest({ ...valid, statement: { transaction_count: 0, balance_tied: false } }),
    false,
  );
  assert.equal(isVerifiedEmptyManifest({ ...valid, envelopes: [{ records: [] }] }), false);
});

function replayFixture() {
  return {
    snapshot: {
      transactions: [{
        id: "provider-row-first",
        account_name: "Provider Card",
        date: "2026-08-01",
        amount: -12500,
        imported_id: "provider:transaction:1",
        imported_payee: "Merchant",
        payee_name: "Merchant",
        category_name: "Shopping",
        notes: "source:statement | #manual",
        cleared: true,
        reconciled: true,
        transfer_id: "provider-transfer-1",
        schedule: "provider-schedule-1",
        is_parent: true,
        is_child: false,
        parent_id: null,
        subtransactions: [{
          id: "provider-child-1",
          amount: -10000,
          notes: "source:statement | #manual",
          category_name: "Shopping",
        }],
      }],
    },
    accounts: [{ id: "provider-account-first", name: "Provider Card", offbudget: false, closed: false }],
    balances: [-12500],
    schedules: [{ id: "provider-schedule-1", name: "Monthly payment", account: "provider-account-first" }],
  };
}

test("full rebuild exact replay passes with changed provider row identifiers", () => {
  const fixture = replayFixture();
  const first = summarizeRebuildState(fixture.snapshot, fixture);
  const replayInput = {
    ...fixture,
    accounts: [{ ...fixture.accounts[0], id: "provider-account-replay" }],
    snapshot: {
      transactions: [{
        ...fixture.snapshot.transactions[0],
        id: "provider-row-replay",
        schedule: "provider-schedule-replay",
        subtransactions: [{
          ...fixture.snapshot.transactions[0].subtransactions[0],
          id: "provider-child-replay",
        }],
      }],
    },
    schedules: [{
      ...fixture.schedules[0],
      id: "provider-schedule-replay",
      account: "provider-account-replay",
    }],
  };
  const replay = summarizeRebuildState(replayInput.snapshot, replayInput);
  assert.equal(compareRebuildStates(first, replay).status, "PASS");
  const receipt = JSON.stringify(first);
  assert.ok(!receipt.includes("provider-account-first"));
  assert.ok(!receipt.includes("provider-row-first"));
  assert.ok(!receipt.includes("provider:transaction:1"));
});

test("full rebuild rejects missing and duplicate imported IDs before any import", () => {
  assert.throws(
    () => validateRebuildManifestPayload({ envelopes: [{ records: [{ amount: -1 }] }] }, "missing.json"),
    /missing\.json.*lacks imported_id/,
  );
  assert.throws(
    () => validateRebuildManifestPayload({
      envelopes: [{ records: [{ imported_id: "provider:1" }, { imported_id: "provider:1" }] }],
    }, "duplicate.json"),
    /duplicate\.json.*duplicate imported_id/,
  );
  assert.throws(
    () => validateRebuildManifestCorpus([
      { filename: "one.json", payload: { envelopes: [{ records: [{ imported_id: "provider:1" }] }] } },
      { filename: "two.json", payload: { envelopes: [{ records: [{ imported_id: "provider:1" }] }] } },
    ]),
    /Duplicate imported_id across manifests/,
  );
});

test("full rebuild reports logical imported-ID drift as a redacted failure", () => {
  const fixture = replayFixture();
  const first = summarizeRebuildState(fixture.snapshot, fixture);
  const changedSnapshot = {
    transactions: [{
      ...fixture.snapshot.transactions[0],
      imported_id: "provider:transaction:changed",
    }],
  };
  const verification = compareRebuildStates(
    first,
    summarizeRebuildState(changedSnapshot, fixture),
  );
  assert.equal(verification.status, "FAIL");
  assert.ok(verification.differences.some(row => row.field === "hashes.transaction_sha256"));
  const receipt = JSON.stringify(verification);
  assert.ok(!receipt.includes("provider:transaction:changed"));
});

test("full rebuild reports balance and economic drift instead of a false pass", () => {
  const fixture = replayFixture();
  const first = summarizeRebuildState(fixture.snapshot, fixture);
  const replay = summarizeRebuildState({
    transactions: [{ ...fixture.snapshot.transactions[0], amount: -12499 }],
  }, { ...fixture, balances: [-12499] });
  const verification = compareRebuildStates(first, replay);
  assert.equal(verification.status, "FAIL");
  assert.ok(verification.differences.some(row => row.field === "economics.amount_sum_minor"));
  assert.ok(verification.differences.some(row => row.field === "balances.balance_sum_minor"));
  assert.ok(verification.differences.some(row => row.field === "hashes.economic_fields_sha256"));
});

test("full rebuild detects notes, split, and schedule/manual-state drift", () => {
  const fixture = replayFixture();
  const first = summarizeRebuildState(fixture.snapshot, fixture);
  const changed = {
    ...fixture,
    snapshot: {
      transactions: [{
        ...fixture.snapshot.transactions[0],
        notes: "source:statement | #changed",
        schedule: null,
        subtransactions: [],
      }],
    },
    schedules: [],
  };
  const verification = compareRebuildStates(first, summarizeRebuildState(changed.snapshot, changed));
  assert.equal(verification.status, "FAIL");
  assert.ok(verification.differences.some(row => row.field === "hashes.manual_state_sha256"));
  assert.ok(verification.differences.some(row => row.field === "hashes.notes_sha256"));
  assert.ok(verification.differences.some(row => row.field === "hashes.splits_sha256"));
  assert.ok(verification.differences.some(row => row.field === "hashes.schedules_sha256"));
});

test("capture rebuild state reads account balances and schedules through the canonical API", async () => {
  const fixture = replayFixture();
  const calls = [];
  const state = await captureRebuildState(fixture.snapshot, {
    getAccounts: async () => fixture.accounts,
    getAccountBalance: async id => {
      calls.push(`balance:${id}`);
      return fixture.balances[0];
    },
    getSchedules: async () => fixture.schedules,
  });
  assert.equal(state.counts.accounts, 1);
  assert.equal(state.balances.balance_sum_minor, -12500);
  assert.equal(state.counts.schedules, 1);
  assert.deepEqual(calls, ["balance:provider-account-first"]);
});

test("full rebuild applies executable manual corrections while retaining links", async () => {
  const fixture = replayFixture();
  let updated;
  const result = await applyDisposableManualCorrections(fixture.snapshot, {
    updateTransaction: async (id, fields) => {
      updated = { id, fields };
    },
  });
  assert.equal(result.status, "applied");
  assert.equal(result.notes, 1);
  assert.equal(result.reconciled, 1);
  assert.equal(result.transfer_links, 1);
  assert.equal(result.split_states, 1);
  assert.equal(result.schedule_links, 1);
  assert.equal(updated.id, "provider-row-first");
  assert.equal(updated.fields.notes, "#manual | Memo: disposable manual correction");
  assert.equal(updated.fields.reconciled, true);
  assert.equal(updated.fields.transfer_id, "provider-transfer-1");
  assert.equal(updated.fields.schedule, "provider-schedule-1");
  assert.equal(updated.fields.subtransactions.length, 1);
});
