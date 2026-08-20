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
  verifyManualCorrectionDelta,
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
      transactions: [
        {
          id: "provider-row-first",
          account: "provider-account-first",
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
          transfer_id: "provider-transfer-peer-first",
          schedule: "provider-schedule-1",
          is_parent: true,
          is_child: false,
          parent_id: null,
          subtransactions: [{
            id: "provider-child-1",
            amount: -10000,
            notes: "source:statement | #manual",
            imported_payee: "Merchant",
            payee_name: "Merchant",
            category_name: "Shopping",
          }],
        },
        {
          id: "provider-transfer-peer-first",
          account: "provider-account-first",
          account_name: "Provider Card",
          date: "2026-08-01",
          amount: 12500,
          imported_id: "provider:transaction:transfer-peer",
          imported_payee: "Transfer",
          payee_name: "Transfer",
          category_name: "",
          notes: "source:statement",
          cleared: true,
          reconciled: false,
          transfer_id: "provider-row-first",
          schedule: null,
          is_parent: false,
          is_child: false,
          parent_id: null,
          subtransactions: [],
        },
      ],
    },
    accounts: [{ id: "provider-account-first", name: "Provider Card", offbudget: false, closed: false }],
    balances: [-12500],
    schedules: [{ id: "provider-schedule-1", name: "Monthly payment", account: "provider-account-first" }],
  };
}

test("full rebuild exact replay passes with changed provider row identifiers", () => {
  const fixture = replayFixture();
  const first = summarizeRebuildState(fixture.snapshot, {
    ...fixture,
    manualCorrectionStatus: "verified",
  });
  const replayInput = {
    ...fixture,
    accounts: [{ ...fixture.accounts[0], id: "provider-account-replay" }],
    snapshot: {
      transactions: [{
        ...fixture.snapshot.transactions[0],
        id: "provider-row-replay",
        transfer_id: "provider-transfer-peer-replay",
        schedule: "provider-schedule-replay",
        subtransactions: [{
          ...fixture.snapshot.transactions[0].subtransactions[0],
          id: "provider-child-replay",
        }],
      }, {
        ...fixture.snapshot.transactions[1],
        id: "provider-transfer-peer-replay",
        transfer_id: "provider-row-replay",
      }],
    },
    schedules: [{
      ...fixture.schedules[0],
      id: "provider-schedule-replay",
      account: "provider-account-replay",
    }],
  };
  const replay = summarizeRebuildState(replayInput.snapshot, {
    ...replayInput,
    manualCorrectionStatus: "verified",
  });
  assert.equal(compareRebuildStates(first, replay).status, "PASS");
  assert.equal(compareRebuildStates(first, replay, { enforceCoverage: true }).status, "PASS");
  const receipt = JSON.stringify(first);
  assert.ok(!receipt.includes("provider-account-first"));
  assert.ok(!receipt.includes("provider-row-first"));
  assert.ok(!receipt.includes("provider:transaction:1"));
});

test("full rebuild uses semantic link endpoints and child fields, not provider IDs", () => {
  const fixture = replayFixture();
  const first = summarizeRebuildState(fixture.snapshot, fixture);
  const rawIdOnly = structuredClone(fixture.snapshot);
  rawIdOnly.transactions[0].subtransactions[0].id = "provider-child-another-id";
  assert.equal(compareRebuildStates(first, summarizeRebuildState(rawIdOnly, fixture)).status, "PASS");

  const rewired = structuredClone(fixture.snapshot);
  rewired.transactions[0].transfer_id = "provider-transfer-missing";
  const transferVerification = compareRebuildStates(first, summarizeRebuildState(rewired, fixture));
  assert.equal(transferVerification.status, "FAIL");
  assert.ok(transferVerification.differences.some(row => row.field === "hashes.manual_state_sha256"));

  const duplicatePeer = structuredClone(fixture);
  duplicatePeer.snapshot.transactions.push({
    ...fixture.snapshot.transactions[1],
    id: "provider-transfer-peer-duplicate",
    imported_id: "provider:transaction:transfer-peer-duplicate",
  });
  duplicatePeer.snapshot.transactions[0].transfer_id = "provider-transfer-peer-duplicate";
  const duplicatePeerVerification = compareRebuildStates(
    first,
    summarizeRebuildState(duplicatePeer.snapshot, duplicatePeer),
  );
  assert.equal(duplicatePeerVerification.status, "FAIL");
  assert.ok(duplicatePeerVerification.differences.some(row => row.field === "hashes.manual_state_sha256"));

  const scheduleRewired = structuredClone(fixture);
  scheduleRewired.schedules.push({
    id: "provider-schedule-other",
    name: "Different schedule",
    account: "provider-account-first",
  });
  scheduleRewired.snapshot.transactions[0].schedule = "provider-schedule-other";
  const scheduleVerification = compareRebuildStates(
    first,
    summarizeRebuildState(scheduleRewired.snapshot, scheduleRewired),
  );
  assert.equal(scheduleVerification.status, "FAIL");
  assert.ok(scheduleVerification.differences.some(row => row.field === "hashes.manual_state_sha256"));

  const parentRewired = structuredClone(fixture.snapshot);
  parentRewired.transactions[0].parent_id = "provider-transfer-peer-first";
  parentRewired.transactions[0].is_child = true;
  const parentVerification = compareRebuildStates(
    first,
    summarizeRebuildState(parentRewired, fixture),
  );
  assert.equal(parentVerification.status, "FAIL");
  assert.ok(parentVerification.differences.some(row => row.field === "hashes.splits_sha256"));

  const childChanged = structuredClone(fixture.snapshot);
  childChanged.transactions[0].subtransactions[0].category_name = "Travel";
  const childVerification = compareRebuildStates(first, summarizeRebuildState(childChanged, fixture));
  assert.equal(childVerification.status, "FAIL");
  assert.ok(childVerification.differences.some(row => row.field === "hashes.splits_sha256"));
});

