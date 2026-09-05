import { createHash } from 'node:crypto';
import type { ActualImportTransaction } from './contracts';

export const ISSUER_PROFILES = ['adcb_v1', 'emirates_islamic_v1', 'wio_credit_v1'] as const;
export type IssuerProfile = (typeof ISSUER_PROFILES)[number];

export interface StatementTransaction {
  transaction_id: string;
  transaction_date: string;
  post_date: string | null;
  card_last4: string | null;
  description: string;
  amount_aed: string;
  signed_amount_aed: string;
  direction: 'DEBIT' | 'CREDIT';
  transaction_type: 'PURCHASE' | 'PAYMENT' | 'REFUND' | 'REWARD_CREDIT' | 'FEE';
  amount_original: string | null;
  currency_original: string;
  exchange_rate: string | null;
  source_line: number;
  review_required: boolean;
}

export interface NormalizedStatement {
  schema_version: 1;
  bank: string;
  adapter: IssuerProfile;
  source_file: string;
  statement_date: string | null;
  period_start: string | null;
  period_end: string | null;
  payment_due_date: string | null;
  opening_balance_aed: string | null;
  closing_balance_aed: string | null;
  minimum_payment_aed: string | null;
  total_payment_due_aed: string | null;
  card_last4s: string[];
  transactions: StatementTransaction[];
  transaction_count: number;
  debit_total_aed: string;
  credit_total_aed: string;
  calculated_closing_balance_aed: string | null;
  balance_difference_aed: string | null;
  balance_tied: boolean;
  ledger_reconciled: false;
  warnings: string[];
}

type Draft = Omit<StatementTransaction, 'transaction_id' | 'signed_amount_aed' | 'transaction_type'>;

const MONEY = '(?:\\d{1,3}(?:,\\d{3})*|\\d+)\\.\\d{2}';
const cents = (value: string): number => {
  const normalized = value.replace(/,/g, '');
  const result = Math.round(Number(normalized) * 100);
  if (!Number.isSafeInteger(result)) throw new Error(`Invalid monetary amount: ${value}`);
  return result;
};
const money = (value: number | null): string | null => value === null ? null : (value / 100).toFixed(2);
const moneyValue = (value: string | undefined): string | null => value === undefined ? null : money(cents(value));

function isoDmy(value: string, shortYear = false): string {
  const parts = value.split('/').map(Number);
  const year = shortYear ? 2000 + parts[2] : parts[2];
  const date = new Date(Date.UTC(year, parts[1] - 1, parts[0]));
  if (date.getUTCFullYear() !== year || date.getUTCMonth() + 1 !== parts[1] || date.getUTCDate() !== parts[0]) {
    throw new Error(`Invalid date: ${value}`);
  }
  return date.toISOString().slice(0, 10);
}

const MONTHS: Record<string, number> = { JAN: 1, FEB: 2, MAR: 3, APR: 4, MAY: 5, JUN: 6, JUL: 7, AUG: 8, SEP: 9, OCT: 10, NOV: 11, DEC: 12 };
function isoWord(day: string, month: string, year: string | number): string {
  const monthNumber = MONTHS[month.toUpperCase()];
  if (!monthNumber) throw new Error(`Invalid month: ${month}`);
  const yearNumber = Number(year);
  const dayNumber = Number(day);
  const value = new Date(Date.UTC(yearNumber, monthNumber - 1, dayNumber));
  if (value.getUTCFullYear() !== yearNumber || value.getUTCMonth() + 1 !== monthNumber || value.getUTCDate() !== dayNumber) {
    throw new Error(`Invalid date: ${day} ${month} ${year}`);
  }
  return value.toISOString().slice(0, 10);
}

function isoPeriodWord(day: string, month: string, periodStart: string | null, periodEnd: string | null): string {
  if (periodStart === null || periodEnd === null) throw new Error('Statement period is required to resolve yearless transaction dates');
  if (periodStart > periodEnd) throw new Error('Statement period start must not follow period end');
  const candidates: string[] = [];
  const startYear = Number(periodStart.slice(0, 4));
  const endYear = Number(periodEnd.slice(0, 4));
  for (let year = startYear; year <= endYear; year += 1) {
    let candidate: string;
    try { candidate = isoWord(day, month, year); } catch { continue; }
    if (candidate >= periodStart && candidate <= periodEnd) candidates.push(candidate);
  }
  if (candidates.length !== 1) {
    throw new Error(`Statement row date ${day} ${month} does not resolve uniquely within ${periodStart}..${periodEnd}`);
  }
  return candidates[0];
}

