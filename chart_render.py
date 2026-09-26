"""
chart_render.py (v2) - Images professionnelles pour Telegram : carte de signal, carte de resultat, bilan.

Charte GOTA : noir profond, or (#d4af37), blanc. Logo reel dans brand/ (voir make_brand.py).
Mise en page en PIXELS (canevas 1 unite = 1 px) : alignements nets, coins arrondis reguliers.
Aucune donnee inventee : tout vient du signal / de l'historique.
"""
from __future__ import annotations
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd
from PIL import Image
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle, FancyBboxPatch  # noqa: E402

from signal_data import symbol_meta, UNIVERSE  # noqa: E402

BRAND_DIR = Path(__file__).parent / "brand"
BG, PANEL, BORDER, GRID = "#06080c", "#0b1017", "#1c2531", "#121922"
TXT, WHITE, MUTED, DIM = "#e8ebf0", "#f4f6f9", "#8a93a1", "#4b5462"
GOLD, GOLD_L = "#d4af37", "#f1d67c"
UP, DOWN = "#26a69a", "#ef5350"                 # bougies (standard plateformes)
GREEN, RED, BLUE = "#2ecc71", "#ef4444", "#38bdf8"
TF_LABEL = {"1m": "M1", "5m": "M5", "15m": "M15", "60m": "H1", "240m": "H4"}

# ------------------------------------------------------------------ outils de mise en page
_IMG: dict = {}
_PIL: dict = {}


def _pil(name: str):
    if name not in _PIL:
        p = BRAND_DIR / name
        _PIL[name] = Image.open(p).convert("RGBA") if p.exists() else None
    return _PIL[name]


def _brand(name: str):
    """Compat : tableau numpy du logo (ou None)."""
    if name not in _IMG:
        im = _pil(name)
        _IMG[name] = np.asarray(im).astype(np.float32) / 255.0 if im is not None else None
    return _IMG[name]


def _spaced(t: str) -> str:
    """Etiquettes en capitales espacees (look 'premium')."""
    return " ".join(t.upper())


class Card:
    """Figure a mise en page en pixels. Deux couches : 'bg' (fond) et 'ov' (annotations au-dessus du graphique)."""

    def __init__(self, w: int, h: int):
        self.w, self.h = w, h
        self.fig = plt.figure(figsize=(w / 100, h / 100), dpi=100, facecolor=BG)
        self.bg = self._layer(0)
        self.ov = None
        self.r = self.fig.canvas.get_renderer()
        # fond : degrade radial discret + liseré or
        yy, xx = np.mgrid[0:90, 0:160]
        d = np.sqrt(((xx - 80) / 80.0) ** 2 + ((yy - 40) / 60.0) ** 2)
        c0, c1 = np.array([15, 20, 29]) / 255.0, np.array([5, 7, 10]) / 255.0
        img = c0[None, None, :] * (1 - np.clip(d, 0, 1))[..., None] + c1[None, None, :] * np.clip(d, 0, 1)[..., None]
        self.bg.imshow(img, extent=(0, w, h, 0), aspect="auto", interpolation="bilinear", zorder=0)
        self._fix(self.bg)
        self.rrect(self.bg, 12, 12, w - 24, h - 24, r=20, fc="none", ec=GOLD, lw=1.1, alpha=0.5, z=1)

    def _layer(self, z: int):
        ax = self.fig.add_axes([0, 0, 1, 1], zorder=z)
        ax.axis("off")
        return ax

    def _fix(self, ax):
        ax.set_xlim(0, self.w)
        ax.set_ylim(self.h, 0)
        ax.set_aspect("auto")

    def overlay(self):
        self.ov = self._layer(20)
        self._fix(self.ov)
        return self.ov

    def chart_axes(self, x, y, w, h):
        return self.fig.add_axes([x / self.w, 1 - (y + h) / self.h, w / self.w, h / self.h], zorder=5, facecolor="none")

    @staticmethod
    def rrect(ax, x, y, w, h, r=10, fc=PANEL, ec=BORDER, lw=1.0, alpha=1.0, z=2):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={r}",
                                    fc=fc, ec=ec, lw=lw, alpha=alpha, zorder=z))

    def text(self, ax, x, y, s, size=12, color=TXT, weight="normal", ha="left", va="center", z=10, **kw):
        return ax.text(x, y, s, fontsize=size, color=color, fontweight=weight, ha=ha, va=va, zorder=z, **kw)

    def width(self, t) -> float:
        return t.get_window_extent(self.r).width

    def logo(self, ax, name: str, x: float, y: float, height: float, z=10, alpha=1.0):
        """Pose un logo de brand/ a la taille EXACTE en pixels (redimensionne avec PIL, alpha premultiplie :
        pas de halo ni d'effet de trame comme avec le reechantillonnage de matplotlib)."""
        im = _pil(name)
        if im is None:
            return 0.0
        h = max(1, int(round(height)))
        w = max(1, int(round(h * im.width / im.height)))
        small = im.convert("RGBa").resize((w, h), Image.LANCZOS).convert("RGBA")
        arr = np.asarray(small).astype(np.float32) / 255.0
        if alpha != 1.0:
            arr[..., 3] *= alpha
        x0, y0 = int(round(x)), int(round(y))
        ax.imshow(arr, extent=(x0, x0 + w, y0 + h, y0), aspect="auto", interpolation="none", zorder=z)
        self._fix(ax)
        return float(w)

    def pill(self, ax, x, y, w, h, s, fc, tc="#06080c", size=13, ec=None, weight="bold", z=12):
        self.rrect(ax, x, y, w, h, r=h / 2, fc=fc, ec=ec or fc, lw=1.4, z=z)
        self.text(ax, x + w / 2, y + h / 2 + 1, s, size=size, color=tc, weight=weight, ha="center", z=z + 1)

    def save(self, path: str) -> str:
        self.fig.savefig(path, dpi=100, facecolor=BG)
        plt.close(self.fig)
        return path


