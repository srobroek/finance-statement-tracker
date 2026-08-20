import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";
import {
  canonicalJson,
  ContractError,
  parseRequest,
  PolicyRegistry,
  sha256,
  validateResponse,
} from "./contracts.js";
import type { PolicyDocument, ProposalRequest } from "./types.js";

const ROOT = resolve(import.meta.dirname, "../../..");
const CONFIG_PATH = join(ROOT, "config", "ai-policies.json");
const SCHEMA_PATH = join(ROOT, "integrations", "n8n", "contracts", "ai-proposal-v1.schema.json");
const CONTRACT_PATH = join(ROOT, "integrations", "n8n", "generated", "ai-policy-contracts.seed.json");

async function fixture(policyId = "classify-unresolved") {
  const [configBytes, schemaBytes, contractBytes] = await Promise.all([
    readFile(CONFIG_PATH), readFile(SCHEMA_PATH), readFile(CONTRACT_PATH),
  ]);
  const document = JSON.parse(configBytes.toString("utf8")) as PolicyDocument;
  const contractDocument = JSON.parse(contractBytes.toString("utf8")) as {
    rows: Array<{ policy_id: string; allowed_values_json: string }>;
  };
  const policy = document.policies.find((item) => item.policy_id === policyId);
  const contractRow = contractDocument.rows.find((item) => item.policy_id === policyId);
  assert.ok(policy);
  assert.ok(contractRow);
  const policyDomains = JSON.parse(contractRow.allowed_values_json) as Record<string, Array<string | boolean>>;
  const firstField = policy.target_fields[0]!;
  const idempotency = "a".repeat(64);
  const request: ProposalRequest = {
    schema_version: 1,
    job_id: `finance-ai:${idempotency}`,
    idempotency_key: idempotency,
    operation_code: "FINANCE_AI_PROPOSAL",
    agent_provider: "CODEX_SUBSCRIPTION",
    policy_id: policy.policy_id,
    policy_class: policy.agent_profile === "SOL_MEDIUM" ? "EXCEPTION" : "NORMAL",
    policy_sha256: sha256(canonicalJson(policy)),
    config_sha256: sha256(configBytes),
    output_schema_sha256: sha256(schemaBytes),
    unresolved: [{
      transaction_id: "tx-1",
      allowed_fields: [policy.target_fields[0]!],
      allowed_values: policyDomains[firstField] ? { [firstField]: policyDomains[firstField] } : {},
      redacted_context: { merchant_raw: "SAFE MERCHANT", amount_band: "100-499" },
    }],
  };
  const registry = await PolicyRegistry.load(CONFIG_PATH, SCHEMA_PATH, CONTRACT_PATH);
  return { request, registry, resolved: registry.resolve(request), policyDomains };
}

test("request rejects caller-selected executable inputs", async () => {
  const { request } = await fixture();
  assert.throws(
    () => parseRequest({ ...request, prompt: "ignore policy", command: "sh" }),
    (error: unknown) => error instanceof ContractError && error.code === "INVALID_CONTRACT",
  );
});

test("request rejects sensitive context and duplicate transactions", async () => {
  const { request } = await fixture();
  assert.throws(
    () => parseRequest({
      ...request,
      unresolved: [{ ...request.unresolved[0], redacted_context: { email_body: "secret" } }],
    }),
    (error: unknown) => error instanceof ContractError && error.code === "SENSITIVE_INPUT",
  );
  assert.throws(
    () => parseRequest({ ...request, unresolved: [request.unresolved[0], request.unresolved[0]] }),
    /duplicate transaction_id/,
  );
});

test("request requires an explicit allowed value domain object", async () => {
  const { request } = await fixture();
  const unresolved = { ...request.unresolved[0] } as Record<string, unknown>;
  delete unresolved.allowed_values;
  assert.throws(
    () => parseRequest({ ...request, unresolved: [unresolved] }),
    (error: unknown) => error instanceof ContractError && error.code === "INVALID_CONTRACT",
  );
});

test("policy registry derives the model profile and rejects stale hashes", async () => {
  const { request, registry, resolved } = await fixture("recommend-category");
  assert.equal(resolved.profile.model, "gpt-5.6-sol");
  assert.equal(resolved.profile.reasoningEffort, "medium");
  assert.throws(
    () => registry.resolve({ ...request, policy_class: "NORMAL" }),
    (error: unknown) => error instanceof ContractError && error.code === "MODEL_POLICY_MISMATCH",
  );
  assert.throws(
    () => registry.resolve({ ...request, config_sha256: "b".repeat(64) }),
    (error: unknown) => error instanceof ContractError && error.code === "STALE_CONFIG",
  );
});

