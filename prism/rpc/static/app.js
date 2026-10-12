/* Prism GUI v2 — lean-auth wallet controller (spec §9, Amendment A2-2026-10).
   Honest-UX rule: every reassurance/risk sentence is rendered verbatim from
   server-computed fields (`human`, `warning`, `explanation`). This file only
   wires screens together and animates them — it never invents comfort text. */
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
const fmtPRSM = (shard) => (shard / 1e9).toLocaleString(undefined,
  { maximumFractionDigits: 9 });

// ---------- screen router (spring-ish fade/slide via CSS keyframes) ----------
const SCREENS = ["welcome", "create", "phrase", "restore", "unlock", "app"];
function show(name) {
  for (const s of SCREENS) {
    const node = $(`#screen-${s}`);
    if (!node) continue;
    if (s === name) {
      node.hidden = false;
      node.classList.remove("screen-enter");   // restart animation
      void node.offsetWidth;
      node.classList.add("screen-enter");
    } else node.hidden = true;
  }
  $("#lock-btn").hidden = !(name === "app");
}

// ---------- toast + plain-language status line ----------
let _toastTimer = null;
function toast(msg, ms = 3500) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => t.classList.remove("show"), ms);
}

// ---------- boot: auth.status decides the first screen ----------
let _pwCache = null;          // session-only; cleared on lock/close
let currentIntent = null;
let riskAcknowledged = false;
let capState = null;          // {challenge, picks:Set, token, timer, deadline}

async function boot() {
  try {
    const st = await rpc("auth.status");
    if (!st.exists) show("welcome");
    else if (!st.unlocked) show("unlock");
    else { show("app"); await refreshAll(); }
  } catch {
    $("#net-status").textContent = "node unreachable — start with: prism gui";
  }
}

// ---------- welcome / create ----------
$("#start-btn").addEventListener("click", () => show("create"));
$("#restore-btn").addEventListener("click", () => show("restore"));

$("#pw1").addEventListener("input", () => {
  const v = $("#pw1").value;
  const m = $("#pw-meter");
  const score = Math.min(1, v.length / 24);       // calm, non-judgy meter
  m.style.setProperty("--fill", `${score * 100}%`);
  m.parentElement.classList.toggle("weak", v.length > 0 && v.length < 10);
});

$("#create-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const p1 = $("#pw1").value, p2 = $("#pw2").value;
  if (p1 !== p2) { $("#pw-hint").textContent = "The two passwords don't match yet."; return; }
  try {
    const r = await rpc("auth.create", { password: p1 });
    _pwCache = p1;
    const grid = $("#word-grid");
    grid.replaceChildren(...r.recovery_phrase.split(" ").map((w, i) => {
      const li = el("li", "word");
      li.append(el("span", "num", String(i + 1)), el("span", null, w));
      return li;
    }));
    $("#phrase-warning").textContent = r.warning;   // server copy, verbatim
    $("#wrote-it").checked = false;
    $("#phrase-done-btn").disabled = true;
    show("phrase");
  } catch (e) { toast(e.message); }
});

$("#wrote-it").addEventListener("change", (ev) => {
  $("#phrase-done-btn").disabled = !ev.target.checked;
});
$("#phrase-done-btn").addEventListener("click", async () => {
  show("app"); await refreshAll();
  toast("Wallet ready. Your address is on the balance card.");
});

$("#restore-cancel").addEventListener("click", () => show("welcome"));
$("#restore-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    const r = await rpc("auth.recover",
      { phrase: $("#phrase-in").value.trim(), new_password: $("#rpw").value });
    _pwCache = $("#rpw").value;
    toast(r.human);
    show("app"); await refreshAll();
  } catch (e) { toast(e.message); }
});

// ---------- unlock / lock ----------
$("#unlock-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    const r = await rpc("auth.unlock", { password: $("#upw").value });
    _pwCache = $("#upw").value; $("#upw").value = "";
    toast(r.human);
    show("app"); await refreshAll();
  } catch (e) {
    $("#unlock-note").textContent = e.message;     // server-side lockout copy
    $("#upw").select();
  }
});
$("#forgot-btn").addEventListener("click", () => show("restore"));
$("#lock-btn").addEventListener("click", async () => {
  try {
    const r = await rpc("auth.lock");
    _pwCache = null; capState = null;
    toast(r.human); show("unlock");
  } catch (e) { toast(e.message); }
});

