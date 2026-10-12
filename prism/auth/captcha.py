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
      {challenge_id, prompt, target, tiles:[shape,...]}
    (The picture itself is rendered on demand — SVG for the GUI via
    render_grid_svg, PNG fallback via render_grid_png; v0.1.5 vector-first.)
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
# Minimal stdlib PNG encoder (truecolor 8-bit + alpha) — vector-first shapes
#
# v0.1.5 fix (Windows bug report): the old renderer drew tiny dark glyphs on
# a light background with no anti-aliasing ("looks nineties"), and the GUI's
# countdown ring rendered as a big black circle because its CSS was missing.
# The picture is now crisp by construction: high-resolution supersampled
# anti-aliased coverage, rounded pastel tiles, vivid colored glyphs, generous
# padding.  The server still emits ONE self-contained image (base64 PNG);
# the GUI overlays invisible tap targets on it — never a second picture.
# ---------------------------------------------------------------------------

_TILE = 150          # logical tile size (px as displayed)
_PAD = 10            # gap between tiles
_SUP = 2             # supersample factor for free anti-aliasing
_RADIUS = 16         # tile corner radius (logical px)
_INK = (44, 44, 52)  # glyph outline colour (dark slate, AA'd against fill)

# Per-shape fills: calm pastels that read clearly in light AND dark themes.
_SHAPE_FILL = {
    "star":     (255, 193, 7),
    "circle":   (95, 180, 255),
    "square":   (108, 255, 158),
    "triangle": (255, 95, 126),
    "diamond":  (180, 95, 255),
}


def render_grid_png(tiles: list[str], target: str) -> bytes:
    """Rasterize the 3×3 grid into one sharp, anti-aliased PNG.

    No imaging dependency: each pixel's coverage is computed analytically
    (rounded-rect SDF for tiles, exact polygon/circle membership for glyphs)
    at _SUP² samples per output pixel, then averaged — true anti-aliasing
    from pure stdlib math.  Byte-deterministic for seeded inputs (tests).
    """
    import math
    step = 1.0 / _SUP
    w = h = GRID * _TILE + (GRID + 1) * _PAD
    page = (250, 250, 252)       # neutral near-white page behind tiles
    tile_bg = (255, 255, 255)
    out = bytearray()
    for py in range(h):
        out.append(0)            # PNG filter byte per scanline
        for px in range(w):
            ti = _tile_at(px, py)
            if ti is None:       # gutter: page colour, fully opaque
                out += bytes(page)
                continue
            shape = tiles[ti]
            fill = _SHAPE_FILL.get(shape, _INK)
            cx, cy = _tile_center(ti)
            acc_r = acc_g = acc_b = acc_a = 0.0
            for sy in range(_SUP):
                fy = py + (sy + 0.5) * step - cy
                for sx in range(_SUP):
                    fx = px + (sx + 0.5) * step - cx
                    t_in = _in_rounded(fx, fy, _TILE / 2, _RADIUS)
                    if not t_in:
                        continue                     # outside tile: page shows
                    g_in = _glyph(shape, fx, fy)
                    if g_in:
                        # glyph body with a crisp AA'd ink rim
                        rim = _glyph(shape, fx, fy, shrink=3.5)
                        col = _INK if not rim else fill
                        acc_r += col[0]; acc_g += col[1]; acc_b += col[2]
                        acc_a += 1.0
                    else:
                        acc_r += tile_bg[0]; acc_g += tile_bg[1]
                        acc_b += tile_bg[2]; acc_a += 1.0
            n = _SUP * _SUP
            a = acc_a / n
            if a <= 0.0:
                out += bytes(page)
            else:
                r = (acc_r / acc_a) * a + page[0] * (1 - a)
                g = (acc_g / acc_a) * a + page[1] * (1 - a)
                b = (acc_b / acc_a) * a + page[2] * (1 - a)
                out += bytes((min(255, max(0, round(r))),
                              min(255, max(0, round(g))),
                              min(255, max(0, round(b)))))
    return _png(w, h, bytes(out))


