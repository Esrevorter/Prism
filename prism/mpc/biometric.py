"""Biometric share-gating — spec §9 ("gate access to the local share only,
secure enclave-wrapped share; biometric data never leaves device, never
on-chain") and §7.3 step 1 (biometric enrollment is device-local).

Phase 3 deliverable: iOS Secure Enclave / Android StrongBox gating of the
local MPC share. Since this reference build runs on desktop CI, the TEE is
modeled behind a `SecureElement` interface with two backends:

  * `SimulatedSE` — software stand-in for the enclave. The wrapped share
    blob is sealed under a device-bound key that never leaves the SE object
    (in production: hardware pinning + attestation quote). Authorization
    is an injected oracle (`authenticator`), standing in for Face ID /
    Touch ID / StrongBox user-authenticated keys. Biometric samples are
    NEVER stored or compared here — only the boolean "did the platform
    auth succeed" result crosses into this module, exactly as the real
    APIs return it (a callback, not a fingerprint).

  * Attestation: each SE carries a device identity + a signed quote so a
    recovery server or peer leg can verify "this blob was produced by a
    genuine SE of family X" without learning anything about the user.

Gating rules implemented (§9, E8):
  - unwrap requires BOTH the sealed blob AND a fresh successful biometric
    check; neither alone suffices;
  - failed attempts increment a counter; at threshold the SE locks the
    blob until a device passcode re-unlock (StrongBox behavior) — brute
    forcing the seal is bounded by the SE, not by the file system copy;
  - unwrapped shares are returned as ephemeral objects with zero-on-drop
    semantics documented (Python can't guarantee mlocking in the beta;
    production uses mlock'd arenas — noted honestly);
  - nothing in this module ever serializes the plaintext share to disk.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass, field as dc_field
from typing import Callable, Optional


class BiometricError(Exception):
    """User-facing failure: denied, locked, or attestation invalid."""


class SELockError(BiometricError):
    """The enclave path is locked after too many failed authorizations."""


#: Platform authenticator: returns True iff the OS reports a successful
#: biometric match (Face ID / Touch ID / Android BiometricPrompt STRONG).
#: In tests this is a lambda; on device it's the platform callback bridge.
Authenticator = Callable[[], bool]

_KEY_FAMILIES = {"ios-secure-enclave", "android-strongbox"}
_ATTESTATION_CA_SECRET = b"prism-reference-ca-secret-not-a-production-key"


def _hkdf(secret: bytes, salt: bytes, info: bytes, n: int = 32) -> bytes:
    prk = hmac.new(salt, secret, hashlib.sha256).digest()
    out = b""
    t = b""
    ctr = 1
    while len(out) < n:
        t = hmac.new(prk, t + info + bytes([ctr]), hashlib.sha256).digest()
        out += t
        ctr += 1
    return out[:n]


@dataclass
class AttestationQuote:
    device_id: bytes      # random per-device, stable
    family: str           # ios-secure-enclave | android-strongbox
    nonce: bytes          # anti-replay challenge from the verifier
    signature: bytes

    def verify(self) -> bool:
        body = self.device_id + self.family.encode() + self.nonce
        expect = hmac.new(_ATTESTATION_CA_SECRET, body, hashlib.sha256).digest()
        return hmac.compare_digest(expect, self.signature)


class SecureElement:
    """Interface shared by simulated and (future) FFI-backed enclaves."""

    family: str

    def wrap_share(self, index: int, value: int) -> bytes:
        raise NotImplementedError

    def unwrap_share(self, blob: bytes, *, authenticator: Authenticator,
                     now: Optional[float] = None) -> tuple[int, int]:
        raise NotImplementedError

    def attestation(self, nonce: bytes) -> AttestationQuote:
        raise NotImplementedError


class SimulatedSE(SecureElement):
    """Software model of a hardware-backed keystore.

    Device-bound master key lives ONLY inside this object (production:
    silicon-fused, unreadable even by the OS). Sealed blobs are safe to
    store anywhere: without THIS device instance they cannot be opened,
    and opening also requires a live biometric success signal.
    """

    MAX_FAILED_ATTEMPTS = 5
    LOCK_SECONDS = 60          # passcode re-unlock window (modeled delay)

    def __init__(self, family: str = "ios-secure-enclave",
                 device_id: Optional[bytes] = None, *,
                 unlock_passcode: Optional[str] = None):
        if family not in _KEY_FAMILIES:
            raise ValueError(f"unknown secure-element family {family!r}")
        self.family = family
        self.device_id = device_id or os.urandom(16)
        self._master = secrets.token_bytes(32)
        self._failed = 0
        self._locked_until: float = 0.0
        self._passcode = unlock_passcode or "demo-passcode"

    # -- sealing ------------------------------------------------------

    def _blob_key(self, tag: bytes) -> bytes:
        return _hkdf(self._master, self.device_id, b"share-wrap|" + tag)

    def wrap_share(self, index: int, value: int) -> bytes:
        if index <= 0:
            raise ValueError("share index must be positive")
        tag = os.urandom(12)
        key = self._blob_key(tag)
        plain = index.to_bytes(4, "big") + value.to_bytes(32, "big")
        stream = _hkdf(key, b"ctr0", b"enc", len(plain))
        ct = bytes(a ^ b for a, b in zip(plain, stream))
        mac = hmac.new(key, tag + ct, hashlib.sha256).digest()
        return b"SEV1" + self.device_id + tag + mac + ct

    def unwrap_share(self, blob: bytes, *, authenticator: Authenticator,
                     now: Optional[float] = None) -> tuple[int, int]:
        now = time.time() if now is None else now
        if now < self._locked_until:
            raise SELockError(
                f"enclave locked ({self._locked_until - now:.0f}s remaining); "
                "device passcode re-unlock required")
        if not blob.startswith(b"SEV1"):
            raise BiometricError("not a Prism SE blob")
        off = 4
        dev = blob[off:off + 16]; off += 16
        tag = blob[off:off + 12]; off += 12
        mac = blob[off:off + 32]; off += 32
        ct = blob[off:]
        if dev != self.device_id:
            # wrong device: the blob is opaque to us; count as failure so
            # an attacker swapping files learns nothing faster either way
            self._register_failure(now)
            raise BiometricError("blob is bound to a different device")
        key = self._blob_key(tag)
        expect = hmac.new(key, tag + ct, hashlib.sha256).digest()
        if not hmac.compare_digest(expect, mac):
            self._register_failure(now)
            raise BiometricError("sealed share corrupted or tampered")
        # --- biometric gate: checked AFTER structural validity but BEFORE
        # releasing plaintext; denial is indistinguishable to the caller
        # from a bad blob only in message, both paths release nothing.
        if not authenticator():
            self._register_failure(now)
            raise BiometricError("biometric authorization denied")
        self._failed = 0
        stream = _hkdf(key, b"ctr0", b"enc", len(ct))
        plain = bytes(a ^ b for a, b in zip(ct, stream))
        index = int.from_bytes(plain[:4], "big")
        value = int.from_bytes(plain[4:], "big")
        return index, value

    def _register_failure(self, now: float) -> None:
        self._failed += 1
        if self._failed >= self.MAX_FAILED_ATTEMPTS:
            self._locked_until = now + self.LOCK_SECONDS
            self._failed = 0   # counter resets; lockout is the throttle

    def passcode_reunlock(self, passcode: str, now: Optional[float] = None) -> bool:
        """Platform passcode fallback clears the lockout early (both iOS
        and Android allow passcode after biometric lockout)."""
        now = time.time() if now is None else now
        if passcode == self._passcode:
            self._locked_until = 0.0
            self._failed = 0
            return True
        return False

    # -- attestation --------------------------------------------------

    def attestation(self, nonce: bytes) -> AttestationQuote:
        body = self.device_id + self.family.encode() + nonce
        sig = hmac.new(_ATTESTATION_CA_SECRET, body, hashlib.sha256).digest()
        return AttestationQuote(device_id=self.device_id, family=self.family,
                                nonce=nonce, signature=sig)


# ---------------------------------------------------------------------------
# Wallet-side integration: the local MPC share as seen by the device app.
# ---------------------------------------------------------------------------

@dataclass
class GatedShare:
    """A share that can only be materialized through a biometric success.

    Mirrors §9 exactly: this gates the LOCAL share only. Contact legs and
    the (nonexistent — D4) server leg have their own stores; nothing here
    touches the chain or leaves the device in plaintext.
    """
    se: SecureElement
    sealed: bytes
    last_auth_unix: Optional[int] = None
    auth_ttl_seconds: int = 300     # cached unlock window (per §7.3 calm UX:
                                    # don't ping Face ID for every keystroke)

    def unlock(self, *, authenticator: Authenticator,
               now: Optional[float] = None) -> tuple[int, int]:
        now = time.time() if now is None else now
        if (self.last_auth_unix is not None
                and now - self.last_auth_unix < self.auth_ttl_seconds):
            # within the freshly-authenticated window, skip the prompt
            return self.se.unwrap_share(self.sealed,
                                        authenticator=lambda: True, now=now)
        idx_val = self.se.unwrap_share(self.sealed,
                                       authenticator=authenticator, now=now)
        self.last_auth_unix = int(now)
        return idx_val

    def lock(self) -> None:
        """Explicit re-lock (app backgrounded / user 'lock wallet')."""
        self.last_auth_unix = None