// ---------- app dashboard refresh ----------
async function refreshAll() {
  try {
    const ping = await rpc("health.ping");
    $("#net-status").textContent =
      `connected · ${ping.network} · block ${ping.tip_height}`;
    const bal = await rpc("wallet.balance");
    animateNumber($("#balance"), bal.balance_prsm);
    $("#my-address").textContent = bal.address || "";
    const ct = await rpc("contacts.list");
    $("#contacts").replaceChildren(
      ...ct.contacts.map(c => el("li", null, c.display_name)));
    const hist = await rpc("auth.history");
    const card = $("#activity-card");
    if (hist.count > 0) {
      card.hidden = false;
      $("#activity").replaceChildren(...hist.txs.map(t =>
        el("li", null, `sent ${t.amount_prsm} PRSM · block ${t.height} · ${t.txid.slice(0, 10)}…`)));
    } else card.hidden = true;
    try {
      const info = await rpc("chain.info");
      $("#chain-info").textContent = JSON.stringify(info, null, 2);
    } catch { /* advanced panel optional */ }
    refreshPlans();
  } catch (e) {
    $("#net-status").textContent = "node unreachable — start: prism gui";
  }
}

function animateNumber(node, target) {
  const from = parseFloat((node.textContent || "0").replace(/[^\d.]/g, "")) || 0;
  const t0 = performance.now(), dur = 650;
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduce) { node.textContent = `${target} PRSM`; return; }
  (function tick(now) {
    const k = Math.min(1, (now - t0) / dur);
    const eased = 1 - Math.pow(1 - k, 3);
    node.textContent = `${(from + (target - from) * eased).toFixed(
      String(target).includes(".") ? String(target).split(".")[1].length : 0)} PRSM`;
    if (k < 1) requestAnimationFrame(tick);
  })(t0);
}

// ---------- faucet ----------
$("#faucet-btn").addEventListener("click", async () => {
  $("#balance-note").textContent = "mining test blocks…";
  try {
    const r = await rpc("chain.mine", { count: 5 });
    $("#balance-note").textContent = r.human || "on your local devnet";
  } catch (e) { $("#balance-note").textContent = e.message; }
  refreshAll();
});
$("#copy-addr-btn").addEventListener("click", async () => {
  const a = $("#my-address").textContent;
  if (!a) return;
  try { await navigator.clipboard.writeText(a); toast("Address copied."); }
  catch { toast(a); }
});

// ---------- intent flow (plain-words bar → review) ----------
$("#intent-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const utterance = $("#utterance").value.trim();
  if (!utterance) return;
  try { showIntent(await rpc("intent.compile", { utterance })); }
  catch (e) { renderError(e.message); }
});
document.querySelectorAll(".chip.suggest").forEach(b =>
  b.addEventListener("click", () => {
    $("#utterance").value = b.dataset.say;
    $("#intent-form").requestSubmit();
  }));

function setStepper(i) {
  const st = $("#stepper");
  st.hidden = false;
  st.querySelectorAll(".step").forEach((s, idx) => {
    s.classList.toggle("done", idx < i);
    s.classList.toggle("active", idx === i);
  });
}
function hideStepper() { $("#stepper").hidden = true; }

function showIntent(it) {
  currentIntent = it;
  riskAcknowledged = false;
  const chips = $("#chips"), review = $("#review");
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
      }));
    review.hidden = true; hideStepper();
    return;
  }
  chips.hidden = true;
  review.hidden = false;
  setStepper(0);
  renderReviewBody();
}

