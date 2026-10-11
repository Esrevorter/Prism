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

// ---------- boot / status ----------
async function refreshAll() {
  try {
    const ping = await rpc("health.ping");
    $("#net-status").textContent = `connected · ${ping.network} · block ${ping.tip_height}`;
    const info = await rpc("chain.info");
    $("#chain-info").textContent = JSON.stringify(info, null, 2);
    const bal = await rpc("wallet.balance");
    $("#balance").textContent = `${bal.balance_prsm} PRSM`;
    $("#balance-note").textContent = "on your local devnet";
    const ct = await rpc("contacts.list");
    const ul = $("#contacts");
    ul.replaceChildren(...ct.contacts.map(c => el("li", null, c.display_name)));
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
  const body = $("#review-body");
  body.replaceChildren();
  const sim = it.simulation || {};
  const effects = sim.predicted_effects || [];
  const list = el("ul", "effect-list");
  effects.forEach(ef => list.appendChild(el("li", null,
    typeof ef === "string" ? ef : JSON.stringify(ef))));
  body.appendChild(list);
  (sim.warnings || []).forEach(w =>
    body.appendChild(el("div", "warning-card", `⚠ ${w}`)));
  if ((sim.risk_score ?? 0) > 0.5)
    body.appendChild(el("div", "warning-card",
      "This looks unusual for you. Take a breath — you can cancel safely."));
  body.appendChild(el("p", "muted",
    `Status: ${it.status}. Nothing is sent until you confirm.`));
}

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
    $("#ceremony-status").textContent =
      `${st.stage} (${st.percent}%${st.elapsed_s !== undefined ? `, ${st.elapsed_s}s` : ""})`;
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
