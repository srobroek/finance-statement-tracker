const money = new Intl.NumberFormat("en-AE", {
  style: "currency",
  currency: "AED",
  maximumFractionDigits: 0,
});

function cardLabel(code) {
  return {
    RAK_WORLD: "RAK World",
    SC_PLATINUM_X: "SC Platinum X",
    EI_AMAZON: "EI Amazon",
  }[code] || code.replaceAll("_", " ");
}

function compactMoney(value) {
  const amount = Number(value);
  if (amount >= 1000) return `AED ${(amount / 1000).toFixed(amount % 1000 ? 1 : 0)}k`;
  return money.format(amount);
}

function tierLabel(tier) {
  const percentage = tier.code.match(/^TIER_(\d+)$/)?.[1];
  return percentage ? `${percentage}%` : tier.code.replaceAll("_", " ");
}

function renderTierPosition(card, actual) {
  const tiers = (card.tiers || []).filter((tier) => Number(tier.minimum_spend_aed) > 0);
  if (tiers.length < 2) return { next: card.safety_target_aed ? `/ ${compactMoney(card.safety_target_aed)}` : "this cycle", ladder: "" };
  const lastThreshold = Number(tiers.at(-1).minimum_spend_aed);
  const nextTier = tiers.find((tier) => !tier.met);
  const markers = tiers.map((tier) => {
    const position = Math.min(100, Number(tier.minimum_spend_aed) / lastThreshold * 100);
    return `<span class="tier-marker${tier.met ? " met" : ""}" style="left:${position}%"><i></i><b>${tierLabel(tier)}</b><small>${compactMoney(tier.minimum_spend_aed)}</small></span>`;
  }).join("");
  return {
    next: nextTier ? `Next ${tierLabel(nextTier)} at ${compactMoney(nextTier.minimum_spend_aed)}` : `${tierLabel(tiers.at(-1))} secured`,
    ladder: `<div class="tier-ladder"><div class="track"><i style="width:${Math.min(100, actual / lastThreshold * 100)}%"></i></div>${markers}</div>`,
  };
}

function typeLabel(item) {
  if (item.label) return item.label;
  if (item.purchase_type === "AMAZON") return "Amazon";
  if (item.purchase_type === "FILLER") return "Filler";
  if (item.purchase_type === "GROCERY") return "Groceries";
  if (item.purchase_type === "DINING") return "Dining";
  if (item.purchase_type === "TRAVEL") return "Travel";
  if (item.currency !== "AED") return "Foreign";
  if (item.channel === "APPLE_PAY_POS") return "Apple Pay";
  if (item.channel === "ONLINE") return "Online";
  if (item.channel === "PHYSICAL_POS") return "Physical";
  return item.purchase_type.replaceAll("_", " ");
}

function compactReason(item) {
  const preferred = item.ranked_cards?.[0];
  if (preferred?.purpose === "THRESHOLD_FILLER" && Number(preferred.target_rate_percent) === 0) {
    return `Tier filler · ${compactMoney(preferred.card_target_remaining_aed)} remaining · no direct cashback`;
  }
  if (preferred && preferred.tier_before !== preferred.tier_after) {
    return `Tier unlock · ${Number(preferred.target_rate_percent).toLocaleString(undefined, { maximumFractionDigits: 2 })}% bucket · ${compactMoney(preferred.estimated_net_value_aed)} cycle value`;
  }
  if (preferred && preferred.tier_before !== preferred.target_tier) {
    return `Target-tier ${Number(preferred.target_rate_percent).toLocaleString(undefined, { maximumFractionDigits: 2 })}% · building ${compactMoney(preferred.tier_threshold_aed)}`;
  }
  const rate = Number(item.estimated_net_return_percent);
  if (Number.isFinite(rate)) return `Est. ${rate.toLocaleString(undefined, { maximumFractionDigits: 2 })}% return`;
  return item.reason;
}

function paymentMethodLabel(channel) {
  return {
    APPLE_PAY_POS: "Apple Pay",
    PHYSICAL_POS: "Physical card",
    ONLINE: "Online",
  }[channel] || channel?.replaceAll("_", " ").toLowerCase() || "";
}

function routeHeading(item, candidate) {
  const card = cardLabel(item.use_card);
  if (!candidate) return card;
  return `${card} · ${paymentMethodLabel(candidate.payment_channel)} → ${bucketLabel(candidate.bucket)}`;
}

