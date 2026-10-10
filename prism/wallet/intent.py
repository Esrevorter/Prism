"""NL Intent Compiler v0 — wallet/ deliverable (spec §6.2 "Transaction
Intent", §7.3 happy path, §8 AI hard rule).

The contract: the natural-language layer and the signer communicate ONLY
through a validated Intent. This module turns a raw utterance into that
structure with a deterministic, auditable grammar (v0 ships no LLM in the
trust path — an LLM may *propose* fields, but every proposal must pass
this validator unchanged; ambiguity always degrades to a clarifying chip
or a safe fallback, never to a guess).

Pipeline (§7.3 steps 2–4):

    utterance ──▶ parse/compile ──▶ [needs_clarify?] ──▶ Intent(draft)
                                        │ ok                │
                                  simulator.preview ◀───────┘
                                        │
                                  fraud flags (advisory only)
                                        ▼
                              awaiting_confirm → user confirms → sign

Hard rules enforced here:
  * Amounts are integers in shards (1e-8 PRSM); floats exist only inside
    the fiat-quote envelope (§6.3 identifier conventions).
  * Unknown / ambiguous recipients NEVER auto-resolve: status stays
    ``draft`` with ``needs_clarify`` set (single-chip UX, not a wall of
    options — §7.3 step 2).
  * Fiat intents MUST carry a locked oracle quote window + max-slippage
    guard before they can leave ``draft`` (§7.3 step 2, E5 stale-rate).
  * Model output is advisory: risk_score > threshold raises a calm
    interruption flag but cannot veto or mutate the intent silently, and
    nothing in this module ever emits a signing decision (§8).

Reference implementation: readable, stdlib-only, tested in
prism/tests/test_wallet_intent.py.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field as dc_field
from typing import Optional

# ---------------------------------------------------------------------------
# Constants (spec Parameters table / §6.2)
# ---------------------------------------------------------------------------

SHARDS_PER_PRSM = 10**8          # base unit "shard" = 1e-8 PRSM (§Parameters)
BASE_FEE_SHARD = 10_000          # fixed 0.0001 PRSM (§Parameters, §fee model)
MAX_PRIORITY_MULTIPLIER = 10     # priority fee capped at 10x base (§fee model)
FROZEN_FEE_SHARD = BASE_FEE_SHARD  # fee market frozen for Refraction (§14 Q1)

PRSM_RATE = 4.05                 # reference PRSM/USD for the stub oracle
RATE_WINDOW_SECONDS = 60         # locked 60-second rate window (§7.3 step 2)
DEFAULT_MAX_SLIPPAGE_PCT = 2.0   # §6.2 example intent

VALID_ACTIONS = {"transfer", "stake", "open_channel"}
VALID_PRIVACY_MODES = {"default_max", "disclosed_to_recipient", "public_audit"}
VALID_FEES = {"base_only", "priority_low", "priority_max"}
VALID_STATUSES = {"draft", "awaiting_confirm", "broadcast", "confirmed",
                  "failed", "cancelled"}

# Fraud-model thresholds (§7.3 step 4): score above RISK_INTERRUPT triggers
# the calm interruption card; between WATCH and INTERRUPT it is informational.
RISK_WATCH = 0.30
RISK_INTERRUPT = 0.70


def prsm_to_shard(prsm: float | str) -> int:
    """Exact decimal→shard conversion (no binary-float drift)."""
    s = str(prsm).strip()
    neg = s.startswith("-")
    s = s.lstrip("+-")
    if "." in s:
        whole, frac = s.split(".", 1)
    else:
        whole, frac = s, ""
    if not re.fullmatch(r"\d*", whole or "0") or not re.fullmatch(r"\d*", frac or "0"):
        raise ValueError(f"not a number: {prsm!r}")
    frac = (frac + "0" * 8)[:8]  # sub-shard precision truncated toward zero
    val = int(whole or "0") * SHARDS_PER_PRSM + int(frac or "0")
    return -val if neg else val


def shard_to_prsm(shard: int) -> str:
    """Canonical display string, trailing zeros trimmed."""
    sign = "-" if shard < 0 else ""
    shard = abs(shard)
    whole, frac = divmod(shard, SHARDS_PER_PRSM)
    frac_s = f"{frac:08d}".rstrip("0")
    return f"{sign}{whole}" + (f".{frac_s}" if frac_s else "")


# ---------------------------------------------------------------------------
# Address book (§6.2 Contact) and the stub price oracle
# ---------------------------------------------------------------------------


@dataclass
class Contact:
    contact_id: str
    display_name: str
    payment_address: str                      # prsm1... style
    nickname_aliases: list[str] = dc_field(default_factory=list)
    verified: bool = False
    verification_method: Optional[str] = None  # qr_in_person|dns_proof|trust_chain
    consent_to_be_named_in_disclosures: bool = False

    def normalized(self) -> "Contact":
        aliases = [a.strip().lower() for a in self.nickname_aliases]
        # The display name's first token is always an implicit alias so
        # "Alex (design)" answers to "alex" (§6.2 nickname_aliases).
        head = re.split(r"[\s(]", self.display_name.strip(), 1)[0].lower()
        if head and head not in aliases:
            aliases.append(head)
        return Contact(self.contact_id, self.display_name, self.payment_address,
                       aliases, self.verified, self.verification_method,
                       self.consent_to_be_named_in_disclosures)


class AddressBook:
    """Nickname index with casefold matching and typo-tolerant suggestions."""

    def __init__(self, contacts: list[Contact] | None = None):
        self._contacts: dict[str, Contact] = {}
        self._alias_index: dict[str, list[str]] = {}
        for c in contacts or []:
            self.add(c)

    def add(self, contact: Contact) -> None:
        c = contact.normalized()
        self._contacts[c.contact_id] = c
        for alias in c.nickname_aliases:
            key = alias.casefold()
            bucket = self._alias_index.setdefault(key, [])
            if c.contact_id not in bucket:
                bucket.append(c.contact_id)

    def lookup(self, term: str) -> list[Contact]:
        """All contacts whose any alias equals `term` (casefolded).

        Returns 0 / 1 / many — the compiler treats >=2 as ambiguity and 0
        as unknown; neither case may be auto-resolved (§7.3 step 2).
        """
        ids = self._alias_index.get(term.strip().casefold(), [])
        return [self._contacts[i] for i in ids]

    def suggest(self, term: str, max_distance: int = 2) -> list[Contact]:
        """Edit-distance <= max_distance fallback → 'Did you mean…' chips."""
        term = term.strip().casefold()
        if not term:
            return []
        out: list[Contact] = []
        seen: set[str] = set()
        for alias, ids in self._alias_index.items():
            if _edit_distance(term, alias) <= max_distance:
                for i in ids:
                    if i not in seen:
                        seen.add(i)
                        out.append(self._contacts[i])
        return sorted(out, key=lambda c: c.display_name)


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


@dataclass
class Quote:
    """Locked fiat→PRSM quote envelope (mirrors §6.2 fiat_quote + window)."""

    currency: str
    fiat_value: float
    rate: float               # USD per PRSM at quote time
    prsm_exact: float         # fiat_value / rate, pre-rounding
    max_slippage_pct: float
    locked_until_unix: int
    oracle: str = "medianizer"

    @property
    def amount_shard(self) -> int:
        return prsm_to_shard(round(self.prsm_exact, 8))

    def slippage_bounds_shard(self) -> tuple[int, int]:
        """Worst accepted PRSM amount under the slippage guard (E5)."""
        lo = prsm_to_shard(round(self.prsm_exact * (1 - self.max_slippage_pct / 100), 8))
        hi = prsm_to_shard(round(self.prsm_exact * (1 + self.max_slippage_pct / 100), 8))
        return lo, hi


class StubOracle:
    """Deterministic reference price feed standing in for Medianizer v0.

    Real deployment replaces this with the signed medianizer stream; the
    compiler only ever sees the Quote envelope, so the swap is local.
    """

    def __init__(self, usd_per_prsm: float = PRSM_RATE):
        self.rate = usd_per_prsm

    def quote(self, currency: str, fiat_value: float, *, now_unix: int,
              window_seconds: int = RATE_WINDOW_SECONDS,
              max_slippage_pct: float = DEFAULT_MAX_SLIPPAGE_PCT) -> Quote:
        if currency.upper() != "USD":
            raise Unsupported(f"stub oracle only prices USD, got {currency!r}")
        if fiat_value <= 0:
            raise Ambiguous("fiat amount must be positive")
        return Quote(currency="USD", fiat_value=float(fiat_value),
                     rate=self.rate, prsm_exact=fiat_value / self.rate,
                     max_slippage_pct=max_slippage_pct,
                     locked_until_unix=now_unix + window_seconds)


# ---------------------------------------------------------------------------
# Compiled payload + the Intent itself (§6.2 schema)
# ---------------------------------------------------------------------------


class IntentError(ValueError):
    """Base class for compiler refusals (never a silent partial intent)."""


class Ambiguous(IntentError):
    pass


class Unsupported(IntentError):
    pass


@dataclass
class Compiled:
    action: str = "transfer"
    recipient_ref: Optional[str] = None          # contact:<id> | addr:<prsm1...>
    amount: dict = dc_field(default_factory=dict)  # {or_prsm: N} | {fiat_quote: {...}}
    privacy_mode: str = "default_max"
    memo: Optional[str] = None
    fee: str = "base_only"

    def to_json(self) -> dict:
        return {"action": self.action, "recipient_ref": self.recipient_ref,
                "amount": self.amount, "privacy_mode": self.privacy_mode,
                "memo": self.memo, "fee": self.fee}


@dataclass
class Simulation:
    predicted_effects: str = ""
    risk_score: float = 0.0
    warnings: list[str] = dc_field(default_factory=list)

    def to_json(self) -> dict:
        return {"predicted_effects": self.predicted_effects,
                "risk_score": self.risk_score, "warnings": self.warnings}


@dataclass
class Confirmation:
    required: bool = True
    method: str = "biometric"
    signed_at: Optional[int] = None

    def to_json(self) -> dict:
        return {"required": self.required, "method": self.method,
                "signed_at": self.signed_at}


@dataclass
class Intent:
    """The NL↔signer contract (§6.2). Immutable once broadcast (v0)."""

    intent_id: str
    raw_utterance: str
    compiled: Compiled
    simulation: Simulation = dc_field(default_factory=Simulation)
    confirmation: Confirmation = dc_field(default_factory=Confirmation)
    status: str = "draft"
    needs_clarify: Optional[str] = None       # single-chip prompt, §7.3 step 2
    created_at_unix: int = 0

    # -- validation ---------------------------------------------------------

    def _slots_missing(self) -> list[str]:
        """Required fields still unresolved; non-empty forces draft+chip.

        'stake' is self-directed: it has no recipient slot at all (§7.x).
        """
        c = self.compiled
        missing: list[str] = []
        if c.action in ("transfer", "open_channel") and c.recipient_ref is None:
            missing.append("recipient")
        amt = c.amount
        if not amt:
            missing.append("amount")
        elif "or_prsm" in amt:
            if not isinstance(amt["or_prsm"], int) or amt["or_prsm"] <= 0:
                missing.append("amount")
        elif "fiat_quote" in amt:
            q = amt["fiat_quote"]
            ok = all(k in q for k in ("currency", "value", "oracle",
                                      "max_slippage_pct", "locked_until_unix",
                                      "rate"))
            ok = ok and float(q.get("value", 0)) > 0 and float(q.get("rate", 0)) > 0
            if not ok:
                missing.append("amount")
        else:
            missing.append("amount")
        return missing

    def validate(self) -> None:
        """Structural gate the signer enforces before ANY share signs.

        Mirrors §6.2: an Intent that fails validation is inert regardless
        of its status field. Drafts may carry unresolved slots (they are
        clarification targets); anything past draft must be complete.
        """
        if self.status not in VALID_STATUSES:
            raise IntentError(f"unknown status {self.status!r}")
        if self.compiled.action not in VALID_ACTIONS:
            raise IntentError(f"unknown action {self.compiled.action!r}")
        if self.compiled.privacy_mode not in VALID_PRIVACY_MODES:
            raise IntentError("unknown privacy_mode")
        if self.compiled.fee not in VALID_FEES:
            raise IntentError("unknown fee tier")
        prio = {"base_only": 0, "priority_low": BASE_FEE_SHARD,
                "priority_max": (MAX_PRIORITY_MULTIPLIER - 1) * BASE_FEE_SHARD}
        if prio[self.compiled.fee] > (MAX_PRIORITY_MULTIPLIER - 1) * BASE_FEE_SHARD:
            raise IntentError("priority fee exceeds 10x base cap")
        missing = self._slots_missing()
        if self.status == "draft":
            # incomplete drafts are legal ONLY with a clarification chip set
            if missing and not self.needs_clarify:
                raise IntentError(f"draft missing {missing} without needs_clarify")
        else:
            if missing:
                raise IntentError(f"{self.status} intent missing {missing}")
            if self.needs_clarify:
                raise IntentError("unresolved clarification cannot leave draft")

    def total_cost_shard(self, *, now_unix: Optional[int] = None) -> int:
        """Shards leaving the wallet (amount + fee [+ priority])."""
        prio = {"base_only": 0, "priority_low": BASE_FEE_SHARD,
                "priority_max": (MAX_PRIORITY_MULTIPLIER - 1) * BASE_FEE_SHARD}
        amt = self.compiled.amount
        if "or_prsm" in amt:
            amount = amt["or_prsm"]
        elif "fiat_quote" in amt:
            q = amt["fiat_quote"]
            amount = prsm_to_shard(round(float(q["value"]) / float(q["rate"]), 8))
        else:
            raise IntentError("intent has no resolved amount")
        return amount + BASE_FEE_SHARD + prio[self.compiled.fee]

    def quote_expired(self, now_unix: int) -> bool:
        q = self.compiled.amount.get("fiat_quote")
        return bool(q) and now_unix >= int(q["locked_until_unix"])

    # -- lifecycle ----------------------------------------------------------

    def mark_awaiting_confirm(self, sim: Simulation, *, now_unix: int) -> "Intent":
        """Draft → awaiting_confirm: requires a completed simulation and,
        for fiat intents, a still-locked quote window (§7.3 steps 2–3)."""
        if self.needs_clarify:
            raise IntentError("resolve clarification first")
        missing = self._slots_missing()
        if missing:
            raise IntentError(f"cannot advance, missing {missing}")
        if not sim.predicted_effects:
            raise IntentError("simulator preview required before confirmation")
        if "fiat_quote" in self.compiled.amount:
            if self.quote_expired(now_unix):
                raise IntentError("quote window expired — re-quote required")
        self.simulation = sim
        self.status = "awaiting_confirm"
        self.validate()
        return self

    def confirm(self, *, now_unix: int) -> "Intent":
        if self.status != "awaiting_confirm":
            raise IntentError(f"cannot confirm from {self.status}")
        if self.confirmation.required and self.confirmation.signed_at is None:
            self.confirmation.signed_at = now_unix
        self.validate()
        return self

    def cancel(self) -> "Intent":
        if self.status in {"broadcast", "confirmed"}:
            raise IntentError("broadcast intents are irreversible; use chain-level controls")
        self.status = "cancelled"
        return self

    def to_json(self) -> dict:
        return {"intent_id": self.intent_id, "raw_utterance": self.raw_utterance,
                "compiled": self.compiled.to_json(),
                "simulation": self.simulation.to_json(),
                "confirmation": self.confirmation.to_json(),
                "status": self.status, "needs_clarify": self.needs_clarify,
                "created_at_unix": self.created_at_unix}

    def canonical_digest(self) -> bytes:
        """Stable hash of everything the signer commits to (audit/hashchain)."""
        blob = json.dumps(self.to_json(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).digest()


# ---------------------------------------------------------------------------
# Deterministic grammar (v0 compiler front-end)
# ---------------------------------------------------------------------------

_MONEY_RE = re.compile(
    r"(?P<fiat>[$€£]\s*(?P<fiat_amt>\d+(?:\.\d{1,2})?))"
    r"|(?P<prsm>(?P<prsm_amt>\d+(?:\.\d+)?)\s*prsm\b)",
    re.IGNORECASE)

_ACTION_MAP = {
    "send": "transfer", "pay": "transfer", "shoot": "transfer",
    "stake": "stake", "lock": "stake",
    "channel": "open_channel",
}

_MEMO_MARKERS = re.compile(r"\bfor\b\s+", re.IGNORECASE)
_TAIL_STRIP = re.compile(
    r"[,;.]?\s*\b(keep it private|privately|anonymously|private)\b[\s.,!]*$",
    re.IGNORECASE)


@dataclass
class ParseResult:
    action: Optional[str] = None
    recipient_term: Optional[str] = None
    fiat_amount: Optional[float] = None
    prsm_amount: Optional[str] = None       # decimal string, exact
    memo: Optional[str] = None
    explicit_public: bool = False
    notes: list[str] = dc_field(default_factory=list)


def parse_utterance(text: str) -> ParseResult:
    """Rule-based extraction. Deliberately conservative: anything the
    grammar doesn't understand is dropped into `notes`, never guessed."""
    r = ParseResult()
    t = text.strip()
    low = t.lower()

    # action
    for verb, act in (("send", "transfer"), ("pay", "transfer"),
                      ("stake", "stake"), ("open channel", "open_channel"),
                      ("channel", "open_channel")):
        if verb in low:
            r.action = _ACTION_MAP.get(verb, act)
            break
    if r.action is None:
        raise Ambiguous("no supported action found (try 'Send …', 'Stake …')")

    # amounts (exactly one money expression allowed; two = ambiguity)
    monies = [m for m in _MONEY_RE.finditer(t)]
    if len(monies) > 1:
        raise Ambiguous("multiple amounts in one utterance — one intent, one amount")
    if monies:
        m = monies[0]
        if m.group("fiat"):
            r.fiat_amount = float(m.group("fiat_amt"))
        else:
            r.prsm_amount = m.group("prsm_amt")

    # privacy mode: default_max unless explicitly asked otherwise (§ principle 2)
    if re.search(r"\b(public|publicly|transparent|auditable|audit)\b", low):
        r.explicit_public = True
        r.notes.append("explicit public/audit phrasing → privacy_mode=public_audit")

    # memo: text after 'for ... ,' up to the next clause boundary
    mm = _MEMO_MARKERS.search(t)
    if mm:
        tail = t[mm.end():]
        # cut at known trailing directives
        cut = re.split(r",|\bkeep it\b|\bprivately\b|\bplease\b", tail, 1)[0]
        cut = cut.strip(" .!?,;")
        if cut:
            r.memo = cut[:140]

    # recipient: strip money phrase + memo clause, then take the person-ish remnant
    resid = t
    if monies:
        m = monies[0]
        resid = resid.replace(m.group(0), " ", 1)
    resid = _TAIL_STRIP.sub(" ", resid)
    if mm:
        resid = resid[:resid.find(mm.group(0))] if mm.group(0) in resid else resid
    resid = re.sub(r"\b(to|for the|please|now|pls)\b", " ", resid, flags=re.IGNORECASE)
    resid = re.sub(r"\b(send|pay|stake|lock|keep it private|privately)\b", " ",
                   resid, flags=re.IGNORECASE)
    resid = re.sub(r"[,.!?;]+", " ", resid)
    resid = re.sub(r"\s+", " ", resid).strip()
    if resid:
        # prefer an embedded address literal if present
        addr = re.search(r"\bprsm1[0-9a-z]{6,}\b", resid)
        if addr:
            r.recipient_term = addr.group(0)
        else:
            r.recipient_term = resid.split(" ")[0]
            if len(resid.split(" ")) > 1:
                r.notes.append(f"multi-token remainder {resid!r}; using first token")
    return r


