import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import test from "node:test";
import { assertChatGptLogin, buildCodexExecArgs, type CodexRunnerOptions } from "./codex.js";
import { ContractError } from "./contracts.js";
import { MODEL_PROFILES, type ResolvedPolicy } from "./types.js";

function resolved(profile: "LUNA_MAX" | "SOL_XHIGH"): ResolvedPolicy {
  return {
    policy: {
      policy_id: "test-policy",
      version: 1,
      agent_profile: profile,
      instruction: "Return a bounded proposal",
      target_fields: ["category"],
    },
    profile: MODEL_PROFILES[profile],
    contract: { allowedFields: ["category"], allowedValues: {} },
  };
}

test("Codex arguments are fixed, ephemeral, read-only, and schema constrained", () => {
  const luna = buildCodexExecArgs(resolved("LUNA_MAX"), { outputSchemaPath: "/schema.json" }, "/tmp/final.json");
  assert.deepEqual(luna, [
    "exec", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules",
    "--disable", "code_mode", "--sandbox", "read-only", "--model", "gpt-5.6-luna",
    "-c", "model_reasoning_effort=\"max\"", "--output-schema", "/schema.json",
    "--output-last-message", "/tmp/final.json", "--json", "-",
  ]);
  const sol = buildCodexExecArgs(resolved("SOL_XHIGH"), { outputSchemaPath: "/schema.json" }, "/tmp/final.json");
  assert.equal(sol[sol.indexOf("--model") + 1], "gpt-5.6-sol");
  assert.ok(sol.includes("model_reasoning_effort=\"xhigh\""));
  assert.ok(!sol.some((value) => /danger|approve-for-me|workspace-write|shell/i.test(value)));
});

test("API-key environment fails before process launch", { concurrency: false }, async () => {
  const before = process.env.OPENAI_API_KEY;
  process.env.OPENAI_API_KEY = "synthetic-test-key";
  const options: CodexRunnerOptions = {
    codexBin: "/usr/local/bin/codex",
    outputSchemaPath: "/schema.json",
    timeoutMs: 1000,
    home: "/home/node",
    codexHome: "/home/node/.codex",
    spawnProcess: (() => { throw new Error("must not spawn"); }) as typeof spawn,
  };
  try {
    await assert.rejects(
      () => assertChatGptLogin(options),
      (error: unknown) => error instanceof ContractError && error.code === "API_KEY_FORBIDDEN",
    );
  } finally {
    if (before === undefined) delete process.env.OPENAI_API_KEY;
    else process.env.OPENAI_API_KEY = before;
  }
});