function candidateValueLabel(candidate) {
  const bucketRate = Number(candidate.target_rate_percent);
  if (candidate.purpose === "THRESHOLD_FILLER" && bucketRate === 0) {
    return `Tier filler · ${compactMoney(candidate.card_target_remaining_aed)} remaining · no direct cashback`;
  }
  if (candidate.tier_before !== candidate.tier_after) {
    return `${bucketRate.toLocaleString(undefined, { maximumFractionDigits: 2 })}% bucket · unlocks ${compactMoney(candidate.estimated_net_value_aed)} cycle value`;
  }
  if (candidate.tier_before !== candidate.target_tier) {
    return `Target-tier ${bucketRate.toLocaleString(undefined, { maximumFractionDigits: 2 })}%`;
  }
  const rate = Number(candidate.estimated_net_return_percent);
  return `Est. ${rate.toLocaleString(undefined, { maximumFractionDigits: 2 })}% return`;
}

function bucketLabel(code) {
  return code.replace(/^(RAK|SC|EI)_/, "").replaceAll("_", " ").toLowerCase();
}

function tierName(code) {
  return code.replaceAll("_", " ").replace(/^TIER (\d+)$/, "$1% tier").toLowerCase();
}

function candidatePosition(candidate) {
  if (!candidate) return "";
  const bucketSpend = compactMoney(candidate.bucket_spend_aed);
  const bucket = candidate.bucket_cap_aed == null
    ? `${bucketLabel(candidate.bucket)} ${bucketSpend} · uncapped`
    : `${bucketLabel(candidate.bucket)} ${bucketSpend}/${compactMoney(candidate.bucket_cap_aed)}`;
  const threshold = Number(candidate.tier_threshold_aed) > 0
    ? `${tierName(candidate.target_tier)} ${compactMoney(candidate.card_spend_aed)}/${compactMoney(candidate.tier_threshold_aed)}`
    : `${tierName(candidate.target_tier)} · no minimum`;
  const method = candidate.payment_channel ? candidate.payment_channel.replaceAll("_", " ").toLowerCase() : "";
  return `${bucket} · ${threshold}${method ? ` · ${method}` : ""}`;
}

function renderRecommendations(items) {
  const root = document.querySelector("#recommendations");
  root.replaceChildren(
    ...items.filter((item) => item.active !== false).map((item) => {
      const node = document.createElement("article");
      node.className = "route-row";
      const avoid = (item.avoid_cards || []).map(cardLabel);
      const preferred = item.ranked_cards?.[0];
      node.innerHTML = `
        <div class="route-main">
          <span class="route-type">${typeLabel(item)}</span>
          <span class="route-use"><span><strong>${routeHeading(item, preferred)}</strong><em title="${item.reason}">${compactReason(item)}</em></span><small>${candidatePosition(preferred)}</small></span>
          <small title="${avoid.length ? avoid.join(", ") : "None"}">${avoid.length ? avoid.join(", ") : ""}</small>
        </div>
      `;
      return node;
    }),
  );
}