def _pf(p: float, digits: int) -> str:
    return f"{p:,.{digits}f}".replace(",", " ")


def _dist(sig, level: float) -> str:
    """Distance depuis l'entree : pips (forex) ou unites de prix."""
    m = symbol_meta(sig.display) or {}
    cls = UNIVERSE.get(sig.display, {}).get("cls", "")
    d = abs(level - sig.entry)
    if cls == "fx" and m.get("point"):
        return f"{d / (m['point'] * 10):.1f} pips"
    return f"{d:,.2f}".replace(",", " ")


# ------------------------------------------------------------------ carte de signal
def _invite_bar(card, bg, ov, y: int, text: str, contact: str = "") -> None:
    """Barre d'invitation du groupe PUBLIC (bas de la fiche) : texte + @contact, centres."""
    card.rrect(bg, 44, y, 1512, 46, r=23, fc="#12100a", ec=GOLD, lw=1.3, z=1)
    t1 = card.text(ov, 0, y + 24, "✉  " + text, size=13, color=WHITE, weight="bold", z=23)
    w1 = card.width(t1)
    t2 = card.text(ov, 0, y + 24, contact, size=13, color=GOLD, weight="bold", z=23) if contact else None
    x0 = 44 + (1512 - (w1 + ((28 + card.width(t2)) if t2 else 0))) / 2
    t1.set_x(x0)
    if t2:
        t2.set_x(x0 + w1 + 28)