def _in_rounded(dx: float, dy: float, half: float, radius: float) -> bool:
    """Point-in-rounded-rect test relative to tile centre."""
    ax, ay = abs(dx), abs(dy)
    if ax > half or ay > half:
        return False
    ox, oy = ax - (half - radius), ay - (half - radius)
    if ox > 0 and oy > 0:
        return ox * ox + oy * oy <= radius * radius
    return True


# ---------------------------------------------------------------------------
# Vector renderer — the primary picture format since v0.1.5
#
# One self-contained SVG: rounded white tiles on a soft page, each glyph a
# filled path in its shape colour with an ink rim.  Scales perfectly on every
# display (the Windows bug was a tiny raster blown up by the webview); no
# per-pixel math, so issuing is ~instant.  The PNG encoder above stays as a
# dependency-free fallback for non-browser consumers/tests.
# ---------------------------------------------------------------------------

def shape_palette() -> dict:
    """Server-authoritative colours, shared verbatim with the GUI overlay."""
    pal = {s: "#%02x%02x%02x" % c for s, c in _SHAPE_FILL.items()}
    pal["ink"] = "#%02x%02x%02x" % _INK
    pal["tile"] = "#ffffff"
    pal["page"] = "#fafafc"
    return pal


def render_grid_svg(tiles: list[str], target: str) -> str:
    """Render the 3×3 grid as one inline-ready SVG document string."""
    import math
    w = h = GRID * _TILE + (GRID + 1) * _PAD
    pal = shape_palette()
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'width="{w}" height="{h}" role="img" aria-label="Puzzle image: nine '
        f'tiles of simple shapes">',
        f'<rect width="{w}" height="{h}" rx="20" fill="{pal["page"]}"/>',
    ]
    for i, shape in enumerate(tiles):
        tx, ty = _tile_origin(i)
        cx, cy = tx + _TILE / 2, ty + _TILE / 2
        fill = pal.get(shape, pal["ink"])
        parts.append(
            f'<rect x="{tx}" y="{ty}" width="{_TILE}" height="{_TILE}" '
            f'rx="{_RADIUS}" fill="{pal["tile"]}"/>')
        d = _glyph_path(shape, cx, cy)
        if d:
            parts.append(
                f'<path d="{d}" fill="{fill}" stroke="{pal["ink"]}" '
                f'stroke-width="4" stroke-linejoin="round"/>')
    parts.append("</svg>")
    return "".join(parts)


def _tile_origin(i: int) -> tuple[int, int]:
    r, c = divmod(i, GRID)
    return (c * (_TILE + _PAD) + _PAD, r * (_TILE + _PAD) + _PAD)


def _glyph_path(shape: str, cx: float, cy: float) -> str:
    """SVG path data for one glyph centred at (cx, cy)."""
    import math
    R = _TILE * 0.30
    def pts2d(pts):
        return " ".join(f"{cx + px:.2f},{cy + py:.2f}" for px, py in pts)
    if shape == "circle":
        return (f"M {cx - R:.2f},{cy} a {R:.2f},{R:.2f} 0 1,0 {2*R:.2f},0 "
                f"a {R:.2f},{R:.2f} 0 1,0 {-2*R:.2f},0 Z")
    if shape == "square":
        s = R * 0.85; k = R * 0.22
        corners = _rounded_rect_points(s, k)
        return "M " + pts2d(corners).replace(" ", " L ", 1) + " Z"
    poly = _poly_points(shape, R)
    if not poly:
        return ""
    return "M " + pts2d(poly).replace(" ", " L ", 1) + " Z"


