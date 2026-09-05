let baseCurrency = "XXX";
let money = currencyFormatter(baseCurrency);
const cardNames = new Map();
const shortCardNames = new Map();

function createNode(tagName, className, text) {
  const node = document.createElement(tagName);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}

function emptyState(title, detail, className = "empty-state") {
  const node = createNode("article", className);
  node.append(createNode("strong", "", title), createNode("span", "", detail));
  return node;
}

function setWidth(node, percentage) {
  node.style.width = `${percentage}%`;
}

function currencyFormatter(currency) {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  });
}

function configureDisplay(payload) {
  baseCurrency = payload.currency || "XXX";
  money = currencyFormatter(baseCurrency);
  if (payload.profile?.name) document.title = payload.profile.name;
  cardNames.clear();
  shortCardNames.clear();
  (payload.cards || []).forEach((card) => {
    cardNames.set(card.card, card.name || card.card.replaceAll("_", " "));
    shortCardNames.set(card.card, card.short_name || card.name || card.card.replaceAll("_", " "));
  });
}

function cardLabel(code) {
  const normalized = String(code || "").trim();
  return cardNames.get(normalized) || (normalized ? normalized.replaceAll("_", " ") : "No card");
}

function compactCardLabel(code) {
  return shortCardNames.get(code) || cardLabel(code);
}

function compactMoney(value) {
  const amount = Number(value);
  if (amount >= 1000) return `${baseCurrency} ${(amount / 1000).toFixed(amount % 1000 ? 1 : 0)}k`;
  return money.format(amount);
}

function exactMoney(value) {
  return new Intl.NumberFormat(undefined, {
    style: "currency", currency: baseCurrency, maximumFractionDigits: 2,
  }).format(Number(value));
}

