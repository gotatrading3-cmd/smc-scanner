"""
chart_render.py - Images pour Telegram : graphique de signal, carte de resultat, carte de bilan.
Theme sombre GOTA TRADING (or sur fond nuit). Aucune donnee inventee : tout vient du signal / de l'historique.
"""
from __future__ import annotations
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle, FancyBboxPatch  # noqa: E402

BG, PANEL, GRID = "#0a0e15", "#111826", "#1b2433"
TXT, MUTED, WHITE = "#c9d1d9", "#6e7681", "#e6edf3"
GREEN, RED, GOLD, BLUE = "#22c55e", "#ef4444", "#fbbf24", "#38bdf8"
LOGO = Path(__file__).parent / "logo.png"
TF_LABEL = {"1m": "M1", "5m": "M5", "15m": "M15", "60m": "H1", "240m": "H4"}
DISCLAIMER = "Contenu éducatif — pas un conseil financier. Le trading comporte un risque de perte en capital."


def _pfmt(p: float, digits: int) -> str:
    return f"{p:,.{digits}f}".replace(",", " ")


def _logo(fig, x=0.925, y=0.905, size=0.07):
    if LOGO.exists():
        try:
            img = plt.imread(str(LOGO))
            ax = fig.add_axes([x, y, size * 0.75, size * 1.0], anchor="NE", zorder=5)
            ax.imshow(img)
            ax.axis("off")
        except Exception:
            pass


def _pill(fig, x, y, w, h, text, fc, tc="#0a0e15", fs=13):
    fig.patches.append(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.002,rounding_size=0.012",
                                      transform=fig.transFigure, fc=fc, ec="none", zorder=6))
    fig.text(x + w / 2, y + h / 2, text, ha="center", va="center", color=tc, fontsize=fs,
             fontweight="bold", zorder=7)


def _spread_labels(items, gap):
    """Evite que les etiquettes de niveaux se chevauchent (items = [(y, texte, couleur)])."""
    items = sorted(items, key=lambda t: t[0])
    ys = [it[0] for it in items]
    for k in range(1, len(ys)):
        if ys[k] - ys[k - 1] < gap:
            ys[k] = ys[k - 1] + gap
    return [(ys[k], items[k][1], items[k][2], items[k][0]) for k in range(len(items))]


