import assert from "node:assert/strict";
import type { AddressInfo } from "node:net";
import { join, resolve } from "node:path";
import test from "node:test";
import { PolicyRegistry } from "./contracts.js";
import { createRunnerServer, loadBearerToken } from "./server.js";

const ROOT = resolve(import.meta.dirname, "../../..");
const CONFIG_PATH = join(ROOT, "config", "ai-policies.json");
const SCHEMA_PATH = join(ROOT, "integrations", "n8n", "contracts", "ai-proposal-v1.schema.json");
const CONTRACT_PATH = join(ROOT, "integrations", "n8n", "generated", "ai-policy-contracts.seed.json");
const TOKEN = "a".repeat(64);

test("loads a Bellwether-style injected bearer and rejects ambiguous sources", async () => {
  assert.equal((await loadBearerToken({ RUNNER_BEARER_TOKEN: TOKEN })).toString("utf8"), TOKEN);
  await assert.rejects(
    () => loadBearerToken({
      RUNNER_BEARER_TOKEN: TOKEN,
      RUNNER_BEARER_TOKEN_FILE: "/run/secrets/codex_agent_runner_bearer",
    }),
    /configure exactly one/,
  );
  await assert.rejects(
    () => loadBearerToken({ RUNNER_BEARER_TOKEN: "too-short" }),
    /64 lowercase hexadecimal characters/,
  );
});

async function withServer(run: (baseUrl: string) => Promise<void>): Promise<void> {
  const registry = await PolicyRegistry.load(CONFIG_PATH, SCHEMA_PATH, CONTRACT_PATH);
  const server = createRunnerServer({
    port: 0,
    bearerToken: Buffer.from(TOKEN),
    registry,
    runner: {
      codexBin: "/not-invoked",
      outputSchemaPath: SCHEMA_PATH,
      timeoutMs: 1,
      home: "/tmp",
      codexHome: "/tmp",
    },
  });
  await new Promise<void>((resolveListen, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolveListen);
  });
  const { port } = server.address() as AddressInfo;
  try {
    await run(`http://127.0.0.1:${port}`);
  } finally {
    await new Promise<void>((resolveClose, reject) => server.close((error) => error ? reject(error) : resolveClose()));
  }
}

test("health is unauthenticated but discloses no credential state", async () => {
  await withServer(async (baseUrl) => {
    const response = await fetch(`${baseUrl}/healthz`);
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.deepEqual(await response.json(), { status: "ok", auth_mode: "CHATGPT_SUBSCRIPTION_REQUIRED" });
  });
});

test("proposal endpoint requires the fixed bearer credential", async () => {
  await withServer(async (baseUrl) => {
    const response = await fetch(`${baseUrl}/v1/jobs/finance-ai-proposal`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: "Bearer wrong" },
      body: "{}",
    });
    assert.equal(response.status, 401);
    assert.deepEqual(await response.json(), { error: "unauthorized" });
  });
});

test("authorized malformed and oversized bodies fail before Codex", async () => {
  await withServer(async (baseUrl) => {
    const headers = { "content-type": "application/json", authorization: `Bearer ${TOKEN}` };
    const malformed = await fetch(`${baseUrl}/v1/jobs/finance-ai-proposal`, {
      method: "POST",
      headers,
      body: "{",
    });
    assert.equal(malformed.status, 422);
    assert.equal((await malformed.json() as { error: string }).error, "INVALID_JSON");

    const oversized = await fetch(`${baseUrl}/v1/jobs/finance-ai-proposal`, {
      method: "POST",
      headers,
      body: JSON.stringify({ padding: "x".repeat(262_144) }),
    });
    assert.equal(oversized.status, 413);
    assert.equal((await oversized.json() as { error: string }).error, "BODY_TOO_LARGE");
  });
});

test("unknown routes remain private and deterministic", async () => {
  await withServer(async (baseUrl) => {
    const response = await fetch(`${baseUrl}/anything-else`);
    assert.equal(response.status, 404);
    assert.deepEqual(await response.json(), { error: "not_found" });
  });
});