function renderDecisionTree(items) {
  const root = document.querySelector("#decision-tree");
  const active = items.filter((item) => item.active !== false);
  const key = (item) => item.code || `${item.purchase_type}:${item.channel}:${item.currency}`;
  const previous = root.dataset.selectedKey;
  root.innerHTML = `<label class="graph-selector"><span>Spend type</span><select aria-label="Decision-tree spend type">${active.map((item) => `<option value="${key(item)}">${typeLabel(item)}</option>`).join("")}</select></label><div class="spend-graph"></div>`;
  const selector = root.querySelector("select");
  if (active.some((item) => key(item) === previous)) selector.value = previous;

  const show = () => {
    const item = active.find((candidate) => key(candidate) === selector.value) || active[0];
    if (!item) return;
    root.dataset.selectedKey = key(item);
    const candidates = (item.ranked_cards || []).map((candidate) => {
      const cap = candidate.bucket_cap_aed == null ? null : Number(candidate.bucket_cap_aed);
      const remaining = candidate.bucket_remaining_aed == null ? null : Number(candidate.bucket_remaining_aed);
      const threshold = Number(candidate.tier_threshold_aed);
      const method = candidate.payment_channel.replaceAll("_", " ").toLowerCase();
      const bucketText = cap == null
        ? `${method} → ${bucketLabel(candidate.bucket)} is uncapped; ${compactMoney(candidate.bucket_spend_aed)} recorded`
        : `${method} → ${bucketLabel(candidate.bucket)} ${compactMoney(candidate.bucket_spend_aed)} / ${compactMoney(cap)}; ${compactMoney(remaining)} headroom`;
      const tierText = threshold > 0
        ? `${tierName(candidate.target_tier)} at ${compactMoney(threshold)}; ${compactMoney(candidate.tier_remaining_aed)} remaining`
        : `${tierName(candidate.target_tier)} has no minimum spend`;
      const switchText = candidate.status === "PREFERRED"
        ? candidate.condition
        : `Then use: ${candidate.condition}`;
      return `<li class="candidate-node ${candidate.status.toLowerCase()}"><div class="candidate-rank"><b>${candidate.order}</b><span>${candidate.status.toLowerCase()}</span></div><div class="candidate-card"><strong>${cardLabel(candidate.card)}</strong><span>${bucketText}</span></div><div class="candidate-logic"><b>${candidateValueLabel(candidate)}</b><span>${tierText}</span><small>${switchText}</small></div></li>`;
    }).join("");
    const methods = (item.methods || []).map((method) => method.replaceAll("_", " ")).join(" · ");
    root.querySelector(".spend-graph").innerHTML = `<header><span>${methods} · ${item.currency}</span><strong>${typeLabel(item)} routing order</strong><small>Routes are ranked by category eligibility, whole-purchase headroom, portfolio pace and target gaps, then reward economics.</small></header><ol>${candidates}</ol>`;
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

function renderCards(cards) {
  const root = document.querySelector("#cards");
  root.replaceChildren(
    ...cards.map((card) => {
      const target = Number(card.safety_target_aed || card.total_spend_aed || 1);
      const actual = Number(card.total_spend_aed);
      const percentage = Math.max(0, Math.min(100, (actual / target) * 100));
      const tierPosition = renderTierPosition(card, actual);
      const buckets = card.buckets
        .map((bucket) => {
          if (!bucket.spend_cap_aed) {
            return `<div class="bucket-row uncapped"><div><span>${bucket.code.replaceAll("_", " ")}</span><b>${money.format(bucket.spend_aed)} · uncapped</b></div></div>`;
          }
          const fill = Math.min(100, (Number(bucket.spend_aed) / Number(bucket.spend_cap_aed)) * 100);
          const full = bucket.status === "FULL" ? " full" : "";
          return `
            <div class="bucket-row">
              <div><span>${bucket.code.replaceAll("_", " ")}</span><b>${money.format(bucket.spend_aed)} / ${money.format(bucket.spend_cap_aed)}</b></div>
              <div class="track${full}"><i style="width:${fill}%"></i></div>
            </div>
          `;
        })
        .join("");

      const node = document.createElement("article");
      node.className = "position-card";
      node.innerHTML = `
        <div class="position-card-header">
          <div class="position-summary-copy">
            <strong>${card.name}</strong>
            <span>${card.tier.replaceAll("_", " ")} · ${(card.pace?.status || "OPEN").replaceAll("_", " ")}</span>
          </div>
          <div class="position-total"><strong>${money.format(actual)}</strong><span>${tierPosition.next}</span></div>
        </div>
        ${tierPosition.ladder || `<div class="track primary"><i style="width:${percentage}%"></i></div>`}
        <div class="bucket-list">${buckets}</div>
        <div class="source-state"><span>${card.provisional_event_count || 0} provisional</span><span>${card.confirmed_event_count || 0} confirmed</span>${Number(card.refund_effect_aed || 0) ? `<span>${money.format(card.refund_effect_aed)} refunded</span>` : ""}</div>
      `;
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
    root.innerHTML = `
      <article class="empty-state">
        <strong>No finalized cycles yet</strong>
        <span>History appears after a statement is imported, reconciled, and finalized.</span>
      </article>
    `;
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
    const buckets = (card.buckets || [])
      .filter((bucket) => Number(bucket.spend_aed) || bucket.spend_cap_aed)
      .map((bucket) => `<li><span>${bucket.code.replaceAll("_", " ")}</span><b>${money.format(bucket.spend_aed)}${bucket.spend_cap_aed ? ` / ${money.format(bucket.spend_cap_aed)}` : ""}</b></li>`)
      .join("");
    root.innerHTML = `
      <article class="history-card">
        <div class="history-title">
          <div><span>${period.period_start} – ${period.period_end}</span><h3>${card.name}</h3></div>
          <b>${period.status.replaceAll("_", " ")}</b>
        </div>
        <div class="history-metrics">
          <div><span>Spend</span><strong>${money.format(card.total_spend_aed)}</strong></div>
          <div><span>Expected cashback</span><strong>${money.format(card.expected_cashback_aed)}</strong></div>
          <div><span>Tier</span><strong>${card.tier.replaceAll("_", " ")}</strong></div>
        </div>
        <p class="history-status">${period.reconciliation_status.replaceAll("_", " ")}</p>
        ${buckets ? `<ul class="history-buckets">${buckets}</ul>` : ""}
      </article>
    `;
  };
  selector.addEventListener("change", show);
  show();
}

function renderAttention(payload) {
  const section = document.querySelector("#attention-section");
  const root = document.querySelector("#attention");
  const alerts = [];
  (payload.alerts || []).forEach((alert) => alerts.push(alert));
  if (payload.data_status?.is_stale) {
    alerts.push({ key: "feed:stale", title: "Live feed is stale", detail: `No successful ingest recorded within ${payload.data_status.stale_after_minutes} minutes. Recommendations may be incomplete.` });
  }
  if (payload.review_count) {
    alerts.push({ key: "review:transactions", title: `${payload.review_count} transaction${payload.review_count === 1 ? "" : "s"} need review`, detail: "Classification or evidence is incomplete and may affect bucket totals." });
  }
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
      node.innerHTML = `
        <div class="alert-copy"><strong>${alert.title}</strong><span>${alert.detail}</span></div>
        <label class="alert-toggle"><input type="checkbox" ${checked ? "checked" : ""}>${checked ? "Hidden" : "Hide"}</label>
      `;
      node.querySelector("input").addEventListener("change", async (event) => {
        event.currentTarget.disabled = true;
        try {
          await setAlertAcknowledgement(alert.key, event.currentTarget.checked);
          await loadDashboard();
        } catch (error) {
          event.currentTarget.checked = !event.currentTarget.checked;
          event.currentTarget.disabled = false;
        }
      });
      return node;
  };
  root.replaceChildren(...visible.map((alert) => alertNode(alert, false)));
  if (hidden.length) {
    const disclosure = document.createElement("details");
    disclosure.className = "hidden-alerts";
    disclosure.innerHTML = `<summary>${hidden.length} hidden alert${hidden.length === 1 ? "" : "s"}</summary>`;
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
  root.className = `as-of ${status?.is_stale ? "stale" : "live"}`;
  if (!lastIngest) {
    root.textContent = "Feed not checked";
    return;
  }
  const time = new Date(lastIngest).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  root.textContent = status.is_stale ? `Stale · ${time}` : `Live · ${time}`;
  root.title = `Last successful ingest: ${new Date(lastIngest).toLocaleString()}`;
}

async function loadDashboard() {
  const [response, periodsResponse] = await Promise.all([
    fetch("/api/dashboard", { cache: "no-store" }),
    fetch("/api/periods", { cache: "no-store" }),
  ]);
  const payload = await response.json();
  const periodsPayload = await periodsResponse.json();
  if (!response.ok) throw new Error(payload.error || "Dashboard is unavailable.");
  if (!periodsResponse.ok) throw new Error(periodsPayload.error || "Period history is unavailable.");
  renderStatus(payload.data_status);
  const routing = payload.routing_graphs?.length ? payload.routing_graphs : payload.recommendations;
  renderRecommendations(routing);
  renderDecisionTree(routing);
  renderAttention(payload);
  renderCards(payload.cards);
  renderPeriodHistory(periodsPayload.periods || []);
}

setupRoutingViews();
loadDashboard().catch((error) => {
  const status = document.querySelector("#as-of");
  status.className = "as-of stale";
  status.textContent = "Unavailable";
  document.querySelector("#recommendations").innerHTML = `<p class="error">${error.message}</p>`;
});

setInterval(loadDashboard, 60_000);
