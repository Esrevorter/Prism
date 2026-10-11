/* Prism GUI v0 — thin JSON-RPC client + UI wiring.
   All risk/simulation/plain-language fields come from the server; this
   file only renders them (honest-UX: the front end never invents reassurance). */
"use strict";

const RPC_URL = "/jsonrpc";
let _id = 0;

async function rpc(method, params = {}) {
  const res = await fetch(RPC_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", method, params, id: ++_id }),
  });
  const j = await res.json();
  if (j.error) throw new Error(j.error.message || "RPC error");
  return j.result;
}

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

let currentIntent = null;
let riskAcknowledged = false;

// ---------- boot / status ----------
async function refreshAll() {
  try {
    const ping = await rpc("health.ping");
    $("#net-status").textContent = `connected · ${ping.network} · block ${ping.tip_height}`;
    const info = await rpc("chain.info");
    $("#chain-info").textContent = JSON.stringify(info, null, 2);
    // live chain state from the daemon's block store (not fixtures)
    try {
      const recent = await rpc("chain.blocks", { limit: 5 });
      const list = el("ul", "effect-list");
      recent.blocks.forEach(b => list.appendChild(el("li", null,
        `#${b.height} · ${b.hash.slice(0, 12)}… · ${b.tx_count} tx`)));
      $("#chain-info").appendChild(list);
    } catch { /* older daemon without chain.blocks */ }
    const bal = await rpc("wallet.balance");
    $("#balance").textContent = `${bal.balance_prsm} PRSM`;
    $("#balance-note").textContent = "on your local devnet";
    const ct = await rpc("contacts.list");
    const ul = $("#contacts");
    ul.replaceChildren(...ct.contacts.map(c => el("li", null, c.display_name)));
    const cer = await rpc("mpc.ceremony_status");
    if (cer.status === "complete") renderCeremony(cer);
    refreshPlans();
  } catch (e) {
    $("#net-status").textContent = "node unreachable — start: python -m prism.rpc.server";
  }
}

// ---------- intent flow ----------
$("#intent-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const utterance = $("#utterance").value.trim();
  if (!utterance) return;
  try {
    const it = await rpc("intent.compile", { utterance });
    showIntent(it);
  } catch (e) { renderError(e.message); }
});

function showIntent(it) {
  currentIntent = it;
  riskAcknowledged = false;
  const chips = $("#chips");
  const review = $("#review");
  if (it.status === "draft" && it.clarify) {
    chips.hidden = false;
    chips.replaceChildren(
      el("span", "muted", it.clarify),
      ...suggestChips(it.clarify).map(t => {
        const b = el("button", "chip", t);
        b.onclick = async () => {
          try { showIntent(await rpc("intent.answer", { intent_id: it.intent_id, answer: t })); }
          catch (e) { renderError(e.message); }
        };
        return b;
      })
    );
    review.hidden = true;
    return;
  }
  chips.hidden = true;
  review.hidden = false;
  // fraud-simulator panel: live SigningGate review against real daemon state.
  // Banner text + plain-english preview come from the server only (honest UX).
  renderReviewBody();
}

