import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = resolve(scriptDir, "../../..");
const contractPath = process.env.AI_POLICY_CONTRACT_PATH
  ?? resolve(repositoryRoot, "integrations/n8n/generated/ai-policy-contracts.seed.json");
const inlineToken = process.env.RUNNER_BEARER_TOKEN?.trim();
const tokenPath = process.env.RUNNER_BEARER_TOKEN_FILE?.trim();
const runnerUrl = process.env.RUNNER_URL ?? "http://codex-agent-runner:5090";
const policyId = process.argv[2] ?? "classify-unresolved";
const field = process.argv[3] ?? "category";
const probeLabel = process.argv[4] ?? "probe-1";
const merchant = process.env.PROBE_MERCHANT ?? "CARREFOUR UAE";

if (inlineToken && tokenPath) throw new Error("configure exactly one runner bearer source");
if (!inlineToken && !tokenPath) throw new Error("RUNNER_BEARER_TOKEN or RUNNER_BEARER_TOKEN_FILE is required");

const contract = JSON.parse(await readFile(contractPath, "utf8"));
const matches = contract.rows.filter((row) => row.policy_id === policyId && row.state === "ACTIVE");
if (matches.length !== 1) throw new Error(`expected exactly one ACTIVE ${policyId} contract`);
const row = matches[0];
const allowedFields = JSON.parse(row.allowed_fields_json);
if (!allowedFields.includes(field)) throw new Error(`${field} is not allowed by ${policyId}`);
const domains = JSON.parse(row.allowed_values_json);
const allowedValues = domains[field] ? { [field]: domains[field] } : {};
const idempotencyKey = createHash("sha256")
  .update(JSON.stringify({ policyId, field, probeLabel, merchant }))
  .digest("hex");
const request = {
  schema_version: 1,
  job_id: `finance-ai:${idempotencyKey}`,
  idempotency_key: idempotencyKey,
  operation_code: "FINANCE_AI_PROPOSAL",
  policy_id: policyId,
  policy_class: row.agent_profile === "SOL_XHIGH" ? "EXCEPTION" : "NORMAL",
  policy_sha256: row.policy_sha256,
  config_sha256: row.config_sha256,
  output_schema_sha256: row.output_schema_sha256,
  unresolved: [{
    transaction_id: `probe:${probeLabel}`,
    allowed_fields: [field],
    allowed_values: allowedValues,
    redacted_context: { merchant_raw: merchant, amount_band: "100-499" },
  }],
};

const token = inlineToken ?? (await readFile(tokenPath, "utf8")).trim();
const response = await fetch(`${runnerUrl}/v1/jobs/finance-ai-proposal`, {
  method: "POST",
  headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
  body: JSON.stringify(request),
});
const body = await response.text();
if (!response.ok) throw new Error(`runner returned HTTP ${response.status}: ${body.slice(0, 500)}`);
const parsed = JSON.parse(body);
if (parsed.job_id !== request.job_id || parsed.idempotency_key !== request.idempotency_key) {
  throw new Error("runner response envelope does not match request");
}
process.stdout.write(`${JSON.stringify(parsed, null, 2)}\n`);
