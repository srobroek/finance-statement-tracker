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

function typeLabel(item) {
  const channel = item.channel === "APPLE_PAY_POS" ? " · Apple Pay" : "";
  const currency = item.currency !== "AED" ? ` · ${item.currency}` : "";
  return `${item.purchase_type.replaceAll("_", " ")}${channel}${currency}`;
}

function renderRecommendations(items) {
  const root = document.querySelector("#recommendations");
  root.replaceChildren(
    ...items.map((item) => {
      const node = document.createElement("article");
      node.className = "recommendation";
      node.innerHTML = `
        <span class="recommendation-category">${typeLabel(item)}</span>
        <strong>${cardLabel(item.use_card)}</strong>
        <span class="route">Use this card</span>
        <details><summary>Why</summary><p class="reason">${item.reason}</p></details>
      `;
      return node;
    }),
  );
}

function renderAvoid(items) {
  const cards = new Map();
  items.forEach((item) => {
    (item.avoid_cards || []).forEach((card) => {
      const categories = cards.get(card) || [];
      categories.push(item.purchase_type.replaceAll("_", " ").toLowerCase());
      cards.set(card, categories);
    });
  });
  const section = document.querySelector("#avoid-section");
  const root = document.querySelector("#avoid");
  if (!cards.size) {
    section.hidden = false;
    root.innerHTML = `
      <article class="empty-state">
        <strong>No cards blocked</strong>
        <span>All cards still have routing value in at least one spend type.</span>
      </article>
    `;
    return;
  }
  section.hidden = false;
  root.replaceChildren(
    ...[...cards.entries()].map(([card, categories]) => {
      const node = document.createElement("article");
      node.className = "avoid-card";
      node.innerHTML = `<strong>${cardLabel(card)}</strong><span>${[...new Set(categories)].join(", ")}</span>`;
      return node;
    }),
  );
}

function renderCards(cards) {
  const root = document.querySelector("#cards");
  root.replaceChildren(
    ...cards.map((card) => {
      const target = Number(card.safety_target_aed || card.total_spend_aed || 1);
      const actual = Number(card.total_spend_aed);
      const percentage = Math.max(0, Math.min(100, (actual / target) * 100));
      const buckets = card.buckets
        .filter((bucket) => bucket.spend_cap_aed)
        .map((bucket) => {
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
        <div class="card-title">
          <div><span>${card.tier.replaceAll("_", " ")}</span><h3>${card.name}</h3></div>
          <b class="pace ${(card.pace?.status || "OPEN").toLowerCase()}">${(card.pace?.status || "OPEN").replaceAll("_", " ")}</b>
        </div>
        <div class="total"><strong>${money.format(actual)}</strong><span>${card.safety_target_aed ? `of ${money.format(target)}` : "this cycle"}</span></div>
        <div class="track primary"><i style="width:${percentage}%"></i></div>
        <div class="source-state"><span>${card.provisional_event_count || 0} provisional</span><span>${card.confirmed_event_count || 0} confirmed</span>${Number(card.refund_effect_aed || 0) ? `<span>${money.format(card.refund_effect_aed)} refunded</span>` : ""}</div>
        <div class="bucket-list">${buckets || '<p class="muted">No capped buckets</p>'}</div>
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
  renderRecommendations(payload.recommendations);
  renderAvoid(payload.recommendations);
  renderAttention(payload);
  renderCards(payload.cards);
  renderPeriodHistory(periodsPayload.periods || []);
}

loadDashboard().catch((error) => {
  const status = document.querySelector("#as-of");
  status.className = "as-of stale";
  status.textContent = "Unavailable";
  document.querySelector("#recommendations").innerHTML = `<p class="error">${error.message}</p>`;
});

setInterval(loadDashboard, 60_000);