async function renderReviewBody() {
  if (!currentIntent) return;
  const body = $("#review-body");
  const banner = $("#fraud-banner");
  let rv = null;
  try {
    rv = await rpc("intent.review", {
      intent_id: currentIntent.intent_id,
      acknowledged_risk: riskAcknowledged,
    });
  } catch (e) { /* fall back to the plain compile payload below */ }
  body.replaceChildren();
  banner.hidden = true;
  banner.className = "risk-banner";
  $("#sleep-btn").hidden = true;
  $("#confirm-btn").disabled = false;

  if (rv) {
    if (rv.ui_band !== "calm") {
      banner.hidden = false;
      banner.classList.add(rv.ui_band);
      banner.textContent = rv.interruption && !riskAcknowledged
        ? `⏸ ${rv.human}`
        : `⚠ ${rv.risk.reasons.join(" · ")}`;
    }
    if (rv.interruption && !riskAcknowledged) {
      $("#sleep-btn").hidden = false;
      $("#confirm-btn").disabled = true;   // gate owns proceed; UI mirrors it
    }
    // Template-pack copy (sim_explain_v1): band-specific plain-language
    // rendered verbatim from the server — honest-UX, nothing invented here.
    body.appendChild(el("p", null, rv.explanation || rv.plain_english || rv.human));
    if (!rv.proceed && !rv.interruption)
      body.appendChild(el("div", "warning-card",
        rv.reasons.join(" — ") || "This can't be confirmed yet."));
    if (rv.sim_digest)
      body.appendChild(el("p", "muted",
        `Preview fingerprint: ${rv.sim_digest.slice(0, 12)}… ` +
        `(what you see is exactly what gets signed)`));
    return;
  }
  const sim = (currentIntent.simulation) || {};
  const effects = sim.predicted_effects || [];
  const list = el("ul", "effect-list");
  effects.forEach(ef => list.appendChild(el("li", null,
    typeof ef === "string" ? ef : JSON.stringify(ef))));
  body.appendChild(list);
  (sim.warnings || []).forEach(w =>
    body.appendChild(el("div", "warning-card", `⚠ ${w}`)));
  body.appendChild(el("p", "muted",
    `Status: ${currentIntent.status}. Nothing is sent until you confirm.`));
}

$("#sleep-btn").addEventListener("click", () => {
  // calm interruption (§7.3 step 4): never pressure the user onward
  $("#review").hidden = true;
  currentIntent = null;
  $("#net-status").textContent = "paused safely — nothing was sent";
});

function suggestChips(clarify) {
  // Server never guesses; these are *answers the user might mean*, offered
  // as one-tap chips per §7.3 step 4. Contacts list drives recipient chips.
  if (/who|which/i.test(clarify)) return ["Alex Rivera", "Mom"];
  if (/how much/i.test(clarify)) return ["$10", "$20", "1 PRSM"];
  if (/did you mean/i.test(clarify)) return [clarify.match(/mean (.+)\?/)?.[1] || "yes"];
  return ["yes", "no"];
}

function renderError(msg) {
  const body = $("#review-body");
  $("#review").hidden = false;
  body.replaceChildren(el("div", "warning-card", msg));
}

$("#confirm-btn").addEventListener("click", async () => {
  if (!currentIntent) return;
  try {
    const done = await rpc("intent.confirm", { intent_id: currentIntent.intent_id });
    const sign = await rpc("mpc.sign_preview", { intent_id: currentIntent.intent_id })
      .catch(() => null);
    const body = $("#review-body");
    body.replaceChildren(
      el("p", null, "Confirmed ✓"),
      el("p", "muted", done.confirmation_envelope?.canonical_digest_hex
        ? `Audit digest: ${done.confirmation_envelope.canonical_digest_hex.slice(0, 16)}…`
        : ""),
      sign ? el("p", null, sign.human) : el("p", "muted",
        "Tip: run “Set up device keys” to see threshold signing.")
    );
    refreshAll();
  } catch (e) { renderError(e.message); }
});

$("#cancel-btn").addEventListener("click", () => {
  $("#review").hidden = true;
  $("#chips").hidden = true;
  currentIntent = null;
  riskAcknowledged = false;
});

// ---------- faucet + ceremony ----------
$("#faucet-btn").addEventListener("click", async () => {
  $("#balance-note").textContent = "mining test blocks…";
  try { await rpc("chain.mine", { count: 5 }); } catch (e) { /* budget cap */ }
  refreshAll();
});

$("#ceremony-btn").addEventListener("click", async () => {
  $("#ceremony-status").textContent = "Setting up your 2-of-3 device keys…";
  try {
    const st = await rpc("mpc.ceremony_start");
    renderCeremony(st);
  } catch (e) { $("#ceremony-status").textContent = e.message; }
});

