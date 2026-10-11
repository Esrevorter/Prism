"""Agent-runtime happy-path smoke test (§7.7 / §8.4).

Runs the FULL lifecycle against the real PrismService (real compiler, real
PolicyEngine, real SigningGate risk, real chain settlement):
  grant_create -> propose -> pending -> tick (execute + settle) -> veto -> revoke
Prints every observation and exits 0 only if ALL transitions are correct.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")  # allow `python -m prism.property.agent_smoke` from repo root

from prism.rpc.service import PrismService


def main() -> int:
    svc = PrismService()
    failures: list[str] = []

    def check(name: str, cond: bool, detail: object = "") -> None:
        status = "OK" if cond else "FAIL"
        print(f"[{status}] {name}: {detail}")
        if not cond:
            failures.append(name)

    # 1. grant_create -------------------------------------------------------
    r = svc.grant_create(days=30, per_action_prsm=25.0, monthly_prsm=400.0,
                         comfort_prsm=None, veto_hours=24.0)
    gid = r["grant"]["grant_id"]
    check("grant_create returns active grant", gid and r["grant"]["revoked_at"] is None, gid[:8])

    # 2. propose (fiat-quoted DCA buy) --------------------------------------
    p = svc.agent_propose("Buy $10 of PRSM to My DCA pot", grant_id=gid)
    check("propose accepted", p.get("ok") is True, p.get("reasons") or p.get("state"))
    aid = p.get("action_id", "")
    check("queued under veto window", p.get("state") == "queued_under_veto_window",
          f"{p.get('state')} executes_in_hours={p.get('executes_in_hours')}")

    # 2b. negative: PRSM-denominated buy must be refused (amount-form rule) --
    bad = svc.agent_propose("Send 5 PRSM to My DCA pot", grant_id=gid)
    check("PRSM-denominated proposal refused", bad.get("refused") is True,
          bad.get("reasons"))

    # 3. pending digest ------------------------------------------------------
    pd = svc.agent_pending()
    check("pending shows the queued action", any(c["action_id"] == aid for c in pd["pending"]),
          f"count={pd['count']}")

    # 4. tick past the veto window -> executed + settled on chain -------------
    # Fund first: an unfunded wallet must honestly refuse (that's a separate
    # negative check below); here we test the funded happy path.
    svc.mine(2)
    assert svc.balance_shard > 0, "faucet gave no balance"
    bal_before = svc.balance_shard
    height_before = svc.chain.tip.header.height
    t = svc.agent_tick(hours_ahead=25.0)
    bcast = [b for b in t.get("broadcast", []) if b.get("action_id", "").startswith(aid[:8])]
    check("tick executed the due action", len(bcast) == 1, t.get("transitions"))
    txid = bcast[0].get("txid") if bcast else None
    check("execution settled with a real txid", bool(txid), txid)
    check("balance actually deducted", svc.balance_shard < bal_before,
          f"{bal_before} -> {svc.balance_shard}")
    check("chain grew by one block", svc.chain.tip.header.height == height_before + 1,
          f"{height_before} -> {svc.chain.tip.header.height}")

    # 5. veto path ------------------------------------------------------------
    p2 = svc.agent_propose("Buy $8 of PRSM to My DCA pot", grant_id=gid)
    aid2 = p2.get("action_id", "")
    check("second proposal queued", p2.get("ok") is True, p2.get("reasons"))
    v = svc.agent_veto(aid2[:8])
    check("veto accepted short id", v.get("ok") is True, v.get("human"))
    t2 = svc.agent_tick(hours_ahead=25.0)
    states = {pa.record.state for pa in svc.agent.pending.values()} | \
             {rec.state for rec in getattr(svc.agent, "history", [])} if hasattr(svc.agent, "history") else set()
    vetoed_ok = all(pa.action_id != aid2 or pa.record.state == "vetoed"
                    for pa in _all_actions(svc))
    check("vetoed action never executed", vetoed_ok, f"tick2 broadcast={t2.get('broadcast')}")

    # 6. revoke cascade -------------------------------------------------------
    p3 = svc.agent_propose("Buy $9 of PRSM to My DCA pot", grant_id=gid)
    aid3 = p3.get("action_id", "")
    rv = svc.agent_revoke(gid)
    check("revoke cascades queued action", rv.get("cascaded_vetoes", 0) >= 1, rv.get("human"))
    after = svc.agent_propose("Buy $1 of PRSM to My DCA pot", grant_id=gid)
    check("post-revoke proposals refused", after.get("refused") is True, after.get("reasons"))
    t3 = svc.agent_tick(hours_ahead=25.0)
    check("revoked-plan action did not execute",
          all(b.get("action_id", "") != aid3 for b in t3.get("broadcast", [])),
          t3.get("broadcast"))

    # 7. report ----------------------------------------------------------------
    rep = svc.agent_report(grant_id=gid)
    check("report produced", rep.get("ok") is not False and rep.get("human"), str(rep)[:120])

    # 8. NEGATIVE: unfunded wallet must refuse, stay queued, settle after funding
    #    (§ ethics.md fail-closed rule — no silent money-state divergence).
    svc2 = PrismService()          # fresh service: balance_shard == 0
    r2 = svc2.grant_create(days=30, per_action_prsm=25.0, monthly_prsm=400.0,
                           comfort_prsm=None, veto_hours=24.0)
    gid2 = r2["grant"]["grant_id"]
    p4 = svc2.agent_propose("Buy $10 of PRSM to My DCA pot", grant_id=gid2)
    aid4 = p4.get("action_id", "")
    t4 = svc2.agent_tick(hours_ahead=25.0)   # window closed, but wallet empty
    refused4 = t4.get("refused_insufficient", [])
    check("unfunded tick refuses non-empty", len(refused4) == 1
          and refused4[0]["action_id"] == aid4, refused4)
    check("unfunded tick broadcast empty", t4.get("broadcast") == [], t4.get("broadcast"))
    pend4 = svc2.agent_pending()
    check("unfunded action stays queued (no terminal state)",
          any(c["action_id"] == aid4 for c in pend4["pending"]), f"count={pend4['count']}")
    states4 = [x["state"] for x in t4.get("transitions", [])]
    check("unfunded action never marked executed/blocked terminally",
          "executed_after_veto_window" not in states4
          and "blocked_by_risk_model" not in states4, states4)
    # fund the wallet -> deferred action settles on the next tick
    svc2.mine(2)
    t5 = svc2.agent_tick(hours_ahead=0.0)
    bcast5 = [b for b in t5.get("broadcast", []) if b.get("action_id") == aid4]
    check("settles after funding", len(bcast5) == 1 and bool(bcast5[0].get("txid")),
          t5.get("broadcast"))
    bal_after = svc2.balance_shard
    check("post-funding balance deducted by the spend",
          0 < bal_after < 6_592_465_754 and svc2.chain.tip.header.height >= 3,
          f"bal={bal_after} h={svc2.chain.tip.header.height}")

    print("=" * 60)
    if failures:
        print(f"SMOKE FAILED ({len(failures)}): {failures}")
        return 1
    print("SMOKE PASSED: ok=True — full propose→pending→tick→execute→veto→revoke path green")
    return 0


def _all_actions(svc: PrismService):
    """Every known PendingAction incl. terminal ones (runtime keeps dicts)."""
    seen: dict[str, object] = {}
    for attr in ("pending", "completed", "terminal"):
        d = getattr(svc.agent, attr, None)
        if isinstance(d, dict):
            seen.update(d)
    hist = getattr(svc.agent, "actions", None)
    if isinstance(hist, (list, tuple)):
        for pa in hist:
            seen[pa.action_id] = pa
    return list(seen.values())


if __name__ == "__main__":
    raise SystemExit(main())