def render_signal_chart(df: pd.DataFrame, sig, out_path: str, digits: int = 5, n_before: int = 56,
                        n_after: int = 0, brand: str = "GOTA TRADING",
                        result: Optional[dict] = None) -> str:
    """df : bougies (index = ouverture UTC) contenant la bougie du signal.
    result (optionnel) : {"label": "TP1 ATTEINT", "r": 1.0, "color": GREEN, "hit": [True, False, False],
                          "stopped": False, "until": Timestamp}"""
    long_ = sig.direction == "LONG"
    i_sig = df.index.get_loc(pd.Timestamp(sig.bar_open))
    start = max(0, i_sig - n_before)
    end = min(len(df), i_sig + 1 + n_after)
    w = df.iloc[start:end]
    n = len(w)
    x = np.arange(n)
    o, h, l, c = (w[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    pad = max(15, int(n * 0.24))

    fig = plt.figure(figsize=(12, 6.75), dpi=100, facecolor=BG)
    ax = fig.add_axes([0.085, 0.10, 0.875, 0.70], facecolor=BG)
    ax.set_xlim(-1, n + pad)

    # echelle robuste : une mèche isolee ne doit pas aplatir tout le graphique
    lvls = [sig.entry, sig.sl, *sig.tps]
    ymin = min(float(np.percentile(l, 1.5)), min(lvls)) - 0.2 * sig.atr
    ymax = max(float(np.percentile(h, 98.5)), max(lvls)) + 0.2 * sig.atr
    ax.set_ylim(ymin, ymax)
    yr = ymax - ymin

    # bougies
    col = np.where(c >= o, GREEN, RED)
    ax.vlines(x, l, h, colors=col, linewidth=1.0, zorder=3)
    ax.bar(x, np.maximum(np.abs(c - o), yr * 0.0008), bottom=np.minimum(o, c), width=0.64, color=col, zorder=4)

    # zone (OB / FVG)
    xb = max(0, w.index.searchsorted(pd.Timestamp(sig.zone_born)))
    zc = GREEN if long_ else RED
    ax.add_patch(Rectangle((xb - 0.4, sig.zone_lo), (i_sig - start) - xb + 0.9, sig.zone_hi - sig.zone_lo,
                           fc=zc, ec=zc, alpha=0.22, lw=1.0, zorder=2))
    zname = ("Order Block " if sig.zone_kind == "OB" else "Fair Value Gap ") + ("haussier" if long_ else "baissier")
    zmid = (sig.zone_lo + sig.zone_hi) / 2
    if xb > 16:      # assez de place a gauche de la zone
        ax.text(xb - 1.8, zmid, zname, color=zc, fontsize=9.5, fontweight="bold", va="center", ha="right", zorder=6)
    else:            # zone proche du bord gauche : etiquette au-dessus
        ax.text(max(xb, 1), sig.zone_hi + yr * 0.012, zname, color=zc, fontsize=9.5, fontweight="bold", va="bottom", zorder=6)

    # position (risque / gain)
    xs = i_sig - start
    xr = n + pad - 1
    ax.add_patch(Rectangle((xs, min(sig.entry, sig.sl)), xr - xs, abs(sig.entry - sig.sl), fc=RED, alpha=0.10, ec="none", zorder=1))
    far = sig.tps[-1]
    ax.add_patch(Rectangle((xs, min(sig.entry, far)), xr - xs, abs(far - sig.entry), fc=GREEN, alpha=0.07, ec="none", zorder=1))
    hit = (result or {}).get("hit", [False, False, False])
    stopped = (result or {}).get("stopped", False)
    ax.hlines(sig.entry, xs - 0.5, xr, colors=WHITE, linestyles="--", linewidth=1.2, zorder=5)
    ax.hlines(sig.sl, xs - 0.5, xr, colors=RED, linewidth=1.6, zorder=5)
    labels = [(sig.entry, f"Entrée  {_pfmt(sig.entry, digits)}", WHITE), (sig.sl, f"SL  {_pfmt(sig.sl, digits)}" + ("  ✗" if stopped else ""), RED)]
    for k, tp in enumerate(sig.tps):
        ax.hlines(tp, xs - 0.5, xr, colors=GREEN, linestyles=":", linewidth=1.5 if not hit[k] else 2.4, zorder=5)
        labels.append((tp, f"TP{k + 1}  {_pfmt(tp, digits)}" + ("  ✓" if hit[k] else f"   +{k + 1}R"), GREEN))
    for ly, txt, cc, ry in _spread_labels(labels, yr * 0.042):
        ax.text(n + 0.6, ly, txt, color=cc, fontsize=10.5, fontweight="bold", va="center", zorder=8,
                bbox=dict(boxstyle="round,pad=0.25", fc=BG, ec=cc, lw=0.8))
    # marqueur du signal
    my = l[i_sig - start] - 0.5 * sig.atr if long_ else h[i_sig - start] + 0.5 * sig.atr
    ax.scatter([xs], [my], marker="^" if long_ else "v", s=110, color=GOLD, zorder=9)

    # axes
    ax.grid(color=GRID, linewidth=0.6, alpha=0.8)
    ax.tick_params(colors=MUTED, labelsize=8.5, length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    ticks = np.linspace(0, n - 1, 7).astype(int)
    ax.set_xticks(ticks)
    ax.set_xticklabels([w.index[t].strftime("%d/%m %Hh") for t in ticks])
    ax.yaxis.tick_left()
    ax.set_yticks(np.linspace(ymin, ymax, 7))
    ax.set_yticklabels([_pfmt(v, digits) for v in np.linspace(ymin, ymax, 7)])

    # en-tete
    tf = TF_LABEL.get(sig.tf, sig.tf)
    fig.text(0.05, 0.925, f"{sig.display}", color=WHITE, fontsize=27, fontweight="bold", va="center")
    fig.text(0.05 + 0.017 * len(sig.display) + 0.045, 0.925, f"·  {tf}", color=MUTED, fontsize=17, va="center")
    _pill(fig, 0.05, 0.845, 0.085, 0.05, "ACHAT" if long_ else "VENTE", GREEN if long_ else RED, fs=13)
    _pill(fig, 0.145, 0.845, 0.088, 0.05, f"Grade {sig.grade}", GOLD, fs=12)
    when = pd.Timestamp(sig.signal_time).strftime("%d/%m/%Y  %H:%M UTC")
    fig.text(0.248, 0.870, when, color=MUTED, fontsize=11, va="center")
    fig.text(0.05, 0.045, f"{brand}  ·  {DISCLAIMER}", color=MUTED, fontsize=8.5, va="center")
    _logo(fig)

    # bandeau de resultat
    if result:
        fig.text(0.68, 0.905, result["label"], color=result.get("color", GOLD), fontsize=22, fontweight="bold",
                 ha="center", va="center", zorder=10,
                 bbox=dict(boxstyle="round,pad=0.4", fc=BG, ec=result.get("color", GOLD), lw=1.6))
        if "r" in result:
            fig.text(0.68, 0.852, f"{result['r']:+.2f} R", color=result.get("color", GOLD), fontsize=15,
                     ha="center", va="center", zorder=10)

    fig.savefig(out_path, dpi=100, facecolor=BG)
    plt.close(fig)
    return out_path


def render_recap_card(title: str, subtitle: str, kpis: List[tuple], trades: List[dict],
                      curve: List[float], out_path: str, brand: str = "GOTA TRADING",
                      curve_label: str = "Résultat cumulé (en R)") -> str:
    """kpis : [(libelle, valeur, couleur)] ; trades : [{date, symbol, dir, outcome, r}] ; curve : R cumule."""
    fig = plt.figure(figsize=(10.8, 13.5), dpi=100, facecolor=BG)
    fig.text(0.07, 0.945, brand, color=GOLD, fontsize=15, fontweight="bold")
    fig.text(0.07, 0.91, title, color=WHITE, fontsize=30, fontweight="bold")
    fig.text(0.07, 0.882, subtitle, color=MUTED, fontsize=13)
    _logo(fig, x=0.83, y=0.90, size=0.075)

    # tuiles KPI
    kw, gap = 0.205, 0.0217
    for k, (lab, val, cc) in enumerate(kpis[:4]):
        x0 = 0.07 + k * (kw + gap)
        fig.patches.append(FancyBboxPatch((x0, 0.775), kw, 0.085, boxstyle="round,pad=0.002,rounding_size=0.012",
                                          transform=fig.transFigure, fc=PANEL, ec=GRID, lw=1.0))
        fig.text(x0 + kw / 2, 0.83, str(val), ha="center", va="center", color=cc, fontsize=24, fontweight="bold")
        fig.text(x0 + kw / 2, 0.792, lab, ha="center", va="center", color=MUTED, fontsize=10.5)

    # courbe
    ax = fig.add_axes([0.10, 0.455, 0.83, 0.28], facecolor=PANEL)
    if len(curve) >= 2:
        xs = np.arange(len(curve))
        up = curve[-1] >= 0
        ax.plot(xs, curve, color=GREEN if up else RED, linewidth=2.4)
        ax.fill_between(xs, curve, 0, color=GREEN if up else RED, alpha=0.12)
        ax.axhline(0, color=MUTED, linewidth=0.8)
    else:
        ax.text(0.5, 0.5, "Pas encore assez de trades", color=MUTED, ha="center", va="center", transform=ax.transAxes, fontsize=13)
    ax.set_title(curve_label, color=TXT, fontsize=12, loc="left", pad=10)
    ax.grid(color=GRID, linewidth=0.6)
    ax.tick_params(colors=MUTED, labelsize=9, length=0)
    for s in ax.spines.values():
        s.set_visible(False)

    # liste des trades
    fig.text(0.07, 0.415, "Derniers trades clôturés", color=TXT, fontsize=13, fontweight="bold")
    y = 0.385
    for t in trades[:9]:
        pos = t["r"] >= 0
        cc = GREEN if pos else RED
        fig.text(0.07, y, t["date"], color=MUTED, fontsize=11.5, va="center")
        fig.text(0.24, y, t["symbol"], color=WHITE, fontsize=12.5, fontweight="bold", va="center")
        fig.text(0.42, y, "ACHAT" if t["dir"] == "LONG" else "VENTE", color=GREEN if t["dir"] == "LONG" else RED,
                 fontsize=11, fontweight="bold", va="center")
        fig.text(0.60, y, t["outcome"], color=TXT, fontsize=11.5, va="center")
        fig.text(0.93, y, f"{t['r']:+.2f} R", color=cc, fontsize=13, fontweight="bold", va="center", ha="right")
        fig.add_artist(plt.Line2D([0.07, 0.93], [y - 0.017, y - 0.017], color=GRID, linewidth=0.7, transform=fig.transFigure))
        y -= 0.036
    fig.text(0.5, 0.045, DISCLAIMER, color=MUTED, fontsize=9.5, ha="center", va="center")
    fig.text(0.5, 0.022, "Les résultats passés ne préjugent pas des résultats futurs. Pertes incluses, jamais masquées.",
             color=MUTED, fontsize=9.5, ha="center", va="center")
    fig.savefig(out_path, dpi=100, facecolor=BG)
    plt.close(fig)
    return out_path