def _rounded_rect_points(half: float, rad: float, n: int = 6) -> list:
    """Polygonal approximation of a rounded square (for path symmetry)."""
    import math
    pts = []
    for sx in (-1, 1):
        for sy in (-1, 1):
            ccx, ccy = sx * (half - rad), sy * (half - rad)
            a0 = math.atan2(ccy, ccx)          # corner outward direction
            for k in range(n + 1):
                a = a0 - math.pi / 2 + k * math.pi / 2 / n
                pts.append((ccx + rad * math.cos(a), ccy + rad * math.sin(a)))
    pts.sort(key=lambda p: math.atan2(p[1], p[0]))
    return pts


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


def _glyph(shape: str, dx: float, dy: float, shrink: float = 0.0) -> bool:
    """True if (dx, dy) [relative to tile centre] lies inside the glyph.

    `shrink` insets the shape by that many logical px — used to paint an
    anti-aliased ink rim around each coloured glyph.  Radius scales with
    the larger tiles so glyphs occupy ~60% of each tile.
    """
    R = _TILE * 0.30 - shrink
    if R <= 0:
        return False
    if shape == "circle":
        return dx * dx + dy * dy <= R * R
    if shape == "square":
        return _in_rounded(dx, dy, R * 0.85, R * 0.22)
    if shape == "triangle":
        return _inside_poly(dx, dy, _poly_points("triangle", R))
    if shape == "diamond":
        return _inside_poly(dx, dy, _poly_points("diamond", R))
    if shape == "star":
        return _inside_poly(dx, dy, _poly_points("star", R))
    return False


def _poly_points(shape: str, R: float) -> list[tuple[float, float]]:
    """Canonical vertex lists; cached per (shape, radius)."""
    key = (shape, round(R, 3))
    cached = _POLY_CACHE.get(key)
    if cached is not None:
        return cached
    import math
    if shape == "triangle":
        pts = [(R * math.cos(-math.pi / 2 + i * 2 * math.pi / 3),
                R * math.sin(-math.pi / 2 + i * 2 * math.pi / 3))
               for i in range(3)]
    elif shape == "diamond":
        rr = R * 1.15
        pts = [(0, -rr), (rr, 0), (0, rr), (-rr, 0)]
    elif shape == "star":
        pts = []
        for k in range(10):
            a = -math.pi / 2 + k * math.pi / 5
            rr = R * 1.15 if k % 2 == 0 else R * 0.5
            pts.append((rr * math.cos(a), rr * math.sin(a)))
    else:
        pts = []
    _POLY_CACHE[key] = pts
    return pts


_POLY_CACHE: dict[tuple[str, float], list[tuple[float, float]]] = {}


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
        """Create a challenge bound to this exact transaction preview.

        The grid ships as ONE self-contained SVG picture (crisp at any DPI —
        v0.1.5 fix for the blurry 'nineties' PNG on Windows).  The server's
        tile colours are embedded so the GUI's tap overlay can mirror them
        exactly; the answer key (`target`) never leaves this process except
        via the display-only view service.auth_challenge already redacts.
        """
        grid = new_image_grid()
        cid = grid["challenge_id"]
        # Regression pin (v0.1.7 broken-captcha post-mortem): the client can
        # only answer honestly if the wire payload carries what is VISIBLE in
        # the picture — the tile shapes themselves.  The GUI composes its tap
        # overlay from `tiles` (see prism/gui/app.js renderCapSvg); when a
        # release dropped this field, every payment failed with "not quite —
        # tap every matching shape" and users could never sign.  Never remove
        # or rename these display-only fields without updating the GUI and
        # test_captcha_wire_contract below in the same commit.
        self._pending[cid] = {
            "grid": grid,
            "intent_digest_hex": intent_digest_hex,
            "attempts": 0,
        }
        return {
            "challenge_id": cid,
            "prompt": grid["prompt"],
            "svg": render_grid_svg(grid["tiles"], grid["target"]),
            "tiles": list(grid["tiles"]),
            "palette": shape_palette(),
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
