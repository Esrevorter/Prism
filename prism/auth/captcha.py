"""Image-grid CAPTCHA engine — prism.auth.captcha (spec §9.2, Amendment A2-2026-10).

Why this exists: the founder mandate replaced the multi-device MPC ceremony
with the leanest possible human-authorization step. Before any irreversible
transaction is signed, the wallet shows a 3×3 grid of simple shapes and asks
"Tap every ★". A bot that automates the UI cannot reliably answer; a person
can, in about three seconds. That is the whole point — ease of use first.

Design constraints (repo rules): stdlib-only, deterministic, no network calls,
no third-party captcha service (privacy-by-default forbids leaking user auth
events to an external provider). The server draws tiles as SVG primitives and
encodes them into a PNG **by hand** (zlib + struct are in the stdlib), so the
frozen binary needs no imaging library.

Security properties, stated honestly (spec §5 honest-UX rule):
  * challenge tokens are HMAC-signed with a per-service secret and bind to
    (challenge_id, intent digest) — a solved token cannot be replayed onto a
    different transaction;
  * TTL default 120 s; single-use ledger (solved ids are consumed);
  * wrong answers do not lock the wallet — they just re-issue the challenge
    (calm UX, §2 principle 4); rate limiting lives at the keystore layer.
This proves "a human clicked", NOT "the owner clicked" — device compromise
beyond the OS is out of threat model (§11.2), same as before.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import struct
import time
import zlib
from dataclasses import dataclass, field
from typing import Optional

CAPTCHA_TTL_S = 120
GRID = 3                       # 3x3 tiles
MAX_ATTEMPTS_PER_CHALLENGE = 5


class CaptchaError(ValueError):
    """User-facing captcha failure (expired / wrong / replayed / unknown)."""


# ---------------------------------------------------------------------------
# Shape vocabulary — each tile renders one primitive; target set is one shape
# ---------------------------------------------------------------------------

SHAPES = ("star", "circle", "square", "triangle", "diamond")


def new_image_grid(seed: Optional[bytes] = None) -> dict:
    """Generate a fresh challenge: 9 tiles, 3–4 of them share the target shape.

    Returns a plain dict so it JSON-round-trips straight through the RPC wire:
      {challenge_id, prompt, target, tiles:[shape,...], png_base64}
    """
    rng = secrets.SystemRandom() if seed is None else _SeededRandom(seed)
    tiles = [rng.choice(SHAPES) for _ in range(GRID * GRID)]
    target = rng.choice(SHAPES)
    # guarantee solvability: force exactly 3..4 target tiles
    want = rng.randint(3, 4)
    idx = list(range(GRID * GRID))
    rng.shuffle(idx)
    for k, i in enumerate(idx):
        tiles[i] = target if k < want else (
            SHAPES[(SHAPES.index(tiles[i]) + 1) % len(SHAPES)]
            if tiles[i] == target else tiles[i])
    return {
        "challenge_id": secrets.token_hex(8),
        "prompt": f"Tap every {'★ star' if target == 'star' else target + ' shape'}",
        "target": target,
        "tiles": tiles,
    }


class _SeededRandom:
    """Deterministic RNG for tests (secrets has no seeding)."""
    def __init__(self, seed: bytes):
        self._s = hashlib.sha256(seed).digest()

    def _next(self) -> int:
        self._s = hashlib.sha256(self._s).digest()
        return int.from_bytes(self._s[:8], "little")

    def choice(self, seq):
        return seq[self._next() % len(seq)]

    def randint(self, a: int, b: int) -> int:
        return a + self._next() % (b - a + 1)

    def shuffle(self, lst):
        for i in range(len(lst) - 1, 0, -1):
            j = self._next() % (i + 1)
            lst[i], lst[j] = lst[j], lst[i]


# ---------------------------------------------------------------------------
# Minimal stdlib PNG encoder (truecolor 8-bit) — tiles drawn as flat glyphs
# ---------------------------------------------------------------------------

_TILE = 96
_PAD = 6


def render_grid_png(tiles: list[str], target: str) -> bytes:
    """Rasterize the 3×3 grid into a PNG without any imaging dependency.

    Shapes are filled polygon masks computed per pixel — crude but perfectly
    adequate for 96 px tiles, and byte-deterministic for tests.
    """
    w = h = GRID * _TILE + (GRID + 1) * _PAD
    bg = (248, 246, 240)
    fg = (44, 44, 52)
    hl = (214, 178, 60)          # target tint never used — honest grids only
    rows = []
    for y in range(h):
        row = bytearray()
        for x in range(w):
            r, g, b = bg
            ti = _tile_at(x, y)
            if ti is not None:
                shape = tiles[ti]
                cx, cy = _tile_center(ti)
                inside, color = _glyph(shape, x - cx, y - cy)
                if inside:
                    r, g, b = color
            row += bytes((r, g, b))
        rows.append(b"\x00" + bytes(row))
    raw = b"".join(rows)
    return _png(w, h, raw)


def _tile_at(x: int, y: int) -> Optional[int]:
    col, rem_x = divmod(x, _TILE + _PAD)
    row, rem_y = divmod(y, _TILE + _PAD)
    if col >= GRID or row >= GRID or rem_x >= _TILE or rem_y >= _TILE:
        return None
    return row * GRID + col


def _tile_center(i: int) -> tuple[int, int]:
    r, c = divmod(i, GRID)
    return (c * (_TILE + _PAD) + _PAD + _TILE // 2,
            r * (_TILE + _PAD) + _PAD + _TILE // 2)


def _glyph(shape: str, dx: int, dy: int) -> tuple[bool, tuple[int, int, int]]:
    """Return (pixel inside glyph, color). Radius ≈ 34 px."""
    R = 34
    ink = (60, 60, 72)
    if shape == "circle":
        return (dx * dx + dy * dy <= R * R, ink)
    if shape == "square":
        return (abs(dx) <= R * 0.85 and abs(dy) <= R * 0.85, ink)
    if shape == "triangle":
        return (dy > -R and dy < R * 0.8 and abs(dx) * 1.15 < (R * 0.8 - dy), ink)
    if shape == "diamond":
        return (abs(dx) + abs(dy) <= R * 1.1, ink)
    if shape == "star":
        # 5-point star via polar test against outer/inner radii
        import math
        ang = math.atan2(dy, dx)
        rad = math.hypot(dx, dy)
        if rad > R * 1.15:
            return (False, ink)
        pts = []
        for k in range(10):
            a = -math.pi / 2 + k * math.pi / 5
            rr = R * 1.1 if k % 2 == 0 else R * 0.45
            pts.append((rr * math.cos(a), rr * math.sin(a)))
        return (_inside_poly(dx, dy, pts), ink)
    return (False, ink)


def _inside_poly(px: int, py: int, pts: list[tuple[float, float]]) -> bool:
    n, inside = len(pts), False
    j = n - 1
    for i in range(n):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if (yi > py) != (yj > py) and px < (xj - xi) * (py - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _png(width: int, height: int, raw_scanlines: bytes) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw_scanlines, 6))
            + chunk(b"IEND", b""))


# ---------------------------------------------------------------------------
# Engine: issue → solve → consume, bound to an intent digest
# ---------------------------------------------------------------------------

@dataclass
class CaptchaToken:
    challenge_id: str
    intent_digest_hex: str
    issued_at: int
    expires_at: int
    mac: str = ""

    def unsigned(self) -> bytes:
        return f"{self.challenge_id}|{self.intent_digest_hex}|" \
               f"{self.issued_at}|{self.expires_at}".encode()


class CaptchaEngine:
    """Per-service instance holding the signing secret + single-use ledger."""

    def __init__(self, secret: Optional[bytes] = None, ttl_s: int = CAPTCHA_TTL_S):
        self.secret = secret or secrets.token_bytes(32)
        self.ttl_s = ttl_s
        self._pending: dict[str, dict] = {}     # challenge_id -> grid spec
        self._consumed: set[str] = set()

    # -- issuing -------------------------------------------------------------
    def issue(self, intent_digest_hex: str) -> dict:
        """Create a challenge bound to this exact transaction preview."""
        grid = new_image_grid()
        cid = grid["challenge_id"]
        self._pending[cid] = {
            "grid": grid,
            "intent_digest_hex": intent_digest_hex,
            "attempts": 0,
        }
        import base64
        png = render_grid_png(grid["tiles"], grid["target"])
        return {
            "challenge_id": cid,
            "prompt": grid["prompt"],
            "png_base64": base64.b64encode(png).decode(),
            "ttl_s": self.ttl_s,
            "human": ("Prove you're human: look at the picture and tap the "
                      "button under each tile that matches. This only unlocks "
                      "signing THIS payment — nothing is sent yet."),
        }

    # -- solving -------------------------------------------------------------
    def solve(self, challenge_id: str, picks: list[int],
              intent_digest_hex: str, now: Optional[int] = None) -> CaptchaToken:
        """Validate taps; on success return a signed single-use token."""
        now = int(time.time()) if now is None else now
        rec = self._pending.get(challenge_id)
        if rec is None:
            raise CaptchaError("challenge expired or unknown — ask for a new picture")
        if rec["intent_digest_hex"] != intent_digest_hex:
            raise CaptchaError("this answer belongs to a different payment")
        if challenge_id in self._consumed:
            raise CaptchaError("challenge already used")
        rec["attempts"] += 1
        if rec["attempts"] > MAX_ATTEMPTS_PER_CHALLENGE:
            del self._pending[challenge_id]
            raise CaptchaError("too many tries — here's a fresh challenge next time")
        grid = rec["grid"]
        want = {i for i, s in enumerate(grid["tiles"]) if s == grid["target"]}
        got = set(picks)
        if got != want:
            raise CaptchaError("not quite — tap every matching shape and try again")
        del self._pending[challenge_id]
        self._consumed.add(challenge_id)
        tok = CaptchaToken(challenge_id=challenge_id,
                           intent_digest_hex=intent_digest_hex,
                           issued_at=now, expires_at=now + self.ttl_s)
        tok.mac = self._mac(tok)
        return tok

    # -- verifying (server-side gate before signing) --------------------------
    def verify(self, tok: CaptchaToken, intent_digest_hex: str,
               now: Optional[int] = None) -> bool:
        now = int(time.time()) if now is None else now
        if tok.intent_digest_hex != intent_digest_hex:
            return False
        if now > tok.expires_at:
            return False
        if tok.challenge_id not in self._consumed:
            return False
        return hmac.compare_digest(tok.mac, self._mac(tok))

    def _mac(self, tok: CaptchaToken) -> str:
        return hmac.new(self.secret, tok.unsigned(), hashlib.sha256).hexdigest()