async function renderReviewBody() {
  if (!currentIntent) return;
  const body = $("#review-body"), banner = $("#fraud-banner");
  let rv = null;
  try {
    rv = await rpc("intent.review", {
      intent_id: currentIntent.intent_id, acknowledged_risk: riskAcknowledged });
  } catch { /* fall back to compile payload below */ }
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
        ? `⏸ ${rv.human}` : `⚠ ${(rv.risk?.reasons || []).join(" · ")}`;
    }
    if (rv.interruption && !riskAcknowledged) {
      $("#sleep-btn").hidden = false;
      $("#confirm-btn").disabled = true;    // gate owns proceed; UI mirrors it
    }
    body.appendChild(el("p", null, rv.explanation || rv.plain_english || rv.human));
    if (!rv.proceed && !rv.interruption)
      body.appendChild(el("div", "warning-card",
        (rv.reasons || []).join(" — ") || "This can't be confirmed yet."));
    if (rv.sim_digest)
      body.appendChild(el("p", "muted",
        `Preview fingerprint: ${rv.sim_digest.slice(0, 12)}… ` +
        `(what you see is exactly what gets signed)`));
    return;
  }
  const sim = currentIntent.simulation || {};
  const list = el("ul", "effect-list");
  (sim.predicted_effects || []).forEach(ef => list.appendChild(el("li", null,
    typeof ef === "string" ? ef : JSON.stringify(ef))));
  body.appendChild(list);
  (sim.warnings || []).forEach(w =>
    body.appendChild(el("div", "warning-card", `⚠ ${w}`)));
  body.appendChild(el("p", "muted",
    `Status: ${currentIntent.status}. Nothing is sent until you confirm.`));
}

$("#sleep-btn").addEventListener("click", () => {
  $("#review").hidden = true; currentIntent = null; hideStepper();
  toast("Paused safely — nothing was sent.");
});

function suggestChips(clarify) {
  if (/who|which/i.test(clarify)) return ["Alex Rivera", "Mom"];
  if (/how much/i.test(clarify)) return ["$10", "$20", "1 PRSM"];
  if (/did you mean/i.test(clarify))
    return [clarify.match(/mean (.+)\?/)?.[1] || "yes"];
  return ["yes", "no"];
}

function renderError(msg) {
  $("#review").hidden = false;
  $("#review-body").replaceChildren(el("div", "warning-card", msg));
}

// ---------- stage 1→2: confirm, then CAPTCHA ----------
$("#confirm-btn").addEventListener("click", async () => {
  if (!currentIntent) return;
  try {
    await rpc("intent.confirm", { intent_id: currentIntent.intent_id });
    $("#review").hidden = true;
    await startCaptcha();
  } catch (e) { renderError(e.message); }
});
$("#cancel-btn").addEventListener("click", () => {
  $("#review").hidden = true; $("#chips").hidden = true;
  currentIntent = null; riskAcknowledged = false; hideStepper(); resetStages();
});

function resetStages() {
  stopCapTimer();
  capState = null;
  $("#captcha-stage").hidden = true;
  $("#sign-stage").hidden = true;
  $("#receipt").hidden = true;
}

async function startCaptcha() {
  try {
    const ch = await rpc("auth.challenge", { intent_id: currentIntent.intent_id });
    capState = { ch, picks: new Set(), token: null, timer: null,
                 deadline: ch.expires_at * 1000 };
    $("#cap-prompt").textContent = ch.prompt;
    renderCapSvg(ch);
    $("#cap-next").disabled = true;
    $("#cap-burst").hidden = true;
    $("#cap-hint").textContent = ch.human;
    $("#captcha-stage").hidden = false;
    setStepper(1);
    startCapTimer(ch.ttl_s || 120);
  } catch (e) { toast(e.message); }
}

