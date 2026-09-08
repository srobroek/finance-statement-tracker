import { createHash } from 'node:crypto';
import type { ActualImportTransaction } from './contracts';

export const ISSUER_PROFILES = ['adcb_v1', 'emirates_islamic_v1', 'rakbank_v1', 'wio_credit_v1'] as const;
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
  transaction_type: 'PURCHASE' | 'PAYMENT' | 'REFUND' | 'REWARD_CREDIT' | 'FEE' | 'CREDIT';
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
  const numericYear = Number(year);
  const numericDay = Number(day);
  const date = new Date(Date.UTC(numericYear, monthNumber - 1, numericDay));
  if (!Number.isInteger(numericYear) || date.getUTCFullYear() !== numericYear || date.getUTCMonth() + 1 !== monthNumber || date.getUTCDate() !== numericDay) {
    throw new Error(`Invalid date: ${day} ${month} ${year}`);
  }
  return date.toISOString().slice(0, 10);
}

function resolveStatementDate(day: string, month: string, periodStart: string | null, periodEnd: string | null): string {
  if (!periodStart || !periodEnd) throw new Error(`Cannot resolve ${day} ${month} without authoritative statement bounds`);
  const start = Date.parse(`${periodStart}T00:00:00Z`);
  const end = Date.parse(`${periodEnd}T00:00:00Z`);
  if (!Number.isFinite(start) || !Number.isFinite(end) || start > end) throw new Error('Invalid authoritative statement bounds');
  const candidates: string[] = [];
  for (let year = Number(periodStart.slice(0, 4)); year <= Number(periodEnd.slice(0, 4)); year += 1) {
    try {
      const value = isoWord(day, month, year);
      const timestamp = Date.parse(`${value}T00:00:00Z`);
      if (timestamp >= start && timestamp <= end) candidates.push(value);
    } catch {
      // Invalid calendar dates are not candidates.
    }
  }
  if (candidates.length !== 1) throw new Error(`Ambiguous or out-of-period statement date: ${day} ${month}`);
  return candidates[0];
}