test("full rebuild blocks dangling transfer, parent, and schedule links", () => {
  const fixture = replayFixture();
  const dangling = structuredClone(fixture.snapshot);
  dangling.transactions[0].transfer_id = "provider-transfer-missing";
  dangling.transactions[0].parent_id = "provider-parent-missing";
  dangling.transactions[0].is_child = true;
  dangling.transactions[0].schedule = "provider-schedule-missing";
  const state = summarizeRebuildState(dangling, fixture);
  const verification = compareRebuildStates(state, state, { enforceCoverage: true });
  assert.equal(verification.status, "BLOCKED");
  assert.ok(verification.blockers.includes("transfers:unresolved"));
  assert.ok(verification.blockers.includes("splits:unresolved"));
  assert.ok(verification.blockers.includes("schedule_links:unresolved"));
  assert.equal(state.counts.unresolved_transfer_links, 1);
  assert.equal(state.counts.unresolved_parent_links, 1);
  assert.equal(state.counts.unresolved_schedule_links, 1);
});

test("full rebuild blocks acceptance when required dimensions are not applicable", () => {
  const empty = summarizeRebuildState({ transactions: [] }, { accounts: [], balances: [], schedules: [] });
  const verification = compareRebuildStates(empty, empty, { enforceCoverage: true });
  assert.equal(verification.status, "BLOCKED");
  assert.ok(verification.blockers.includes("transfers:not-applicable"));
  assert.ok(verification.blockers.includes("splits:not-applicable"));
  assert.ok(verification.blockers.includes("schedules:not-applicable"));
  assert.ok(verification.blockers.includes("manual_state:not-applicable"));
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

test("capture rebuild state enriches child payee and category semantics", async () => {
  const fixture = replayFixture();
  const raw = structuredClone(fixture.snapshot);
  const child = raw.transactions[0].subtransactions[0];
  delete child.payee_name;
  delete child.category_name;
  child.payee = "provider-payee-1";
  child.category = "provider-category-1";
  const api = {
    getAccounts: async () => fixture.accounts,
    getAccountBalance: async () => fixture.balances[0],
    getSchedules: async () => fixture.schedules,
    getPayees: async () => [{ id: "provider-payee-1", name: "Merchant" }],
    getCategories: async () => [{ id: "provider-category-1", name: "Shopping" }],
  };
  const first = await captureRebuildState(raw, api);
  const replayRaw = structuredClone(raw);
  replayRaw.transactions[0].subtransactions[0].payee = "provider-payee-replay";
  replayRaw.transactions[0].subtransactions[0].category = "provider-category-replay";
  const replay = await captureRebuildState(replayRaw, {
    ...api,
    getPayees: async () => [{ id: "provider-payee-replay", name: "Merchant" }],
    getCategories: async () => [{ id: "provider-category-replay", name: "Shopping" }],
  });
  assert.equal(compareRebuildStates(first, replay).status, "PASS");
});

test("full rebuild applies executable manual corrections while retaining links", async () => {
  const fixture = replayFixture();
  let updated;
  const result = await applyDisposableManualCorrections(fixture.snapshot, {
    updateTransaction: async (id, fields) => {
      updated = { id, fields };
      Object.assign(fixture.snapshot.transactions[0], fields);
    },
    getTransactions: async () => [fixture.snapshot.transactions[0]],
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
  assert.equal(updated.fields.transfer_id, "provider-transfer-peer-first");
  assert.equal(updated.fields.schedule, "provider-schedule-1");
  assert.equal(updated.fields.subtransactions.length, 1);
  assert.equal(result.readback.status, "PASS");
});

test("full rebuild proves manual correction delta preserves economics and links", () => {
  const fixture = replayFixture();
  const baseline = summarizeRebuildState(fixture.snapshot, fixture);
  const correctedSnapshot = structuredClone(fixture.snapshot);
  correctedSnapshot.transactions[0].notes = "#manual | Memo: disposable manual correction";
  const corrected = summarizeRebuildState(correctedSnapshot, {
    ...fixture,
    manualCorrectionStatus: "verified",
  });
  const delta = verifyManualCorrectionDelta(baseline, corrected, {
    status: "applied",
    readback: { status: "PASS" },
  });
  assert.equal(delta.status, "PASS");
});