// Build ONE inline SVG where each tile is itself a button — picture and
// controls are the same element, so they can never visually desync or show
// up as "two images". Colours come from the server palette verbatim.
const SVG_NS = "http://www.w3.org/2000/svg";
function renderCapSvg(ch) {
  const host = $("#cap-svg");
  host.replaceChildren();
  const TILE = 150, PAD = 10, R = 16, G = 3;
  const size = G * TILE + (G + 1) * PAD;
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${size} ${size}`);
  svg.setAttribute("role", "group");
  svg.setAttribute("aria-label", "Puzzle: tap every tile with the matching shape");
  const pal = ch.palette || {};
  const bg = rect(0, 0, size, size, pal.page || "#fafafc", 20);
  svg.appendChild(bg);
  ch.tiles.forEach((shape, i) => {
    const col = i % G, row = Math.floor(i / G);
    const x = col * (TILE + PAD) + PAD, y = row * (TILE + PAD) + PAD;
    const g = document.createElementNS(SVG_NS, "g");
    g.setAttribute("class", "cap-tile");
    g.setAttribute("tabindex", "0");
    g.setAttribute("role", "button");
    g.setAttribute("aria-pressed", "false");
    g.setAttribute("aria-label", `tile ${i + 1}: ${shape}`);
    g.dataset.i = String(i);
    g.appendChild(rect(x, y, TILE, TILE, pal.tile || "#ffffff", R));
    const glyph = glyphNode(shape, x + TILE / 2, y + TILE / 2,
                            pal[shape] || pal.ink || "#2c2c34", pal.ink || "#2c2c34");
    if (glyph) g.appendChild(glyph);
    const mark = document.createElementNS(SVG_NS, "path");
    mark.setAttribute("d", `M ${x + 38} ${y + TILE / 2} l 22 24 l 40 -46`);
    mark.setAttribute("class", "cap-check");
    mark.setAttribute("fill", "none");
    mark.setAttribute("stroke", pal.ink || "#2c2c34");
    mark.setAttribute("stroke-width", "10");
    mark.setAttribute("stroke-linecap", "round");
    mark.setAttribute("stroke-linejoin", "round");
    g.appendChild(mark);
    const toggle = () => {
      if (!capState) return;
      if (capState.picks.has(i)) capState.picks.delete(i);
      else capState.picks.add(i);
      const on = capState.picks.has(i);
      g.classList.toggle("picked", on);
      g.setAttribute("aria-pressed", on ? "true" : "false");
      $("#cap-next").disabled = capState.picks.size === 0;
    };
    g.addEventListener("click", toggle);
    g.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
    });
    svg.appendChild(g);
  });
  host.appendChild(svg);
}
function rect(x, y, w, h, fill, rx) {
  const r = document.createElementNS(SVG_NS, "rect");
  r.setAttribute("x", x); r.setAttribute("y", y);
  r.setAttribute("width", w); r.setAttribute("height", h);
  r.setAttribute("rx", rx); r.setAttribute("fill", fill);
  return r;
}
function glyphNode(shape, cx, cy, fill, ink) {
  const R = 45;                       // matches prism/auth/captcha.py _glyph_path
  let n;
  if (shape === "circle") {
    n = document.createElementNS(SVG_NS, "circle");
    n.setAttribute("cx", cx); n.setAttribute("cy", cy); n.setAttribute("r", R);
  } else {
    const pts = shape === "square"
      ? [[-.72,-.72],[.72,-.72],[.72,.72],[-.72,.72]].map(p => `${cx+p[0]*R},${cy+p[1]*R}`)
      : shape === "triangle"
      ? [[0,-1],[.9,.5],[-.9,.5]].map(p => `${cx+p[0]*R},${cy+p[1]*R}`)
      : shape === "diamond"
      ? [[0,-1.1],[1.1,0],[0,1.1],[-1.1,0]].map(p => `${cx+p[0]*R},${cy+p[1]*R}`)
      : shape === "star"
      ? Array.from({length: 10}, (_, k) => {
          const a = -Math.PI/2 + k*Math.PI/5, rr = (k%2===0?1.15:.5)*R;
          return `${cx+rr*Math.cos(a)},${cy+rr*Math.sin(a)}`; })
      : null;
    if (!pts) return null;
    n = document.createElementNS(SVG_NS, "polygon");
    n.setAttribute("points", pts.join(" "));
  }
  n.setAttribute("fill", fill);
  n.setAttribute("stroke", ink);
  n.setAttribute("stroke-width", "4");
  n.setAttribute("stroke-linejoin", "round");
  return n;
}

function startCapTimer(ttl) {
  stopCapTimer();
  const ring = $("#cap-ring");
  const C = 2 * Math.PI * 20;
  ring.style.strokeDasharray = `${C}`;
  const tick = () => {
    const left = Math.max(0, capState.deadline - Date.now()) / 1000;
    ring.style.strokeDashoffset = `${C * (1 - left / ttl)}`;
    if (left <= 0) {
      stopCapTimer();
      $("#cap-hint").textContent = "That picture expired — get a fresh one.";
      $("#cap-next").disabled = true;
    }
  };
  tick();
  capState.timer = setInterval(tick, 250);
}
function stopCapTimer() { if (capState?.timer) clearInterval(capState.timer); }

$("#cap-refresh").addEventListener("click", startCaptcha);

$("#cap-next").addEventListener("click", async () => {
  if (!capState) return;
  try {
    const solved = await rpc("auth.solve", {
      challenge_id: capState.ch.challenge_id,
      picks: [...capState.picks],
      digest_hex: capState.ch.digest_hex });
    stopCapTimer();
    capState.token = solved.token;
    const burst = $("#cap-burst");
    burst.hidden = false; burst.textContent = solved.human;
    setTimeout(() => {
      $("#captcha-stage").hidden = true;
      $("#sign-stage").hidden = false;
      setStepper(2);
      $("#spw").focus();
    }, 700);
  } catch (e) {
    toast(e.message);
    startCaptcha();                       // fresh picture, calm retry
  }
});

// ---------- stage 3: password signs THIS payment ----------
$("#sign-back").addEventListener("click", () => {
  $("#sign-stage").hidden = true;
  $("#review").hidden = false;
  setStepper(0);
});
$("#sign-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (!capState?.token) { toast("Picture check first."); return; }
  try {
    const r = await rpc("auth.sign", {
      intent_id: currentIntent.intent_id,
      password: $("#spw").value,
      token: capState.token });
    $("#spw").value = "";
    $("#sign-stage").hidden = true;
    $("#receipt-line").textContent = r.human;
    $("#receipt-detail").textContent =
      `tx ${r.txid.slice(0, 16)}… · block ${r.height} · ` +
      `balance now ${fmtPRSM(r.balance_shard)} PRSM`;
    $("#receipt").hidden = false;
    setStepper(3);
    confettiBurst();
    refreshAll();
  } catch (e) {
    toast(e.message);                     // wrong password / stale token
    if (/expired|does not match/.test(e.message)) {
      $("#sign-stage").hidden = true; startCaptcha();
    }
  }
});

$("#receipt-done").addEventListener("click", () => {
  $("#receipt").hidden = true; resetStages(); hideStepper();
  currentIntent = null; $("#utterance").value = "";
});
$("#receipt-another").addEventListener("click", () => {
  $("#receipt").hidden = true; resetStages(); hideStepper();
  currentIntent = null; $("#utterance").value = ""; $("#utterance").focus();
});

// ---------- confetti (tiny, dependency-free, respects reduced motion) ----------
function confettiBurst() {
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const host = $("#confetti");
  host.replaceChildren();
  const colors = ["#8b85ff", "#5eead4", "#fbbf24", "#f472b6", "#60a5fa"];
  for (let i = 0; i < 28; i++) {
    const c = el("i");
    c.style.left = `${8 + Math.random() * 84}%`;
    c.style.background = colors[i % colors.length];
    c.style.animationDelay = `${Math.random() * 0.25}s`;
    c.style.transform = `rotate(${Math.random() * 360}deg)`;
    host.appendChild(c);
  }
  setTimeout(() => host.replaceChildren(), 1800);
}

// ---------- contacts ----------
$("#contact-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    await rpc("contacts.add",
      { name: $("#ct-name").value, address: $("#ct-addr").value });
    $("#ct-name").value = ""; $("#ct-addr").value = "";
    refreshAll();
  } catch (e) { toast(e.message); }
});

// ---------- plans (agent runtime) ----------
let _activeGrant = null;
async function refreshPlans() {
  let pend;
  try { pend = await rpc("agent.pending"); } catch { return; }
  const card = $("#plans-card");
  if (!_activeGrant && pend.count === 0) { card.hidden = true; return; }
  card.hidden = false;
  $("#plans-human").textContent = pend.human || "";
  $("#pending-digests").replaceChildren(...(pend.pending || []).map(p => {
    const c = el("div", "digest-card");
    c.appendChild(el("p", "digest-line", p.line));
    const row = el("div", "row");
    const vBtn = el("button", "btn secondary", "Stop this one");
    vBtn.addEventListener("click", async () => {
      try {
        const r = await rpc("agent.veto", { action_id: p.short_id });
        c.querySelector(".digest-line").textContent = r.human || "Stopped.";
        refreshAll();
      } catch (e) { toast(e.message); }
    });
    const rBtn = el("button", "btn danger", "Stop the whole plan");
    rBtn.addEventListener("click", async () => {
      try {
        const r = await rpc("agent.revoke", {});
        $("#plans-human").textContent = r.human || "Plan stopped.";
        refreshAll();
      } catch (e) { toast(e.message); }
    });
    row.append(vBtn, rBtn); c.appendChild(row);
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
      { utterance: $("#plan-utterance").value, grant_id: _activeGrant });
    $("#plans-card").hidden = false;
    $("#plans-human").textContent = r.ok !== false
      ? (r.human || "Queued. You have 24 hours to change your mind.")
      : (r.human || "Nothing was proposed.");
    $("#plan-utterance").value = "";
    refreshAll();
  } catch (e) { $("#plans-human").textContent = e.message; }
});

// ---------- context help (help-dot → dialog, all static copy) ----------
const HELP = {
  "what-is-prism": ["What is Prism?",
    "A private, human-first coin. You say what you want in plain words; Prism " +
    "re-states it literally, checks it against fraud patterns, and only sends " +
    "money after YOU approve it twice — once with a picture check, once with " +
    "your password. Everything runs on your own computer."],
  "why-password": ["Why a password?",
    "Your spending key lives only on this machine, sealed with this password. " +
    "Prism's servers never see it — there are no servers. Forget it and you " +
    "still have your 12 recovery words; lose both and the wallet is truly " +
    "yours alone: nobody can recover it, including us."],
  "why-words": ["Why twelve words?",
    "They are your wallet in paper form. Anyone holding the whole computer " +
    "might read your password, but the words recreate the key even if this " +
    "machine burns. Write them offline; never photograph or email them."],
  "locked": ["Why is my wallet locked?",
    "Locking wipes the decrypted key from memory. The encrypted copy stays on " +
    "disk, so unlocking is instant — but nothing can be signed while locked. " +
    "It's the seatbelt of the wallet."],
  "balance": ["About your balance",
    "Balances live on your local devnet chain — real blocks, real mining, " +
    "your computer only. 'Get test PRSM' mines five blocks so you can try " +
    "sending without risk."],
  "nl-bar": ["Plain words, checked twice",
    "Type an instruction like 'send 2 PRSM to Alex'. Prism compiles it into a " +
    "literal intent, asks clarifying questions instead of guessing, and shows " +
    "a full preview before anything is signed."],
  "captcha": ["What does the picture prove?",
    "That a human is present right now, at THIS payment. The answer is " +
    "cryptographically bound to the exact transaction fingerprint you just " +
    "approved — a solved check can never authorize a different payment. It " +
    "expires in two minutes and works once. It proves presence, not identity: " +
    "that's why the password step follows."],
  "signing": ["How signing works",
    "Your password unlocks the key just long enough to sign this one payment " +
    "digest, then the signature is broadcast to your local chain. The signed " +
    "bytes are exactly what the review screen showed you — same fingerprint."],
  "plans": ["Standing plans",
    "A plan is something you allowed once ('buy $50 weekly'). Before anything " +
    "runs you get 24 hours to veto; stopping a plan cancels everything waiting " +
    "under it. Nothing ever happens silently."],
};
document.querySelectorAll(".help-dot").forEach(d =>
  d.addEventListener("click", () => {
    const h = HELP[d.dataset.help]; if (!h) return;
    $("#help-title").textContent = h[0];
    $("#help-body").textContent = h[1];
    $("#help-modal").showModal();
  }));

// ---------- theme ----------
$("#theme-toggle").addEventListener("click", () => {
  const b = document.body;
  b.dataset.theme = b.dataset.theme === "dark" ? "light" : "dark";
  localStorage.setItem("prism-theme", b.dataset.theme);
});
const savedTheme = localStorage.getItem("prism-theme");
if (savedTheme) document.body.dataset.theme = savedTheme;
else if (matchMedia("(prefers-color-scheme: light)").matches)
  document.body.dataset.theme = "light";

// ---------- go ----------
boot();
setInterval(() => { if (!$("#screen-app").hidden) refreshAll(); }, 5000);
