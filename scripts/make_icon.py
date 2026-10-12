#!/usr/bin/env python3
"""Generate the Prism brand icon: golden serif 'P' on a dark rounded tile.

Single source of truth for ALL platform icons (Windows .ico, macOS .icns,
Linux .png set, MS Store tiles).  Re-run after editing BRAND_SVG:

    python scripts/make_icon.py            # writes every icon + verifies
    python scripts/make_icon.py --check    # regenerate to temp, diff vs repo

Design notes (founder brief, 2026-10-12): hi-res golden letter P, identical
across installers/taskbars on Windows, macOS and Linux.  Rendered from an
inline SVG so the geometry is resolution-independent; rasterised at 1024 px
and downscaled with LANCZOS so small sizes stay crisp.
"""
from __future__ import annotations

import argparse
import io
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw
Image.INFO = {}  # deterministic PNGs: no date/text chunks

REPO = Path(__file__).resolve().parent.parent
ICONS = REPO / "packaging/tauri/prism-gui/src-tauri/icons"

BRAND_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 512 512">
  <defs>
    <linearGradient id="gold" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#FFE79A"/>
      <stop offset=".45" stop-color="#F3C24E"/>
      <stop offset="1" stop-color="#B8862B"/>
    </linearGradient>
    <linearGradient id="tile" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#1B2230"/>
      <stop offset="1" stop-color="#0D1117"/>
    </linearGradient>
  </defs>
  <rect width="512" height="512" rx="104" fill="url(#tile)"/>
  <rect x="10" y="10" width="492" height="492" rx="96" fill="none" stroke="#F3C24E" stroke-opacity=".28" stroke-width="4"/>
  <g transform="translate(256 268) scale(1.18) translate(-256 -256)">
    <text x="256" y="256" text-anchor="middle" dominant-baseline="central" dy="14"
          font-family="'DejaVu Serif','Liberation Serif',Georgia,serif" font-weight="bold"
          font-size="392" fill="url(#gold)" stroke="#7A5A16" stroke-width="3"
          paint-order="stroke fill">P</text>
  </g>
</svg>"""


def _rsvg_available() -> bool:
    return subprocess.run(["which", "rsvg-convert"], capture_output=True).returncode == 0


def render_master(size: int = 1024) -> Image.Image:
    """Rasterise BRAND_SVG at `size` px.  Prefers rsvg-convert (exact), falls
    back to Pillow's built-in SVG reader (approximate) so CI without librsvg
    still produces the brand rather than failing the build silently."""
    if _rsvg_available():
        with tempfile.NamedTemporaryFile(suffix=".svg", mode="w", delete=False) as tf:
            tf.write(BRAND_SVG)
            svg_path = tf.name
        png_path = svg_path + ".png"
        try:
            # NB: this rsvg-convert build silently emits 0 bytes for `-o -`,
            # so write to a real file and read it back (verified 2026-10-12).
            subprocess.run(
                ["rsvg-convert", "-w", str(size), "-h", str(size), "--format=png",
                 "-o", png_path, svg_path],
                capture_output=True, check=True,
            )
            img = Image.open(png_path).convert("RGBA")
            if img.size != (size, size) or not img.getbbox():
                raise RuntimeError("rsvg produced an empty raster")
            return img
        finally:
            Path(svg_path).unlink(missing_ok=True)
            Path(png_path).unlink(missing_ok=True)
    print("WARN: rsvg-convert not found; using Pillow SVG fallback (verify visually)", file=sys.stderr)
    img = Image.open(io.BytesIO(BRAND_SVG.encode())).convert("RGBA")
    return img.resize((size, size), Image.LANCZOS)


def build_all(master: Image.Image, out_root: Path) -> None:
    ic = out_root
    ic.mkdir(parents=True, exist_ok=True)
    # Full-colour master sources
    master.save(ic / "icon.png")                       # 1024 hi-res PNG (Linux hicolor, docs)
    master.resize((256, 256), Image.LANCZOS).convert("RGB").save(REPO / "packaging/tauri/prism-gui/icon-source.png" if out_root == ICONS else ic / "icon-source.png")
    # Windows multi-resolution ICO (taskbar/alt-tab/explorer)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [master.resize((s, s), Image.LANCZOS) for s in sizes]
    frames[-1].save(ic / "icon.ico", sizes=[(s, s) for s in sizes], append_images=frames[:-1])
    # Tauri PNG set
    for s, name in [(32, "32x32.png"), (128, "128x128.png"), (256, "128x128@2x.png")]:
        master.resize((s, s), Image.LANCZOS).save(ic / name)
    # Windows Store / Start-menu tiles (square, no rounding needed)
    for s, name in [(30, "Square30x30Logo.png"), (44, "Square44x44Logo.png"), (71, "Square71x71Logo.png"),
                    (89, "Square89x89Logo.png"), (107, "Square107x107Logo.png"), (142, "Square142x142Logo.png"),
                    (150, "Square150x150Logo.png"), (284, "Square284x284Logo.png"), (310, "Square310x310Logo.png")]:
        master.resize((s, s), Image.LANCZOS).save(ic / name)
    store = master.resize((50, 50), Image.LANCZOS)
    store.save(ic / "StoreLogo.png")
    # macOS .icns (needs iconutil on macOS; fall back to a renamed bundle member on Linux)
    if sys.platform == "darwin":
        with tempfile.TemporaryDirectory() as td:
            iconset = Path(td) / "prism.iconset"
            iconset.mkdir()
            for s in (16, 32, 64, 128, 256, 512):
                master.resize((s, s), Image.LANCZOS).save(iconset / f"icon_{s}x{s}.png")
                master.resize((s * 2, s * 2), Image.LANCZOS).save(iconset / f"icon_{s}x{s}@2x.png")
            subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(ic / "icon.icns")], check=True)
    else:
        print("NOTE: iconutil unavailable off-macOS; refreshing icon.icns from 1024px PNG container")
        (ic / "icon.icns").write_bytes(_png_as_icns(master))


def _png_as_icns(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png = buf.getvalue()
    return b"icns" + len(png).to_bytes(4, "big") + b"icp4" + len(png).to_bytes(4, "big") + png


def verify(out_root: Path) -> list[str]:
    problems: list[str] = []
    for rel in ["icon.png", "icon.ico"]:
        p = out_root / rel
        if not p.exists():
            problems.append(f"{rel}: missing"); continue
        im = Image.open(p).convert("RGBA")
        import numpy as np
        arr = np.array(im)[..., :3]
        bright = float((arr.max(axis=2) > 120).mean())
        if bright < 0.05:
            problems.append(f"{rel}: {bright:.1%} bright pixels — looks like a blank/black tile")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="regenerate into temp dir and diff against repo icons")
    args = ap.parse_args()
    master = render_master()
    if args.check:
        with tempfile.TemporaryDirectory() as td:
            build_all(master, Path(td))
            # README.txt is a hand-written note that lives in the icons dir; not generator output
            rc = subprocess.run(["diff", "-rq", "--exclude=README.txt", td, str(ICONS)]).returncode
            print("icons match generator" if rc == 0 else "icons OUT OF SYNC with scripts/make_icon.py")
            return rc
    build_all(master, ICONS)
    problems = verify(ICONS)
    if problems:
        print("VERIFY FAILED:\n  " + "\n  ".join(problems)); return 1
    print(f"OK: brand icons regenerated at {ICONS} (golden P, verified non-blank)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
