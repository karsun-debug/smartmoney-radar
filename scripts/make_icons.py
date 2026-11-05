"""Regenerate the PWA / iPhone home-screen icons in spiderhead/static/ (needs Pillow)."""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "spiderhead" / "static"
BG, WHITE, BLIP = (42, 120, 214, 255), (255, 255, 255, 255), (255, 214, 102, 255)


def icon(size: int, maskable: bool = False) -> Image.Image:
    im = Image.new("RGBA", (size, size), BG)
    pad = size * (0.22 if maskable else 0.12)
    c, r = size / 2, size / 2 - pad
    sweep = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(sweep).pieslice([c - r, c - r, c + r, c + r], start=-90, end=-35, fill=(255, 255, 255, 80))
    im = Image.alpha_composite(im, sweep)
    d = ImageDraw.Draw(im)
    for k in (1.0, 0.66, 0.33):
        rr = r * k
        d.ellipse([c - rr, c - rr, c + rr, c + rr], outline=WHITE, width=max(2, int(size * 0.025)))
    d.line([c, c, c, c - r], fill=WHITE, width=max(2, int(size * 0.02)))
    dot = size * 0.035
    d.ellipse([c - dot, c - dot, c + dot, c + dot], fill=WHITE)
    for fx, fy in ((0.60, 0.30), (0.35, 0.62), (0.70, 0.66)):
        x, y = size * fx, size * fy
        d.ellipse([x - dot, y - dot, x + dot, y + dot], fill=BLIP)
    return im


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, size, mask in (("icon-192.png", 192, False), ("icon-512.png", 512, False),
                             ("icon-maskable-512.png", 512, True), ("apple-touch-icon.png", 180, False),
                             ("favicon-32.png", 32, False)):
        img = icon(size, mask)
        if name == "apple-touch-icon.png":
            img = img.convert("RGB")          # iOS ignores transparency on home-screen icons
        img.save(OUT / name, optimize=True)
        print(OUT / name)
