import assert from "node:assert/strict";
import test from "node:test";

import {
  assertCommitEnabled,
  partitionCrossSourceStatementDuplicates,
} from "./actualctl.mjs";


test("Actual commit requires the explicit production write gate", () => {
  assert.doesNotThrow(() => assertCommitEnabled(false, {}));
  assert.doesNotThrow(() => assertCommitEnabled(true, { ALLOW_ACTUAL_WRITES: "true" }));
  assert.throws(
    () => assertCommitEnabled(true, {}),
    /Actual commits are disabled/,
  );
});

test("Actual write gate is case insensitive but rejects other values", () => {
  assert.doesNotThrow(() => assertCommitEnabled(true, { ALLOW_ACTUAL_WRITES: "TRUE" }));
  assert.throws(
    () => assertCommitEnabled(true, { ALLOW_ACTUAL_WRITES: "1" }),
    /Actual commits are disabled/,
  );
});

test("unique statement rows already captured by a browser export are suppressed", () => {
  const incoming = [{
    date: "2026-06-21",
    amount: -24965,
    imported_payee: "KIBSONS INTERNATIONAL HTTPS WWW K",
    imported_id: "statement:adcb_v1:new",
  }];
  const existing = [{
    date: "2026-06-21",
    amount: -24965,
    imported_payee: "Kibsons International - https://www.k",
    imported_id: "browser:adcb-personal-internet-banking:existing",
  }];

  const result = partitionCrossSourceStatementDuplicates(incoming, existing);

  assert.equal(result.records.length, 0);
  assert.equal(result.suppressed.length, 1);
  assert.equal(result.suppressed[0].matched_existing_id, existing[0].imported_id);
});

test("cross-source suppression remains conservative for repeated or committed rows", () => {
  const repeated = [
    {
      date: "2026-06-21",
      amount: -1000,
      imported_payee: "COFFEE SHOP",
      imported_id: "statement:adcb_v1:first",
    },
    {
      date: "2026-06-21",
      amount: -1000,
      imported_payee: "COFFEE SHOP",
      imported_id: "statement:adcb_v1:second",
    },
  ];
  const existing = [
    {
      date: "2026-06-21",
      amount: -1000,
      imported_payee: "COFFEE SHOP",
      imported_id: "browser:adcb-personal-internet-banking:existing",
    },
    {
      date: "2026-06-22",
      amount: -2000,
      imported_payee: "SHOP",
      imported_id: "statement:adcb_v1:committed",
    },
  ];
  const incoming = [
    ...repeated,
    {
      date: "2026-06-22",
      amount: -2000,
      imported_payee: "SHOP",
      imported_id: "statement:adcb_v1:committed",
    },
  ];

  const result = partitionCrossSourceStatementDuplicates(incoming, existing);

  assert.equal(result.records.length, 3);
  assert.equal(result.suppressed.length, 0);
});