# ---------------------------------------------------------------------------
# Simulator + fraud-advisor stubs (§8: advisory only, never signing input)
# ---------------------------------------------------------------------------


def simulate(intent: Intent, *, now_unix: int,
             balance_shard: Optional[int] = None) -> Simulation:
    """Plain-English preview (§7.3 step 3) + structural warnings.

    The real build delegates to ai/ transaction-sim model; v0 keeps the
    wording deterministic so tests and audits see identical output.
    """
    c = intent.compiled
    warnings: list[str] = []
    prio = {"base_only": 0, "priority_low": BASE_FEE_SHARD,
            "priority_max": (MAX_PRIORITY_MULTIPLIER - 1) * BASE_FEE_SHARD}
    fee_txt = "$0.001" if c.fee == "base_only" else \
        f"${(BASE_FEE_SHARD + prio[c.fee]) * PRSM_RATE / SHARDS_PER_PRSM:.4f}"
    priv_txt = {
        "default_max": "It will appear private to everyone except the recipient and you.",
        "disclosed_to_recipient": "The recipient gets a copyable disclosure ID.",
        "public_audit": "WARNING: this transfer opts into audit-visible disclosure.",
    }[c.privacy_mode]
    amt = c.amount
    amount_shard: Optional[int] = None
    if "or_prsm" in amt:
        amount_shard = amt["or_prsm"]
        amount_txt = f"{shard_to_prsm(amount_shard)} PRSM"
    elif "fiat_quote" in amt:
        q = amt["fiat_quote"]
        amount_shard = prsm_to_shard(round(float(q["value"]) / float(q["rate"]), 8))
        amount_txt = (f"≈{shard_to_prsm(amount_shard)} PRSM "
                      f"(${q['value']:.2f} @ ${q['rate']}/PRSM)")
        if intent.quote_expired(now_unix):
            warnings.append("quote window expired — re-quote before confirming")
    else:
        # incomplete draft: preview what's known, flag the open slots (§7.3 step 2)
        parts = []
        if c.recipient_ref is None:
            parts.append("recipient?")
        parts.append("amount?")
        amount_txt = "missing " + ", ".join(parts)
    verb = {"transfer": "sends", "stake": "stakes",
            "open_channel": "opens a channel with capacity of"}[c.action]
    effects = (f"This {verb} {amount_txt}. Fee: {fee_txt}. "
               + ("It will appear private to everyone except you."
                  if c.action == "stake" else priv_txt))
    if balance_shard is not None and amount_shard is not None:
        total = amount_shard + BASE_FEE_SHARD + prio[c.fee]
        if total > balance_shard:
            warnings.append(f"insufficient funds: need {shard_to_prsm(total)} PRSM, "
                            f"have {shard_to_prsm(balance_shard)}")
    return Simulation(predicted_effects=effects, risk_score=0.0, warnings=warnings)


