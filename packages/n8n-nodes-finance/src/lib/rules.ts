import { JsonObject, assertObject, requiredString } from './contracts';
import { RE2JS } from 're2js';

const PROTECTED_FIELDS = new Set([
  'amount', 'amount_aed', 'spend_aed', 'amount_original', 'currency',
  'source_direction', 'transaction_id', 'source_id', 'imported_id',
  'transaction_type', 'transaction_type_locked', 'channel', 'reconciliation_state', 'cashback_finalized', 'locked_fields',
]);
const CONDITION_FIELDS = new Set([
  'transaction_at', 'card', 'account', 'owner', 'institution', 'account_last4',
  'merchant_raw', 'amount', 'amount_aed', 'spend_aed', 'currency', 'amount_original',
  'source_direction', 'channel', 'source_type', 'vendor', 'transaction_type',
  'evidence_policy', 'evidence_status', 'review_required', 'is_refund',
  'is_foreign', 'is_subscription', 'property_code', 'rental_unit', 'reference',
  'mcc', 'history_count', 'tags',
]);
const NUMERIC_FIELDS = new Set(['amount', 'amount_aed', 'spend_aed', 'amount_original', 'history_count']);
const BOOLEAN_FIELDS = new Set(['review_required', 'is_refund', 'is_foreign', 'is_subscription']);
const DATE_FIELDS = new Set(['transaction_at']);
const NUMERIC_OPERATORS = new Set(['numeric_equals', 'gt', 'gte', 'lt', 'lte', 'between', 'polarity']);
const BOOLEAN_OPERATORS = new Set(['is_true', 'is_false']);
const TEXT_OPERATORS = new Set(['contains', 'contains_any', 'starts_with', 'ends_with', 'regex']);
const DATE_OPERATORS = new Set(['date_on', 'date_before', 'date_after', 'date_between']);
const N8N_ACTION_FIELDS = new Set([
  'vendor', 'evidence_policy', 'evidence_status',
  'review_required', 'is_subscription', 'owner', 'property_code', 'rental_unit',
]);
const STAGES = new Set(['TRANSACTION_NORMALIZATION', 'VENDOR_NORMALIZATION', 'EVIDENCE']);
const STAGE_ORDER = ['TRANSACTION_NORMALIZATION', 'VENDOR_NORMALIZATION', 'EVIDENCE'] as const;

export const N8N_RULE_COMPATIBILITY = Object.freeze({
  condition_operators: [
    'equals', 'not_equals', 'contains', 'contains_any', 'not_contains', 'starts_with', 'ends_with',
    'regex', 'in', 'not_in', 'numeric_equals', 'gt', 'gte', 'lt', 'lte', 'between',
    'date_on', 'date_before', 'date_after', 'date_between', 'polarity', 'is_empty',
    'not_empty', 'is_true', 'is_false', 'has_tag',
  ],
  actions: ['set', 'set_if_empty', 'require_review', 'request_evidence'],
  rejected_actual_owned_actions: ['add_tag', 'add_tags', 'remove_tag'],
});

export interface NonRepresentableRule {
  schema_version: 1;
  rule_id: string;
  execution_owner: 'N8N_ONLY';
  actual_representable: false;
  stage: string;
  priority: number;
  match: { any: Array<{ all: Array<{ field: string; operator: string; value?: unknown; second_value?: unknown; case_sensitive?: boolean; negate?: boolean }> }> };
  actions: Array<{ action: 'set' | 'set_if_empty' | 'require_review' | 'request_evidence'; field?: string; value?: unknown }>;
  stop_on_match: boolean;
}

export function normalizeTransaction(input: unknown): JsonObject {
  assertObject(input, 'transaction');
  const result: JsonObject = { ...input };
  if (typeof result.merchant_raw === 'string') result.merchant_raw = result.merchant_raw.replace(/\s+/g, ' ').trim();
  if (typeof result.vendor === 'string') result.vendor = result.vendor.replace(/\s+/g, ' ').trim();
  if (typeof result.currency === 'string') result.currency = result.currency.trim().toUpperCase();
  if (Array.isArray(result.tags)) result.tags = [...new Set(result.tags.filter(value => typeof value === 'string').map(value => value.trim()).filter(Boolean))].sort();
  return result;
}

function comparable(value: unknown, caseSensitive: boolean): unknown {
  return typeof value === 'string' && !caseSensitive ? value.toLocaleLowerCase() : value;
}

function safeRegexSource(value: unknown): string {
  const source = requiredString(value, 'regex', 512);
  try { RE2JS.compile(source); } catch { throw new Error('regular expression is outside the RE2-compatible linear-time subset'); }
  return source;
}