def render_signal_chart(df: pd.DataFrame, sig, out_path: str, digits: int = 5, n_before: int = 62,
                        n_after: int = 0, brand: str = "GOTA TRADING",
                        result: Optional[dict] = None, footer: str = "", contact: str = "") -> str:
    """df : bougies (index = ouverture UTC) contenant la bougie du signal.
    result (optionnel) : {"label": "TP1 ATTEINT", "r": 1.0, "color": GREEN, "hit": [True, False, False], "stopped": False}
    footer / contact (fiche PUBLIQUE) : barre d'invitation a nous ecrire, a la place des pastilles de confirmation."""
    W, H = 1600, 900
    long_ = sig.direction == "LONG"
    dir_col = UP if long_ else DOWN
    card = Card(W, H)
    bg = card.bg
    ov = card.overlay()

    # ---------------------------------------------------------------- en-tete
    lw_ = card.logo(bg, "logo_lockup.png", 44, 32, 94)
    bg.add_line(plt.Line2D([44 + lw_ + 30, 44 + lw_ + 30], [42, 126], color=BORDER, lw=1.2, zorder=3))
    tx = 44 + lw_ + 58
    t_name = card.text(bg, tx, 66, sig.display, size=38, color=WHITE, weight="bold")
    wn = card.width(t_name)
    tf = TF_LABEL.get(sig.tf, sig.tf)
    card.pill(bg, tx + wn + 18, 48, 62, 34, tf, fc="none", tc=GOLD, ec=GOLD, size=13)
    when = pd.Timestamp(sig.signal_time).strftime("%d/%m/%Y  ·  %H:%M UTC")
    card.text(bg, tx, 108, "Signal du " + when, size=12.5, color=MUTED)
    rr = abs(sig.tps[-1] - sig.entry) / max(sig.risk, 1e-12)
    x = W - 44
    for lab, w_, fc, tc, ec in ((f"R:R  1 : {rr:.0f}", 142, "none", WHITE, "#3a4452"),
                                (f"GRADE  {sig.grade}", 150, "none", GOLD, GOLD),
                                ("ACHAT" if long_ else "VENTE", 150, dir_col, "#06080c", dir_col)):
        x -= w_
        card.pill(bg, x, 52, w_, 46, lab, fc=fc, tc=tc, ec=ec, size=14)
        x -= 14

    # ---------------------------------------------------------------- graphique
    CX, CY, CW, CH = 44, 148, 1512, 520
    card.rrect(bg, CX, CY, CW, CH, r=14, fc="#080c12", ec=BORDER, lw=1.0, z=1)
    ax = card.chart_axes(CX + 76, CY + 14, CW - 76 - 176, CH - 14 - 34)
    i_sig = df.index.get_loc(pd.Timestamp(sig.bar_open))
    start = max(0, i_sig - n_before)
    end = min(len(df), i_sig + 1 + n_after)
    w = df.iloc[start:end]
    n = len(w)
    x_ = np.arange(n)
    o, h, l, c = (w[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    xs = i_sig - start
    span = max(14, (n - 1 - xs) + 3)              # la boite de position couvre toutes les bougies suivantes
    xe = xs + span
    ax.set_xlim(-1, xe + 2)
    mk = _brand("logo_mark.png")                   # filigrane discret (symbole du logo) sous les bougies
    if mk is not None:
        card.logo(bg, "logo_mark.png", CX + CW * 0.40, CY + CH * 0.20, 190, z=1.6, alpha=0.06)

    lvls = [sig.entry, sig.sl, *sig.tps]
    ymin = min(float(np.percentile(l, 1.5)), min(lvls)) - 0.25 * sig.atr
    ymax = max(float(np.percentile(h, 98.5)), max(lvls)) + 0.25 * sig.atr
    ax.set_ylim(ymin, ymax)
    yr = ymax - ymin

    col = np.where(c >= o, UP, DOWN)
    ax.vlines(x_, l, h, colors=col, linewidth=1.5, zorder=3)
    ax.bar(x_, np.maximum(np.abs(c - o), yr * 0.0012), bottom=np.minimum(o, c), width=0.66, color=col, zorder=4)

    # zone (OB / FVG)
    xb = max(0, w.index.searchsorted(pd.Timestamp(sig.zone_born)))
    ax.add_patch(Rectangle((xb - 0.45, sig.zone_lo), xs - xb + 1.0, sig.zone_hi - sig.zone_lo,
                           fc=dir_col, ec=dir_col, alpha=0.20, lw=1.0, zorder=2))
    zname = ("Order Block " if sig.zone_kind == "OB" else "Fair Value Gap ") + ("haussier" if long_ else "baissier")
    zmid = (sig.zone_lo + sig.zone_hi) / 2
    zbox = dict(boxstyle="round,pad=0.3", fc="#080c12", ec=dir_col, lw=0.9, alpha=0.93)
    if result:                                         # cartes de suivi : le bandeau de resultat occupe ce coin
        pass
    elif xb > 14:
        ax.text(xb - 1.6, zmid, zname, color=dir_col, fontsize=10.5, fontweight="bold", va="center", ha="right", zorder=8, bbox=zbox)
    else:
        ax.text(max(xb, 1), sig.zone_hi + yr * 0.03, zname, color=dir_col, fontsize=10.5, fontweight="bold", va="bottom", zorder=8, bbox=zbox)

    # ---- position (risque / recompense), comme l'outil "position" d'une plateforme
    hit = (result or {}).get("hit", [False, False, False])
    stopped = (result or {}).get("stopped", False)
    ax.add_patch(Rectangle((xs, min(sig.entry, sig.sl)), span, abs(sig.entry - sig.sl), fc=DOWN, alpha=0.20, ec="none", zorder=1.5))
    ax.add_patch(Rectangle((xs, min(sig.entry, sig.tps[-1])), span, abs(sig.tps[-1] - sig.entry), fc=UP, alpha=0.15, ec="none", zorder=1.5))
    ax.hlines(sig.sl, xs, xe, colors=DOWN, linewidth=1.8, zorder=5)
    ax.hlines(sig.entry, xs, xe, colors=WHITE, linewidth=1.6, zorder=5)
    for k, tp in enumerate(sig.tps):
        ax.hlines(tp, xs, xe, colors=UP, linestyles="-" if k == 2 else "--", linewidth=1.8 if (k == 2 or hit[k]) else 1.2, zorder=5, alpha=1 if (k == 2 or hit[k]) else 0.75)
    marker_y = l[xs] - 0.45 * sig.atr if long_ else h[xs] + 0.45 * sig.atr
    ax.scatter([xs], [marker_y], marker="^" if long_ else "v", s=150, color=GOLD, zorder=9, edgecolors="#06080c", linewidths=0.8)

    # etiquettes dans les boites
    up_y = (sig.entry + sig.tps[-1]) / 2
    dn_y = (sig.entry + sig.sl) / 2
    lbox = dict(boxstyle="round,pad=0.3", fc="#080c12", ec="none", alpha=0.82)
    ax.text(xe - 0.5, up_y, f"OBJECTIF   {_dist(sig, sig.tps[-1])}  ·  +{rr:.0f}R", color=UP, fontsize=10.5, fontweight="bold",
            va="center", ha="right", zorder=8, bbox=lbox)
    ax.text(xe - 0.5, dn_y, f"RISQUE   {_dist(sig, sig.sl)}  ·  1R", color=DOWN, fontsize=10.5, fontweight="bold",
            va="center", ha="right", zorder=8, bbox=lbox)

    # ---- etiquettes de prix a droite (calquee au-dessus, coordonnees ecran)
    fig_h = card.fig.get_figheight() * 100
    tags = [(sig.sl, f"SL  {_pf(sig.sl, digits)}" + ("  ✗" if stopped else ""), DOWN, WHITE),
            (sig.entry, f"ENTRÉE  {_pf(sig.entry, digits)}", WHITE, "#06080c")]
    for k, tp in enumerate(sig.tps):
        tags.append((tp, f"TP{k + 1}  {_pf(tp, digits)}" + ("  ✓" if hit[k] else ""), UP, "#06080c"))
    pos = []
    for price, label, fc, tc in tags:
        px, py = ax.transData.transform((xe, price))
        pos.append([fig_h - py, label, fc, tc])
    pos.sort(key=lambda t: t[0])
    for k in range(1, len(pos)):                      # anti-chevauchement (hauteur d'etiquette 26 px)
        if pos[k][0] - pos[k - 1][0] < 28:
            pos[k][0] = pos[k - 1][0] + 28
    x_tag = ax.transData.transform((xe, ymin))[0] + 10
    for py, label, fc, tc in pos:
        card.rrect(ov, x_tag, py - 13, 168, 26, r=4, fc=fc, ec=fc, lw=0.5, z=21)
        card.text(ov, x_tag + 10, py + 1, label, size=10.8, color=tc, weight="bold", z=22)

    # axes
    ax.grid(color=GRID, linewidth=0.8)
    ax.tick_params(colors=MUTED, labelsize=9.5, length=0, pad=8)
    for s in ax.spines.values():
        s.set_visible(False)
    ticks = np.linspace(0, n - 1, 8).astype(int)
    ax.set_xticks(ticks)
    ax.set_xticklabels([w.index[t].strftime("%d/%m %Hh") for t in ticks])
    ax.set_yticks(np.linspace(ymin + 0.03 * yr, ymax - 0.03 * yr, 7))
    ax.set_yticklabels([_pf(v, digits) for v in np.linspace(ymin + 0.03 * yr, ymax - 0.03 * yr, 7)])

    # bandeau de resultat (cartes de suivi)
    if result:
        rc = result.get("color", GOLD)
        t_lab = card.text(ov, CX + 128, CY + 55, result["label"], size=25, color=rc, weight="bold", z=22)
        card.rrect(ov, CX + 100, CY + 24, max(400, card.width(t_lab) + 56), 92, r=12, fc="#070b10", ec=rc, lw=2.2, alpha=0.96, z=21)
        sub = result.get("sub") or (f"Résultat :  {result['r']:+.2f} R" if "r" in result else None)
        if sub:
            card.text(ov, CX + 128, CY + 94, sub, size=15, color=WHITE, z=22)

    # ---------------------------------------------------------------- tableau des niveaux
    TY, TH = 690, 104
    card.rrect(bg, 44, TY, 1512, TH, r=14, fc=PANEL, ec=BORDER, lw=1.0, z=1)
    cells = [("ENTRÉE", _pf(sig.entry, digits), WHITE, "prix du signal", None),
             ("STOP LOSS", _pf(sig.sl, digits), DOWN, "−" + _dist(sig, sig.sl), "stop" if stopped else None)]
    for k, tp in enumerate(sig.tps):
        cells.append((f"TAKE PROFIT {k + 1}", _pf(tp, digits), UP, "+" + _dist(sig, tp) + f"  ·  +{k + 1}R", "tp" if hit[k] else None))
    cells.append(("RATIO R:R", f"1 : {rr:.0f}", GOLD, "risque / objectif", None))
    cw = 1512 / len(cells)
    for k, (lab, val, colr, sub, flag) in enumerate(cells):
        cx = 44 + k * cw + cw / 2
        if k:
            bg.add_line(plt.Line2D([44 + k * cw] * 2, [TY + 16, TY + TH - 16], color=BORDER, lw=1.0, zorder=3))
        card.text(bg, cx, TY + 24, _spaced(lab), size=9.6, color=MUTED, ha="center")
        card.text(bg, cx, TY + 56, val, size=22, color=colr, weight="bold", ha="center")
        if flag:
            card.text(bg, cx, TY + 87, "✓  ATTEINT" if flag == "tp" else "✗  TOUCHÉ", size=11, color=GREEN if flag == "tp" else RED, weight="bold", ha="center")
        else:
            card.text(bg, cx, TY + 87, sub, size=10.5, color=MUTED, ha="center")

    # ---------------------------------------------------------------- pastilles de confirmation (VIP) / invitation (public)
    CYc = 812
    if footer:
        _invite_bar(card, bg, ov, CYc, footer, contact)
        return card.save(out_path)
    scored =[("sweep", "Liquidité"), ("stack", "OB + FVG"), ("volume", "Volume Profile"),
              ("momentum", "RSI"), ("discount", "Discount" if long_ else "Premium"), ("session", "Session")]
    nsc = sum(1 for k, _ in scored if sig.checks.get(k))
    lab_t = card.text(bg, 48, CYc + 21, _spaced("Confirmations") + f"   {nsc}/6", size=10, color=MUTED)
    xx = 48 + card.width(lab_t) + 26
    t = card.text(ov, xx + 16, CYc + 21, "4 conditions obligatoires  ✓", size=10.8, color=GOLD, weight="bold", z=23)
    wch = card.width(t) + 32
    card.rrect(ov, xx, CYc, wch, 42, r=21, fc="none", ec=GOLD, lw=1.3, z=22)
    xx += wch + 12
    for key, lab in scored:
        if not sig.checks.get(key):                       # fiche epuree : seules les confirmations remplies sont affichees
            continue
        t = card.text(ov, xx + 16, CYc + 21, "✓  " + lab, size=10.8, color=GREEN, weight="bold", z=23)
        wch = card.width(t) + 32
        card.rrect(ov, xx, CYc, wch, 42, r=21, fc="#0e1a14", ec="#1f6b45", lw=1.1, z=22)
        xx += wch + 10

    return card.save(out_path)


# ------------------------------------------------------------------ carte de bilan
def render_recap_card(title: str, subtitle: str, kpis: List[tuple], trades: List[dict],
                      curve: List[float], out_path: str, brand: str = "GOTA TRADING",
                      curve_label: str = "Résultat cumulé (en R)") -> str:
    """kpis : [(libelle, valeur, couleur)] ; trades : [{date, symbol, dir, outcome, r}] ; curve : R cumule."""
    W, H = 1080, 1350
    card = Card(W, H)
    bg = card.bg
    ov = card.overlay()

    lk = _brand("logo_lockup.png")
    lwid = 132 * lk.shape[1] / lk.shape[0] if lk is not None else 0
    card.logo(bg, "logo_lockup.png", (W - lwid) / 2, 46, 132)
    t_title = card.text(bg, W / 2, 218, title, size=30, color=WHITE, weight="bold", ha="center")
    fs = 30.0
    while card.width(t_title) > W - 120 and fs > 14:          # le titre ne deborde jamais
        fs -= 1
        t_title.set_fontsize(fs)
    card.text(bg, W / 2, 256, subtitle, size=13, color=MUTED, ha="center")

    kw, gap = (W - 2 * 60 - 3 * 16) / 4, 16
    for k, (lab, val, cc) in enumerate(kpis[:4]):
        x0 = 60 + k * (kw + gap)
        card.rrect(bg, x0, 292, kw, 118, r=14, fc=PANEL, ec=BORDER, lw=1.0, z=2)
        card.text(bg, x0 + kw / 2, 337, str(val), size=27, color=cc, weight="bold", ha="center")
        card.text(bg, x0 + kw / 2, 380, lab.upper(), size=9.4, color=MUTED, ha="center")

    card.rrect(bg, 60, 434, W - 120, 330, r=14, fc=PANEL, ec=BORDER, lw=1.0, z=1)
    ax = card.chart_axes(120, 480, W - 200, 250)
    if len(curve) >= 2:
        xs = np.arange(len(curve))
        up = curve[-1] >= 0
        cc = UP if up else DOWN
        ax.plot(xs, curve, color=cc, linewidth=2.6)
        ax.fill_between(xs, curve, 0, color=cc, alpha=0.14)
        ax.axhline(0, color=MUTED, linewidth=0.9)
    else:
        ax.text(0.5, 0.5, "Pas encore assez de trades clôturés", color=MUTED, ha="center", va="center", transform=ax.transAxes, fontsize=13)
    ax.grid(color=GRID, linewidth=0.8)
    ax.tick_params(colors=MUTED, labelsize=9.5, length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    card.text(bg, 86, 462, curve_label, size=12.5, color=TXT, weight="bold")

    card.text(bg, 60, 806, _spaced("Derniers trades clôturés"), size=11, color=MUTED)
    y = 842
    for t in trades[:10]:
        pos = t["r"] >= 0
        card.text(bg, 64, y, t["date"], size=12, color=MUTED)
        card.text(bg, 170, y, t["symbol"], size=13, color=WHITE, weight="bold")
        card.text(bg, 340, y, "ACHAT" if t["dir"] == "LONG" else "VENTE", size=11, color=UP if t["dir"] == "LONG" else DOWN, weight="bold")
        card.text(bg, 470, y, t["outcome"], size=12, color=TXT)
        card.text(bg, W - 64, y, f"{t['r']:+.2f} R", size=13.5, color=UP if pos else DOWN, weight="bold", ha="right")
        bg.add_line(plt.Line2D([60, W - 60], [y + 21, y + 21], color=GRID, lw=1.0, zorder=3))
        y += 42
    return card.save(out_path)