def fraud_flags(intent: Intent, history: dict[str, list[int]] | None = None
                ) -> tuple[float, list[str]]:
    """Advisory destination/history score (§7.3 step 4).

    v0 heuristic stands in for on-device model v3; wallet UI consumes the
    score (watch/interrupt bands) but this function CANNOT block, mutate,
    or sign — hard rule from §8.
    """
    flags: list[str] = []
    score = 0.0
    ref = intent.compiled.recipient_ref or ""
    hist = history or {}
    if ref.startswith("addr:") and ref not in hist:
        score += 0.5
        flags.append("first payment to a never-seen address")
    contact_verified = hist.get("contact_verified", [1])
    if ref.startswith("contact:") and contact_verified and not contact_verified[-1]:
        score += 0.35
        flags.append("recipient contact is unverified")
    if intent.compiled.privacy_mode == "public_audit":
        score += 0.1
        flags.append("public-audit mode selected")
    return min(score, 1.0), flags


# ---------------------------------------------------------------------------
# The compiler
# ---------------------------------------------------------------------------


class IntentCompiler:
    """Utterance → Intent, given an address book and an oracle."""

    def __init__(self, book: AddressBook, oracle: StubOracle | None = None):
        self.book = book
        self.oracle = oracle or StubOracle()

    def compile(self, utterance: str, *, now_unix: int,
                balance_shard: Optional[int] = None) -> Intent:
        parsed = parse_utterance(utterance)
        c = Compiled(action=parsed.action or "transfer")

        # ---- recipient resolution (never auto-guessed) ----
        clarify: Optional[str] = None
        term = parsed.recipient_term
        if c.action == "stake":
            term = None  # self-directed; don't misread "Stake 5 PRSM to X" tail
        if not term:
            if c.action in ("transfer", "open_channel"):
                clarify = "Who should I send to?"
        elif term.lower().startswith("prsm1"):
            c.recipient_ref = f"addr:{term}"
        else:
            hits = self.book.lookup(term)
            if len(hits) == 1:
                c.recipient_ref = f"contact:{hits[0].contact_id}"
            elif len(hits) > 1:
                names = ", ".join(h.display_name for h in hits)
                clarify = f"Which one — {names}?"
            else:
                sugg = self.book.suggest(term)
                if len(sugg) == 1:
                    clarify = f"Did you mean {sugg[0].display_name}?"
                elif len(sugg) > 1:
                    clarify = (f"Did you mean "
                               f"{', '.join(s.display_name for s in sugg[:2])}?")
                else:
                    clarify = f"I don't know '{term}' — add them to contacts?"

        # ---- amount ----
        if parsed.prsm_amount is not None:
            c.amount = {"or_prsm": prsm_to_shard(parsed.prsm_amount)}
        elif parsed.fiat_amount is not None:
            try:
                q = self.oracle.quote("USD", parsed.fiat_amount, now_unix=now_unix)
            except Unsupported as e:
                clarify = clarify or str(e)
                q = None
            if q is not None:
                c.amount = {"fiat_quote": {
                    "currency": q.currency, "value": q.fiat_value,
                    "oracle": q.oracle, "rate": q.rate,
                    "max_slippage_pct": q.max_slippage_pct,
                    "locked_until_unix": q.locked_until_unix}}
        else:
            clarify = clarify or "How much?"

        c.memo = parsed.memo
        c.privacy_mode = "public_audit" if parsed.explicit_public else "default_max"
        c.fee = "base_only"

        intent = Intent(intent_id=str(uuid.uuid4()), raw_utterance=utterance,
                        compiled=c, needs_clarify=clarify,
                        created_at_unix=now_unix, status="draft")
        # Invariant: every unresolved slot must be covered by a chip prompt.
        missing = intent._slots_missing()
        if missing and not clarify:
            order = [m for m in ("recipient", "amount") if m in missing]
            clarify = ("Who should I send to?" if order == ["recipient"] else
                       "How much?" if order == ["amount"] else
                       "Recipient and amount?")
            intent.needs_clarify = clarify
        intent.validate()

        # Advisory fraud overlay lands in simulation even for drafts so the
        # UI can pre-arm the interruption card (§7.3 step 4).
        sim = simulate(intent, now_unix=now_unix, balance_shard=balance_shard)
        score, flags = fraud_flags(intent)
        sim.risk_score = round(max(sim.risk_score, score), 4)
        sim.warnings.extend(flags)
        intent.simulation = sim
        if clarify is None:
            intent.mark_awaiting_confirm(sim, now_unix=now_unix)
        return intent


