import assert from "node:assert/strict";
import test from "node:test";
import { downloadAndSyncBudget } from "./initial-sync.mjs";

test("initial sync recovers EPIPE with unchanged binding and bounded delay", async () => {
  const calls = [], delays = [];
  let syncs = 0;
  const options = { password: "fictional-budget-key" };
  await downloadAndSyncBudget({
    async downloadBudget(id, supplied) { calls.push(id); assert.equal(supplied, options); },
    async sync() { if (++syncs === 1) throw Object.assign(new Error("socket"), { cause: { code: "EPIPE" } }); },
  }, "existing-sync", options, async ms => delays.push(ms));
  assert.deepEqual(calls, ["existing-sync", "existing-sync"]);
  assert.deepEqual(delays, [250]);
});

test("transport retries stop after three attempts; auth and unknown errors stop immediately", async () => {
  for (const [code, expected] of [["network-failure", 3], ["invalid-password", 1], ["budget-not-found", 1], [undefined, 1]]) {
    let calls = 0;
    const failure = Object.assign(new Error("failed"), { code });
    await assert.rejects(downloadAndSyncBudget({ async downloadBudget() { calls++; throw failure; }, async sync() { assert.fail("not reached"); } }, "sync", undefined, async () => {}), error => error === failure);
    assert.equal(calls, expected);
  }
});
