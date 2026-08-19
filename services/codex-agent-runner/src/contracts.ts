import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import {
  ALLOWED_FIELDS,
  MODEL_PROFILES,
  type AllowedField,
  type PolicyConfig,
  type PolicyContractDocument,
  type PolicyDocument,
  type Proposal,
  type ProposalRequest,
  type ProposalResponse,
  type ResolvedPolicy,
  type UnresolvedItem,
} from "./types.js";

const HASH = /^[a-f0-9]{64}$/;
const POLICY_ID = /^[a-z0-9][a-z0-9:_-]{0,127}$/;
const FORBIDDEN_CONTEXT_KEY = /(?:message|email|account|card[_-]?number|password|token|secret|credential|cookie)/i;
const allowedFieldSet = new Set<string>(ALLOWED_FIELDS);

function record(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new ContractError("INVALID_CONTRACT", `${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[], label: string): void {
  const allowed = new Set(keys);
  const extras = Object.keys(value).filter((key) => !allowed.has(key));
  if (extras.length) {
    throw new ContractError("INVALID_CONTRACT", `${label} contains forbidden fields: ${extras.join(", ")}`);
  }
}

function text(value: unknown, label: string, max = 256): string {
  if (typeof value !== "string" || value.length < 1 || value.length > max) {
    throw new ContractError("INVALID_CONTRACT", `${label} must be a non-empty string up to ${max} characters`);
  }
  return value;
}

export class ContractError extends Error {
  constructor(public readonly code: string, message: string) {
    super(message);
    this.name = "ContractError";
  }
}

export function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value as Record<string, unknown>)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson((value as Record<string, unknown>)[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

export function sha256(value: string | Buffer): string {
  return createHash("sha256").update(value).digest("hex");
}

function parseAllowedValues(value: unknown, allowedFields: readonly AllowedField[]): UnresolvedItem["allowed_values"] {
  if (value === undefined) {
    throw new ContractError("INVALID_CONTRACT", "allowed_values is required");
  }
  const source = record(value, "allowed_values");
  const result: NonNullable<UnresolvedItem["allowed_values"]> = {};
  for (const [field, rawValues] of Object.entries(source)) {
    if (!allowedFields.includes(field as AllowedField)) {
      throw new ContractError("INVALID_CONTRACT", `allowed_values contains unrequested field ${field}`);
    }
    if (!Array.isArray(rawValues) || rawValues.length < 1 || rawValues.length > 500) {
      throw new ContractError("INVALID_CONTRACT", `allowed_values.${field} must contain 1-500 values`);
    }
    const values = rawValues.map((item) => {
      if (typeof item === "boolean") return item;
      return text(item, `allowed_values.${field}`, 128);
    });
    if (new Set(values.map(String)).size !== values.length) {
      throw new ContractError("INVALID_CONTRACT", `allowed_values.${field} contains duplicates`);
    }
    result[field as AllowedField] = values;
  }
  return result;
}

function parseUnresolved(value: unknown): UnresolvedItem {
  const source = record(value, "unresolved item");
  exactKeys(source, ["transaction_id", "allowed_fields", "redacted_context", "allowed_values"], "unresolved item");
  const transactionId = text(source.transaction_id, "transaction_id");
  if (!Array.isArray(source.allowed_fields) || source.allowed_fields.length < 1) {
    throw new ContractError("INVALID_CONTRACT", "allowed_fields must be a non-empty array");
  }
  const fields = source.allowed_fields.map((field) => {
    if (typeof field !== "string" || !allowedFieldSet.has(field)) {
      throw new ContractError("INVALID_CONTRACT", `unsupported proposal field ${String(field)}`);
    }
    return field as AllowedField;
  });
  if (new Set(fields).size !== fields.length) {
    throw new ContractError("INVALID_CONTRACT", "allowed_fields contains duplicates");
  }
  const context = record(source.redacted_context, "redacted_context");
  if (Object.keys(context).length > 24) {
    throw new ContractError("INVALID_CONTRACT", "redacted_context exceeds 24 properties");
  }
  const redactedContext: UnresolvedItem["redacted_context"] = {};
  for (const [key, raw] of Object.entries(context)) {
    if (FORBIDDEN_CONTEXT_KEY.test(key)) {
      throw new ContractError("SENSITIVE_INPUT", `redacted_context key is forbidden: ${key}`);
    }
    if (raw !== null && !["string", "number", "boolean"].includes(typeof raw)) {
      throw new ContractError("INVALID_CONTRACT", `redacted_context.${key} must be scalar`);
    }
    redactedContext[key] = typeof raw === "string" ? text(raw, `redacted_context.${key}`, 500) : raw as number | boolean | null;
  }
  const allowedValues = parseAllowedValues(source.allowed_values, fields);
  return {
    transaction_id: transactionId,
    allowed_fields: fields,
    redacted_context: redactedContext,
    allowed_values: allowedValues,
  };
}

export function parseRequest(value: unknown): ProposalRequest {
  const source = record(value, "request");
  exactKeys(source, [
    "schema_version", "job_id", "idempotency_key", "operation_code", "policy_id", "policy_class",
    "policy_sha256", "config_sha256", "output_schema_sha256", "unresolved",
  ], "request");
  if (source.schema_version !== 1 || source.operation_code !== "FINANCE_AI_PROPOSAL") {
    throw new ContractError("INVALID_CONTRACT", "unsupported request schema or operation");
  }
  const idempotencyKey = text(source.idempotency_key, "idempotency_key", 64);
  if (!HASH.test(idempotencyKey) || source.job_id !== `finance-ai:${idempotencyKey}`) {
    throw new ContractError("INVALID_CONTRACT", "job_id/idempotency_key mismatch");
  }
  const policyId = text(source.policy_id, "policy_id", 128);
  if (!POLICY_ID.test(policyId)) throw new ContractError("INVALID_CONTRACT", "invalid policy_id");
  if (source.policy_class !== "NORMAL" && source.policy_class !== "EXCEPTION") {
    throw new ContractError("INVALID_CONTRACT", "invalid policy_class");
  }
  for (const field of ["policy_sha256", "config_sha256", "output_schema_sha256"] as const) {
    if (typeof source[field] !== "string" || !HASH.test(source[field])) {
      throw new ContractError("INVALID_CONTRACT", `${field} must be sha256`);
    }
  }
  if (!Array.isArray(source.unresolved) || source.unresolved.length < 1 || source.unresolved.length > 100) {
    throw new ContractError("INVALID_CONTRACT", "unresolved must contain 1-100 transactions");
  }
  const unresolved = source.unresolved.map(parseUnresolved);
  if (new Set(unresolved.map((item) => item.transaction_id)).size !== unresolved.length) {
    throw new ContractError("INVALID_CONTRACT", "duplicate transaction_id");
  }
  return {
    schema_version: 1,
    job_id: String(source.job_id),
    idempotency_key: idempotencyKey,
    operation_code: "FINANCE_AI_PROPOSAL",
    policy_id: policyId,
    policy_class: source.policy_class,
    policy_sha256: String(source.policy_sha256),
    config_sha256: String(source.config_sha256),
    output_schema_sha256: String(source.output_schema_sha256),
    unresolved,
  };
}

export class PolicyRegistry {
  private constructor(
    private readonly document: PolicyDocument,
    private readonly configHash: string,
    private readonly outputSchemaHash: string,
    private readonly contracts: Map<string, ResolvedPolicy["contract"]>,
  ) {}

  static async load(configPath: string, outputSchemaPath: string, contractPath: string): Promise<PolicyRegistry> {
    const [configBytes, schemaBytes, contractBytes] = await Promise.all([
      readFile(configPath), readFile(outputSchemaPath), readFile(contractPath),
    ]);
    const parsed = JSON.parse(configBytes.toString("utf8")) as PolicyDocument;
    const contractDocument = JSON.parse(contractBytes.toString("utf8")) as PolicyContractDocument;
    if (parsed.schema_version !== 1 || !Array.isArray(parsed.policies)) {
      throw new ContractError("INVALID_CONFIG", "AI policy document is invalid");
    }
    if (contractDocument.schema_version !== 1 || !Array.isArray(contractDocument.rows)) {
      throw new ContractError("INVALID_CONFIG", "AI policy contract document is invalid");
    }
    const configHash = sha256(configBytes);
    const outputSchemaHash = sha256(schemaBytes);
    const contracts = new Map<string, ResolvedPolicy["contract"]>();
    for (const policy of parsed.policies) {
      const rows = contractDocument.rows.filter((row) => row.policy_id === policy.policy_id && row.state === "ACTIVE");
      if (rows.length !== 1) {
        throw new ContractError("INVALID_CONFIG", `policy ${policy.policy_id} must have exactly one active contract`);
      }
      const row = rows[0]!;
      const expectedPolicyHash = sha256(canonicalJson(policy));
      if (row.policy_version !== policy.version || row.agent_profile !== policy.agent_profile ||
          row.policy_sha256 !== expectedPolicyHash || row.config_sha256 !== configHash ||
          row.output_schema_sha256 !== outputSchemaHash) {
        throw new ContractError("INVALID_CONFIG", `policy contract drift for ${policy.policy_id}`);
      }
      let allowedFields: unknown;
      let allowedValues: unknown;
      try {
        allowedFields = JSON.parse(row.allowed_fields_json);
        allowedValues = JSON.parse(row.allowed_values_json);
      } catch {
        throw new ContractError("INVALID_CONFIG", `policy contract JSON is invalid for ${policy.policy_id}`);
      }
      if (!Array.isArray(allowedFields) || allowedFields.length < 1 ||
          allowedFields.some((field) => typeof field !== "string" || !allowedFieldSet.has(field)) ||
          canonicalJson(allowedFields) !== canonicalJson(policy.target_fields)) {
        throw new ContractError("INVALID_CONFIG", `policy contract fields drift for ${policy.policy_id}`);
      }
      const parsedDomains = parseAllowedValues(allowedValues, allowedFields as AllowedField[]);
      contracts.set(policy.policy_id, {
        allowedFields: allowedFields as AllowedField[],
        allowedValues: parsedDomains,
      });
    }
    if (contracts.size !== parsed.policies.length || contractDocument.rows.length !== parsed.policies.length) {
      throw new ContractError("INVALID_CONFIG", "policy contract contains missing or unexpected rows");
    }
    return new PolicyRegistry(parsed, configHash, outputSchemaHash, contracts);
  }

  resolve(request: ProposalRequest): ResolvedPolicy {
    if (request.config_sha256 !== this.configHash || request.output_schema_sha256 !== this.outputSchemaHash) {
      throw new ContractError("STALE_CONFIG", "request configuration hashes do not match the runner image");
    }
    const policy = this.document.policies.find((candidate) => candidate.policy_id === request.policy_id);
    if (!policy) throw new ContractError("UNKNOWN_POLICY", `unknown AI policy ${request.policy_id}`);
    const expectedPolicyHash = sha256(canonicalJson(policy));
    if (request.policy_sha256 !== expectedPolicyHash) {
      throw new ContractError("STALE_CONFIG", "request policy hash does not match the runner image");
    }
    const profile = MODEL_PROFILES[policy.agent_profile];
    if (!profile || request.policy_class !== profile.policyClass) {
      throw new ContractError("MODEL_POLICY_MISMATCH", "policy class is not derived from the configured agent profile");
    }
    const contract = this.contracts.get(policy.policy_id);
    if (!contract) throw new ContractError("INVALID_CONFIG", `missing compiled contract for ${policy.policy_id}`);
    const targetFields = new Set(contract.allowedFields);
    for (const item of request.unresolved) {
      for (const field of item.allowed_fields) {
        if (!targetFields.has(field)) {
          throw new ContractError("FIELD_POLICY_MISMATCH", `${field} is not a target of ${policy.policy_id}`);
        }
        const expected = contract.allowedValues[field];
        const supplied = item.allowed_values[field];
        if (expected && canonicalJson(supplied) !== canonicalJson(expected)) {
          throw new ContractError("DOMAIN_POLICY_MISMATCH", `${field} domain does not match the compiled policy contract`);
        }
        if (!expected && supplied !== undefined) {
          throw new ContractError("DOMAIN_POLICY_MISMATCH", `${field} must not carry an unconfigured value domain`);
        }
      }
    }
    return { policy, profile, contract };
  }
}

function validateValue(proposal: Proposal, item: UnresolvedItem, policy: PolicyConfig): void {
  const { field, value } = proposal;
  if (field === "tags") {
    if (!Array.isArray(value) || value.length < 1 || value.length > 12 || new Set(value).size !== value.length ||
      value.some((tag) => typeof tag !== "string" || !/^[a-z0-9:_-]{1,64}$/.test(tag))) {
      throw new ContractError("INVALID_PROPOSAL", "tags proposal is invalid");
    }
    if (policy.allowed_tags && value.some((tag) => !policy.allowed_tags?.includes(String(tag)))) {
      throw new ContractError("INVALID_PROPOSAL", "tags proposal contains an unconfigured tag");
    }
  } else if (field === "review_required" || field === "is_subscription") {
    if (typeof value !== "boolean") throw new ContractError("INVALID_PROPOSAL", `${field} must be boolean`);
  } else if (field === "category_recommendation") {
    const candidate = record(value, "category_recommendation");
    exactKeys(candidate, ["name", "group", "reason"], "category_recommendation");
    text(candidate.name, "category recommendation name", 80);
    text(candidate.group, "category recommendation group", 80);
    text(candidate.reason, "category recommendation reason", 300);
  } else if (field === "rule_recommendation") {
    const candidate = record(value, "rule_recommendation");
    if (candidate.enabled !== false || !Number.isInteger(candidate.evidence_count) || Number(candidate.evidence_count) < 3) {
      throw new ContractError("INVALID_PROPOSAL", "rule recommendation must be disabled and evidence-backed");
    }
  } else {
    text(value, `${field} proposal`, 128);
  }
  const configured = policy.allowed_values?.[field];
  const dynamic = item.allowed_values?.[field];
  const allowed = dynamic ?? configured;
  if (allowed && !allowed.some((candidate) => canonicalJson(candidate) === canonicalJson(value))) {
    throw new ContractError("INVALID_PROPOSAL", `${field} proposal is outside configured values`);
  }
}

export function validateResponse(value: unknown, request: ProposalRequest, resolved: ResolvedPolicy): ProposalResponse {
  const source = record(value, "response");
  exactKeys(source, [
    "schema_version", "job_id", "idempotency_key", "policy_id", "policy_class", "policy_sha256",
    "config_sha256", "output_schema_sha256", "runner_receipt_id", "runner_model",
    "runner_reasoning_effort", "auth_mode", "proposals",
  ], "response");
  const expected: Record<string, unknown> = {
    schema_version: 1,
    job_id: request.job_id,
    idempotency_key: request.idempotency_key,
    policy_id: request.policy_id,
    policy_class: request.policy_class,
    policy_sha256: request.policy_sha256,
    config_sha256: request.config_sha256,
    output_schema_sha256: request.output_schema_sha256,
    runner_model: resolved.profile.model,
    runner_reasoning_effort: resolved.profile.reasoningEffort,
    auth_mode: "CHATGPT_SUBSCRIPTION",
  };
  for (const [field, expectedValue] of Object.entries(expected)) {
    if (source[field] !== expectedValue) throw new ContractError("ENVELOPE_MISMATCH", `${field} does not match the request`);
  }
  text(source.runner_receipt_id, "runner_receipt_id");
  if (!Array.isArray(source.proposals)) throw new ContractError("INVALID_PROPOSAL", "proposals must be an array");
  const allowedById = new Map(request.unresolved.map((item) => [item.transaction_id, item]));
  const max = request.unresolved.reduce((count, item) => count + item.allowed_fields.length, 0);
  if (source.proposals.length > max) throw new ContractError("INVALID_PROPOSAL", "too many proposals");
  const pairs = new Set<string>();
  const proposals = source.proposals.map((raw) => {
    const proposalSource = record(raw, "proposal");
    exactKeys(proposalSource, ["transaction_id", "field", "value", "confidence", "reason_code"], "proposal");
    const transactionId = text(proposalSource.transaction_id, "proposal.transaction_id");
    const field = proposalSource.field as AllowedField;
    const item = allowedById.get(transactionId);
    if (!item || !item.allowed_fields.includes(field)) throw new ContractError("FORBIDDEN_PROPOSAL", "proposal targets an unrequested field");
    const pair = `${transactionId}\0${field}`;
    if (pairs.has(pair)) throw new ContractError("INVALID_PROPOSAL", "duplicate proposal field");
    pairs.add(pair);
    if (typeof proposalSource.confidence !== "number" || proposalSource.confidence < 0 || proposalSource.confidence > 1) {
      throw new ContractError("INVALID_PROPOSAL", "confidence must be between zero and one");
    }
    const proposal: Proposal = {
      transaction_id: transactionId,
      field,
      value: proposalSource.value,
      confidence: proposalSource.confidence,
      ...(proposalSource.reason_code === undefined ? {} : { reason_code: text(proposalSource.reason_code, "reason_code", 128) }),
    };
    validateValue(proposal, item, resolved.policy);
    return proposal;
  });
  return {
    ...expected,
    runner_receipt_id: String(source.runner_receipt_id),
    proposals,
  } as ProposalResponse;
}