function transactionType(description: string, direction: 'DEBIT' | 'CREDIT'): StatementTransaction['transaction_type'] {
  const value = description.toUpperCase();
  if (['PAYMENT RECEIVED', 'CREDIT REPAYMENT', 'CARD REPAYMENT'].some(token => value.includes(token))) return 'PAYMENT';
  if (value.includes('REFUND')) return 'REFUND';
  if ((value.includes('CASHBACK') || value.includes('REWARD CREDIT')) && direction === 'CREDIT') return 'REWARD_CREDIT';
  if (value.includes('FEE') || value.startsWith('VAT ON')) return 'FEE';
  return direction === 'CREDIT' ? 'CREDIT' : 'PURCHASE';
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
    drafts.push({
      transaction_date: resolveStatementDate(match[3], match[4], periodStart, periodEnd),
      post_date: resolveStatementDate(match[1], match[2], periodStart, periodEnd),
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

function parseRakbank(text: string, sourceFile: string): NormalizedStatement {
  const lines = text.split(/\r?\n/);
  const tableHeaderIndex = lines.findIndex(line => /^DATE\s+TRANSACTION$/i.test(line.trim()));
  if (tableHeaderIndex < 0) throw new Error('rakbank_v1 transaction table header was not found');

  const period = /STATEMENT\s+PERIOD\s*:\s*(\d{2}\/\d{2}\/\d{4})\s+TO\s+(\d{2}\/\d{2}\/\d{4})/i.exec(text);
  if (!period) throw new Error('rakbank_v1 statement period is missing');
  const periodStart = isoDmy(period[1]);
  const periodEnd = isoDmy(period[2]);
  const issued = /DATE\s+ISSUED\s*:\s*(\d{2}\/\d{2}\/\d{4})/i.exec(text);
  const due = /PAYMENT\s+DUE\s+DATE[^\d]{0,120}(\d{2}\/\d{2}\/\d{4})/i.exec(text);
  const openingMatch = new RegExp(`PREVIOUS\\s+BALANCE\\s+AED\\s+(${MONEY})`, 'i').exec(text);
  const closingMatch = new RegExp(`CURRENT\\s+BALANCE\\s+AED\\s+(${MONEY})`, 'i').exec(text);
  if (!openingMatch || !closingMatch) throw new Error('rakbank_v1 printed balance summary is incomplete');

  const headerText = lines.slice(0, tableHeaderIndex).join('\n');
  const minimumMatch = new RegExp(`MINIMUM\\s+PAYMENT\\s+DUE[\\s\\S]{0,120}?AED\\s+(${MONEY})`, 'i').exec(headerText);
  const totalDueMatch = new RegExp(`TOTAL\\s+AMOUNT\\s+DUE[\\s\\S]{0,180}?AED\\s+(${MONEY})`, 'i').exec(headerText);

  const cardLast4s: string[] = [];
  for (const line of lines) {
    const cardMatch = /CARD\s+NUMBER\s*:\s*([^:\r\n]+)/i.exec(line);
    const groups = cardMatch?.[1].match(/\d{4}/g);
    const last4 = groups?.at(-1);
    if (last4 && !cardLast4s.includes(last4)) cardLast4s.push(last4);
  }

  const datePrefix = /^(\d{2}\/\d{2}\/\d{4})\b/;
  const moneyMatches = (value: string): RegExpMatchArray[] => [...value.matchAll(new RegExp(MONEY, 'g'))];
  const drafts: Draft[] = [];
  let index = tableHeaderIndex + 1;
  while (index < lines.length) {
    const line = lines[index].trim();
    if (!line) {
      index += 1;
      continue;
    }
    const dateMatch = datePrefix.exec(line);
    if (!dateMatch) {
      index += 1;
      continue;
    }
    const sourceLine = index + 1;
    let combined = line;
    let nextIndex = index + 1;
    while (moneyMatches(combined).length < 2 && nextIndex < lines.length) {
      const continuation = lines[nextIndex].trim();
      if (datePrefix.test(continuation)) break;
      if (continuation) combined += ` ${continuation}`;
      nextIndex += 1;
    }
    const amounts = moneyMatches(combined);
    if (amounts.length !== 2) throw new Error(`rakbank_v1 transaction row at line ${sourceLine} could not be parsed`);
    const firstIndex = amounts[0].index ?? -1;
    const prefix = combined.slice(dateMatch[0].length, firstIndex).trim();
    const currencies = [...prefix.matchAll(/\b[A-Za-z]{3}\b/g)];
    const currencyMatch = currencies.at(-1);
    if (!currencyMatch || currencyMatch.index === undefined) throw new Error(`rakbank_v1 transaction row at line ${sourceLine} could not be parsed`);
    const currency = currencyMatch[0].toUpperCase();
    const description = prefix.slice(0, currencyMatch.index).trim().replace(/[ ,;:]+$/, '');
    if (!description) throw new Error(`rakbank_v1 transaction row at line ${sourceLine} could not be parsed`);
    const tail = combined.slice(firstIndex + amounts[0][0].length);
    const credit = /\bCR\b/i.test(tail);
    const explicitDebit = new RegExp(`(?:^|\\s)-\\s*${MONEY}(?:\\s+CR)?\\s*$`, 'i').test(tail.trim());
    if (currency === 'AED' && !credit && !explicitDebit) throw new Error(`rakbank_v1 transaction direction is not explicit at line ${sourceLine}`);
    const transactionDate = isoDmy(dateMatch[1]);
    if (transactionDate < periodStart || transactionDate > periodEnd) throw new Error(`rakbank_v1 transaction date is outside statement period at line ${sourceLine}`);
    drafts.push({
      transaction_date: transactionDate,
      post_date: null,
      card_last4: cardLast4s[0] ?? null,
      description,
      amount_aed: moneyValue(amounts[1][0])!,
      direction: credit ? 'CREDIT' : 'DEBIT',
      amount_original: currency === 'AED' ? null : moneyValue(amounts[0][0]),
      currency_original: currency,
      exchange_rate: null,
      source_line: sourceLine,
      review_required: false,
    });
    index = nextIndex;
  }
  if (drafts.length === 0) throw new Error('rakbank_v1 transaction rows were not parsed');

  const summaryStart = lines.findIndex(line => /PREVIOUS\s+BALANCE/i.test(line));
  const summaryEnd = lines.findIndex(line => /CURRENT\s+BALANCE/i.test(line));
  if (summaryStart < 0 || summaryEnd < summaryStart) throw new Error('rakbank_v1 printed balance summary is incomplete');
  let summaryNet = 0;
  let summaryComponents = 0;
  for (const line of lines.slice(summaryStart, summaryEnd + 1)) {
    const summaryMatch = new RegExp(`AED\\s+(${MONEY})(.*)$`, 'i').exec(line);
    if (!summaryMatch) continue;
    if (summaryMatch[2].includes('+')) {
      summaryNet += cents(summaryMatch[1]);
      summaryComponents += 1;
    } else if (summaryMatch[2].includes('-')) {
      summaryNet -= cents(summaryMatch[1]);
      summaryComponents += 1;
    }
  }
  if (summaryComponents === 0) throw new Error('rakbank_v1 printed transaction summary is incomplete');
  const transactions = finishTransactions('RAKBANK', drafts);
  const rowNet = drafts.reduce((sum, draft) => sum + (draft.direction === 'DEBIT' ? cents(draft.amount_aed) : -cents(draft.amount_aed)), 0);
  const opening = moneyValue(openingMatch[1])!;
  const closing = moneyValue(closingMatch[1])!;
  if (rowNet !== summaryNet) throw new Error('rakbank_v1 transaction rows do not reconcile to printed summary');
  if (cents(opening) + rowNet !== cents(closing)) throw new Error('rakbank_v1 printed summary does not reconcile to balances');
  return finishStatement({
    bank: 'RAKBANK',
    adapter: 'rakbank_v1',
    source_file: sourceFile,
    statement_date: issued ? isoDmy(issued[1]) : null,
    period_start: periodStart,
    period_end: periodEnd,
    payment_due_date: due ? isoDmy(due[1]) : null,
    opening_balance_aed: opening,
    closing_balance_aed: closing,
    minimum_payment_aed: moneyValue(minimumMatch?.[1]),
    total_payment_due_aed: moneyValue(totalDueMatch?.[1]),
    card_last4s: cardLast4s,
    transactions,
    warnings: [],
  });
}

export function parseStatement(text: string, profile: IssuerProfile, sourceFile = ''): NormalizedStatement {
  if (typeof text !== 'string' || text.trim().length < 20 || text.length > 10_000_000) throw new Error('extracted statement text must contain 20..10000000 characters');
  if (!ISSUER_PROFILES.includes(profile)) throw new Error(`Unknown or unverified issuer profile: ${profile}`);
  if (profile === 'adcb_v1') return parseAdcb(text, sourceFile);
  if (profile === 'emirates_islamic_v1') return parseEmiratesIslamic(text, sourceFile);
  if (profile === 'rakbank_v1') return parseRakbank(text, sourceFile);
  return parseWio(text, sourceFile);
}

export function detectIssuerProfile(text: string): IssuerProfile {
  const matches: IssuerProfile[] = [];
  if (/PREVIOUS BALANCE OUTSTANDING/i.test(text) && /CARD NO/i.test(text)) matches.push('adcb_v1');
  if (/STATEMENT OF CARD ACCOUNT/i.test(text) && /OPENING BALANCE/i.test(text)) matches.push('emirates_islamic_v1');
  if (/RAKBANK/i.test(text) && /STATEMENT PERIOD/i.test(text) && /CARD NUMBER/i.test(text) && /CURRENT BALANCE/i.test(text)) matches.push('rakbank_v1');
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