function isoTransactionWord(day: string, month: string, postDate: string): string {
  const postYear = Number(postDate.slice(0, 4));
  const candidates: string[] = [];
  for (const year of [postYear, postYear - 1]) {
    let candidate: string;
    try { candidate = isoWord(day, month, year); } catch { continue; }
    if (candidate <= postDate) candidates.push(candidate);
  }
  if (candidates.length === 0) {
    throw new Error(`Statement transaction date ${day} ${month} cannot be resolved from posting date ${postDate}`);
  }
  return candidates.sort().at(-1)!;
}

function transactionType(description: string, direction: 'DEBIT' | 'CREDIT'): StatementTransaction['transaction_type'] {
  const value = description.toUpperCase();
  if (['PAYMENT RECEIVED', 'CREDIT REPAYMENT', 'CARD REPAYMENT'].some(token => value.includes(token))) return 'PAYMENT';
  if (value.includes('CASHBACK') && direction === 'CREDIT') return 'REWARD_CREDIT';
  if (/\bFEES?\b/.test(value) || value.startsWith('VAT ON')) return 'FEE';
  return direction === 'CREDIT' ? 'REFUND' : 'PURCHASE';
}

function finishTransactions(bankKey: string, drafts: Draft[]): StatementTransaction[] {
  const counts = new Map<string, number>();
  return drafts.map(draft => {
    const key = [draft.card_last4 ?? '', draft.transaction_date, draft.description.toUpperCase().replace(/\s+/g, ' ').trim(), draft.amount_aed, draft.direction].join('|');
    const occurrence = (counts.get(key) ?? 0) + 1;
    counts.set(key, occurrence);
    const transactionId = createHash('sha256').update(`${bankKey}|${key}|${occurrence}`).digest('hex').slice(0, 24);
    const amount = cents(draft.amount_aed);
    return {
      ...draft,
      transaction_id: transactionId,
      signed_amount_aed: money(draft.direction === 'CREDIT' ? -amount : amount)!,
      transaction_type: transactionType(draft.description, draft.direction),
    };
  });
}

function finishStatement(base: Omit<NormalizedStatement, 'schema_version' | 'transaction_count' | 'debit_total_aed' | 'credit_total_aed' | 'calculated_closing_balance_aed' | 'balance_difference_aed' | 'balance_tied' | 'ledger_reconciled'>): NormalizedStatement {
  const debit = base.transactions.filter(row => row.direction === 'DEBIT').reduce((sum, row) => sum + cents(row.amount_aed), 0);
  const credit = base.transactions.filter(row => row.direction === 'CREDIT').reduce((sum, row) => sum + cents(row.amount_aed), 0);
  const opening = base.opening_balance_aed === null ? null : cents(base.opening_balance_aed);
  const closing = base.closing_balance_aed === null ? null : cents(base.closing_balance_aed);
  const calculated = opening === null ? null : opening + debit - credit;
  const difference = calculated === null || closing === null ? null : calculated - closing;
  return {
    schema_version: 1,
    ...base,
    transaction_count: base.transactions.length,
    debit_total_aed: money(debit)!,
    credit_total_aed: money(credit)!,
    calculated_closing_balance_aed: money(calculated),
    balance_difference_aed: money(difference),
    balance_tied: difference !== null && Math.abs(difference) <= 1,
    ledger_reconciled: false,
  };
}

