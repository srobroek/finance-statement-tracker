import { timingSafeEqual } from "node:crypto";
import { readFile } from "node:fs/promises";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { ContractError, parseRequest, PolicyRegistry } from "./contracts.js";
import { runProposal, type CodexRunnerOptions } from "./codex.js";

const MAX_BODY_BYTES = 262_144;

interface ServiceConfig {
  port: number;
  bearerToken: Buffer;
  registry: PolicyRegistry;
  runner: CodexRunnerOptions;
}

export async function loadBearerToken(
  environment: NodeJS.ProcessEnv = process.env,
): Promise<Buffer> {
  const inlineToken = environment.RUNNER_BEARER_TOKEN?.trim();
  const configuredPath = environment.RUNNER_BEARER_TOKEN_FILE?.trim();
  if (inlineToken && configuredPath) {
    throw new Error("configure exactly one of RUNNER_BEARER_TOKEN or RUNNER_BEARER_TOKEN_FILE");
  }

  const rawToken = inlineToken
    ?? (await readFile(configuredPath ?? "/run/secrets/codex_agent_runner_bearer", "utf8")).trim();
  if (!/^[0-9a-f]{64}$/.test(rawToken)) {
    throw new Error("runner bearer token must be 64 lowercase hexadecimal characters");
  }
  const bearerToken = Buffer.from(rawToken, "utf8");
  return bearerToken;
}

function respond(response: ServerResponse, status: number, value: unknown): void {
  const body = JSON.stringify(value);
  response.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": Buffer.byteLength(body),
    "cache-control": "no-store",
  });
  response.end(body);
}

function authorized(request: IncomingMessage, expected: Buffer): boolean {
  const header = request.headers.authorization;
  if (!header?.startsWith("Bearer ")) return false;
  const supplied = Buffer.from(header.slice(7), "utf8");
  return supplied.length === expected.length && timingSafeEqual(supplied, expected);
}

async function body(request: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const raw of request) {
    const chunk = Buffer.isBuffer(raw) ? raw : Buffer.from(raw);
    size += chunk.length;
    if (size > MAX_BODY_BYTES) throw new ContractError("BODY_TOO_LARGE", "request body exceeds 256 KiB");
    chunks.push(chunk);
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8")) as unknown;
  } catch {
    throw new ContractError("INVALID_JSON", "request body is not valid JSON");
  }
}

export function createRunnerServer(config: ServiceConfig) {
  return createServer(async (request, response) => {
    try {
      if (request.method === "GET" && request.url === "/healthz") {
        respond(response, 200, { status: "ok", auth_mode: "CHATGPT_SUBSCRIPTION_REQUIRED" });
        return;
      }
      if (request.method !== "POST" || request.url !== "/v1/jobs/finance-ai-proposal") {
        respond(response, 404, { error: "not_found" });
        return;
      }
      if (!authorized(request, config.bearerToken)) {
        respond(response, 401, { error: "unauthorized" });
        return;
      }
      const proposalRequest = parseRequest(await body(request));
      const resolved = config.registry.resolve(proposalRequest);
      const result = await runProposal(proposalRequest, resolved, config.runner);
      respond(response, 200, result);
    } catch (error) {
      const known = error instanceof ContractError;
      const status = known && error.code === "BODY_TOO_LARGE" ? 413 : known ? 422 : 500;
      respond(response, status, {
        error: known ? error.code : "INTERNAL_ERROR",
        message: known ? error.message : "Internal runner failure",
      });
    }
  });
}

export async function main(): Promise<void> {
  const policyPath = process.env.AI_POLICY_CONFIG_PATH ?? "/app/config/ai-policies.json";
  const schemaPath = process.env.AI_OUTPUT_SCHEMA_PATH ?? "/app/contracts/ai-proposal-v1.schema.json";
  const policyContractPath = process.env.AI_POLICY_CONTRACT_PATH ?? "/app/contracts/ai-policy-contracts.seed.json";
  const bearerToken = await loadBearerToken();
  const config: ServiceConfig = {
    port: Number(process.env.PORT ?? "5090"),
    bearerToken,
    registry: await PolicyRegistry.load(policyPath, schemaPath, policyContractPath),
    runner: {
      codexBin: process.env.CODEX_BIN ?? "/usr/local/bin/codex",
      outputSchemaPath: schemaPath,
      timeoutMs: Number(process.env.CODEX_TIMEOUT_MS ?? "300000"),
      home: process.env.HOME ?? "/home/node",
      codexHome: process.env.CODEX_HOME ?? "/home/node/.codex",
    },
  };
  const server = createRunnerServer(config);
  server.listen(config.port, "0.0.0.0", () => process.stdout.write(`codex-agent-runner listening on ${config.port}\n`));
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch((error: unknown) => {
    process.stderr.write(`${error instanceof Error ? error.message : "startup failed"}\n`);
    process.exitCode = 1;
  });
}
