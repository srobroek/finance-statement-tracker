import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { loadFullRebuildManifests } from "./full-rebuild.mjs";

test("full rebuild loads browser sources before statements with stable ordering", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "full-rebuild-manifest-test-"));
  try {
    await fs.mkdir(path.join(root, "browser"));
    await fs.mkdir(path.join(root, "statements"));
    await fs.writeFile(path.join(root, "browser", "capture.json"), "{}\n");
    await fs.writeFile(path.join(root, "statements", "b.json"), "{}\n");
    await fs.writeFile(path.join(root, "statements", "a.json"), "{}\n");
    const rows = await loadFullRebuildManifests(root, {
      manifest_sources: [
        { id: "statements", import_order: 20, globs: ["statements/*.json"] },
        { id: "browser", import_order: 10, files: ["browser/capture.json"] },
      ],
    });
    assert.deepEqual(rows.map(row => row.source_id), ["browser", "statements", "statements"]);
    assert.deepEqual(rows.slice(1).map(row => path.basename(row.filename)), ["a.json", "b.json"]);
  } finally {
    await fs.rm(root, { recursive: true, force: true });
  }
});

test("full rebuild rejects manifests outside the repository root", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "full-rebuild-root-test-"));
  try {
    await assert.rejects(
      loadFullRebuildManifests(root, {
        manifest_sources: [{ id: "unsafe", files: ["../outside.json"] }],
      }),
      /escapes repository root/,
    );
  } finally {
    await fs.rm(root, { recursive: true, force: true });
  }
});
