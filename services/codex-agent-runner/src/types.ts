export const MODEL_PROFILES = {
  LUNA_MAX: {
    policyClass: "NORMAL",
    model: "gpt-5.6-luna",
    reasoningEffort: "max",
  },
  SOL_XHIGH: {
    policyClass: "EXCEPTION",
    model: "gpt-5.6-sol",
    reasoningEffort: "xhigh",
  },
} as const;

export type AgentProfile = keyof typeof MODEL_PROFILES;
export type PolicyClass = (typeof MODEL_PROFILES)[AgentProfile]["policyClass"];

export const ALLOWED_FIELDS = [
  "vendor",
  "category",
  "subcategory",
  "tags",
  "evidence_policy",
  "review_required",
  "category_recommendation",
  "is_subscription",
  "property_code",
  "rental_unit",
  "channel",
  "reward_bucket",
  "rule_recommendation",
] as const;

export type AllowedField = (typeof ALLOWED_FIELDS)[number];

export interface UnresolvedItem {
  transaction_id: string;
  allowed_fields: AllowedField[];
  redacted_context: Record<string, string | number | boolean | null>;
  allowed_values: Partial<Record<AllowedField, Array<string | boolean>>>;
}

export interface ProposalRequest {
  schema_version: 1;
  job_id: string;
  idempotency_key: string;
  operation_code: "FINANCE_AI_PROPOSAL";
  agent_provider: "CODEX_SUBSCRIPTION" | "CLAUDE_SUBSCRIPTION";
  policy_id: string;
  policy_class: PolicyClass;
  policy_sha256: string;
  config_sha256: string;
  output_schema_sha256: string;
  unresolved: UnresolvedItem[];
}

export interface Proposal {
  transaction_id: string;
  field: AllowedField;
  value: unknown;
  confidence: number;
  reason_code?: string;
}

export interface ProposalResponse {
  schema_version: 1;
  job_id: string;
  idempotency_key: string;
  agent_provider: "CODEX_SUBSCRIPTION" | "CLAUDE_SUBSCRIPTION";
  policy_id: string;
  policy_class: PolicyClass;
  policy_sha256: string;
  config_sha256: string;
  output_schema_sha256: string;
  runner_receipt_id: string;
  runner_model: "gpt-5.6-luna" | "gpt-5.6-sol";
  runner_reasoning_effort: "max" | "xhigh";
  auth_mode: "CHATGPT_SUBSCRIPTION";
  proposals: Proposal[];
}

export interface PolicyConfig {
  policy_id: string;
  version: number;
  agent_profile: AgentProfile;
  instruction: string;
  target_fields: AllowedField[];
  allowed_values?: Partial<Record<AllowedField, Array<string | boolean>>>;
  allowed_tags?: string[];
}

export interface PolicyDocument {
  schema_version: 1;
  policies: PolicyConfig[];
}

export interface PolicyContractRow {
  policy_id: string;
  policy_version: number;
  agent_profile: AgentProfile;
  policy_sha256: string;
  config_sha256: string;
  output_schema_sha256: string;
  allowed_fields_json: string;
  allowed_values_json: string;
  state: "ACTIVE";
}

export interface PolicyContractDocument {
  schema_version: 1;
  rows: PolicyContractRow[];
}

export interface ResolvedPolicyContract {
  allowedFields: AllowedField[];
  allowedValues: Partial<Record<AllowedField, Array<string | boolean>>>;
}

export interface ResolvedPolicy {
  policy: PolicyConfig;
  profile: (typeof MODEL_PROFILES)[AgentProfile];
  contract: ResolvedPolicyContract;
}