function typeLabel(item) {
  if (item.label) return tidySpendLabel(item.label);
  return (item.purchase_type || item.channel || "Spend")
    .replaceAll("_", " ")
    .toLowerCase()
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function tidySpendLabel(label) {
  return label.replace(/\bewallet\b/gi, "E-wallet").replace(/\bfiller\b/gi, "Other spend");
}

function routeHeading(item, candidate, compact = false) {
  const code = candidate?.card || item.use_card;
  const name = compact ? compactCardLabel(code) : cardLabel(code);
  const method = shortPaymentMethod(candidate?.payment_channel);
  return method && candidate?.payment_channel !== "UNKNOWN" ? `${name} · ${method}` : name;
}

function shortPaymentMethod(channel) {
  return {
    APPLE_PAY_POS: "Apple Pay",
    PHYSICAL_POS: "Physical",
    ONLINE: "Online",
  }[channel] || channel?.replaceAll("_", " ").toLowerCase() || "";
}

function candidateValueLabel(candidate) {
  const rate = candidateRewardRateLabel(candidate);
  const fee = Number(candidate.configured_fx_fee_percent);
  return Number.isFinite(fee) && fee > 0
    ? `Gross ${rate} · configured FX fee ${fee.toLocaleString(undefined, { maximumFractionDigits: 2 })}%`
    : rate;
}

function candidateRewardRateLabel(candidate) {
  const target = candidate.target_rate_percent == null
    ? Number.NaN : Number(candidate.target_rate_percent);
  const current = candidate.current_tier_rate_percent == null
    ? Number.NaN : Number(candidate.current_tier_rate_percent);
  const formatRate = value => `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}%`;
  if (candidate.position_mode === "UNLIMITED") {
    return Number.isFinite(target) ? `${formatRate(target)} cashback` : "Rate unavailable";
  }
  if (candidate.purpose === "THRESHOLD_FILLER" && target === 0) {
    return "Tier spend only";
  }
  // Routing values simulate a configured purchase amount and can include
  // rewards unlocked on earlier spend. Show rates, never those amounts as
  // cashback earned. A simulated tier crossing is still conditional today.
  const targetUnmet = candidate.estimate_basis === "CONDITIONAL_TARGET_TIER"
    || (candidate.target_tier && candidate.tier_before !== candidate.target_tier);
  if (targetUnmet && Number.isFinite(target)) {
    const currentText = Number.isFinite(current) && current !== target ? ` · now ${formatRate(current)}` : "";
    return `${formatRate(target)} at target tier${currentText}`;
  }
  const rate = Number.isFinite(current) ? current : target;
  return Number.isFinite(rate) ? `${formatRate(rate)} cashback` : "Rate unavailable";
}

function bucketLabel(code) {
  return tidySpendLabel(code.replace(/^(RAK|SC|EI)_/, "").replaceAll("_", " ").toLowerCase());
}

function tierName(code) {
  return ({BASE: "Standard tier", BELOW_MIN: "Minimum not met", ENHANCED: "Higher tier", STANDARD: "Standard tier"})[code] || code.replaceAll("_", " ").replace(/^TIER (\d+)$/, "$1% tier");
}

function renderRecommendations(items) {
  const root = document.querySelector("#recommendations");
  root.replaceChildren(...(items || []).map(item => {
    const routes = item.active === false ? [] : item.ranked_cards || [];
    const node = createNode("details", "route-row");
    const summary = createNode("summary", "route-main");
    summary.append(createNode("strong", "", typeLabel(item)), createNode("span", "route-choice", routes.length ? routeHeading(item, routes[0], true) : "No route"));
    node.append(summary);
    const list = createNode("ol", "route-options");
    routes.forEach(candidate => {
      const row = createNode("li");
      row.append(createNode("strong", "", routeHeading(item, candidate, true)), createNode("span", "", candidateValueLabel(candidate)));
      if (candidate.bucket_remaining_aed != null) row.append(createNode("small", "", `${exactMoney(candidate.bucket_remaining_aed)} left`));
      if (Number(candidate.tier_remaining_aed) > 0) row.append(createNode("small", "", `${exactMoney(candidate.tier_remaining_aed)} to target tier`));
      list.append(row);
    });
    node.append(list);
    return node;
  }));
}

function setupScreenViews() {
  const buttons = [...document.querySelectorAll("[data-screen-view]")];
  const select = value => {
    document.body.dataset.screenActive = value;
    buttons.forEach(button => button.setAttribute("aria-selected", String(button.dataset.screenView === value)));
  };
  buttons.forEach(button => button.addEventListener("click", () => select(button.dataset.screenView)));
  select("cards");
}

function applicationServerKey(value) {
  const padding = "=".repeat((4 - value.length % 4) % 4);
  const base64 = (value + padding).replaceAll("-", "+").replaceAll("_", "/");
  return Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
}

async function browserPushManager() {
  // Declarative Web Push is exposed directly by current Safari/iOS. The app
  // intentionally has no service-worker notification fallback.
  return window.pushManager || null;
}

function isInstalledApp() {
  return window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
}

async function updatePushButton(button, config, manager) {
  const subscription = manager ? await manager.getSubscription() : null;
  button.dataset.subscribed = String(Boolean(subscription));
  button.textContent = subscription ? "Alerts on" : "Enable alerts";
  button.classList.toggle("enabled", Boolean(subscription));
  button.title = subscription
    ? "Push notifications are enabled. Tap to disable."
    : "Enable bucket, cycle and routing notifications.";
  button.hidden = !config.enabled;
}

async function setupPushNotifications() {
  const button = document.querySelector("#push-toggle");
  try {
    const response = await fetch("/api/push/config", { cache: "no-store" });
    const config = await response.json();
    if (!response.ok || !config.enabled || !("Notification" in window)) return;
    if (!isInstalledApp()) return;
    const manager = await browserPushManager();
    if (!manager) return;
    await updatePushButton(button, config, manager);
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        const existing = await manager.getSubscription();
        if (existing) {
          await fetch("/api/push/subscriptions", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action: "unsubscribe", subscription: existing.toJSON() }),
          });
          await existing.unsubscribe();
        } else {
          const permission = await Notification.requestPermission();
          if (permission !== "granted") throw new Error("Notification permission was not granted.");
          const subscription = await manager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: applicationServerKey(config.public_key),
          });
          const subscribeResponse = await fetch("/api/push/subscriptions", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action: "subscribe", subscription: subscription.toJSON() }),
          });
          if (!subscribeResponse.ok) {
            const problem = await subscribeResponse.json();
            await subscription.unsubscribe();
            throw new Error(problem.error || "Could not enable push notifications.");
          }
        }
        await updatePushButton(button, config, manager);
      } catch (error) {
        button.title = error.message;
        button.textContent = "Alerts failed";
      } finally {
        button.disabled = false;
      }
    });
  } catch (error) {
    button.hidden = true;
  }
}

