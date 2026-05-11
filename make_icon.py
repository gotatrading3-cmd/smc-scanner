"""
Genere logo.ico (multi-tailles) pour le raccourci GOTA TRADING.
Reproduit le logo gold peaks + texte GOTA serif sur fond noir.
"""
from __future__ import annotations
import sys
import os
import math
from pathlib import Path

for _c in [
    os.path.expandvars("%APPDATA%\\Python\\Python312\\site-packages"),
    os.path.expanduser("~/AppData/Roaming/Python/Python312/site-packages"),
]:
    if _c and os.path.isdir(_c) and _c not in sys.path:
        sys.path.insert(0, _c)
        break

from PIL import Image, ImageDraw, ImageFont, ImageFilter  # noqa: E402

OUT = Path(__file__).parent / "logo.ico"
PNG_OUT = Path(__file__).parent / "logo.png"

# Couleurs gold (gradient simule via 3 tons)
GOLD_LIGHT = (253, 230, 138)   # #fde68a
GOLD_MID = (251, 191, 36)      # #fbbf24
GOLD_DARK = (217, 119, 6)      # #d97706
GOLD_DEEP = (146, 64, 14)      # #92400e
BG_BLACK = (8, 8, 8, 255)
TEXT_WHITE = (245, 245, 245, 255)


def make_logo(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), BG_BLACK)
    draw = ImageDraw.Draw(img)
    s = size / 100  # facteur d'echelle (logo concu sur viewport 100x100)

    # Path du double peak (W inverse) - meme coords que SVG
    points = [
        (10, 60), (30, 25), (38, 38), (50, 12), (62, 38), (70, 25), (90, 60),
        (78, 60), (70, 45), (60, 60), (50, 35), (40, 60), (30, 45), (22, 60),
    ]
    scaled = [(x * s, y * s) for x, y in points]

    # Gradient gold simule : on dessine plusieurs polygones de couleurs differentes
    # Dessine en couches du fonce au clair pour l'effet
    draw.polygon(scaled, fill=GOLD_DEEP)

    # Couche middle
    mid_pts = [(x, y + size * 0.005) for x, y in scaled]
    draw.polygon(mid_pts, fill=GOLD_DARK)

    # Couche haute
    high_pts = [(x, y + size * 0.012) for x, y in scaled]
    draw.polygon(high_pts, fill=GOLD_MID)

    # Top highlights
    top_pts = [(x, y + size * 0.025) for x, y in scaled]
    draw.polygon(top_pts, fill=GOLD_LIGHT)

    # Redessine en mode normal avec gold-mid pour finir propre
    draw.polygon(scaled, fill=None, outline=GOLD_LIGHT, width=max(1, int(size / 100)))

    # Petites fleches au-dessus des peaks
    arrow_w = max(1, int(size / 80))
    # Peak central
    draw.line([(48 * s, 14 * s), (50 * s, 10 * s), (52 * s, 14 * s)], fill=GOLD_LIGHT, width=arrow_w)
    # Peaks lateraux
    draw.line([(28 * s, 27 * s), (30 * s, 23 * s), (32 * s, 27 * s)], fill=GOLD_LIGHT, width=arrow_w)
    draw.line([(68 * s, 27 * s), (70 * s, 23 * s), (72 * s, 27 * s)], fill=GOLD_LIGHT, width=arrow_w)

    # Slashes lateraux
    slash_w = max(1, int(size / 70))
    draw.line([(6 * s, 64 * s), (22 * s, 64 * s)], fill=GOLD_MID, width=slash_w)
    draw.line([(78 * s, 64 * s), (94 * s, 64 * s)], fill=GOLD_MID, width=slash_w)

    # Texte GOTA
    if size >= 48:
        try:
            font_size = int(size * 0.16)
            font_paths = [
                "C:\\Windows\\Fonts\\georgia.ttf",
                "C:\\Windows\\Fonts\\times.ttf",
                "C:\\Windows\\Fonts\\georgiab.ttf",
            ]
            font = None
            for fp in font_paths:
                if os.path.exists(fp):
                    font = ImageFont.truetype(fp, font_size)
                    break
            if not font:
                font = ImageFont.load_default()
            text = "GOTA"
            bbox = draw.textbbox((0, 0), text, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
            x = (size - text_w) / 2 - bbox[0]
            y = size * 0.78
            # Letter-spacing manuel
            spacing = max(1, int(size * 0.025))
            full_w = sum((draw.textbbox((0, 0), c, font=font)[2] - draw.textbbox((0, 0), c, font=font)[0]) for c in text) + spacing * (len(text) - 1)
            x = (size - full_w) / 2
            for c in text:
                draw.text((x, y), c, fill=TEXT_WHITE, font=font)
                cb = draw.textbbox((0, 0), c, font=font)
                x += (cb[2] - cb[0]) + spacing
        except Exception as e:
            print(f"  warn: {e}")

    return img


def main():
    print("Generation logo multi-tailles...")
    sizes = [256, 128, 64, 48, 32, 16]
    images = []
    for sz in sizes:
        img = make_logo(sz)
        images.append(img)
        if sz == 256:
            img.save(PNG_OUT)
            print(f"  PNG 256x256 sauve : {PNG_OUT}")

    # Sauve ICO multi-resolution
    images[0].save(OUT, format="ICO", sizes=[(s, s) for s in sizes], append_images=images[1:])
    print(f"  ICO multi-tailles sauve : {OUT}")
    print(f"  Tailles : {sizes}")


if __name__ == "__main__":
    main()
