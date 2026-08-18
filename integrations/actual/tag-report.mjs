const TAG_PATTERN = /(?:^|\s)#([A-Za-z0-9_:-]+)/g;

export function parseTags(notes = "") {
  return new Set([...String(notes).matchAll(TAG_PATTERN)].map(match => match[1].toLocaleLowerCase()));
}

export function matchesTagFilter(tags, { any = [], all = [], none = [] } = {}) {
  const normalized = new Set([...tags].map(tag => String(tag).toLocaleLowerCase()));
  const anyTags = any.map(tag => String(tag).toLocaleLowerCase());
  const allTags = all.map(tag => String(tag).toLocaleLowerCase());
  const noTags = none.map(tag => String(tag).toLocaleLowerCase());
  if (anyTags.length && !anyTags.some(tag => normalized.has(tag))) return false;
  if (allTags.length && !allTags.every(tag => normalized.has(tag))) return false;
  if (noTags.some(tag => normalized.has(tag))) return false;
  return true;
}

function effectiveRows(transactions) {
  const parents = new Map(transactions.filter(row => row.is_parent).map(row => [row.id, row]));
  const childParentIds = new Set(
    transactions.filter(row => row.is_child || row.parent_id).map(row => row.parent_id).filter(Boolean),
  );
  return transactions
    .filter(row => !(row.is_parent && childParentIds.has(row.id)))
    .map(row => {
      const parent = row.parent_id ? parents.get(row.parent_id) : null;
      if (!parent?.notes) return row;
      return { ...row, notes: `${parent.notes} ${row.notes ?? ""}`.trim() };
    });
}

function dimensionValue(row, groupBy) {
  if (groupBy === "category") return row.category_name ?? "Uncategorized";
  if (groupBy === "payee") return row.payee_name ?? row.imported_payee ?? "Unassigned";
  if (groupBy === "account") return row.account_name ?? "Unassigned";
  throw new Error(`Unsupported group-by dimension: ${groupBy}`);
}

export function buildTagReport(
  transactions,
  { any = [], all = [], none = [], groupBy = "category" } = {},
) {
  const matched = effectiveRows(transactions)
    .map(row => ({ ...row, parsed_tags: parseTags(row.notes) }))
    .filter(row => matchesTagFilter(row.parsed_tags, { any, all, none }));
  const groups = new Map();
  const add = (key, row) => {
    const value = groups.get(key) ?? { key, net: 0, spend: 0, count: 0 };
    const amount = Number(row.amount ?? 0);
    value.net += -amount;
    value.spend += Math.max(-amount, 0);
    value.count += 1;
    groups.set(key, value);
  };
  for (const row of matched) {
    if (groupBy === "tag") {
      const tags = [...row.parsed_tags];
      if (!tags.length) add("Untagged", row);
      else tags.forEach(tag => add(`#${tag}`, row));
    } else {
      add(dimensionValue(row, groupBy), row);
    }
  }
  const matchedNet = matched.reduce((total, row) => total - Number(row.amount ?? 0), 0);
  const matchedSpend = matched.reduce((total, row) => total + Math.max(-Number(row.amount ?? 0), 0), 0);
  const groupedSpend = [...groups.values()].reduce((total, row) => total + row.spend, 0);
  return {
    schema_version: 1,
    filters: { any, all, none },
    group_by: groupBy,
    matched_transaction_count: matched.length,
    matched_net_minor: matchedNet,
    matched_spend_minor: matchedSpend,
    grouped_spend_minor: groupedSpend,
    duplicated_spend_minor: Math.max(groupedSpend - matchedSpend, 0),
    groups: [...groups.values()].sort((left, right) => right.spend - left.spend || left.key.localeCompare(right.key)),
  };
}

export function csvTags(value) {
  return String(value ?? "")
    .split(",")
    .map(tag => tag.trim().replace(/^#/, ""))
    .filter(Boolean);
}
