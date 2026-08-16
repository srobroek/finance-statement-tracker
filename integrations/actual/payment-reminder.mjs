const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

export function statementPaymentReminderSpec(payload, { today } = {}) {
  if (Array.isArray(payload) || !payload?.statement) {
    return { status: "skipped", reason: "NO_STATEMENT_METADATA" };
  }
  const dueDate = payload.statement.payment_due_date;
  if (!dueDate) return { status: "skipped", reason: "NO_EVIDENCED_DUE_DATE" };
  if (!ISO_DATE.test(String(dueDate))) {
    throw new Error(`Invalid statement payment due date: ${dueDate}`);
  }

  const closingBalance = Number(payload.statement.closing_balance_aed);
  if (!Number.isFinite(closingBalance)) {
    throw new Error("Statement closing_balance_aed must be numeric when a due date is present");
  }
  if (closingBalance <= 0) {
    return { status: "skipped", reason: "NO_PAYMENT_DUE" };
  }

  const envelopes = payload.envelopes ?? [];
  if (envelopes.length !== 1 || !envelopes[0]?.account) {
    return { status: "skipped", reason: "AMBIGUOUS_PAYMENT_ACCOUNT" };
  }
  const currentDate = String(today ?? new Date().toISOString().slice(0, 10));
  if (!ISO_DATE.test(currentDate)) throw new Error(`Invalid current date: ${currentDate}`);
  if (String(dueDate) < currentDate) {
    return { status: "skipped", reason: "DUE_DATE_PASSED" };
  }

  const account = String(envelopes[0].account);
  return {
    status: "ready",
    schedule: {
      name: `${account} · statement payment · ${dueDate}`,
      account,
      payee: null,
      amount: Math.round(closingBalance * 100),
      amountOp: "is",
      date: String(dueDate),
      posts_transaction: false,
    },
  };
}
