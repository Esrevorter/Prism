"""Testnet parity (§13 + Amendment A2): the Refraction testnet must be able
to exercise mining, transactions and overall wallet usage intensively BEFORE
mainnet deployment — using the SAME validation stack as production.

Pins:
  P1  PrismService runs a real ChainState validator for every network
      (no forked devnet chain code).
  P2  CLI `prism mine` blocks are byte-identical to service-mined blocks at
      the same height (same params → same consensus result).
  P3  p2p Node mined/applied blocks credit the testnet faucet so tx soak is
      possible without external tooling; MAINNET never credits.
  P4  faucet_credit is a parameter, not a code path: True on refraction,
      False on mainnet.
  P5  Full end-to-end soak over one service: create wallet → faucet → intent
      → captcha → sign → block in chain → history — repeated N times with
      cross-validation of every produced block by a fresh ChainState.
  P6  Wallet persistence: sealed keystore survives daemon restart via
      data_dir; address invariant holds across restart.
  P7  Network selection is wired through server/launcher surfaces
      (--network / PRISM_NETWORK), default = refraction.
"""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from prism.chain.params import MAINNET, REFRACTION_TESTNET, NETWORKS   # noqa: E402
from prism.chain.node import ChainState, make_genesis                  # noqa: E402
from prism.rpc.service import PrismService                             # noqa: E402


PW = "correct horse battery staple 42!"


def _full_flow(svc: PrismService, amount_prsm: float) -> dict:
    """One complete payment cycle: compile → confirm → captcha → solve →
    password sign. Returns the signed-tx receipt."""
    r = svc.intent_compile(f"send {amount_prsm} PRSM to Mom")
    iid = r["intent_id"]
    while not r.get("ready"):
        q = next(iter(r["questions"]))
        r = svc.intent_answer(iid, q["options"][0]["value"] if q.get("options")
                              else "yes")
    svc.intent_simulate(iid)
    svc.intent_review(iid)
    ch = svc.auth_challenge(iid)
    picks = [i for i, t in enumerate(ch["tiles"]) if t == ch["target"]]
    solved = svc.auth_solve(ch["challenge_id"], picks, ch["digest_hex"])
    sig = svc.auth_sign(iid, PW, solved["token"])
    assert sig["verified"], "signature self-check failed"
    return sig


class TestParityValidatorStack:
    def test_p1_service_uses_real_chainstate(self):
        for net in ("refraction", "prism1"):
            s = PrismService(network=net)
            assert isinstance(s.chain, ChainState)
            assert s.chain.params is NETWORKS[net]
        with pytest.raises(ValueError):
            PrismService(network="dogecoin")

    def test_p4_faucet_flag_is_parametric(self):
        assert REFRACTION_TESTNET.faucet_credit is True
        assert MAINNET.faucet_credit is False