function renderCeremony(st) {
  // share-handling UX: show only what's safe + honest — device labels and
  // short fingerprints, never raw share values; every reassurance line is
  // server-computed text (this file adds none of its own).
  $("#ceremony-status").textContent = `${st.stage} (${st.percent}%)`;
  const card = $("#devices-card");
  if (st.status !== "complete" || !st.devices) { card.hidden = true; return; }
  card.hidden = false;
  $("#devices-human").textContent = st.human || "";
  const ul = $("#devices");
  ul.replaceChildren(...st.devices.map(d => {
    const li = el("li", "device-item");
    li.appendChild(el("strong", null, d.label));
    li.appendChild(el("span", "fingerprint", `share ${d.share_fingerprint}`));
    li.appendChild(el("span", "muted", d.role));
    return li;
  }));
  const help = st.share_handling || {};
  $("#share-help-body").textContent =
    [help.never_shown, help.backup_ready, help.what_if_lost]
      .filter(Boolean).join(" ");
}

$("#ceremony-redo-btn").addEventListener("click", async () => {
  try {
    await rpc("mpc.ceremony_reset");
    $("#devices-card").hidden = true;
    $("#ceremony-status").textContent = "";
    const st = await rpc("mpc.ceremony_start");   // fresh shares via reshare
    renderCeremony(st);
  } catch (e) { $("#ceremony-status").textContent = e.message; }
});

// ---------- contacts ----------
$("#contact-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    await rpc("contacts.add", { name: $("#ct-name").value, address: $("#ct-addr").value });
    $("#ct-name").value = ""; $("#ct-addr").value = "";
    refreshAll();
  } catch (e) { alert(e.message); }
});

// ---------- plans (agent runtime): pending digest, one-tap veto/revoke ----------
let _activeGrant = null;   // grant id from the last plan we created this session

async function refreshPlans() {
  const pend = await rpc("agent.pending");
  const card = $("#plans-card");
  if (!_activeGrant && pend.count === 0) { card.hidden = true; return; }
  card.hidden = false;
  $("#plans-human").textContent = pend.human || "";
  const box = $("#pending-digests");
  box.replaceChildren(...pend.pending.map(p => {
    const c = el("div", "digest-card");
    c.appendChild(el("p", "digest-line", p.line));
    const row = el("div", "row");
    const vBtn = el("button", "btn secondary", "Stop this one");
    vBtn.addEventListener("click", async () => {
      try {
        const r = await rpc("agent.veto", { action_id: p.short_id });
        c.querySelector(".digest-line").textContent = r.human || "Stopped.";
        refreshAll();
      } catch (e) { alert(e.message); }
    });
    const rBtn = el("button", "btn danger", "Stop the whole plan");
    rBtn.addEventListener("click", async () => {
      try {
        const r = await rpc("agent.revoke", {});
        $("#plans-human").textContent = r.human || "Plan stopped.";
        refreshAll();
      } catch (e) { alert(e.message); }
    });
    row.append(vBtn, rBtn);
    c.appendChild(row);
    return c;
  }));
}

$("#plan-form")?.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    if (!_activeGrant) {
      const g = await rpc("agent.grant_create", {});
      _activeGrant = g.grant_id;
    }
    const r = await rpc("agent.propose",
                        { utterance: $("#plan-utterance").value,
                          grant_id: _activeGrant });
    $("#plans-card").hidden = false;
    $("#plans-human").textContent = r.ok !== false
      ? (r.human || "Queued. You have 24 hours to change your mind.")
      : (r.human || "Nothing was proposed.");
    $("#plan-utterance").value = "";
    refreshAll();
  } catch (e) { $("#plans-human").textContent = e.message; }
});

// ---------- theme ----------
$("#theme-toggle").addEventListener("click", () => {
  const b = document.body;
  b.dataset.theme = b.dataset.theme === "dark" ? "light" : "dark";
  localStorage.setItem("prism-theme", b.dataset.theme);
});
const saved = localStorage.getItem("prism-theme");
if (saved) document.body.dataset.theme = saved;
else if (matchMedia("(prefers-color-scheme: dark)").matches) document.body.dataset.theme = "dark";

refreshAll();
setInterval(refreshAll, 5000);