function parseEmiratesIslamic(text: string, sourceFile: string): NormalizedStatement {
  if (!/STATEMENT OF CARD ACCOUNT/i.test(text) || !/OPENING BALANCE/i.test(text)) throw new Error('Document does not match emirates_islamic_v1');
  const start = /From:\s*(\d{1,2})(?:st|nd|rd|th)\s+([A-Za-z]{3})\s+(\d{4})/i.exec(text);
  const end = /(\d{1,2})(?:st|nd|rd|th)\s+([A-Za-z]{3})\s+(\d{4})\s*\nTo:/i.exec(text) ?? /To:\s*(\d{1,2})(?:st|nd|rd|th)\s+([A-Za-z]{3})\s+(\d{4})/i.exec(text);
  const periodStart = start ? isoWord(start[1], start[2], start[3]) : null;
  const periodEnd = end ? isoWord(end[1], end[2], end[3]) : null;
  const opening = new RegExp(`OPENING BALANCE\\s+(${MONEY})`, 'i').exec(text)?.[1];
  const last4 = /PRIMARY CARD NO:\s*\d{4}X+(\d{4})/i.exec(text)?.[1] ?? null;
  const metadata = new RegExp(`Card Limit Available Limit Minimum Payment Due Payment Due Date Total Payment Due Profit/Other Charges \\(AED\\) Current Balance \\(AED\\)\\s+${MONEY}\\s+${MONEY}\\s+(${MONEY})\\s+(\\d{2}/\\d{2}/\\d{2})\\s+(${MONEY})\\s+${MONEY}\\s+(${MONEY})`, 'i').exec(text);
  const row = new RegExp(`^(\\d{1,2})\\s+([A-Z]{3})\\s+(\\d{1,2})\\s+([A-Z]{3})\\s+(.+?)\\s+(${MONEY})(CR)?$`, 'i');
  const drafts: Draft[] = [];
  text.split(/\r?\n/).map(line => line.trim()).filter(Boolean).forEach((line, index) => {
    const match = row.exec(line);
    if (!match) return;
    const postDate = isoPeriodWord(match[1], match[2], periodStart, periodEnd);
    drafts.push({
      transaction_date: isoTransactionWord(match[3], match[4], postDate),
      post_date: postDate,
      card_last4: last4,
      description: match[5].trim(),
      amount_aed: moneyValue(match[6])!,
      direction: match[7] ? 'CREDIT' : 'DEBIT',
      amount_original: null,
      currency_original: 'AED',
      exchange_rate: null,
      source_line: index + 1,
      review_required: false,
    });
  });
  const transactions = finishTransactions('EMIRATES_ISLAMIC', drafts);
  return finishStatement({
    bank: 'Emirates Islamic', adapter: 'emirates_islamic_v1', source_file: sourceFile,
    statement_date: periodEnd, period_start: periodStart, period_end: periodEnd,
    payment_due_date: metadata ? isoDmy(metadata[2], true) : null,
    opening_balance_aed: moneyValue(opening), closing_balance_aed: moneyValue(metadata?.[4]),
    minimum_payment_aed: moneyValue(metadata?.[1]), total_payment_due_aed: moneyValue(metadata?.[3]),
    card_last4s: last4 ? [last4] : [], transactions,
    warnings: transactions.length ? [] : ['No transaction rows were parsed'],
  });
}

