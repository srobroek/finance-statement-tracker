import { parseArgs as nodeParseArgs } from "node:util";

const stringOptions = names => Object.fromEntries(
  names.map(name => [name, { type: "string" }]),
);

const booleanOption = { type: "boolean" };

export const ACTUALCTL_OPTIONS = {
  ...stringOptions([
    "all-tags",
    "any-tags",
    "config",
    "end",
    "group-by",
    "input",
    "name",
    "plan",
    "result",
    "start",
    "without-tags",
  ]),
  apply: booleanOption,
  commit: booleanOption,
};

export const FULL_REBUILD_OPTIONS = stringOptions([
  "bootstrap",
  "end",
  "result",
  "root",
  "snapshot",
  "start",
  "validation",
]);

export const PRODUCTION_REBUILD_OPTIONS = {
  ...stringOptions([
    "approve-preservation-sha256",
    "backup",
    "bootstrap",
    "end",
    "result",
    "root",
    "snapshot",
    "start",
    "validation",
  ]),
  apply: booleanOption,
};

export const MANUAL_STATE_AUDIT_OPTIONS = stringOptions([
  "output",
  "root",
  "snapshot",
  "validation",
]);

function booleanOptionNames(options) {
  return new Set(
    Object.entries(options)
      .filter(([, option]) => option.type === "boolean")
      .map(([name]) => name),
  );
}

function normalizeBooleanTokens(values, names) {
  const normalized = [];
  for (let index = 0; index < values.length; index += 1) {
    const token = values[index];
    if (typeof token === "string" && token.startsWith("--")) {
      const separator = token.indexOf("=");
      const name = token.slice(2, separator === -1 ? undefined : separator);
      if (names.has(name)) {
        if (separator === -1 && typeof values[index + 1] === "string" && !values[index + 1].startsWith("--")) {
          normalized.push(`--${name}=${values[index + 1]}`);
          index += 1;
        } else if (separator === -1) {
          normalized.push(`--${name}=true`);
        } else {
          normalized.push(token);
        }
        continue;
      }
    }
    normalized.push(token);
  }
  return normalized;
}

function normalizeBooleanValue(name, value) {
  if (typeof value === "boolean") return value;
  if (typeof value === "string") {
    if (value.toLowerCase() === "true") return true;
    if (value.toLowerCase() === "false") return false;
  }
  throw new TypeError(`Option --${name} expects true or false`);
}

export function parseCliArgs(values, options) {
  const booleanNames = booleanOptionNames(options);
  const parseOptions = Object.fromEntries(
    Object.entries(options).map(([name, option]) => [
      name,
      option.type === "boolean" ? { ...option, type: "string" } : option,
    ]),
  );
  const parsed = nodeParseArgs({
    args: normalizeBooleanTokens(values, booleanNames),
    options: parseOptions,
    allowPositionals: false,
    strict: true,
  });
  const result = { ...parsed.values };
  for (const name of booleanNames) {
    result[name] = name in result ? normalizeBooleanValue(name, result[name]) : false;
  }
  return result;
}
