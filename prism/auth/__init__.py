"""prism.auth — lean captcha-gated transaction authorization (spec §9, Amendment A2-2026-10).

Replaces the former MPC multi-device ceremony stack with a deliberately simple
model: the spend key lives on the user's device, sealed behind a password;
every irreversible action is authorized by a human via an image-grid CAPTCHA
challenge that must be solved before the local Ed25519 signature is produced.

Modules:
  captcha.py  Image-grid CAPTCHA engine (stdlib-only, deterministic PNG
              encoder) + HMAC challenge tokens + single-use ledger.
  keystore.py Password-sealed local spend key (scrypt KDF + HMAC-SHA256
              counter-mode envelope), lockout after repeated failures.

Trust story (honest UX, spec §5): the CAPTCHA proves *a human* is present at
the keyboard and blocks malware that cannot see the screen; it is NOT a
cryptographic delegation of signing power. The sealing password is what
protects the key at rest. Losing the password without the recovery phrase
means losing the wallet — stated plainly at creation (§7.1).
"""
from . import captcha, keystore
from .captcha import (CAPTCHA_TTL_S, CaptchaEngine, CaptchaError,
                      CaptchaToken, new_image_grid)
from .keystore import KeyStore, WalletKey

__all__ = [
    "captcha", "keystore",
    "CaptchaEngine", "CaptchaError", "CaptchaToken", "CAPTCHA_TTL_S",
    "new_image_grid",
    "KeyStore", "WalletKey",
]
