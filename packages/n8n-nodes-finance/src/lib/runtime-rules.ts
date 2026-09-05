import compiled from '../generated/n8n-runtime-rules.json';
import { validateNonRepresentableRule, type NonRepresentableRule } from './rules';

/** Only repository-compiled FULL_LEDGER rules may drive the statement pipeline. */
export function loadPackagedLedgerRules(): NonRepresentableRule[] {
  if (compiled.schema_version !== 1 || compiled.execution_owner !== 'N8N_ONLY'
      || compiled.authoring_source !== 'config/static-rules.seed.json'
      || !/^[a-f0-9]{64}$/.test(compiled.authoring_source_sha256) || !Array.isArray(compiled.rules)) {
    throw new Error('PACKAGED_LEDGER_RULE_CONTRACT_INVALID');
  }
  const rules = compiled.rules.filter(rule => rule.rule_sets.length === 1 && rule.rule_sets[0] === 'FULL_LEDGER')
    .map(validateNonRepresentableRule);
  if (!rules.length || new Set(rules.map(rule => rule.rule_id)).size !== rules.length) {
    throw new Error('PACKAGED_LEDGER_RULE_SET_EMPTY_OR_DUPLICATE');
  }
  return rules;
}

export const PACKAGED_RULE_SOURCE_SHA256 = compiled.authoring_source_sha256;
