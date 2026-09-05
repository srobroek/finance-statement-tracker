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

function cardEvidenceLabel(card) {
  return String(card?.provenance_authority || "").toUpperCase() === "AUTHORITATIVE"
    ? "Issuer terms verified"
    : "Card terms not fully verified";
}

function cardEvidenceNode(card) {
  const node = createNode("div", "source-state");
  node.append(createNode("span", "", cardEvidenceLabel(card)));
  if (card?.position_mode !== "UNLIMITED" && card?.position_detail) {
    node.append(createNode("span", "", card.position_detail));
  }
  if (card?.provenance_reason) node.title = String(card.provenance_reason);
  return node;
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

function tierLabel(tier) {
  const percentage = tier.code.match(/^TIER_(\d+)$/)?.[1];
  return percentage ? `${percentage}%` : tier.code.replaceAll("_", " ");
}

function renderTierPosition(card, actual) {
  const tiers = (card.tiers || []).filter((tier) => Number(tier.minimum_spend_aed) > 0);
  if (tiers.length < 2) return { next: card.safety_target_aed ? `/ ${compactMoney(card.safety_target_aed)}` : "this cycle", ladder: null };
  const lastThreshold = Number(tiers.at(-1).minimum_spend_aed);
  const nextTier = tiers.find((tier) => !tier.met);
  const ladder = createNode("div", "tier-ladder");
  const track = createNode("div", "track");
  const progress = createNode("i");
  setWidth(progress, Math.min(100, actual / lastThreshold * 100));
  track.append(progress);
  ladder.append(track);
  tiers.forEach((tier) => {
    const position = Math.min(100, Number(tier.minimum_spend_aed) / lastThreshold * 100);
    const marker = createNode("span", `tier-marker${tier.met ? " met" : ""}`);
    marker.style.left = `${position}%`;
    marker.append(
      createNode("i"),
      createNode("b", "", tierLabel(tier)),
      createNode("small", "", compactMoney(tier.minimum_spend_aed)),
    );
    ladder.append(marker);
  });
  return {
    next: nextTier ? `Next ${tierLabel(nextTier)} at ${compactMoney(nextTier.minimum_spend_aed)}` : `${tierLabel(tiers.at(-1))} spend target reached`,
    ladder,
  };
}

function typeLabel(item) {
  if (item.label) return item.label;
  return (item.purchase_type || item.channel || "Spend")
    .replaceAll("_", " ")
    .toLowerCase()
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function compactReason(item) {
  const preferred = item.ranked_cards?.[0];
  if (!preferred) return item.reason || "No eligible card route";
  const remaining = Number(preferred.tier_remaining_aed);
  const gap = remaining > 0 ? ` · ${exactMoney(remaining)} more qualifying spend needed` : "";
  return candidateValueLabel(preferred) + gap;
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
    return "No direct cashback · counts toward minimum spend";
  }
  // Routing values simulate a configured purchase amount and can include
  // rewards unlocked on earlier spend. Show rates, never those amounts as
  // cashback earned. A simulated tier crossing is still conditional today.
  const targetUnmet = candidate.estimate_basis === "CONDITIONAL_TARGET_TIER"
    || (candidate.target_tier && candidate.tier_before !== candidate.target_tier);
  if (targetUnmet && Number.isFinite(target)) {
    const currentText = Number.isFinite(current) && current !== target ? ` · current tier ${formatRate(current)}` : "";
    return `${formatRate(target)} if tier requirements are met${currentText}`;
  }
  const rate = Number.isFinite(current) ? current : target;
  return Number.isFinite(rate) ? `${formatRate(rate)} cashback` : "Rate unavailable";
}

function bucketLabel(code) {
  return code.replace(/^(RAK|SC|EI)_/, "").replaceAll("_", " ").toLowerCase();
}

function tierName(code) {
  return code.replaceAll("_", " ").replace(/^TIER (\d+)$/, "$1% tier").toLowerCase();
}

function renderRecommendations(items) {
  const root = document.querySelector("#recommendations");
  const routeItems = Array.isArray(items) ? items : [];
  if (!routeItems.length) {
    root.replaceChildren(emptyState("No routing recommendations yet", "No configured card routes are available."));
    return;
  }
  root.replaceChildren(
    ...routeItems.map((item) => {
      const node = document.createElement("details");
      node.className = "route-row";
      const preferred = item.ranked_cards?.[0];
      const hasRoute = item.active !== false && Boolean(preferred);
      node.classList.toggle("unavailable", !hasRoute);
      const useLabel = hasRoute ? routeHeading(item, preferred, true) : "No eligible route";
      const fullUseLabel = hasRoute ? routeHeading(item, preferred) : "No eligible card route";
      const summary = createNode("summary", "route-main");
      summary.setAttribute(
        "aria-label",
        `${typeLabel(item)}: ${fullUseLabel}. Tap for routing details.`,
      );
      summary.append(createNode("span", "route-type", typeLabel(item)));

      const cards = createNode("span", "route-cards");
      const use = createNode("strong", `card-choice ${hasRoute ? "use" : "unavailable"}`, useLabel);
      use.dataset.short = useLabel;
      use.title = fullUseLabel;
      cards.append(use);

      summary.append(cards);

      const reason = createNode("div", "route-reason");
      reason.append(createNode("span", "", "Why"));
      const reasonText = createNode("p");
      if (hasRoute) {
        reasonText.append(document.createTextNode(compactReason(item)));
      } else {
        reasonText.append(createNode("b", "", "No eligible card route"));
        reasonText.append(document.createTextNode(". This spend type currently has no card that satisfies the configured rules."));
      }
      reason.append(reasonText);
      node.append(summary, reason);
      return node;
    }),
  );
}

function renderDecisionTree(items) {
  const root = document.querySelector("#decision-tree");
  const routeItems = Array.isArray(items) ? items : [];
  const key = (item) => item.code || `${item.purchase_type}:${item.channel}:${item.currency}`;
  const previous = root.dataset.selectedKey;
  root.replaceChildren();
  const selectorLabel = createNode("label", "graph-selector");
  selectorLabel.append(createNode("span", "", "Spend type"));
  const selector = createNode("select");
  selector.setAttribute("aria-label", "Decision-tree spend type");
  routeItems.forEach((item) => {
    const option = createNode("option", "", typeLabel(item));
    option.value = key(item);
    selector.append(option);
  });
  selectorLabel.append(selector);
  const graph = createNode("div", "spend-graph");
  root.append(selectorLabel, graph);
  if (!routeItems.length) {
    root.replaceChildren(emptyState("Decision tree unavailable", "There are no configured routing rules to display."));
    return;
  }
  if (routeItems.some((item) => key(item) === previous)) selector.value = previous;

  const show = () => {
    const item = routeItems.find((candidate) => key(candidate) === selector.value) || routeItems[0];
    if (!item) return;
    root.dataset.selectedKey = key(item);
    const candidates = item.active === false ? [] : (item.ranked_cards || []).map((candidate) => {
      const cap = candidate.bucket_cap_aed == null ? null : Number(candidate.bucket_cap_aed);
      const remaining = candidate.bucket_remaining_aed == null ? null : Number(candidate.bucket_remaining_aed);
      const threshold = Number(candidate.tier_threshold_aed);
      const method = shortPaymentMethod(candidate.payment_channel);
      const bucketText = candidate.position_mode === "UNLIMITED"
        ? `${method} · no cashback cap`
        : cap == null
        ? `${method} · ${bucketLabel(candidate.bucket)} · ${exactMoney(candidate.bucket_spend_aed)} · uncapped`
        : `${method} · ${bucketLabel(candidate.bucket)} ${exactMoney(candidate.bucket_spend_aed)}/${exactMoney(cap)} · ${exactMoney(remaining)} left`;
      const tierText = candidate.position_mode === "UNLIMITED"
        ? "No minimum spend · statement-only totals"
        : threshold > 0
        ? `${exactMoney(candidate.card_spend_aed)} / ${exactMoney(threshold)} qualifying spend · ${exactMoney(candidate.tier_remaining_aed)} to go`
        : `${tierName(candidate.target_tier)} has no minimum spend`;
      const candidateNode = createNode("li", `candidate-node ${candidate.status.toLowerCase()}`);
      const rank = createNode("div", "candidate-rank");
      rank.append(createNode("b", "", candidate.order));
      const candidateCard = createNode("div", "candidate-card");
      const candidateName = createNode("strong", "", compactCardLabel(candidate.card));
      candidateName.title = cardLabel(candidate.card);
      candidateCard.append(candidateName, createNode("span", "", bucketText));
      const logic = createNode("div", "candidate-logic");
      logic.append(
        createNode("b", "", candidateValueLabel(candidate)),
        createNode("span", "", tierText),
      );
      candidateNode.append(rank, candidateCard, logic);
      return candidateNode;
    });
    const methods = (item.methods || []).map((method) => method.replaceAll("_", " ")).join(" · ");
    const header = createNode("header");
    header.append(
      createNode("span", "", methods ? `${methods} · ${item.currency}` : `${item.currency}`),
      createNode("strong", "", `${typeLabel(item)} routing order`),
      createNode("small", "", "Start with the first card for the checked amount. Change the purchase amount to recheck remaining bucket capacity."),
    );
    if (!candidates.length) {
      graph.replaceChildren(header, emptyState("No eligible card route", "This spend type currently has no card that satisfies the configured rules."));
      return;
    }
    const list = createNode("ol");
    list.append(...candidates);
    graph.replaceChildren(header, list);
  };
  selector.addEventListener("change", show);
  show();
}

function setupRoutingViews() {
  document.querySelectorAll("[data-routing-view]").forEach((button) => {
    button.addEventListener("click", () => {
      const view = button.dataset.routingView;
      document.querySelector("#recommendations-view").hidden = view !== "list";
      document.querySelector("#decision-tree").hidden = view !== "tree";
      document.querySelectorAll("[data-routing-view]").forEach((item) => item.setAttribute("aria-selected", String(item === button)));
    });
  });
}

function setupScreenViews() {
  const mobile = window.matchMedia("(max-width: 599px)");
  const buttons = [...document.querySelectorAll("[data-screen-view]")];
  const title = document.querySelector("#screen-title");
  const select = (screen) => {
    if (!mobile.matches) {
      document.body.removeAttribute("data-screen-active");
      title.textContent = "Which card?";
      return;
    }
    const selected = buttons.find((button) => button.dataset.screenView === screen) || buttons[0];
    document.body.dataset.screenActive = selected.dataset.screenView;
    title.textContent = selected.dataset.screenTitle;
    buttons.forEach((button) => button.setAttribute("aria-selected", String(button === selected)));
    window.scrollTo(0, 0);
  };
  buttons.forEach((button) => button.addEventListener("click", () => select(button.dataset.screenView)));
  mobile.addEventListener("change", () => select(document.body.dataset.screenActive || "routing"));
  const requested = new URLSearchParams(window.location.search).get("screen");
  select(requested || document.body.dataset.screenActive || "routing");
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
    if (!isInstalledApp()) {
      button.hidden = false;
      button.disabled = true;
      button.textContent = "Install app for alerts";
      button.title = "Add Cashback to the Home Screen, then enable alerts from the installed app.";
      return;
    }
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
  if (!Array.isArray(cards) || !cards.length) {
    root.replaceChildren(emptyState("No card positions yet", "No card balances are available yet."));
    return;
  }
  root.replaceChildren(
    ...cards.map((card) => {
      if (card.position_mode === "UNLIMITED") {
        const node = document.createElement("article");
        node.className = "position-card unlimited-position-card";
        const header = createNode("div", "position-card-header");
        const summary = createNode("div", "position-summary-copy");
        const name = createNode("strong", "", card.short_name || card.name);
        name.title = card.name;
        summary.append(
          name,
          createNode("span", "", card.tracking_mode === "STATEMENT_ONLY" ? "Statement only" : "Open"),
        );
        const total = createNode("div", "position-total unlimited-position");
        total.append(
          createNode("strong", "", card.position_headline || "Unlimited"),
          createNode("span", "", card.position_detail || "No minimum or cap"),
        );
        header.append(summary, total);
        node.append(header, cardEvidenceNode(card));
        return node;
      }
      const target = Number(card.safety_target_aed || card.total_spend_aed || 1);
      const actual = Number(card.total_spend_aed);
      const percentage = Math.max(0, Math.min(100, (actual / target) * 100));
      const tierPosition = renderTierPosition(card, actual);
      const bucketList = createNode("div", "bucket-list");
      card.buckets.forEach((bucket) => {
        if (!bucket.spend_cap_aed) {
          const row = createNode("div", "bucket-row uncapped");
          const values = createNode("div");
          values.append(
            createNode("span", "", bucket.code.replaceAll("_", " ")),
            createNode("b", "", `${money.format(bucket.spend_aed)} · ${card.reward_eligibility_verified === false ? "eligible limit unverified" : "uncapped"}`),
          );
          row.append(values);
          bucketList.append(row);
          return;
        }
        const fill = Math.min(100, (Number(bucket.spend_aed) / Number(bucket.spend_cap_aed)) * 100);
        const full = bucket.status === "FULL" ? " full" : "";
        const row = createNode("div", "bucket-row");
        const values = createNode("div");
        values.append(
          createNode("span", "", bucket.code.replaceAll("_", " ")),
          createNode("b", "", `${money.format(bucket.spend_aed)} / ${money.format(bucket.spend_cap_aed)}`),
        );
        const track = createNode("div", `track${full}`);
        const progress = createNode("i");
        setWidth(progress, fill);
        track.append(progress);
        row.append(values, track);
        bucketList.append(row);
      });

      const node = document.createElement("article");
      node.className = "position-card";
      const header = createNode("div", "position-card-header");
      const summary = createNode("div", "position-summary-copy");
      const name = createNode("strong", "", card.short_name || card.name);
      name.title = card.name;
      summary.append(
        name,
        createNode("span", "", card.tracking_mode === "STATEMENT_ONLY" ? "Statement only" : `${tierName(card.tier)} · ${(card.pace?.status || "OPEN").replaceAll("_", " ")}`),
      );
      const total = createNode("div", "position-total");
      total.append(createNode("strong", "", money.format(actual)), createNode("span", "", tierPosition.next));
      header.append(summary, total);
      node.append(header);
      if (tierPosition.ladder) {
        node.append(tierPosition.ladder);
      } else if (Number(card.safety_target_aed) > 0) {
        const track = createNode("div", "track primary");
        const progress = createNode("i");
        setWidth(progress, percentage);
        track.append(progress);
        node.append(track);
      }
      node.append(bucketList);
      node.append(cardEvidenceNode(card));
      if (Number(card.refund_effect_aed || 0)) {
        const sourceState = createNode("div", "source-state");
        sourceState.append(createNode("span", "", `${money.format(card.refund_effect_aed)} refunded this cycle`));
        node.append(sourceState);
      }
      return node;
    }),
  );
}

function renderPeriodHistory(periods) {
  const section = document.querySelector("#history-section");
  const selector = document.querySelector("#period-selector");
  const root = document.querySelector("#period-history");
  if (!periods.length) {
    section.hidden = false;
    selector.hidden = true;
    const empty = createNode("article", "empty-state");
    empty.append(
      createNode("strong", "", "No finalized cycles yet"),
      createNode("span", "", "History appears after a statement is imported, reconciled, and finalized."),
    );
    root.replaceChildren(empty);
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
      ["Expected cashback", money.format(card.expected_cashback_aed)],
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
  const root = document.querySelector("#as-of");
  const lastIngest = status?.last_successful_ingest_at;
  const warning = document.querySelector("#feed-warning");
  warning.hidden = Boolean(lastIngest) && !status?.is_stale;
  warning.textContent = !lastIngest
    ? "Feed not checked. Routing and bucket balances may be incomplete."
    : status.is_stale
      ? `Feed is stale. Last checked ${new Date(lastIngest).toLocaleString()}. Routing and bucket balances may be incomplete.`
      : "";
  root.className = `as-of ${status?.is_stale ? "stale" : "live"}`;
  if (!lastIngest) {
    root.textContent = "Feed not checked";
    return;
  }
  const time = new Date(lastIngest).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  root.textContent = status.is_stale ? `Stale · ${time}` : `Live · ${time}`;
  root.title = `Last successful ingest: ${new Date(lastIngest).toLocaleString()}`;
}

function renderRewardDisclosure(estimate) {
  const root = document.querySelector("#reward-disclosure");
  const label = estimate?.label || "Estimated rewards based on configured terms";
  const authority = estimate?.authority || "NON_AUTHORITATIVE";
  const evidenceLabel = authority === "AUTHORITATIVE" ? "Issuer terms verified" : "Card terms not fully verified";
  root.textContent = `${label} · ${evidenceLabel}`;
}

let routingPurchaseAmount = "100";

function setupRoutingAmount() {
  const form = document.querySelector("#routing-amount-form");
  const input = document.querySelector("#routing-amount");
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    routingPurchaseAmount = input.value;
    await refreshDashboard().catch(() => {});
  });
}

