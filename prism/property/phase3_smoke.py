"""Phase 3 acceptance smoke (spec §13): fee predictability, payment channels +
watchtower beta, biometric share-gating. Pure stdlib; run from repo root:

    python -m prism.property.phase3_smoke
"""
import hashlib
import time

from prism.l2 import channels, fees
from prism.mpc import biometric


def check_fees() -> str:
    # §10.1: fixed base fee, whole-multiple lattice, priority cap at 10x.
    assert fees.validate_fee(fees.BASE_FEE_SHARDS) == 10_000
    assert fees.validate_fee(7 * fees.BASE_FEE_SHARDS) == 70_000
    for bad in (5_000, 15_000, 11 * fees.BASE_FEE_SHARDS):
        try:
            fees.validate_fee(bad)
            raise AssertionError(f"off-lattice/over-cap fee {bad} accepted")
        except fees.FeePolicyError:
            pass
    # Deterministic pre-sign quotes for the three UI tiers.
    q = [fees.quote(t) for t in ("base_only", "priority_low", "priority_max")]
    assert q[0].total_shard < q[1].total_shard < q[2].total_shard
    assert q[2].multiple == fees.PRIORITY_FEE_MAX_MULTIPLE
    # Block-packing policy: greedy priority density, deterministic.
    txs = [fees.TxView(tx_id=f"t{i}", size_bytes=300, fee_shards=i * 10_000)
           for i in range(1, 6)]
    packed, rejected = fees.pack_block(txs, block_size_bytes=1_200)   # room for 4
    assert [t.tx_id for t in packed] == ["t5", "t4", "t3", "t2"]      # density order
    assert [t.tx_id for t in rejected] == ["t1"]                      # lowest deferred
    smuggle = fees.TxView(tx_id="evil", size_bytes=100, fee_shards=12 * fees.BASE_FEE_SHARDS)
    packed2, rejected2 = fees.pack_block([smuggle], block_size_bytes=10_000)
    assert packed2 == [] and rejected2 == [smuggle]                   # 12x never packed
    return ("fees: base+10x lattice enforced, deterministic tier quotes, "
            "density-ordered packing + non-standard smuggling refusal OK")


def _tag(secret: bytes, ch: "channels.Channel") -> bytes:
    # mirrors channels.seal_blob's tower-side index derivation exactly
    return hashlib.sha256(
        b"|".join([b"tag", secret, ch.channel_id.encode()])).digest()[:16]


def check_channels() -> str:
    a = channels.KeyPair.generate()
    b = channels.KeyPair.generate()
    ch, open_states = channels.open_channel(1_000_000, a, b)
    latest = channels.channel_pay(ch, a, b, 250_000, from_a=True)
    assert latest.bal_a == 250_000 and latest.bal_b == 750_000   # A paid B 250k
    assert latest.verify_against(a.public, b.public)   # instant, zero-fee in-channel
    # Watchtower with encrypted penalty blobs (E3).
    secret = bytes(range(32))
    wt = channels.Watchtower()
    wt.register(channels.seal_blob(secret, latest))
    tag = _tag(secret, ch)
    assert wt.on_close_seen(tag, latest.version)["action"] == "none"     # honest close
    res = wt.on_close_seen(tag, 1)                                        # stale broadcast
    assert res["action"] == "penalize" and res["with_version"] == latest.version
    cheat = channels.SignedState(ch.channel_id, 1, 500_000, 500_000, b"", b"")
    po = channels.penalty_outcome(latest, cheat)
    assert po["winner"] == "b" and po["credit_b"] == 1_000_000            # full sweep to victim
    rt = channels.unseal_blob(secret, channels.seal_blob(secret, latest))
    assert (rt.bal_a, rt.bal_b) == (latest.bal_a, latest.bal_b)           # confidentiality round-trip
    try:
        channels.unseal_blob(b"X" * 32, channels.seal_blob(secret, latest))
        raise AssertionError("wrong-secret blob opened")
    except channels.ChannelError:
        pass
    return ("channels: instant sub-cent payments, stale-close penalty sweep, "
            "encrypted tower blobs confidential+tamper-evident OK")


def check_biometrics() -> str:
    se = biometric.SimulatedSE(family="ios-secure-enclave",
                               unlock_passcode="demo-passcode")
    sealed = se.wrap_share(index=1, value=12345)
    gs = biometric.GatedShare(se=se, sealed=sealed)
    idx, val = gs.unlock(authenticator=lambda: True)
    assert (idx, val) == (1, 12345)                    # biometric success -> share materializes
    gs.lock()
    try:
        gs.unlock(authenticator=lambda: False, now=time.time() + 10_000)
        raise AssertionError("share opened without biometric success")
    except biometric.BiometricError:
        pass                                           # no biometric -> no share (§9)
    # Lockout after MAX_FAILED_ATTEMPTS, passcode re-unlock path.
    n0 = time.time() + 1_000_000
    for _ in range(biometric.SimulatedSE.MAX_FAILED_ATTEMPTS):
        try:
            se.unwrap_share(sealed, authenticator=lambda: False, now=n0)
        except biometric.BiometricError:
            pass
    try:
        se.unwrap_share(sealed, authenticator=lambda: True, now=n0)
        raise AssertionError("unwrap allowed during lockout")
    except biometric.SELockError:
        pass
    assert se.passcode_reunlock("demo-passcode", now=n0)
    assert se.unwrap_share(sealed, authenticator=lambda: True, now=n0) == (1, 12345)
    # Device binding: a foreign SE instance cannot open the blob.
    foreign = biometric.SimulatedSE(family="android-strongbox")
    try:
        foreign.unwrap_share(sealed, authenticator=lambda: True)
        raise AssertionError("blob opened on a different device")
    except biometric.BiometricError:
        pass
    quote = se.attestation(nonce=b"n" * 16)
    assert quote.verify()
    return ("biometrics: unwrap requires live biometric, lockout+passcode "
            "re-unlock, device-bound sealing, attestation quote verifies OK")


def main() -> int:
    t0 = time.perf_counter()
    results = [check_fees(), check_channels(), check_biometrics()]
    dt = time.perf_counter() - t0
    print(f"PHASE3 SMOKE PASSED ({len(results)} surfaces, {dt:.2f}s)")
    for r in results:
        print("  ✔", r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