function renderCards(cards) {
  const root = document.querySelector("#cards");
  root.replaceChildren(...(cards || []).map(card => {
    const node = createNode("article", "position-card");
    const details = createNode("details", "card-details");
    const header = createNode("summary", "card-header");
    const nextTier = (card.tiers || []).find(tier => !tier.met && Number(tier.minimum_spend_aed) > 0);
    const position = nextTier
      ? `${exactMoney(card.total_spend_aed || 0)} / ${exactMoney(nextTier.minimum_spend_aed)}`
      : exactMoney(card.total_spend_aed || 0);
    const spend = createNode("span", "", position);
    spend.title = nextTier ? `Spend / ${tierName(nextTier.code)}` : "Cycle spend";
    header.append(createNode("strong", "", card.short_name || card.name), spend);
    details.append(header);
    const facts = createNode("div", "card-facts");
    if (card.safety_target_aed) facts.append(createNode("span", "", `${exactMoney(Math.max(0, Number(card.safety_target_aed) - Number(card.total_spend_aed)))} to ${exactMoney(card.safety_target_aed)} target`));
    if (card.tier) facts.append(createNode("span", "", tierName(card.tier)));
    if (Number(card.refund_effect_aed)) facts.append(createNode("span", "", `${exactMoney(card.refund_effect_aed)} refunded`));
    details.append(facts); node.append(details);
    if (card.reward_eligibility_verified === false || (card.position_headline && /unverified|unknown/i.test(card.position_headline))) node.append(createNode("span", "eligibility", "Reward eligibility unknown"));
    const buckets = createNode("div", "bucket-list");
    (card.buckets || []).forEach(bucket => {
      const spent = Number(bucket.spend_aed || 0);
      const cap = bucket.spend_cap_aed == null ? null : Number(bucket.spend_cap_aed);
      const row = createNode("div", "bucket-row");
      const label = createNode("div", "bucket-label");
      label.append(createNode("span", "", bucketLabel(bucket.code)), createNode("strong", "", cap == null ? exactMoney(spent) : `${exactMoney(Math.max(0, cap - spent))} left`));
      row.append(label);
      if (cap != null && cap > 0) {
        const track = createNode("div", `track${spent >= cap ? " full" : ""}`);
        const fill = createNode("i"); setWidth(fill, Math.max(0, Math.min(100, spent / cap * 100))); track.append(fill); row.append(track);
        row.append(createNode("small", "", `${exactMoney(spent)} / ${exactMoney(cap)}`));
      } else row.append(createNode("small", "", card.reward_eligibility_verified === false ? "Limit unknown" : "No cap"));
      buckets.append(row);
    });
    node.append(buckets); return node;
  }));
}

function renderPeriodHistory(periods) {
  const section = document.querySelector("#history-section");
  const selector = document.querySelector("#period-selector");
  const root = document.querySelector("#period-history");
  if (!periods.length) {
    section.hidden = true;
    root.replaceChildren();
    return;
  }
  section.hidden = false;
  selector.hidden = false;
  selector.replaceChildren(
    ...periods.map((period, index) => {
      const option = document.createElement("option");
      option.value = String(index);
      option.textContent = `${cardLabel(period.card)} · ${period.period_start} – ${period.period_end}`;
      return option;
    }),
  );

  const show = () => {
    const period = periods[Number(selector.value) || 0];
    const card = period.summary;
    const bucketRows = (card.buckets || [])
      .filter((bucket) => Number(bucket.spend_aed) || bucket.spend_cap_aed)
      .map((bucket) => {
        const row = createNode("li");
        row.append(
          createNode("span", "", bucket.code.replaceAll("_", " ")),
          createNode("b", "", `${money.format(bucket.spend_aed)}${bucket.spend_cap_aed ? ` / ${money.format(bucket.spend_cap_aed)}` : ""}`),
        );
        return row;
      });
    const history = createNode("article", "history-card");
    const title = createNode("div", "history-title");
    const titleCopy = createNode("div");
    titleCopy.append(
      createNode("span", "", `${period.period_start} – ${period.period_end}`),
      createNode("h3", "", card.name),
    );
    title.append(titleCopy, createNode("b", "", period.status.replaceAll("_", " ")));
    const metrics = createNode("div", "history-metrics");
    [
      ["Spend", money.format(card.total_spend_aed)],
      ["Cashback", card.expected_cashback_aed == null ? "Unknown" : exactMoney(card.expected_cashback_aed)],
      ["Tier", tierName(card.tier)],
    ].forEach(([label, value]) => {
      const metric = createNode("div");
      metric.append(createNode("span", "", label), createNode("strong", "", value));
      metrics.append(metric);
    });
    history.append(title, metrics, createNode("p", "history-status", period.reconciliation_status.replaceAll("_", " ")));
    if (bucketRows.length) {
      const bucketList = createNode("ul", "history-buckets");
      bucketList.append(...bucketRows);
      history.append(bucketList);
    }
    root.replaceChildren(history);
  };
  selector.addEventListener("change", show);
  show();
}