function conditionMatches(row: JsonObject, condition: NonRepresentableRule['match']['any'][number]['all'][number]): boolean {
  const actual = comparable(row[condition.field], Boolean(condition.case_sensitive));
  const expected = comparable(condition.value, Boolean(condition.case_sensitive));
  let matched: boolean;
  switch (condition.operator) {
    case 'equals': matched = actual === expected; break;
    case 'not_equals': matched = actual !== expected; break;
    case 'contains': matched = typeof actual === 'string' && typeof expected === 'string' && actual.includes(expected); break;
    case 'contains_any': matched = Array.isArray(condition.value) && condition.value.some(item => typeof actual === 'string' && actual.includes(String(comparable(item, Boolean(condition.case_sensitive))))); break;
    case 'not_contains': matched = typeof actual === 'string' && typeof expected === 'string' && !actual.includes(expected); break;
    case 'starts_with': matched = typeof actual === 'string' && typeof expected === 'string' && actual.startsWith(expected); break;
    case 'ends_with': matched = typeof actual === 'string' && typeof expected === 'string' && actual.endsWith(expected); break;
    case 'regex': {
      const source = safeRegexSource(condition.value);
      const flags = condition.case_sensitive ? 0 : RE2JS.CASE_INSENSITIVE;
      matched = typeof actual === 'string' && RE2JS.compile(source, flags).test(String(row[condition.field]));
      break;
    }
    case 'in': matched = Array.isArray(condition.value) && condition.value.map(item => comparable(item, Boolean(condition.case_sensitive))).includes(actual); break;
    case 'not_in': matched = Array.isArray(condition.value) && !condition.value.map(item => comparable(item, Boolean(condition.case_sensitive))).includes(actual); break;
    case 'numeric_equals': matched = Number(actual) === Number(expected); break;
    case 'gt': matched = Number(actual) > Number(expected); break;
    case 'gte': matched = Number(actual) >= Number(expected); break;
    case 'lt': matched = Number(actual) < Number(expected); break;
    case 'lte': matched = Number(actual) <= Number(expected); break;
    case 'between': matched = Number(actual) >= Number(expected) && Number(actual) <= Number(condition.second_value); break;
    case 'date_on': matched = String(actual) === String(expected); break;
    case 'date_before': matched = String(actual) < String(expected); break;
    case 'date_after': matched = String(actual) > String(expected); break;
    case 'date_between': matched = String(actual) >= String(expected) && String(actual) <= String(condition.second_value); break;
    case 'polarity': {
      const number = Number(actual);
      matched = expected === 'positive' ? number > 0 : expected === 'negative' ? number < 0 : expected === 'zero' ? number === 0 : false;
      break;
    }
    case 'is_empty': matched = actual === null || actual === undefined || actual === ''; break;
    case 'not_empty': matched = actual !== null && actual !== undefined && actual !== ''; break;
    case 'is_true': matched = actual === true; break;
    case 'is_false': matched = actual === false; break;
    case 'has_tag': {
      const tags = row[condition.field];
      matched = Array.isArray(tags) && tags.map(String).includes(String(condition.value));
      break;
    }
    default: throw new Error(`unsupported N8N_ONLY operator: ${condition.operator}`);
  }
  return condition.negate ? !matched : matched;
}

export function validateNonRepresentableRule(value: unknown): NonRepresentableRule {
  assertObject(value, 'rule');
  if (value.schema_version !== 1 || value.execution_owner !== 'N8N_ONLY' || value.actual_representable !== false) {
    throw new Error('rule must explicitly be owned by N8N_ONLY and non-representable in Actual');
  }
  const stage = requiredString(value.stage, 'rule.stage', 64);
  if (!STAGES.has(stage)) throw new Error(`stage is not eligible for n8n execution: ${stage}`);
  if (!Number.isInteger(value.priority)) throw new Error('rule.priority must be an integer');
  assertObject(value.match, 'rule.match');
  if (!Array.isArray(value.match.any) || value.match.any.length === 0) throw new Error('rule.match.any must be non-empty');
  for (const group of value.match.any) {
    assertObject(group, 'condition group');
    if (!Array.isArray(group.all) || group.all.length === 0) throw new Error('blank condition groups are invalid');
    for (const condition of group.all) {
      assertObject(condition, 'condition');
      const field = requiredString(condition.field, 'condition.field', 64);
      if (!CONDITION_FIELDS.has(field)) throw new Error(`field is not readable by n8n rules: ${field}`);
      const operator = requiredString(condition.operator, 'condition.operator', 32);
      if (!N8N_RULE_COMPATIBILITY.condition_operators.includes(operator)) throw new Error(`unsupported N8N_ONLY operator: ${operator}`);
      if (NUMERIC_OPERATORS.has(operator) && !NUMERIC_FIELDS.has(field)) throw new Error(`numeric operator is invalid for ${field}`);
      if (BOOLEAN_OPERATORS.has(operator) && !BOOLEAN_FIELDS.has(field)) throw new Error(`boolean operator is invalid for ${field}`);
      if (DATE_OPERATORS.has(operator) && !DATE_FIELDS.has(field)) throw new Error(`date operator is invalid for ${field}`);
      if (TEXT_OPERATORS.has(operator) && (NUMERIC_FIELDS.has(field) || BOOLEAN_FIELDS.has(field))) throw new Error(`text operator is invalid for ${field}`);
      if (operator === 'has_tag' && field !== 'tags') throw new Error('has_tag is valid only for tags');
      if (operator === 'polarity' && !['positive', 'negative', 'zero'].includes(String(condition.value))) throw new Error('polarity requires positive, negative, or zero');
      if (operator === 'regex') safeRegexSource(condition.value);
      if (['contains_any', 'in', 'not_in'].includes(operator) && !Array.isArray(condition.value)) throw new Error(`${operator} requires an array value`);
      if (['between', 'date_between'].includes(operator) && condition.second_value === undefined) throw new Error(`${operator} requires second_value`);
    }
  }
  if (!Array.isArray(value.actions) || value.actions.length === 0) throw new Error('rule.actions must be non-empty');
  for (const action of value.actions) {
    assertObject(action, 'action');
    const kind = requiredString(action.action, 'action.action', 32);
    if (!['set', 'set_if_empty', 'require_review', 'request_evidence'].includes(kind)) throw new Error(`unsupported n8n action: ${kind}`);
    if (kind === 'set' || kind === 'set_if_empty') {
      const field = requiredString(action.field, 'action.field', 64);
      const topicDuringNormalization = field === 'transaction_type' && stage === 'TRANSACTION_NORMALIZATION';
      if ((!topicDuringNormalization && PROTECTED_FIELDS.has(field)) || (!topicDuringNormalization && !N8N_ACTION_FIELDS.has(field))) throw new Error(`field is not mutable in n8n: ${field}`);
      if (topicDuringNormalization && !['PAYMENT', 'TRANSFER', 'REWARD_CREDIT', 'REFUND', 'FEE', 'PURCHASE'].includes(String(action.value))) throw new Error('transaction_type action has an invalid canonical topic');
    }
  }
  return value as unknown as NonRepresentableRule;
}