# ---------------------------------------------------------------------------
# Follow-up chips: resolve a clarification against the pending draft
# ---------------------------------------------------------------------------


def apply_clarification(pending: Intent, answer: str, book: AddressBook,
                        oracle: StubOracle, *, now_unix: int) -> Intent:
    """Fold a chip answer ('Alex Rivera', '$50', 'yes') into the draft.

    Re-validates and advances to awaiting_confirm when fully resolved.
    The original draft is left untouched (immutable-by-convention; the
    returned object carries a fresh intent_id so audit logs stay honest).
    """
    if pending.status != "draft" or not pending.needs_clarify:
        raise IntentError("no pending clarification on this intent")
    c = Compiled(**json.loads(json.dumps(pending.compiled.to_json())))
    q = c.amount.get("fiat_quote")
    if q:  # re-lock the window on any follow-up turn (§7.3 step 2)
        new_q = oracle.quote(q["currency"], q["value"], now_unix=now_unix,
                             max_slippage_pct=q["max_slippage_pct"])
        c.amount = {"fiat_quote": {"currency": new_q.currency,
                                   "value": new_q.fiat_value,
                                   "oracle": new_q.oracle, "rate": new_q.rate,
                                   "max_slippage_pct": new_q.max_slippage_pct,
                                   "locked_until_unix": new_q.locked_until_unix}}

    ans = answer.strip()
    low = ans.lower()

    need_amount = not c.amount or "or_prsm" not in c.amount and "fiat_quote" not in c.amount
    need_recipient = c.recipient_ref is None and c.action in ("transfer", "open_channel")

    # money answer? (folded independently of the recipient slot)
    if need_amount:
        m = _MONEY_RE.search(ans)
        if m:
            if m.group("fiat"):
                new_q = oracle.quote("USD", float(m.group("fiat_amt")),
                                     now_unix=now_unix)
                c.amount = {"fiat_quote": {"currency": new_q.currency,
                                           "value": new_q.fiat_value,
                                           "oracle": new_q.oracle, "rate": new_q.rate,
                                           "max_slippage_pct": new_q.max_slippage_pct,
                                           "locked_until_unix": new_q.locked_until_unix}}
            else:
                c.amount = {"or_prsm": prsm_to_shard(m.group("prsm_amt"))}

    # recipient answer? (exact alias hit first, then typo suggestions)
    if need_recipient and c.recipient_ref is None:
        if low.startswith("prsm1"):
            c.recipient_ref = f"addr:{low}"
        else:
            hits = book.lookup(ans)
            if len(hits) == 1:
                c.recipient_ref = f"contact:{hits[0].contact_id}"
            elif len(hits) > 1:
                pending.needs_clarify = (
                    f"Still unsure — {', '.join(h.display_name for h in hits[:3])}?")
                return pending
            else:
                sugg = book.suggest(ans)
                if len(sugg) == 1:
                    c.recipient_ref = f"contact:{sugg[0].contact_id}"
                elif len(sugg) > 1:
                    pending.needs_clarify = (
                        f"Still unsure — {', '.join(s.display_name for s in sugg[:3])}?")
                    return pending

    # mini-utterance fallback: "$50 to Alex" / "Alex" typed as a sentence
    still_open = (need_amount and not c.amount) or (
        need_recipient and c.recipient_ref is None)
    if still_open:
        try:
            p = parse_utterance(f"send {ans}")
            if need_amount and not c.amount:
                if p.prsm_amount:
                    c.amount = {"or_prsm": prsm_to_shard(p.prsm_amount)}
                elif p.fiat_amount:
                    nq = oracle.quote("USD", p.fiat_amount, now_unix=now_unix)
                    c.amount = {"fiat_quote": {"currency": nq.currency,
                                               "value": nq.fiat_value,
                                               "oracle": nq.oracle, "rate": nq.rate,
                                               "max_slippage_pct": nq.max_slippage_pct,
                                               "locked_until_unix": nq.locked_until_unix}}
            if need_recipient and c.recipient_ref is None and p.recipient_term:
                hits = book.lookup(p.recipient_term) or book.suggest(p.recipient_term)
                if len(hits) == 1:
                    c.recipient_ref = f"contact:{hits[0].contact_id}"
        except (Ambiguous, Unsupported):
            pass

    resolved = bool(c.amount) and (c.recipient_ref is not None or not need_recipient)
    if not resolved:
        raise Ambiguous(f"couldn't fold {answer!r} into the pending intent")

    out = Intent(intent_id=str(uuid.uuid4()),
                 raw_utterance=f'{pending.raw_utterance} → "{answer}"',
                 compiled=c, needs_clarify=None,
                 created_at_unix=now_unix, status="draft")
    out.validate()
    sim = simulate(out, now_unix=now_unix)
    score, flags = fraud_flags(out)
    sim.risk_score = round(max(sim.risk_score, score), 4)
    sim.warnings.extend(flags)
    out.simulation = sim
    out.mark_awaiting_confirm(sim, now_unix=now_unix)
    return out


