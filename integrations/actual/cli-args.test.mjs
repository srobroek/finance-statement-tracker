import assert from "node:assert/strict";
import test from "node:test";

import {
  ACTUALCTL_OPTIONS,
  FULL_REBUILD_OPTIONS,
  MANUAL_STATE_AUDIT_OPTIONS,
  PRODUCTION_REBUILD_OPTIONS,
  parseCliArgs,
} from "./cli-args.mjs";

test("boolean options distinguish omitted, present, and explicit false values", () => {
  assert.equal(parseCliArgs([], ACTUALCTL_OPTIONS).apply, false);
  assert.equal(parseCliArgs(["--apply"], ACTUALCTL_OPTIONS).apply, true);
  assert.equal(parseCliArgs(["--apply", "false"], ACTUALCTL_OPTIONS).apply, false);
  assert.equal(parseCliArgs(["--apply=false"], ACTUALCTL_OPTIONS).apply, false);
  assert.equal(parseCliArgs(["--apply=true"], PRODUCTION_REBUILD_OPTIONS).apply, true);
});

test("boolean options reject values outside the explicit boolean vocabulary", () => {
  assert.throws(
    () => parseCliArgs(["--apply", "0"], PRODUCTION_REBUILD_OPTIONS),
    /expects true or false/,
  );
  assert.throws(
    () => parseCliArgs(["--apply=enabled"], ACTUALCTL_OPTIONS),
    /expects true or false/,
  );
});

test("string options retain false as a string and require an argument", () => {
  assert.equal(
    parseCliArgs(["--root", "false"], FULL_REBUILD_OPTIONS).root,
    "false",
  );
  assert.throws(
    () => parseCliArgs(["--validation"], FULL_REBUILD_OPTIONS),
    /argument missing/,
  );
  assert.throws(
    () => parseCliArgs(["--snapshot", "--output"], MANUAL_STATE_AUDIT_OPTIONS),
    /argument is ambiguous/,
  );
});

test("schemas reject unknown options and positionals while duplicate values are deterministic", () => {
  assert.equal(
    parseCliArgs(["--root", "first", "--root", "second"], FULL_REBUILD_OPTIONS).root,
    "second",
  );
  assert.throws(
    () => parseCliArgs(["--unknown", "value"], FULL_REBUILD_OPTIONS),
    /Unknown option/,
  );
  assert.throws(
    () => parseCliArgs(["unexpected"], FULL_REBUILD_OPTIONS),
    /Unexpected argument/,
  );
});
