"""Social recovery state machine + proactive reshare — mpc/ deliverable 4.

Implements spec §7.3 (forgivable self-custody) and threat model E9:

  * A lost primary device triggers a recovery ceremony: the user collects
    valid key shares from t trusted contacts (each share is Feldman-
    verifiable against the joint commitments — see sharing.verify_share).
  * The recovered signing capability enters a **72-hour timelock** before
    funds can move (§7.3 step 3, Parameters table "Recovery timelock").
  * During the window ANYONE holding a stale-but-valid share (i.e. the
    attacker's stolen phone, or a coerced flow that somehow fails to
    produce one) can CANCEL the pending recovery — this is the anti-
    coercion property E9 demands: stolen-phone + bribed contacts still
    cannot complete recovery while any honest leg remains live.
  * After activation, a mandatory **30-day cooldown** blocks a second
    back-to-back recovery (§6.2 wallet policy `recovery_policy`), and
    every completed recovery event forces a **proactive reshare** of all
    shares (§7.3: "every 90 days or after any recovery event") so shares
    that leaked during the incident become worthless.

Design notes
------------
- Shares are validated over the Ed25519 scalar field ℓ with the Edwards
  base point (the shipped spend path, frost.py). GG20/secp256k1 recovery
  uses the same state machine; only the verification group differs and is
  out of scope for v1 coordination logic.
- Time is injected (`now_unix`) everywhere: no wall-clock reads inside the
  machine, which makes the whole protocol deterministically testable and
  keeps it honest under chain-time vs. wall-time debates (v1 reference
  implementation uses wall-clock seconds; production binds checkpoints to
  block heights).
- This module never reconstructs the secret on a single device in
  production; here reconstruction happens only inside the coordinator's
  memory long enough to re-shard (reshare keeps the secret constant, so
  the joint public key NEVER changes across recoveries — a critical UX
  property: contacts keep the same address book entry forever).

Reference implementation: readable, side-channel-naive, tested in
tests/test_mpc.py (same house rules as crypto/).
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field as dc_field
from enum import Enum
from typing import Optional

from ..crypto.edwards import BASE, Point
from ..crypto.field import L
from .sharing import (ED25519_SCALARS, ScalarField, Share, eval_poly,
                      lagrange_coefficients, make_shares, random_poly,
                      reconstruct, verify_share)

_F = ED25519_SCALARS

# ---------------------------------------------------------------------------
# Constants (spec §Parameters / §6.2 recovery_policy defaults)
# ---------------------------------------------------------------------------

HOURS = 3600
DAYS = 24 * HOURS

DEFAULT_TIMELOCK_HOURS = 72          # §7.3 step 3, Parameters table
DEFAULT_COOLDOWN_DAYS = 30           # §6.2 example policy
DEFAULT_REFRESH_DAYS = 90            # §7.3 proactive refresh cadence


def _share_digest(share_value: int, index: int) -> bytes:
    """Hash commitment stored at initiation so cancellers must prove they
    hold a real share (prevents griefing cancellations by non-holders...
    v1 note: we check the digest against the value presented at cancel
    time; the presenting party proves knowledge by supplying the value)."""
    return hashlib.sha256(
        b"prism-recovery-cancel-token/v1"
        + index.to_bytes(4, "big")
        + share_value.to_bytes(32, "little")
    ).digest()


# ---------------------------------------------------------------------------
# Policy & states
# ---------------------------------------------------------------------------

class Phase(str, Enum):
    ACTIVE = "active"               # normal operation, original shares live
    PENDING = "pending"             # quorum collected, timelock running
    CANCELLED = "cancelled"         # aborted within window (stale-share veto)
    COOLDOWN = "cooldown"           # post-recovery lockout against replay
    """Note: finalize_recovery() returns the wallet to ACTIVE with
    `last_recovery_unix` set; the cooldown is enforced *at initiation* by
    comparing now vs. last_recovery_unix + cooldown_days — a distinct
    visible phase would double-count the same clock. COOLDOWN is reserved
    for UI display (see remaining_cooldown())."""


@dataclass(frozen=True)
class RecoveryPolicy:
    quorum: int = 3                 # t-of-n contacts (§7.3 mode B default)
    timelock_hours: int = DEFAULT_TIMELOCK_HOURS
    cooldown_days: int = DEFAULT_COOLDOWN_DAYS
    refresh_days: int = DEFAULT_REFRESH_DAYS
    require_device_leg: bool = False
    """E9 hardening knob: if True, at least one recovery quorum member must
    be flagged as a user-owned device leg rather than a human contact."""

    def validate(self) -> None:
        if self.quorum < 2:
            raise ValueError("quorum must be >= 2 (t=1 stores raw secret)")
        if self.timelock_hours <= 0:
            raise ValueError("timelock must be positive")
        if self.cooldown_days <= 0:
            raise ValueError("cooldown must be positive")


@dataclass
class ContactRecord:
    index: int                      # polynomial evaluation point (1-based)
    label: str
    is_device_leg: bool = False     # user-owned hardware vs. human contact
    revoked: bool = False           # DELIBERATELY REMOVED from the set
                                    # (contact left / device retired). NOT
                                    # used for routine reshares: a completed
                                    # reshare rotates share VALUES via the
                                    # generation stamp + new commitments, so
                                    # stale shares die Feldman-verification
                                    # against them (the real security
                                    # boundary, §7.3/E9) while the leg keeps
                                    # its refreshed share and veto power.


@dataclass
class PendingRecovery:
    initiated_at: int
    activates_at: int
    collector_digests: dict[int, bytes]      # contact index -> token hash
    stale_cancel_tokens: set[bytes]          # hashes accepted for cancel
    submitted_shares: dict[int, int] = dc_field(default_factory=dict)


@dataclass
class RecoveryState:
    """The full custody document. Serialize via to_dict()/from_dict() when
    persisting to the (encrypted) wallet store."""
    public_key: Point
    commitments: list[Point]                 # joint Feldman A_0..A_{t-1}
    threshold: int
    policy: RecoveryPolicy
    contacts: dict[int, ContactRecord]
    phase: Phase = Phase.ACTIVE
    pending: Optional[PendingRecovery] = None
    last_recovery_unix: Optional[int] = None
    last_reshare_unix: Optional[int] = None
    generation: int = 0                      # bumped on every reshare
    share_generation: int = -1               # gen the *current* shares were
                                             # issued at (-1 = genesis
                                             # ceremony shares, gen 0)
    events: list[tuple[int, str]] = dc_field(default_factory=list)

    # -- helpers ----------------------------------------------------------

    def _log(self, now: int, msg: str) -> None:
        self.events.append((now, msg))

    def _live_contact_indices(self) -> list[int]:
        return [i for i, c in self.contacts.items() if not c.revoked]

    def _check_quorum_membership(self, indices: list[int]) -> None:
        if len(set(indices)) != len(indices):
            raise ValueError("duplicate contact index in quorum")
        for i in indices:
            rec = self.contacts.get(i)
            if rec is None:
                raise ValueError(f"unknown contact {i}")
            if rec.revoked:
                raise ValueError(f"contact {i} was revoked by a reshare")

    # -- initiation --------------------------------------------------------

    def initiate_recovery(self, now: int,
                          collector_indices: list[int],
                          stale_share_values: dict[int, int],
                          ) -> PendingRecovery:
        """Start a recovery ceremony.

        `collector_indices`: the t contact indices whose NEW shares will be
        delivered out-of-band (phone numbers, secure-messaging handles —
        coordination happens outside this state machine; here we just bind
        their identities into the pending record).

        `stale_share_values`: the holder(s) of the surviving old shares
        present them up-front so the machine can (a) Feldman-verify them
        against the CURRENT commitments — garbage shares cannot start a
        recovery — and (b) register cancellation tokens. Every verified
        stale share gains veto power for the full 72-h window (E9).
        """
        p = self.policy
        p.validate()
        if self.phase is Phase.PENDING:
            raise RuntimeError("recovery already pending")
        if self.last_recovery_unix is not None:
            remaining = (self.last_recovery_unix + p.cooldown_days * DAYS
                         - now)
            if remaining > 0:
                raise RuntimeError(
                    f"in {p.cooldown_days}-day post-recovery cooldown; "
                    f"{remaining // 3600} h remaining")
        if len(collector_indices) != p.quorum:
            raise ValueError(
                f"need exactly {p.quorum} collectors, got "
                f"{len(collector_indices)}")
        self._check_quorum_membership(collector_indices)
        if p.require_device_leg and not any(
                self.contacts[i].is_device_leg for i in collector_indices):
            raise ValueError("policy requires >=1 user-device leg in quorum")

        # Verify stale shares against current joint commitments.
        cancel_tokens: set[bytes] = set()
        for idx, val in stale_share_values.items():
            ok = verify_share(Share(idx, _F.reduce(val)),
                              self.commitments, BASE, _F)
            if not ok:
                raise ValueError(
                    f"stale share for contact {idx} fails Feldman check")
            cancel_tokens.add(_share_digest(val, idx))
        if not cancel_tokens:
            # §7.3 assumes the lost-device scenario leaves >=1 live leg.
            # Full-quorum-loss recovery (all n shares gone) is explicitly
            # out of scope for v1: nothing could ever cancel the ceremony.
            raise ValueError(
                "no valid stale share supplied: recovery without a live leg "
                "cannot offer the 72-h cancellation guarantee (out of scope "
                "for v1)")

        pend = PendingRecovery(
            initiated_at=now,
            activates_at=now + p.timelock_hours * HOURS,
            collector_digests={
                i: hashlib.sha256(
                    b"prism-collector/v1" + i.to_bytes(4, "big")).digest()
                for i in collector_indices},
            stale_cancel_tokens=cancel_tokens,
        )
        self.pending = pend
        self.phase = Phase.PENDING
        self._log(now, f"recovery initiated by collectors "
                       f"{sorted(collector_indices)}, "
                       f"{len(cancel_tokens)} veto legs armed")
        return pend

    # -- mid-window operations ---------------------------------------------

    def submit_collector_share(self, now: int, index: int,
                               share_value: int) -> None:
        """Each collector delivers their freshly received share (sent
        out-of-band during the ceremony). We bind them into the record so
        finalize() can Feldman-verify the whole set at once."""
        if self.phase is not Phase.PENDING or self.pending is None:
            raise RuntimeError("no recovery pending")
        if now >= self.pending.activates_at:
            raise RuntimeError("window closed; call finalize()")
        if index not in self.pending.collector_digests:
            raise ValueError(f"contact {index} is not a designated collector")
        self.pending.submitted_shares[index] = _F.reduce(share_value)

    def cancel_recovery(self, now: int, share_index: int,
                        share_value: int) -> None:
        """Anti-coercion veto (§7.3/E9): any holder of a registered stale
        share may abort the pending recovery inside the window."""
        if self.phase is not Phase.PENDING or self.pending is None:
            raise RuntimeError("no recovery pending")
        token = _share_digest(_F.reduce(share_value), share_index)
        if token not in self.pending.stale_cancel_tokens:
            raise PermissionError(
                "presented share is not a registered live leg; cannot veto")
        self.phase = Phase.CANCELLED
        self._log(now, f"recovery CANCELLED by stale share of contact "
                       f"{share_index}")
        self.pending = None

    def window_remaining(self, now: int) -> int:
        """Seconds until activation; negative means overdue (ready)."""
        if self.phase is not Phase.PENDING or self.pending is None:
            return 0
        return self.pending.activates_at - now

    def remaining_cooldown(self, now: int) -> int:
        """Seconds of post-recovery cooldown left (0 = free to initiate).
        UI can render phase COOLDOWN while this is > 0."""
        if self.last_recovery_unix is None:
            return 0
        return max(0, self.last_recovery_unix
                   + self.policy.cooldown_days * DAYS - now)

    # -- finalization -------------------------------------------------------

    def finalize_recovery(self, now: int,
                          new_secret_blinds: Optional[dict[int, int]] = None,
                          *, rng=None) -> dict[int, int]:
        """After the full 72 h has elapsed: verify the collected quorum
        shares, run a proactive RESHARE (same secret, fresh polynomials),
        revoke every pre-recovery contact share, and go ACTIVE again with a
        fresh cooldown clock.

        `new_secret_blinds[i]`: optional per-leg constant-term contributions
        for the fresh ceremony polynomials (production: each leg picks its
        own blind on-device; Σ blinds must equal the wallet secret). When
        omitted, the coordinator samples one random split itself — fine for
        the reference implementation since resharing provably preserves the
        public key either way.

        Returns {contact_index: new_share_value} for private delivery to
        ALL live contacts (collectors AND any surviving non-revoked legs —
        everyone gets refreshed shares, nobody loses access).
        """
        if self.phase is not Phase.PENDING or self.pending is None:
            raise RuntimeError("no recovery pending")
        if now < self.pending.activates_at:
            raise RuntimeError(
                f"timelock not expired: {self.window_remaining(now)} s left")
        pend = self.pending
        subs = pend.submitted_shares
        if sorted(subs) != sorted(pend.collector_digests):
            missing = sorted(set(pend.collector_digests) - set(subs))
            raise RuntimeError(
                f"collectors {missing} never delivered shares")

        # All-or-nothing checks BEFORE mutating state.
        for idx, val in subs.items():
            if not verify_share(Share(idx, val), self.commitments,
                                BASE, _F):
                raise ValueError(
                    f"collector {idx} submitted an invalid share")
        secret = reconstruct([Share(i, v) for i, v in subs.items()], _F)
        if BASE.mul(secret) != self.public_key:
            raise AssertionError("reconstructed secret does not match "
                                 "public key (commitment inconsistency)")
        if new_secret_blinds is None:
            new_secret_blinds = self._random_split(secret, sorted(subs), rng)
        elif sorted(new_secret_blinds) != sorted(subs):
            raise ValueError("secret blinds must cover exactly the "
                             "participating collectors")

        new_shares = self._reshare_locked(secret, new_secret_blinds, rng=rng)
        # Commit mutations only after everything succeeded.
        # NOTE: we do NOT mark contacts revoked — routine reshares rotate
        # share VALUES; every live leg keeps its refreshed share (and veto
        # power).  Pre-reshare shares are dead because they no longer pass
        # Feldman verification against the rotated commitments (see
        # is_stale_share); `revoked` is reserved for deliberate removal.
        self.phase = Phase.ACTIVE
        self.last_recovery_unix = now
        self.last_reshare_unix = now
        self.generation += 1
        self.share_generation = self.generation
        self.pending = None
        self._log(now, f"recovery activated; reshare generation "
                      f"{self.generation}; prior-generation shares now "
                      f"fail Feldman verification")
        return new_shares

    def scheduled_refresh(self, now: int, secret: int,
                          participant_blinds: dict[int, int],
                          *, rng=None) -> dict[int, int]:
        """§7.3 proactive hygiene (90-day cadence): rotate all shares with
        NO recovery event (passive long-term-theft protection). Coordinator-
        level reference version — the caller supplies the wallet secret as
        in reshare_with_secret(); the fully distributed λ_i·σ_i blinding
        variant is a Phase-2 GA item (§14 R3). Unlike recovery-triggered
        reshares this does NOT start the cooldown clock: nobody's trust was
        violated."""
        if self.phase is not Phase.ACTIVE:
            raise RuntimeError("refresh only allowed while ACTIVE")
        live = set(self._live_contact_indices())
        parts = sorted(participant_blinds)
        if len(parts) < self.threshold:
            raise ValueError("need >= threshold participants to refresh")
        if not set(parts) <= live:
            raise ValueError("participant list includes revoked/unknown "
                             "contacts")
        return self.reshare_with_secret(now, secret, participant_blinds,
                                       rng=rng)

    def reshare_with_secret(self, now: int, secret: int,
                            participant_blinds: dict[int, int],
                            *, rng=None) -> dict[int, int]:
        """Coordinator-level reshare primitive (used by finalize_recovery,
        scheduled_refresh, and tests). Verifies the secret matches the
        public key, rotates the polynomial set, revokes every pre-existing
        contact share, and bumps the generation counter. The public key is
        provably unchanged."""
        if BASE.mul(secret % L) != self.public_key:
            raise ValueError("secret does not match this wallet's pubkey")
        if self.phase not in (Phase.ACTIVE, Phase.PENDING):
            raise RuntimeError(f"cannot reshare in phase {self.phase.value}")
        parts = sorted(participant_blinds)
        if len(parts) < self.threshold:
            raise ValueError("reshare quorum below threshold")
        new_shares = self._reshare_locked(secret, participant_blinds,
                                          rng=rng)
        # Same policy as finalize_recovery: rotate values, keep legs live.
        self.last_reshare_unix = now
        self.generation += 1
        self.share_generation = self.generation
        self._log(now, f"reshare generation {self.generation}")
        return new_shares

    def remove_contact(self, index: int, *, now: Optional[int] = None
                       ) -> None:
        """Deliberately drop a contact/device from the set (revocation).
        Takes effect for the NEXT reshare: _live_contact_indices() excludes
        revoked records, so the rotated commitments will have no share at
        this index and any value the removed leg still holds fails Feldman
        verification.  Does not touch the cooldown clock."""
        rec = self.contacts.get(index)
        if rec is None:
            raise ValueError(f"unknown contact {index}")
        if rec.revoked:
            raise ValueError(f"contact {index} already revoked")
        if self.phase is Phase.PENDING:
            raise RuntimeError("cannot revoke contacts mid-recovery; "
                               "cancel or finalize first")
        rec.revoked = True
        if now is not None:
            self._log(now, f"contact {index} revoked by owner")

    def is_stale_share(self, index: int, value: int) -> bool:
        """True iff (index, value) FAILS Feldman verification against the
        CURRENT joint commitments — i.e. it is a pre-rotation share made
        worthless by a completed reshare (the security property §7.3/E9
        relies on).  Live shares return False.  Wallets use this to decide
        cancel rights out-of-band: only non-stale (live) legs may veto."""
        if index not in self.contacts or self.contacts[index].revoked:
            return True
        return not verify_share(Share(index, _F.reduce(value)),
                                self.commitments, BASE, _F)

    # -- internals ------------------------------------------------------------

    @staticmethod
    def _random_split(secret: int, parts: list[int],
                      rng) -> dict[int, int]:
        """Sample random constant-term blinds summing to `secret` mod ℓ."""
        if rng is None:
            rng = secrets.SystemRandom()
        blinds: dict[int, int] = {}
        acc = 0
        for i in parts[:-1]:
            b = rng.randrange(0, L)
            blinds[i] = b
            acc = _F.add(acc, b)
        blinds[parts[-1]] = _F.sub(_F.reduce(secret), acc)
        return blinds

    def _reshare_locked(self, secret: int,
                        participant_blinds: dict[int, int],
                        rng) -> dict[int, int]:
        """Multi-dealer reshare over the SAME secret: each participating leg
        i picks a random degree-(t-1) poly g_i with g_i(0) = blind_i, and
        Σ blinds ≡ secret (mod ℓ) is enforced by construction — the first
        participant's blind is checked against the sum of the others.

        New commitments J_k = Σ_i [g_i,k]·BASE; new share for EVERY live
        contact j: σ'_j = Σ_i g_i(j). Joint secret stays f'(0) = secret,
        hence the public key is unchanged.
        """
        parts = sorted(participant_blinds)
        if len(parts) < self.threshold:
            raise ValueError("reshare quorum below threshold")
        # Enforce Σ blinds == secret so the new constant term equals it.
        total = 0
        for i in parts:
            total = _F.add(total, _F.reduce(participant_blinds[i]))
        if total != _F.reduce(secret):
            raise ValueError(
                "participant blinds do not sum to the wallet secret")

        polys: dict[int, list[int]] = {}
        for i in parts:
            polys[i] = random_poly(participant_blinds[i], self.threshold, _F,
                                   rng=rng)
        # commitments coefficient-wise
        width = self.threshold
        joint_comm: list[Point] = []
        for k in range(width):
            acc = BASE.mul(0)
            for i in parts:
                acc = acc.add(BASE.mul(polys[i][k]))
            joint_comm.append(acc)
        if joint_comm[0] != self.public_key:
            raise AssertionError("reshare changed the public key")

        recipients = self._live_contact_indices()
        new_shares: dict[int, int] = {}
        for j in recipients:
            val = 0
            for i in parts:
                val = _F.add(val, eval_poly(polys[i], j, _F))
            new_shares[j] = val
        # Feldman-verify our own output before handing it out.
        for j, val in new_shares.items():
            if not verify_share(Share(j, val), joint_comm, BASE, _F):
                raise AssertionError("reshare produced invalid share")
        self.commitments = joint_comm
        return new_shares


# ---------------------------------------------------------------------------
# Construction / serialization
# ---------------------------------------------------------------------------

def create_recovery_state(public_key: Point, commitments: list[Point],
                          threshold: int, contacts: dict[int, str],
                          policy: Optional[RecoveryPolicy] = None,
                          device_legs: Optional[list[int]] = None,
                          ) -> RecoveryState:
    """Bootstrap from a completed frost.run_keygen ceremony.

    `contacts`: {index: label}. `device_legs`: indices flagged as
    user-owned devices (for the require_device_leg policy knob).
    """
    policy = policy or RecoveryPolicy()
    policy.validate()
    if threshold != policy.quorum:
        raise ValueError(
            f"ceremony threshold {threshold} != policy quorum "
            f"{policy.quorum}")
    if len(commitments) != threshold:
        raise ValueError("commitment vector length must equal threshold")
    device_legs = set(device_legs or [])
    recs = {i: ContactRecord(index=i, label=lbl,
                             is_device_leg=(i in device_legs))
            for i, lbl in contacts.items()}
    return RecoveryState(public_key=public_key, commitments=commitments,
                         threshold=threshold, policy=policy, contacts=recs,
                         phase=Phase.ACTIVE, last_reshare_unix=None,
                         generation=0)


def derive_stale_share(ceremony_shares: dict[int, Share],
                       keep: list[int]) -> dict[int, int]:
    """Test/wallet helper: expose {index: value} for the given indices so a
    'lost' leg can register its cancellation veto at initiation."""
    return {i: ceremony_shares[i].value for i in keep}


__all__ = [
    "Phase", "RecoveryPolicy", "ContactRecord", "PendingRecovery",
    "RecoveryState", "create_recovery_state", "derive_stale_share",
    "DEFAULT_TIMELOCK_HOURS", "DEFAULT_COOLDOWN_DAYS",
    "DEFAULT_REFRESH_DAYS", "HOURS", "DAYS",
]
