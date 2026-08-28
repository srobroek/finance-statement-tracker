import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import type { ProposalRequest, ProposalResponse, ResolvedPolicy } from "./types.js";
import { ContractError, validateResponse } from "./contracts.js";

const MAX_EVENT_BYTES = 1_048_576;

export interface CodexRunnerOptions {
  codexBin: string;
  outputSchemaPath: string;
  timeoutMs: number;
  home: string;
  codexHome: string;
  spawnProcess?: typeof spawn;
}

function cleanEnvironment(options: CodexRunnerOptions): NodeJS.ProcessEnv {
  for (const key of ["OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN"]) {
    if (process.env[key]) throw new ContractError("API_KEY_FORBIDDEN", `${key} must not be present`);
  }
  const env: NodeJS.ProcessEnv = {
    HOME: options.home,
    CODEX_HOME: options.codexHome,
    PATH: process.env.PATH ?? "/usr/local/bin:/usr/bin:/bin",
    LANG: process.env.LANG ?? "C.UTF-8",
  };
  for (const key of ["SSL_CERT_FILE", "SSL_CERT_DIR", "HTTPS_PROXY", "NO_PROXY"]) {
    if (process.env[key]) env[key] = process.env[key];
  }
  return env;
}

async function invoke(
  options: CodexRunnerOptions,
  args: string[],
  input = "",
  maxStdout = MAX_EVENT_BYTES,
): Promise<{ stdout: string; stderr: string }> {
  const child = (options.spawnProcess ?? spawn)(options.codexBin, args, {
    shell: false,
    stdio: ["pipe", "pipe", "pipe"],
    env: cleanEnvironment(options),
  });
  let stdout = "";
  let stderr = "";
  let overflow = false;
  child.stdout?.on("data", (chunk: Buffer) => {
    stdout += chunk.toString("utf8");
    if (Buffer.byteLength(stdout) > maxStdout) {
      overflow = true;
      child.kill("SIGKILL");
    }
  });
  child.stderr?.on("data", (chunk: Buffer) => {
    stderr = (stderr + chunk.toString("utf8")).slice(-16_384);
  });
  child.stdin?.end(input);
  const exitCode = await new Promise<number | null>((resolve, reject) => {
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      reject(new ContractError("CODEX_TIMEOUT", "Codex execution timed out"));
    }, options.timeoutMs);
    child.once("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.once("close", (code) => {
      clearTimeout(timer);
      resolve(code);
    });
  });
  if (overflow) throw new ContractError("CODEX_OUTPUT_LIMIT", "Codex event stream exceeded the limit");
  if (exitCode !== 0) throw new ContractError("CODEX_FAILED", `Codex exited ${String(exitCode)}: ${stderr.slice(-500)}`);
  return { stdout, stderr };
}

export async function assertChatGptLogin(options: CodexRunnerOptions): Promise<void> {
  const result = await invoke(options, ["login", "status"], "", 16_384);
  if (!isChatGptLoginStatus(result.stdout, result.stderr)) {
    throw new ContractError("CHATGPT_LOGIN_REQUIRED", "Codex runner is not authenticated with ChatGPT");
  }
}

export function isChatGptLoginStatus(stdout: string, stderr: string): boolean {
  // Codex CLI 0.148.0 emits this status on stderr. Accept either stream so a
  // harmless CLI presentation change cannot make a valid subscription login
  // look unauthenticated.
  return /Logged in using ChatGPT/i.test(`${stdout}\n${stderr}`);
}

function buildPrompt(request: ProposalRequest, resolved: ResolvedPolicy, receiptId: string): string {
  const envelope = {
    schema_version: 1,
    job_id: request.job_id,
    idempotency_key: request.idempotency_key,
    agent_provider: request.agent_provider,
    policy_id: request.policy_id,
    policy_class: request.policy_class,
    policy_sha256: request.policy_sha256,
    config_sha256: request.config_sha256,
    output_schema_sha256: request.output_schema_sha256,
    runner_receipt_id: receiptId,
    runner_model: resolved.profile.model,
    runner_reasoning_effort: resolved.profile.reasoningEffort,
    auth_mode: "CHATGPT_SUBSCRIPTION",
  };
  return [
    "You are a bounded finance proposal engine. Return only the JSON object required by the supplied output schema.",
    "Never change source facts, amounts, dates, direction, transaction topic, deduplication identity, reconciliation state, or cashback arithmetic.",
    "Propose at most one value for each requested transaction_id and allowed field. Omit uncertain proposals. Do not add fields or prose.",
    "For every proposal, put the proposed JSON value directly in value; do not JSON-encode it as text. Use an empty reason_code when no reason code applies.",
    `Policy instruction: ${resolved.policy.instruction}`,
    `The response envelope must exactly equal: ${JSON.stringify(envelope)}`,
    `Proposal input: ${JSON.stringify(request.unresolved)}`,
  ].join("\n");
}

export function buildCodexExecArgs(
  resolved: ResolvedPolicy,
  options: Pick<CodexRunnerOptions, "outputSchemaPath">,
  finalPath: string,
): string[] {
  return [
    "exec",
    "--ephemeral",
    "--skip-git-repo-check",
    "--ignore-user-config",
    "--ignore-rules",
    "--disable", "code_mode",
    "--disable", "code_mode_host",
    "--sandbox", "read-only",
    "--model", resolved.profile.model,
    "-c", `model_reasoning_effort=\"${resolved.profile.reasoningEffort}\"`,
    "--output-schema", options.outputSchemaPath,
    "--output-last-message", finalPath,
    "--json",
    "-",
  ];
}

function validateEventStream(events: string): void {
  let completed = false;
  for (const line of events.split(/\r?\n/).filter(Boolean)) {
    let event: Record<string, unknown>;
    try {
      event = JSON.parse(line) as Record<string, unknown>;
    } catch {
      throw new ContractError("CODEX_EVENT_INVALID", "Codex emitted non-JSON event output");
    }
    if (event.type === "error" || event.type === "turn.failed") {
      throw new ContractError("CODEX_FAILED", "Codex event stream reported failure");
    }
    if (event.type === "turn.completed") completed = true;
  }
  if (!completed) throw new ContractError("CODEX_INCOMPLETE", "Codex event stream lacks turn.completed");
}

export async function runProposal(
  request: ProposalRequest,
  resolved: ResolvedPolicy,
  options: CodexRunnerOptions,
): Promise<ProposalResponse> {
  await assertChatGptLogin(options);
  const work = await mkdtemp(join(tmpdir(), "finance-codex-"));
  const finalPath = join(work, "final.json");
  const receiptId = randomUUID();
  const args = buildCodexExecArgs(resolved, options, finalPath);
  try {
    const result = await invoke(options, args, buildPrompt(request, resolved, receiptId));
    validateEventStream(result.stdout);
    const parsed = JSON.parse(await readFile(finalPath, "utf8")) as unknown;
    const validated = validateResponse(parsed, request, resolved);
    if (validated.runner_receipt_id !== receiptId) {
      throw new ContractError("ENVELOPE_MISMATCH", "runner_receipt_id was changed by the model");
    }
    return validated;
  } finally {
    await rm(work, { recursive: true, force: true });
  }
}
