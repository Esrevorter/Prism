"""prism.mpc — forgivable self-custody stack (spec §7.3, Decision D4).

Layered like the rest of the monorepo:

  sharing.py   Shamir secret sharing + Feldman VSS over pluggable scalar
               fields (ℓ for Ed25519, n for secp256k1) — algebraic core.
  frost.py     FROST-style threshold EdDSA: multi-dealer keygen ceremony,
               two-round signing, stock-EdDSA-compatible 64-byte outputs.
               THIS IS THE SHIPPED SPEND PATH (Prism signs with Ed25519).
  secp256k1.py Pure-Python affine k1 group (bridge-signer support only).
  gg20.py      GG20-style threshold ECDSA over secp256k1 for cross-chain
               bridge signers. ⚠️ ideal-MtA reference build — see the
               HONESTY BOX in its module docstring; not value-safe.
  recovery.py  Social-recovery state machine: 72-h timelock, stale-share
               cancellation veto (E9), 30-day post-recovery cooldown, and
               proactive resharing that provably preserves the public key.

Trust model (Decision D4): every leg is user-owned — devices or user-
chosen contacts. Prism operates no recovery leg of any kind.

Status: reference implementation, honest-but-curious, NOT constant time.
Test suite: prism/tests/test_mpc.py.
"""
from . import frost, gg20, recovery, secp256k1, sharing
from .frost import (CeremonyResult, NoncePair, PartialSignature,
                    aggregate_nonces, aggregate_signature, generate_nonce,
                    run_keygen, sign_share, sign_threshold, verify_signature)
from .gg20 import (GG20KeySet, GG20Signature, ecdsa_verify, gg20_keygen,
                   gg20_sign, mta_pair)
from .recovery import (ContactRecord, PendingRecovery, Phase, RecoveryPolicy,
                       RecoveryState, create_recovery_state,
                       derive_stale_share)
from .secp256k1 import BASE as SECP256K1_BASE
from .secp256k1 import N as SECP256K1_N
from .secp256k1 import ECPoint
from .sharing import (ED25519_SCALARS, ScalarField, Share,
                      combine_commitments, lagrange_coefficients, make_shares,
                      poly_commitments, random_poly, reconstruct,
                      verify_share)

__all__ = [
    # submodules
    "sharing", "frost", "secp256k1", "gg20", "recovery",
    # sharing
    "ScalarField", "ED25519_SCALARS", "Share", "random_poly", "make_shares",
    "lagrange_coefficients", "reconstruct", "poly_commitments",
    "verify_share", "combine_commitments",
    # frost
    "CeremonyResult", "NoncePair", "PartialSignature", "run_keygen",
    "generate_nonce", "aggregate_nonces", "sign_share",
    "aggregate_signature", "sign_threshold", "verify_signature",
    # secp256k1 / gg20
    "ECPoint", "SECP256K1_BASE", "SECP256K1_N",
    "GG20KeySet", "GG20Signature", "mta_pair", "gg20_keygen", "gg20_sign",
    "ecdsa_verify",
    # recovery
    "Phase", "RecoveryPolicy", "ContactRecord", "PendingRecovery",
    "RecoveryState", "create_recovery_state", "derive_stale_share",
]
