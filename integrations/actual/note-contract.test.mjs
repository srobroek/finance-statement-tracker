import assert from "node:assert/strict";
import test from "node:test";

import { validateCanonicalActualNotes } from "./note-contract.mjs";


test("canonical Actual notes allow tags first and limited details", () => {
  const notes = "#rental #rental:lt713 | Doc: Finance Evidence/2026/08/dewa/a.pdf";
  assert.equal(validateCanonicalActualNotes(notes), notes);
});

test("canonical Actual notes reject technical tags and arbitrary metadata", () => {
  assert.throws(() => validateCanonicalActualNotes("#browser-import"), /forbidden/);
  assert.throws(() => validateCanonicalActualNotes("#shared | source:statement"), /Unsupported/);
  assert.throws(() => validateCanonicalActualNotes("#cashback-ei_amazon"), /forbidden/);
  assert.throws(() => validateCanonicalActualNotes("#foreign | FX: NOK 340.00"), /Unsupported/);
  assert.throws(() => validateCanonicalActualNotes("Memo: first | #shared"), /Unsupported/);
});
