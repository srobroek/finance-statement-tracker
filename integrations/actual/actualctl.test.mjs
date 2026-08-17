import assert from "node:assert/strict";
import test from "node:test";

import { assertCommitEnabled } from "./actualctl.mjs";


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