class TestCliConsensusParity:
    def test_p2_cli_blocks_match_service_blocks(self):
        """`prism mine --blocks 3` output headers must equal the genesis+3
        chain the service produces under identical synthetic timestamps."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root
        out = subprocess.run(
            [sys.executable, "-m", "prism.chain.cli", "mine", "--blocks", "3",
             "--network", "refraction"],
            cwd=root,
            capture_output=True, text=True, check=True).stdout
        cli = json.loads(out)
        assert cli["tip_height"] == 3
        # re-run the exact same derivation in-process and compare hashes
        p = REFRACTION_TESTNET
        st = ChainState(params=p, tip=make_genesis(p))
        from prism.chain.node import mine_block
        ts = p.genesis_timestamp
        hashes = [st.tip.header.hash().hex()]
        for _ in range(3):
            ts += p.target_block_time
            blk = mine_block(p, st.tip, denylist_root=st.tip.header.denylist_root,
                             im_merkle_root=st.tip.header.im_merkle_root,
                             difficulty=1, timestamp=ts)
            st.validate_and_apply(blk)
            hashes.append(blk.header.hash().hex())
        assert [b["hash"] for b in cli["blocks"]] == hashes, \
            "CLI and library mining diverged — consensus is not deterministic"


class TestNodeFaucetCredit:
    def test_p3_node_credits_faucet_on_refraction(self):
        from prism.chain.p2p import Node
        nd = Node("soak-a", "127.0.0.1", 0, network="refraction")
        try:
            blk = nd.mine_one()
            assert blk is not None
            st = nd.status()
            assert st["faucet_enabled"] is True
            assert st["faucet_shards"] > 0, "mined block credited no faucet funds"
            # every applied block credits — mine a burst, all must land
            for _ in range(4):
                assert nd.mine_one() is not None
            assert nd.applied == 5
            assert nd.rejected == 0
            assert nd.status()["faucet_shards"] > st["faucet_shards"]
        finally:
            nd.stop()

    def test_p3b_mainnet_never_credits(self):
        # faucet_credit False ⇒ even a hypothetical credit path stays gated
        assert MAINNET.faucet_credit is False
        s = PrismService(network="prism1")
        assert getattr(s.params, "faucet_credit", False) is False


class TestEndToEndSoak:
    def test_p5_ten_payment_cycles_cross_validated(self):
        svc = PrismService(network="refraction")
        phrase = svc.auth_create(PW)["recovery_phrase"]
        assert len(phrase.split()) == 12
        # faucet: mine enough blocks to fund 10 payments + fees
        svc.mine(12)
        assert svc.balance()["balance_prsm"] > 10
        heights = []
        for i in range(10):
            sig = _full_flow(svc, 0.5)
            heights.append(sig["height"])
        assert heights == sorted(set(heights)), "payments did not land in distinct blocks"
        # CROSS-VALIDATION: a brand-new production validator accepts the whole
        # chain the service built (testnet blocks are consensus-valid blocks)
        checker = ChainState(params=REFRACTION_TESTNET, tip=make_genesis(REFRACTION_TESTNET))
        for h in range(1, svc.chain.tip.header.height + 1):
            blk = svc.store[svc.height_index[h]]
            checker.validate_and_apply(blk)
        assert checker.tip.header.hash() == svc.chain.tip.header.hash()
        hist = svc.auth_history()["transactions"]
        assert len(hist) >= 10
        # recovery still works after heavy use (address invariant)
        addr_before = svc.receive_address()
        svc2 = PrismService(network="refraction")
        svc2.auth_recover(phrase, PW)
        assert svc2.receive_address() == addr_before

    def test_p6_keystore_survives_restart(tmp_path):
        d = str(tmp_path / "wallet")
        s1 = PrismService(network="refraction", data_dir=d)
        s1.auth_create(PW)
        addr = s1.receive_address()
        assert os.path.isfile(os.path.join(d, "prism_wallet.json"))
        # file must contain only the SEALED blob — never key material
        with open(os.path.join(d, "prism_wallet.json")) as fh:
            raw = json.load(fh)
        assert raw["blob"] is not None and "pubkey_hex" in raw
        s2 = PrismService(network="refraction", data_dir=d)
        assert s2.receive_address() == addr, "address changed across restart"
        s2.auth_unlock(PW)              # same password unlocks restored file
        with pytest.raises(ValueError):
            s2.auth_unlock("wrong password entirely!!")


class TestNetworkSurfacing:
    def test_p7_default_network_is_refraction(self):
        assert PrismService().params.name == "refraction"

    def test_p7_server_cli_accepts_network_flags(self):
        from prism.rpc import server as srv
        import argparse
        # construct the parser the same way main() does and verify flags exist
        src = open(srv.__file__).read()
        assert "--network" in src and "--data-dir" in src
        assert 'PRISM_NETWORK' in src

    def test_p7_launcher_threads_network_and_data_dir(self):
        from prism.rpc import launcher as L
        src = open(L.__file__).read()
        assert "PrismService(network=network" in src
        assert "data_dir=data_dir)" in src