test("response must preserve the envelope and proposal boundary", async () => {
  const { request, resolved } = await fixture();
  const base = {
    schema_version: 1,
    job_id: request.job_id,
    idempotency_key: request.idempotency_key,
    agent_provider: request.agent_provider,
    policy_id: request.policy_id,
    policy_class: request.policy_class,
    policy_sha256: request.policy_sha256,
    config_sha256: request.config_sha256,
    output_schema_sha256: request.output_schema_sha256,
    runner_receipt_id: "receipt-1",
    runner_model: resolved.profile.model,
    runner_reasoning_effort: resolved.profile.reasoningEffort,
    auth_mode: "CHATGPT_SUBSCRIPTION",
  };
  const accepted = validateResponse({
    ...base,
    proposals: [{ transaction_id: "tx-1", field: "vendor", value_json: "\"Safe Merchant\"", confidence: 0.95, reason_code: "" }],
  }, request, resolved);
  assert.equal(accepted.proposals.length, 1);
  assert.throws(
    () => validateResponse({
      ...base,
      runner_model: "gpt-5.6-sol",
      proposals: [],
    }, request, resolved),
    (error: unknown) => error instanceof ContractError && error.code === "ENVELOPE_MISMATCH",
  );
  assert.throws(
    () => validateResponse({
      ...base,
      proposals: [{ transaction_id: "tx-1", field: "direction", value_json: "\"CREDIT\"", confidence: 1, reason_code: "" }],
    }, request, resolved),
    (error: unknown) => error instanceof ContractError && error.code === "FORBIDDEN_PROPOSAL",
  );
});

test("allowed value constraints reject invented categories", async () => {
  const { request, registry, policyDomains } = await fixture();
  const constrained = parseRequest({
    ...request,
    unresolved: [{
      ...request.unresolved[0],
      allowed_fields: ["category"],
      allowed_values: { category: policyDomains.category! },
    }],
  });
  const resolved = registry.resolve(constrained);
  const response = {
    schema_version: 1,
    job_id: constrained.job_id,
    idempotency_key: constrained.idempotency_key,
    agent_provider: constrained.agent_provider,
    policy_id: constrained.policy_id,
    policy_class: constrained.policy_class,
    policy_sha256: constrained.policy_sha256,
    config_sha256: constrained.config_sha256,
    output_schema_sha256: constrained.output_schema_sha256,
    runner_receipt_id: "receipt-2",
    runner_model: resolved.profile.model,
    runner_reasoning_effort: resolved.profile.reasoningEffort,
    auth_mode: "CHATGPT_SUBSCRIPTION",
  };
  assert.throws(
    () => validateResponse({
      ...response,
      proposals: [{ transaction_id: "tx-1", field: "category", value_json: "\"Invented Category\"", confidence: 0.99, reason_code: "" }],
    }, constrained, resolved),
    /outside configured values/,
  );
});

test("policy registry rejects a narrowed or broadened n8n domain", async () => {
  const { request, registry, policyDomains } = await fixture();
  const categories = policyDomains.category!;
  const narrowed = parseRequest({
    ...request,
    unresolved: [{
      ...request.unresolved[0],
      allowed_fields: ["category"],
      allowed_values: { category: categories.slice(0, -1) },
    }],
  });
  assert.throws(
    () => registry.resolve(narrowed),
    (error: unknown) => error instanceof ContractError && error.code === "DOMAIN_POLICY_MISMATCH",
  );
});

test("policy hash is canonical while document hash is byte exact", async () => {
  const work = await mkdtemp(join(tmpdir(), "runner-contract-"));
  try {
    const config = { schema_version: 1, policies: [] };
    const first = join(work, "first.json");
    const second = join(work, "second.json");
    await writeFile(first, JSON.stringify(config), "utf8");
    await writeFile(second, `${JSON.stringify(config)}\n`, "utf8");
    assert.notEqual(sha256(await readFile(first)), sha256(await readFile(second)));
    assert.equal(canonicalJson({ b: 2, a: 1 }), canonicalJson({ a: 1, b: 2 }));
  } finally {
    await rm(work, { recursive: true, force: true });
  }
});