function parseAdcb(text: string, sourceFile: string): NormalizedStatement {
  if (!/PREVIOUS BALANCE OUTSTANDING/i.test(text) || !/CARD NO/i.test(text)) throw new Error('Document does not match adcb_v1');
  const opening = new RegExp(`PREVIOUS BALANCE OUTSTANDING\\s+([+-]?${MONEY})`, 'i').exec(text)?.[1];
  const closing = new RegExp(`NEW BALANCE OUTSTANDING\\s+([+-]?${MONEY})`, 'i').exec(text)?.[1];
  const headers = [...text.matchAll(/^\d{2}\/\d{2}\/\d{2}$/gm)].map(match => isoDmy(match[0], true));
  const lines = text.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  const row = new RegExp(`^(\\d{2}/\\d{2}/\\d{4})\\s+(.+?)\\s+(${MONEY})(?:\\s+(CR))?$`, 'i');
  const foreignTail = new RegExp(`^(.*)\\s+(${MONEY})\\s+([A-Z]{3})$`, 'i');
  let currentCard: string | null = null;
  let pending = -1;
  const cards: string[] = [];
  const drafts: Draft[] = [];
  lines.forEach((line, index) => {
    const card = /Card No\s*:\s*X+(\d{4})/i.exec(line)?.[1];
    if (card) { currentCard = card; if (!cards.includes(card)) cards.push(card); return; }
    const rate = /^\[1\s+([A-Z]{3})=AED\s+([0-9.]+)\]$/i.exec(line);
    if (rate && pending >= 0) { drafts[pending].exchange_rate = rate[2]; pending = -1; return; }
    const match = row.exec(line);
    if (!match || ['PREVIOUS BALANCE OUTSTANDING', 'NEW BALANCE OUTSTANDING'].includes(match[2].toUpperCase())) return;
    const foreign = foreignTail.exec(match[2]);
    const amountOriginal = foreign ? moneyValue(foreign[2]) : null;
    drafts.push({
      transaction_date: isoDmy(match[1]), post_date: null, card_last4: currentCard,
      description: (foreign?.[1] ?? match[2]).trim(), amount_aed: moneyValue(match[3])!,
      direction: match[4] ? 'CREDIT' : 'DEBIT', amount_original: amountOriginal,
      currency_original: foreign?.[3].toUpperCase() ?? 'AED', exchange_rate: null,
      source_line: index + 1, review_required: currentCard === null,
    });
    pending = amountOriginal ? drafts.length - 1 : -1;
  });
  const transactions = finishTransactions('ADCB', drafts);
  const dates = transactions.map(row => row.transaction_date).sort();
  return finishStatement({
    bank: 'ADCB', adapter: 'adcb_v1', source_file: sourceFile,
    statement_date: headers[0] ?? null, period_start: dates[0] ?? null,
    period_end: headers[0] ?? dates.at(-1) ?? null, payment_due_date: headers[1] ?? null,
    opening_balance_aed: moneyValue(opening), closing_balance_aed: moneyValue(closing),
    minimum_payment_aed: null, total_payment_due_aed: null, card_last4s: cards,
    transactions, warnings: [
      ...(transactions.length ? [] : ['No transaction rows were parsed']),
      ...(transactions.some(item => item.card_last4 === null) ? ['One or more transactions appeared before a card section header'] : []),
    ],
  });
}

function parseWio(text: string, sourceFile: string): NormalizedStatement {
  if (!/CREDIT STATEMENT/i.test(text) || !/ACCOUNT NUMBER/i.test(text) || !/WIO/i.test(text)) throw new Error('Document does not match wio_credit_v1');
  const period = /FROM\s+(\d{2}\/\d{2}\/\d{4})\s+TO\s+(\d{2}\/\d{2}\/\d{4})/i.exec(text);
  const last4 = /ACCOUNT NUMBER\s+\d*(\d{4})/i.exec(text)?.[1] ?? null;
  const due = new RegExp(`PAYMENT DUE DATE MIN\\. PAYMENT DUE TOTAL TO PAY\\s+(\\d{2}/\\d{2}/\\d{4})\\s+(${MONEY})\\s+(${MONEY})`, 'i').exec(text);
  const opening = new RegExp(`Balance From Last Statement\\s+([+-]?${MONEY})`, 'i').exec(text)?.[1];
  const closing = new RegExp(`Closing balance(?:\\s+\\(Total to pay\\))?\\s+([+-]?${MONEY})`, 'i').exec(text)?.[1];
  const row = new RegExp(`^(\\d{2}/\\d{2}/\\d{4})\\s+([A-Z]\\d+)\\s+(.+?)(?:\\s+\\*{4}(\\d{4}))?\\s+([+-])(${MONEY})$`, 'i');
  const cards = last4 ? [last4] : [];
  const drafts: Draft[] = [];
  let pending = -1;
  text.split(/\r?\n/).map(line => line.trim()).forEach((line, index) => {
    const rate = /^Rate:\s*([0-9.]+)\s*\(AED\/([A-Z]{3})\)$/i.exec(line);
    if (rate && pending >= 0) { drafts[pending].exchange_rate = rate[1]; drafts[pending].currency_original = rate[2].toUpperCase(); pending = -1; return; }
    const match = row.exec(line);
    if (!match) return;
    const card = match[4] ?? last4;
    if (card && !cards.includes(card)) cards.push(card);
    const direction = match[5] === '+' ? 'CREDIT' : 'DEBIT';
    drafts.push({
      transaction_date: isoDmy(match[1]), post_date: null, card_last4: card,
      description: `${match[3].trim()} [${match[2]}]`, amount_aed: moneyValue(match[6])!,
      direction, amount_original: null, currency_original: 'AED', exchange_rate: null,
      source_line: index + 1, review_required: card === null,
    });
    pending = direction === 'DEBIT' && transactionType(match[3], direction) === 'PURCHASE' ? drafts.length - 1 : -1;
  });
  const transactions = finishTransactions('WIO', drafts);
  return finishStatement({
    bank: 'Wio', adapter: 'wio_credit_v1', source_file: sourceFile,
    statement_date: period ? isoDmy(period[2]) : null,
    period_start: period ? isoDmy(period[1]) : null, period_end: period ? isoDmy(period[2]) : null,
    payment_due_date: due ? isoDmy(due[1]) : null, opening_balance_aed: moneyValue(opening),
    closing_balance_aed: moneyValue(closing), minimum_payment_aed: moneyValue(due?.[2]),
    total_payment_due_aed: moneyValue(due?.[3]), card_last4s: cards, transactions,
    warnings: transactions.length ? [] : ['No transaction rows were parsed'],
  });
}

