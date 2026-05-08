"""
Volume Profile - distribution horizontale du volume par niveau de prix.

Concepts calcules :
- POC  (Point of Control)   : niveau au volume max
- VAH  (Value Area High)    : borne haute des 70% de volume
- VAL  (Value Area Low)     : borne basse des 70% de volume
- HVN  (High Volume Nodes)  : pics secondaires de volume
- LVN  (Low Volume Nodes)   : creux de volume (zones traversees rapidement)

Resultat : structure VolumeProfileResult avec niveaux + helpers.
"""
from __future__ import annotations
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Tuple


@dataclass
class VolumeProfileResult:
    bins: int
    bin_edges: np.ndarray            # taille bins+1
    profile: np.ndarray              # taille bins (volumes)
    poc: float
    vah: float
    val: float
    value_area_pct: float
    hvn: List[float] = field(default_factory=list)
    lvn: List[float] = field(default_factory=list)
    total_volume: float = 0.0

    def bin_price(self, idx: int) -> float:
        return float((self.bin_edges[idx] + self.bin_edges[idx + 1]) / 2)

    def is_in_value_area(self, price: float) -> bool:
        return self.val <= price <= self.vah


def compute_volume_profile(
    df: pd.DataFrame,
    bins: int = 50,
    value_area_pct: float = 0.70,
) -> VolumeProfileResult:
    """
    Repartit le volume de chaque bougie sur les bins traverses par sa range high-low.
    """
    if df.empty or "volume" not in df.columns:
        raise ValueError("DataFrame doit contenir au moins low/high/volume")

    price_min = float(df["low"].min())
    price_max = float(df["high"].max())
    if price_max == price_min:
        raise ValueError("Range de prix nulle")

    edges = np.linspace(price_min, price_max, bins + 1)
    profile = np.zeros(bins)

    lows = df["low"].to_numpy()
    highs = df["high"].to_numpy()
    vols = df["volume"].to_numpy()

    for i in range(len(df)):
        lo, hi, v = lows[i], highs[i], vols[i]
        if v <= 0 or hi <= lo:
            continue
        lo_bin = max(0, np.searchsorted(edges, lo, side="right") - 1)
        hi_bin = min(bins - 1, np.searchsorted(edges, hi, side="right") - 1)
        n = hi_bin - lo_bin + 1
        if n > 0:
            profile[lo_bin:hi_bin + 1] += v / n

    total = profile.sum()
    poc_idx = int(profile.argmax())
    poc = float((edges[poc_idx] + edges[poc_idx + 1]) / 2)

    # Value Area : on etend autour du POC jusqu'a couvrir value_area_pct du volume
    target = total * value_area_pct
    accum = profile[poc_idx]
    lo_idx = hi_idx = poc_idx
    while accum < target and (lo_idx > 0 or hi_idx < bins - 1):
        next_lo_vol = profile[lo_idx - 1] if lo_idx > 0 else -1
        next_hi_vol = profile[hi_idx + 1] if hi_idx < bins - 1 else -1
        if next_hi_vol >= next_lo_vol and hi_idx < bins - 1:
            hi_idx += 1
            accum += profile[hi_idx]
        elif lo_idx > 0:
            lo_idx -= 1
            accum += profile[lo_idx]
        else:
            break
    val = float(edges[lo_idx])
    vah = float(edges[hi_idx + 1])

    # HVN / LVN : pics et creux locaux dans le profil
    hvn, lvn = _find_nodes(profile, edges, threshold=0.7)

    return VolumeProfileResult(
        bins=bins, bin_edges=edges, profile=profile,
        poc=round(poc, 2), vah=round(vah, 2), val=round(val, 2),
        value_area_pct=value_area_pct,
        hvn=hvn, lvn=lvn,
        total_volume=float(total),
    )


def _find_nodes(profile: np.ndarray, edges: np.ndarray, threshold: float = 0.7):
    """
    HVN : maxima locaux >= threshold * max_volume
    LVN : minima locaux <= (1 - threshold) * max_volume
    """
    if profile.max() == 0:
        return [], []
    max_vol = profile.max()
    hvn, lvn = [], []
    for i in range(1, len(profile) - 1):
        price = (edges[i] + edges[i + 1]) / 2
        if profile[i] > profile[i - 1] and profile[i] > profile[i + 1]:
            if profile[i] >= threshold * max_vol:
                hvn.append(round(float(price), 2))
        if profile[i] < profile[i - 1] and profile[i] < profile[i + 1]:
            if profile[i] <= (1 - threshold) * max_vol:
                lvn.append(round(float(price), 2))
    return hvn, lvn


def ascii_profile(result: VolumeProfileResult, width: int = 40) -> str:
    """
    Rendu ASCII horizontal du Volume Profile (lecture en console).
    """
    lines = []
    max_vol = result.profile.max()
    if max_vol == 0:
        return "(profil vide)"
    for i in range(result.bins - 1, -1, -1):
        price = result.bin_price(i)
        bar_len = int(result.profile[i] / max_vol * width)
        bar = "#" * bar_len
        marks = []
        if abs(price - result.poc) < (result.bin_edges[1] - result.bin_edges[0]):
            marks.append("<- POC")
        elif abs(price - result.vah) < (result.bin_edges[1] - result.bin_edges[0]):
            marks.append("<- VAH")
        elif abs(price - result.val) < (result.bin_edges[1] - result.bin_edges[0]):
            marks.append("<- VAL")
        lines.append(f"  {price:>10,.2f} | {bar:<{width}s}  {' '.join(marks)}")
    return "\n".join(lines)


def print_volume_profile_summary(df: pd.DataFrame, bins: int = 30) -> VolumeProfileResult:
    vp = compute_volume_profile(df, bins=bins)
    last = float(df["close"].iloc[-1])
    print(f"\n[VOLUME PROFILE]  {bins} bins, value area {int(vp.value_area_pct * 100)}%")
    print(f"  POC : {vp.poc:,.2f}     VAH : {vp.vah:,.2f}     VAL : {vp.val:,.2f}")
    print(f"  Prix actuel : {last:,.2f}  ->  "
          f"{'DANS la value area' if vp.is_in_value_area(last) else 'HORS value area'}")
    if vp.hvn:
        print(f"  HVN (zones de forte activite) : {vp.hvn[:8]}")
    if vp.lvn:
        print(f"  LVN (zones de transit rapide)  : {vp.lvn[:8]}")
    print(f"\n  Profil :")
    print(ascii_profile(vp, width=40))
    return vp