function renderAttention(payload) {
  const section = document.querySelector("#attention-section");
  const root = document.querySelector("#attention");
  const alerts = [];
  (payload.alerts || []).forEach((alert) => alerts.push(alert));
  if (payload.data_status?.variance_count) {
    const count = payload.data_status.variance_count;
    alerts.push({ key: "reconciliation:variance", title: `${count} statement variance${count === 1 ? "" : "s"}`, detail: "Notification events did not match the authoritative statement and were excluded from cashback totals." });
  }
  if (!alerts.length) {
    section.hidden = true;
    return;
  }
  section.hidden = false;
  const acknowledged = new Set(payload.data_status?.acknowledged_alerts || []);
  const visible = alerts.filter((alert) => !acknowledged.has(alert.key));
  const hidden = alerts.filter((alert) => acknowledged.has(alert.key));
  const alertNode = (alert, checked) => {
      const node = document.createElement("article");
      node.className = "alert-card";
      const copy = createNode("div", "alert-copy");
      copy.append(createNode("strong", "", alert.title), createNode("span", "", alert.detail));
      const actions = createNode("div", "alert-actions");
      const toggle = createNode("label", "alert-toggle");
      const input = createNode("input");
      input.type = "checkbox";
      input.checked = checked;
      toggle.append(input, document.createTextNode(checked ? "Hidden" : "Hide"));
      actions.append(toggle);
      node.append(copy, actions);
      input.addEventListener("change", async (event) => {
        const control = event.currentTarget;
        control.disabled = true;
        let saved = false;
        try {
          await setAlertAcknowledgement(alert.key, control.checked);
          saved = true;
          await refreshDashboard();
        } catch (error) {
          if (!saved) control.checked = !control.checked;
          control.title = error instanceof Error ? error.message : "Could not update alert.";
        } finally {
          control.disabled = false;
        }
      });
      return node;
  };
  root.replaceChildren(...visible.map((alert) => alertNode(alert, false)));
  if (hidden.length) {
    const disclosure = document.createElement("details");
    disclosure.className = "hidden-alerts";
    disclosure.append(createNode("summary", "", `${hidden.length} hidden alert${hidden.length === 1 ? "" : "s"}`));
    disclosure.append(...hidden.map((alert) => alertNode(alert, true)));
    root.append(disclosure);
  }
}

async function setAlertAcknowledgement(alertKey, acknowledged) {
  const response = await fetch("/api/alerts/ack", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ alert_key: alertKey, acknowledged }),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Could not update alert.");
}

function renderStatus(status) {
  const node = document.querySelector("#as-of");
  const last = status?.last_successful_check_at || status?.last_successful_ingest_at;
  node.className = status?.is_stale || !last ? "stale" : "live";
  const stamp = last ? new Date(last).toLocaleString([], {month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}) : "Not synced";
  node.textContent = `${last ? (status?.is_stale ? "Overdue · " : "Checked · ") : ""}${stamp}`;
  node.title = last ? new Date(last).toLocaleString() : "Not synced";
}

let dashboardLoadSequence = 0;

function renderDashboardError(error) {
  const detail = error instanceof Error ? error.message : String(error);
  const status = document.querySelector("#as-of");
  status.className = "as-of stale";
  status.textContent = "Unavailable";
  status.title = detail;

  document.querySelector("#recommendations").replaceChildren(emptyState("Dashboard unavailable", "Refresh failed. The next automatic refresh will retry.", "error"));

  document.querySelector("#cards").replaceChildren(emptyState("Card positions unavailable", "Refresh failed. The next automatic refresh will retry.", "error"));
  document.querySelector("#attention-section").hidden = true;
  document.querySelector("#history-section").hidden = false;
  document.querySelector("#period-selector").hidden = true;
  document.querySelector("#period-history").replaceChildren(emptyState("History unavailable", "Refresh failed. The next automatic refresh will retry.", "error"));
}

async function loadDashboard() {
  const sequence = ++dashboardLoadSequence;
  const [response, periodsResponse] = await Promise.all([
    fetch("/api/dashboard", { cache: "no-store" }),
    fetch("/api/periods", { cache: "no-store" }),
  ]);
  const payload = await response.json();
  const periodsPayload = await periodsResponse.json();
  if (!response.ok) throw new Error(payload.error || "Dashboard is unavailable.");
  if (!periodsResponse.ok) throw new Error(periodsPayload.error || "Period history is unavailable.");
  if (sequence !== dashboardLoadSequence) return;
  configureDisplay(payload);
  renderStatus(payload.data_status);
  const routing = payload.routing_graphs || [];
  renderRecommendations(routing);

  renderAttention(payload);
  renderCards(payload.cards);
  renderPeriodHistory(periodsPayload.periods || []);
}

async function refreshDashboard() {
  const load = loadDashboard();
  const sequence = dashboardLoadSequence;
  try {
    return await load;
  } catch (error) {
    if (sequence === dashboardLoadSequence) renderDashboardError(error);
    throw error;
  }
}

setupScreenViews();
setupPushNotifications();
refreshDashboard().catch(() => {});

setInterval(() => refreshDashboard().catch(() => {}), 60_000);
