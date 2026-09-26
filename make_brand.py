"""
make_brand.py - Prepare le logo GOTA pour les images (signaux, bilans) et l'application.

    python make_brand.py                       # utilise brand/logo_original.(jpg|png)
    python make_brand.py chemin\\vers\\logo.png  # ou un fichier precis

A partir du logo d'origine (fond noir) on produit dans brand/ :
  logo_lockup.png   symbole + mot GOTA, fond TRANSPARENT (pose sur n'importe quelle couleur)
  logo_mark.png     symbole seul (3 fleches dorees)
  logo_word.png     mot GOTA seul
et on met a jour logo.png (256x256) et logo.ico pour l'application / raccourci bureau.
"""
from __future__ import annotations
import sys
import os
from pathlib import Path

for _c in [
    r"C:\Users\GOTA TRADING\AppData\Roaming\Python\Python312\site-packages",
    os.path.expandvars("%APPDATA%\\Python\\Python312\\site-packages"),
]:
    if _c and os.path.isdir(_c) and _c not in sys.path:
        sys.path.insert(0, _c)
        break

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

DIR = Path(__file__).parent
BRAND = DIR / "brand"


def _find_source(arg: str | None) -> Path:
    if arg:
        return Path(arg)
    for name in ("logo_original.png", "logo_original.jpg", "logo_original_0.jpg"):
        if (BRAND / name).exists():
            return BRAND / name
    raise SystemExit("Logo d'origine introuvable : mets-le dans brand/logo_original.png")


def cut_background(img: Image.Image) -> Image.Image:
    """Fond noir -> transparence (alpha issu de la luminosite), couleurs 'de-multipliees'."""
    a = np.asarray(img.convert("RGB")).astype(np.float32)
    v = a.max(axis=2)
    # bruit de fond JPEG : on mesure sur les bords de l'image
    edge = np.concatenate([v[:8].ravel(), v[-8:].ravel(), v[:, :8].ravel(), v[:, -8:].ravel()])
    t0 = float(np.percentile(edge, 99.5)) + 6.0
    t1 = t0 + 80.0
    alpha = np.clip((v - t0) / (t1 - t0), 0.0, 1.0)
    alpha = alpha * alpha * (3.0 - 2.0 * alpha)                      # smoothstep
    rgb = np.clip(a / np.maximum(alpha[..., None], 0.05), 0, 255)
    rgb = np.where(alpha[..., None] < 0.02, 0, rgb)
    out = np.dstack([rgb, alpha * 255.0]).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def crop_alpha(im: Image.Image, pad: int = 8, thr: int = 30) -> Image.Image:
    al = np.asarray(im)[..., 3]
    ys, xs = np.where(al > thr)
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    return im.crop((max(0, x0 - pad), max(0, y0 - pad), min(im.width, x1 + pad + 1), min(im.height, y1 + pad + 1)))


def find_gap(im: Image.Image):
    """Plus grand vide horizontal entre le symbole (haut) et le mot (bas) : (y_debut, y_fin) ou None."""
    al = np.asarray(im)[..., 3] > 30
    rows = al.any(axis=1)
    gaps, start = [], None
    for y, r in enumerate(rows):
        if not r and start is None:
            start = y
        if r and start is not None:
            gaps.append((y - start, start, y))
            start = None
    gaps = [g for g in gaps if g[1] > 5]
    if not gaps:
        return None
    _, g0, g1 = max(gaps)
    return g0, g1


def bleed_color(im: Image.Image, gap) -> Image.Image:
    """Les pixels quasi transparents portent la couleur de leur zone (or / blanc) au lieu du noir :
    evite le halo sombre autour des lettres quand l'image est redimensionnee."""
    a = np.asarray(im).copy()
    g0, g1 = gap if gap else (a.shape[0], a.shape[0])
    top, bot = a[:g0], a[g1:]
    solid = top[..., 3] > 230
    gold = top[solid][:, :3].mean(axis=0) if solid.any() else np.array([212, 175, 55])
    low_t = top[..., 3] < 235
    top[low_t, 0:3] = np.where(top[low_t, 3:4] < 90, gold, top[low_t, 0:3])
    low_b = bot[..., 3] < 235
    bot[low_b, 0:3] = np.where(bot[low_b, 3:4] < 90, np.array([246, 246, 246]), bot[low_b, 0:3])
    a[:g0], a[g1:] = top, bot
    a[g0:g1, :, 0:3] = 0
    return Image.fromarray(a, "RGBA")


def split_mark_word(im: Image.Image):
    gap = find_gap(im)
    if not gap:
        return im, None
    g0, g1 = gap
    return crop_alpha(im.crop((0, 0, im.width, g0))), crop_alpha(im.crop((0, g1, im.width, im.height)))


def main() -> None:
    src = _find_source(sys.argv[1] if len(sys.argv) > 1 else None)
    BRAND.mkdir(exist_ok=True)
    print("source :", src)
    img = Image.open(src)
    rgba = crop_alpha(cut_background(img), pad=10)
    rgba = bleed_color(rgba, find_gap(rgba))
    mark, word = split_mark_word(rgba)
    up = lambda im: im.resize((im.width * 2, im.height * 2), Image.LANCZOS)     # 2x : reste net une fois redimensionne
    up(rgba).save(BRAND / "logo_lockup.png")
    up(mark).save(BRAND / "logo_mark.png")
    if word is not None:
        up(word).save(BRAND / "logo_word.png")
    print(f"logo_lockup.png {up(rgba).size} | mark {up(mark).size} | word {up(word).size if word else '-'}")

    # icone carree pour l'application / raccourci (fond noir, logo centre)
    S = 256
    icon = Image.new("RGBA", (S, S), (6, 8, 12, 255))
    lk = rgba.copy()
    scale = (S * 0.82) / max(lk.width, lk.height)
    lk = lk.resize((int(lk.width * scale), int(lk.height * scale)), Image.LANCZOS)
    icon.alpha_composite(lk, ((S - lk.width) // 2, (S - lk.height) // 2))
    icon.save(DIR / "logo.png")
    icon.save(DIR / "logo.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("logo.png + logo.ico mis a jour")


if __name__ == "__main__":
    main()