# ---------------------------------------------------------------------------
# Benchmark harness (§ Phase-1 acceptance: ≥95% correct-or-safe-fallback)
# ---------------------------------------------------------------------------

# (utterance, expected_status, expected_action, expected_recipient_prefix,
#  expected_amount_kind)
BENCHMARK_CASES: list[tuple[str, str, str, Optional[str], str]] = [
    ("Send $50 to Alex for the design session, keep it private",
     "awaiting_confirm", "transfer", "contact:alex", "fiat_quote"),
    ("send 12.5 PRSM to alex@studio",
     "awaiting_confirm", "transfer", "contact:alex", "or_prsm"),
    ("Pay Sam 0.25 PRSM for the proofs",
     "awaiting_confirm", "transfer", "contact:sam", "or_prsm"),
    ("Send $20 to Jordan for session fees",
     "awaiting_confirm", "transfer", "contact:jordan", "fiat_quote"),
    ("Send 1 PRSM to prsm1qqw3e5r7t9y",
     "awaiting_confirm", "transfer", "addr:prsm1qqw3e5r7t9y", "or_prsm"),
    ("Stake 100 PRSM",                           # self-directed action: no recipient slot
     "awaiting_confirm", "stake", None, "or_prsm"),
    ("Send $50 to Taylor",                       # ambiguous nickname → chip
     "draft", "transfer", None, "fiat_quote"),
    ("Send $50 to nobody",                       # unknown → did-you-mean/fallback
     "draft", "transfer", None, "fiat_quote"),
    ("Send money to Alex",                       # 'money' misread as recipient
     "draft", "transfer", None, ""),             #   → both slots stay chips
    ("Buy groceries",                            # no action → hard refusal
     "error", "", None, ""),
    ("Send 3 PRSM to Alex please, keep it private",
     "awaiting_confirm", "transfer", "contact:alex", "or_prsm"),
    ("send $12.34 to sam for logo revisions",
     "awaiting_confirm", "transfer", "contact:sam", "fiat_quote"),
]


