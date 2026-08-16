import assert from "node:assert/strict";
import test from "node:test";

import { statementPaymentReminderSpec } from "./payment-reminder.mjs";

const manifest = {
  statement: {
    payment_due_date: "2026-08-25",
    closing_balance_aed: "285.70",
  },
  envelopes: [{ account: "EI Amazon", records: [] }],
};

test("builds a non-posting one-time reminder from statement evidence", () => {
  const result = statementPaymentReminderSpec(manifest, { today: "2026-08-16" });

  assert.equal(result.status, "ready");
  assert.equal(result.schedule.amount, 28570);
  assert.equal(result.schedule.date, "2026-08-25");
  assert.equal(result.schedule.account, "EI Amazon");
  assert.equal(result.schedule.payee, null);
  assert.equal(result.schedule.posts_transaction, false);
});

test("does not create reminders from forecasts, paid balances, or past dates", () => {
  assert.equal(
    statementPaymentReminderSpec({ statement: {}, envelopes: manifest.envelopes }).reason,
    "NO_EVIDENCED_DUE_DATE",
  );
  assert.equal(
    statementPaymentReminderSpec({
      ...manifest,
      statement: { ...manifest.statement, closing_balance_aed: "0.00" },
    }, { today: "2026-08-16" }).reason,
    "NO_PAYMENT_DUE",
  );
  assert.equal(
    statementPaymentReminderSpec(manifest, { today: "2026-08-26" }).reason,
    "DUE_DATE_PASSED",
  );
});

test("refuses to choose a payment account when a statement spans accounts", () => {
  const result = statementPaymentReminderSpec({
    ...manifest,
    envelopes: [
      { account: "Primary", records: [] },
      { account: "Supplementary", records: [] },
    ],
  }, { today: "2026-08-16" });

  assert.equal(result.reason, "AMBIGUOUS_PAYMENT_ACCOUNT");
});