let dashboardLoadSequence = 0;

function renderDashboardError(error) {
  const detail = error instanceof Error ? error.message : String(error);
  const status = document.querySelector("#as-of");
  status.className = "as-of stale";
  status.textContent = "Unavailable";
  status.title = detail;
  const warning = document.querySelector("#feed-warning");
  warning.hidden = false;
  warning.textContent = "Dashboard refresh failed. Routing and bucket balances are unavailable.";
  document.querySelector("#recommendations").replaceChildren(emptyState("Dashboard unavailable", "Refresh failed. The next automatic refresh will retry.", "error"));
  document.querySelector("#decision-tree").replaceChildren(emptyState("Decision tree unavailable", "Refresh failed. The next automatic refresh will retry.", "error"));
  document.querySelector("#cards").replaceChildren(emptyState("Card positions unavailable", "Refresh failed. The next automatic refresh will retry.", "error"));
  document.querySelector("#attention-section").hidden = true;
  document.querySelector("#history-section").hidden = false;
  document.querySelector("#period-selector").hidden = true;
  document.querySelector("#period-history").replaceChildren(emptyState("History unavailable", "Refresh failed. The next automatic refresh will retry.", "error"));
}

async function loadDashboard() {
  const sequence = ++dashboardLoadSequence;
  const [response, periodsResponse] = await Promise.all([
    fetch(`/api/dashboard?purchase_amount=${encodeURIComponent(routingPurchaseAmount)}`, { cache: "no-store" }),
    fetch("/api/periods", { cache: "no-store" }),
  ]);
  const payload = await response.json();
  const periodsPayload = await periodsResponse.json();
  if (!response.ok) throw new Error(payload.error || "Dashboard is unavailable.");
  if (!periodsResponse.ok) throw new Error(periodsPayload.error || "Period history is unavailable.");
  if (sequence !== dashboardLoadSequence) return;
  configureDisplay(payload);
  document.querySelector("#routing-currency").textContent = baseCurrency;
  document.querySelector("#routing-amount-status").textContent = `Routes checked for ${new Intl.NumberFormat(undefined, { style: "currency", currency: baseCurrency, maximumFractionDigits: 2 }).format(Number(payload.routing_purchase_amount))}. Change the amount before a different purchase.`;
  renderRewardDisclosure(payload.reward_estimate);
  renderStatus(payload.data_status);
  const routing = payload.routing_graphs?.length ? payload.routing_graphs : payload.recommendations;
  renderRecommendations(routing);
  renderDecisionTree(routing);
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

setupRoutingViews();
setupRoutingAmount();
setupScreenViews();
setupPushNotifications();
refreshDashboard().catch(() => {});

setInterval(() => refreshDashboard().catch(() => {}), 60_000);
