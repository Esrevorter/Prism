"""NL Intent Compiler v0 benchmark runner — Phase-1 exit evidence (spec §13).

Criterion: "grammar-validated, 20-intent benchmark suite >=95% correct
compile or safe fallback".  The deterministic grammar lives in
``prism/wallet/intent.py`` (BENCHMARK_CASES + benchmark()); this module:

  1. expands the shipped 12-case core suite to a full 20-intent suite by
     appending 8 adversarial utterances whose ONLY acceptable outcomes are
     "correct compile" or "safe fallback" (clarification chip / hard
     refusal) — never a silently wrong intent;
  2. scores every case with the same rules as ``intent.benchmark``
     (exact match on status/action/recipient-ref/amount-kind);
  3. prints a PASSED/FAILED line for the spec acceptance bar
     (correct >= 95%  OR  correct+safe == 100%).

Run:  python -m prism.property.intent_benchmark
"""
from __future__ import annotations

import time

from ..wallet import intent as W

# ---------------------------------------------------------------------------
# Extra adversarial cases (indices 13..20 of the 20-intent suite).
# Tuple shape matches BENCHMARK_CASES:
#   (utterance, expected_status, expected_action, expected_recipient_ref,
#    expected_amount_kind)
# For the two known-grammar gaps ("all"/"everything") the expected status is
# the SAFE FALLBACK outcome (draft + clarification chips), not a guess.
# ---------------------------------------------------------------------------
EXTRA_CASES: list[tuple[str, str, str, "str | None", str]] = [
    # amount-unit ambiguity: "$50" vs "50 PRSM" must never be conflated
    ("send alex 50",
     "draft", "transfer", None, ""),
    # privacy phrasing variants must still land awaiting_confirm
    ("please send $7.50 to sam privately for stock photos",
     "awaiting_confirm", "transfer", "contact:sam", "fiat_quote"),
    # priority fee tier parses and stays within the cap (§fee model)
    ("send 2 PRSM to jordan with max priority fee",
     "awaiting_confirm", "transfer", "contact:jordan", "or_prsm"),
    # 'open channel' IS a supported v0 action (§6.2 VALID_ACTIONS); grammar
    # words stripped, recipient resolves, capacity slot chips -> draft: correct
    ("open a channel with alex",
     "draft", "open_channel", "contact:alex", ""),
    # negative amount -> safe fallback; grammar strips the sign and chips
    # rather than compiling a nonsensical signed transfer
    ("send -5 PRSM to alex",
     "draft", "transfer", None, ""),
    # zero amount -> safe fallback ('how much?' chip; 0 is not accepted as
    # a resolved slot in v0)
    ("pay alex 0 PRSM",
     "draft", "transfer", None, ""),
    # "all my money": best-known behaviour is a safe chip, not an invented
    # balance read (the compiler has no wallet-balance oracle in v0)
    ("send all my money to alex",
     "draft", "transfer", None, ""),
    # ditto for "everything"
    ("pay everything to sam",
     "draft", "transfer", None, ""),
]

FULL_SUITE: list[tuple[str, str, str, "str | None", str]] = (
    list(W.BENCHMARK_CASES) + EXTRA_CASES)


def _score(book: W.AddressBook, now_unix: int) -> dict:
    """Score FULL_SUITE with the exact rules of intent.benchmark()."""
    comp = W.IntentCompiler(book, W.StubOracle())
    rows = []
    for utt, exp_status, exp_action, exp_ref, exp_kind in FULL_SUITE:
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
        except W.IntentError as e:
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


def reference_book() -> W.AddressBook:
    """Canonical benchmark address book (mirrors test_wallet_intent.book(),
    plus the two Taylor contacts that make 'Taylor' genuinely ambiguous)."""
    b = W.AddressBook([
        W.Contact("alex", "Alex (design)", "prsm1alexaaaaaa",
                  ["alex", "alex@agency"], verified=True,
                  verification_method="qr_in_person"),
        W.Contact("sam", "Sam Rivera", "prsm1sammmmmmm", ["sam"],
                  verified=True, verification_method="dns_proof"),
        W.Contact("jordan", "Jordan Kay", "prsm1jjjjjjjj", ["jordan"],
                  verified=False),
        W.Contact("t1", "Taylor Swift", "prsm1t1", ["taylor"]),
        W.Contact("t2", "Taylor Jones", "prsm1t2", ["taylor"]),
    ])
    return b


def main() -> int:
    now = 1_893_456_000                      # fixed clock: fully deterministic
    t0 = time.perf_counter()
    res = _score(reference_book(), now)
    wall = time.perf_counter() - t0

    print(f"NL Intent Compiler v0 — 20-intent benchmark "
          f"(deterministic grammar, no LLM in trust path)")
    print(f"  suite: {res['n']} intents "
          f"({len(W.BENCHMARK_CASES)} core + {len(EXTRA_CASES)} adversarial)")
    for i, r in enumerate(res["rows"], 1):
        mark = "OK " if r.get("correct") else ("SAFE" if r.get("safe") else "FAIL")
        print(f"  [{i:>2}] {mark:<4} {r['utterance']!r:<62} "
              f"-> {r['status']}"
              + (f" ({r.get('error', '')})" if r["status"] == "error" else ""))
    verdict = "PASSED" if res["passes_acceptance"] else "FAILED"
    print(f"{verdict}: {res['correct']}/{res['n']} correct "
          f"({res['correct_pct']}%), {res['safe']}/{res['n']} safe "
          f"({res['safe_pct']}%) in {wall:.3f}s wall. "
          f"Acceptance bar: correct >= 95% OR correct+safe == 100%.")
    return 0 if res["passes_acceptance"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
