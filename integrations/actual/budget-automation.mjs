const TEMPLATE_TYPES = new Set([
  "average",
  "by",
  "copy",
  "goal",
  "periodic",
  "percentage",
  "refill",
  "remainder",
  "schedule",
  "simple",
  "spend",
]);

const CLEANUP_ROLES = new Set(["source", "sink", "overspend"]);

export function validateBudgetAutomationConfig(config) {
  if (config?.schema_version !== "actual-budget-automation-v1") {
    throw new Error("budget automation config must use actual-budget-automation-v1");
  }
  if (!config.required_actual_version) {
    throw new Error("budget automation config requires required_actual_version");
  }
  if (!Array.isArray(config.categories)) {
    throw new Error("budget automation categories must be an array");
  }
  const names = new Set();
  for (const row of config.categories) {
    if (!row.category || names.has(String(row.category).toLocaleLowerCase())) {
      throw new Error("budget automation category names must be present and unique");
    }
    names.add(String(row.category).toLocaleLowerCase());
    if (!Array.isArray(row.templates) || !Array.isArray(row.cleanup)) {
      throw new Error(`budget automation ${row.category} requires templates and cleanup arrays`);
    }
    for (const template of row.templates) {
      if (!TEMPLATE_TYPES.has(template.type)) {
        throw new Error(`budget automation ${row.category} uses unsupported template ${template.type}`);
      }
      if (!template.directive) {
        throw new Error(`budget automation ${row.category} templates require directive`);
      }
      if (template.directive === "template" && template.type !== "remainder" &&
          !Number.isInteger(template.priority)) {
        throw new Error(`budget automation ${row.category} template priorities must be integers`);
      }
    }
    for (const cleanup of row.cleanup) {
      if (!CLEANUP_ROLES.has(cleanup.role)) {
        throw new Error(`budget automation ${row.category} uses unsupported cleanup role ${cleanup.role}`);
      }
      if (cleanup.role === "sink" && (!Number.isFinite(cleanup.weight) || cleanup.weight <= 0)) {
        throw new Error(`budget automation ${row.category} sink weight must be positive`);
      }
      if (cleanup.role === "overspend" && !cleanup.group) {
        throw new Error(`budget automation ${row.category} overspend cleanup requires a group`);
      }
    }
  }
}

export function cleanupGroupNames(config) {
  validateBudgetAutomationConfig(config);
  return [...new Set(config.categories.flatMap(row =>
    row.cleanup.map(cleanup => cleanup.group).filter(Boolean),
  ))].sort((left, right) => left.localeCompare(right));
}

export function compileCleanup(cleanup, groupIds) {
  return cleanup.map(item => {
    const groupId = item.group ? groupIds.get(String(item.group).toLocaleLowerCase()) : null;
    if (item.group && !groupId) throw new Error(`Unknown cleanup group: ${item.group}`);
    if (item.role === "source") return { role: "source", groupId };
    if (item.role === "sink") return { role: "sink", groupId, weight: item.weight };
    return { role: "overspend", groupId };
  });
}

export function canonicalCleanup(cleanup, groupsById) {
  return cleanup.map(item => ({
    role: item.role,
    ...(item.groupId ? { group: groupsById.get(item.groupId) ?? item.groupId } : {}),
    ...(item.role === "sink" ? { weight: item.weight } : {}),
  }));
}

