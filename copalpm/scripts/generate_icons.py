"""Regenerate placeholder ICO assets for the Copal shell integration.

Maintainer-only — run when you want to refresh the placeholder icons that
ship in the wheel. Writes to copalpm/src/copalpm/assets/.

Requirements: Pillow (pip install pillow).

Usage:
    uv run --directory copalpm python scripts/generate_icons.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ASSETS = Path(__file__).resolve().parent.parent / "src" / "copalpm" / "assets"

# Copal brand-ish palette (placeholder — swap when real branding lands).
BG = (24, 28, 36)        # near-black
FG = (235, 235, 235)     # off-white
ACCENT_START = (94, 200, 134)   # green
ACCENT_STOP = (220, 96, 96)     # red
ACCENT_NEW = (110, 168, 254)    # blue
ACCENT_BRAND = (220, 180, 80)   # amber for the umbrella mark


def _font(size: int) -> ImageFont.ImageFont:
    """Best-effort sans-serif font; falls back to PIL default if none found."""
    for name in ("Arial.ttf", "DejaVuSans.ttf", "segoeui.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_glyph(size: int, glyph: str, accent: tuple[int, int, int]) -> Image.Image:
    """Draw a single placeholder icon: dark rounded square with a glyph."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    pad = max(2, size // 16)
    radius = max(4, size // 6)
    d.rounded_rectangle(
        (pad, pad, size - pad - 1, size - pad - 1),
        radius=radius,
        fill=BG,
        outline=accent,
        width=max(1, size // 32),
    )

    font_size = int(size * 0.62)
    font = _font(font_size)
    bbox = d.textbbox((0, 0), glyph, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    x = (size - w) // 2 - bbox[0]
    y = (size - h) // 2 - bbox[1]
    d.text((x, y), glyph, font=font, fill=accent)
    return img


def _write_ico(path: Path, glyph: str, accent: tuple[int, int, int]) -> None:
    """Write a multi-resolution ICO (16/32/48/64 px) for Windows + Finder."""
    sizes = [16, 24, 32, 48, 64, 256]
    frames = [_draw_glyph(s, glyph, accent) for s in sizes]
    # Pillow writes ICO with the listed sizes when given the largest as base.
    base = frames[-1]
    base.save(path, format="ICO", sizes=[(s, s) for s in sizes])


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    _write_ico(ASSETS / "copal.ico", "C", ACCENT_BRAND)
    _write_ico(ASSETS / "copal-start.ico", "▶", ACCENT_START)  # ▶
    _write_ico(ASSETS / "copal-stop.ico", "■", ACCENT_STOP)    # ■
    _write_ico(ASSETS / "copal-new.ico", "+", ACCENT_NEW)
    print(f"Wrote 4 ICO files to {ASSETS}")


if __name__ == "__main__":
    main()