export function parseStatement(text: string, profile: IssuerProfile, sourceFile = ''): NormalizedStatement {
  if (typeof text !== 'string' || text.trim().length < 20 || text.length > 10_000_000) throw new Error('extracted statement text must contain 20..10000000 characters');
  if (!ISSUER_PROFILES.includes(profile)) throw new Error(`Unknown or unverified issuer profile: ${profile}`);
  if (profile === 'adcb_v1') return parseAdcb(text, sourceFile);
  if (profile === 'emirates_islamic_v1') return parseEmiratesIslamic(text, sourceFile);
  return parseWio(text, sourceFile);
}

export function detectIssuerProfile(text: string): IssuerProfile {
  const matches: IssuerProfile[] = [];
  if (/PREVIOUS BALANCE OUTSTANDING/i.test(text) && /CARD NO/i.test(text)) matches.push('adcb_v1');
  if (/STATEMENT OF CARD ACCOUNT/i.test(text) && /OPENING BALANCE/i.test(text)) matches.push('emirates_islamic_v1');
  if (/CREDIT STATEMENT/i.test(text) && /ACCOUNT NUMBER/i.test(text) && /WIO/i.test(text)) matches.push('wio_credit_v1');
  if (matches.length !== 1) throw new Error(matches.length === 0 ? 'No verified issuer profile recognized this document' : 'Issuer profile detection was ambiguous');
  return matches[0];
}

export function detectAndParseStatement(text: string, sourceFile = ''): NormalizedStatement {
  return parseStatement(text, detectIssuerProfile(text), sourceFile);
}

export function projectStatementToActual(statement: NormalizedStatement): ActualImportTransaction[] {
  if (statement.schema_version !== 1 || !ISSUER_PROFILES.includes(statement.adapter)) throw new Error('statement is not a verified canonical statement');
  if (!Array.isArray(statement.transactions) || statement.transactions.length === 0) throw new Error('statement contains no projectable transactions');
  return statement.transactions.map((row, index) => {
    if (!/^[a-f0-9]{24}$/.test(row.transaction_id)) throw new Error(`transaction ${index} has an invalid deterministic ID`);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(row.transaction_date)) throw new Error(`transaction ${index} has an invalid date`);
    const magnitude = Math.round(Number(row.amount_aed) * 100);
    if (!Number.isSafeInteger(magnitude) || magnitude < 0) throw new Error(`transaction ${index} has an invalid AED magnitude`);
    const amount = row.direction === 'DEBIT' ? -magnitude : magnitude;
    return {
      imported_id: `statement:${statement.adapter}:${row.transaction_id}`,
      date: row.transaction_date,
      amount,
      imported_payee: row.description,
      cleared: true,
    };
  });
}
