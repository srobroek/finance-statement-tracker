import fs from "node:fs";
import { fileURLToPath } from "node:url";


const configPath = fileURLToPath(new URL("../../config/actual-note-contract.json", import.meta.url));
const contract = JSON.parse(fs.readFileSync(configPath, "utf8"));
if (contract.schema_version !== "actual-note-contract-v2") {
  throw new Error("Unsupported Actual note contract");
}

const forbiddenTags = new Set(contract.forbidden_tags.map(value => String(value).toLowerCase()));
const forbiddenTagPrefixes = (contract.forbidden_tag_prefixes ?? []).map(value => String(value).toLowerCase());
const allowedDetails = new Map(contract.detail_order.map((value, index) => [value, index]));
const tagPattern = /^#[a-z0-9][a-z0-9_:-]*$/;


export function validateCanonicalActualNotes(notes) {
  if (typeof notes !== "string") throw new Error("Actual notes must be a string");
  if (!notes) return notes;
  if (/\r|\n|\t/.test(notes) || notes.trim() !== notes) {
    throw new Error("Actual notes contain non-canonical whitespace");
  }
  const segments = notes.split(contract.delimiter);
  let index = 0;
  if (segments[0]?.startsWith("#")) {
    const tags = segments[0].split(" ");
    if (!tags.length || tags.some(tag => !tagPattern.test(tag))) {
      throw new Error("Actual notes contain an invalid tag block");
    }
    const normalized = tags.map(tag => tag.slice(1).toLowerCase());
    if (normalized.some(tag => forbiddenTags.has(tag) || forbiddenTagPrefixes.some(prefix => tag.startsWith(prefix)))) {
      throw new Error("Actual notes contain a forbidden technical tag");
    }
    if (new Set(normalized).size !== normalized.length) {
      throw new Error("Actual notes contain duplicate tags");
    }
    if (normalized.join("\0") !== [...normalized].sort().join("\0")) {
      throw new Error("Actual note tags must be deterministically sorted");
    }
    index = 1;
  }
  let lastDetail = -1;
  for (; index < segments.length; index += 1) {
    const match = /^(Doc|Review|Memo): (.+)$/.exec(segments[index]);
    if (!match) throw new Error(`Unsupported Actual note segment: ${segments[index]}`);
    const detailIndex = allowedDetails.get(match[1]);
    if (detailIndex < lastDetail) throw new Error("Actual note details are out of order");
    if (match[1] === "Doc" && !match[2].startsWith(contract.document_root)) {
      throw new Error(`Actual note documents must live below ${contract.document_root}`);
    }
    if (match[2].includes("|") || match[2].length > 500) {
      throw new Error("Actual note detail is unsafe");
    }
    lastDetail = detailIndex;
  }
  return notes;
}