export function applyNonRepresentableRules(input: unknown, values: unknown[]): JsonObject {
  let result = normalizeTransaction(input);
  const rules = values.map(validateNonRepresentableRule).sort((left, right) => STAGE_ORDER.indexOf(left.stage as never) - STAGE_ORDER.indexOf(right.stage as never) || left.priority - right.priority || left.rule_id.localeCompare(right.rule_id));
  let stage = '';
  let stopped = false;
  let topicFinalized = result.transaction_type_locked === true;
  for (const rule of rules) {
    if (rule.stage !== stage) {
      if (!topicFinalized && stage === 'TRANSACTION_NORMALIZATION') { result = finalizeTransactionType(result); topicFinalized = true; }
      if (!topicFinalized && rule.stage !== 'TRANSACTION_NORMALIZATION') { result = finalizeTransactionType(result); topicFinalized = true; }
      stage = rule.stage; stopped = false;
    }
    if (stopped) continue;
    const matched = rule.match.any.some(group => group.all.every(condition => conditionMatches(result, condition)));
    if (!matched) continue;
    for (const action of rule.actions) {
      if (action.action === 'require_review') result.review_required = true;
      else if (action.action === 'request_evidence') result.evidence_status = 'REQUESTED';
      else if (action.action === 'set') result[action.field!] = action.value;
      else if (result[action.field!] === undefined || result[action.field!] === null || result[action.field!] === '') result[action.field!] = action.value;
    }
    if (rule.stop_on_match) stopped = true;
  }
  if (!topicFinalized) result = finalizeTransactionType(result);
  return result;
}

export function finalizeTransactionType(input: JsonObject): JsonObject {
  if (input.transaction_type_locked === true) return input;
  const value = { ...input };
  const description = String(value.description ?? value.merchant_raw ?? '').toUpperCase();
  const direction = String(value.source_direction ?? value.direction ?? '').toUpperCase();
  let topic = typeof value.transaction_type === 'string' ? value.transaction_type.toUpperCase() : '';
  if (!topic) {
    if (direction === 'CREDIT') {
      if (/(PAYMENT RECEIVED|CREDIT REPAYMENT|CARD REPAYMENT)/.test(description)) topic = 'PAYMENT';
      else if (/CASHBACK|REWARD CREDIT/.test(description)) topic = 'REWARD_CREDIT';
      else topic = 'REFUND';
    } else if (/\bTRANSFER\b/.test(description)) topic = 'TRANSFER';
    else if (/\bFEE\b|^VAT ON/.test(description)) topic = 'FEE';
    else topic = 'PURCHASE';
  }
  if (!['PAYMENT', 'TRANSFER', 'REWARD_CREDIT', 'REFUND', 'FEE', 'PURCHASE'].includes(topic)) throw new Error(`cannot finalize unknown transaction topic: ${topic}`);
  value.transaction_type = topic;
  value.transaction_type_locked = true;
  return value;
}

export function assertProtectedFieldsUnchanged(before: unknown, after: unknown): void {
  assertObject(before, 'before'); assertObject(after, 'after');
  for (const field of PROTECTED_FIELDS) {
    if (JSON.stringify(before[field]) !== JSON.stringify(after[field])) throw new Error(`protected field was changed: ${field}`);
  }
}