def benchmark(book: AddressBook, oracle: StubOracle | None = None,
              *, now_unix: int = 1_893_456_000) -> dict:
    """Score the deterministic grammar on BENCHMARK_CASES.

    'correct' = exact match on status/action/recipient/amount-kind.
    'safe'    = refused or degraded to a clarification chip (never a wrong
                silent intent). Acceptance target: correct >= 95% OR
                (correct + safe) == 100%.
    """
    comp = IntentCompiler(book, oracle)
    rows = []
    for utt, exp_status, exp_action, exp_ref, exp_kind in BENCHMARK_CASES:
        got = {"utterance": utt, "expected": exp_status}
        try:
            it = comp.compile(utt, now_unix=now_unix)
            kind = ("fiat_quote" if "fiat_quote" in it.compiled.amount
                    else "or_prsm" if "or_prsm" in it.compiled.amount else "")
            ok = (it.status == exp_status
                  and it.compiled.action == exp_action
                  and (exp_ref is None or it.compiled.recipient_ref == exp_ref)
                  and kind == exp_kind)
            got.update(status=it.status, action=it.compiled.action,
                       recipient=it.compiled.recipient_ref, amount_kind=kind,
                       correct=ok, safe=True)
        except IntentError as e:
            refused = exp_status == "error"
            got.update(status="error", error=str(e), correct=refused,
                       safe=refused or exp_status == "draft")
        rows.append(got)
    n = len(rows)
    correct = sum(r.get("correct", False) for r in rows)
    safe = sum(r.get("safe", False) for r in rows)
    return {"n": n, "correct": correct, "safe": safe,
            "correct_pct": round(100 * correct / n, 1),
            "safe_pct": round(100 * safe / n, 1),
            "passes_acceptance": (correct / n >= 0.95) or (safe == n),
            "rows": rows}
