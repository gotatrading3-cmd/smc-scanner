"""
signal_engine.py - Moteur de signaux "GOTA Confluence" (multi-confirmations).

Aucune entree/sortie ici : on donne des DataFrames OHLC(V), on recupere des Signal.
Le MEME code sert au LIVE (derniere bougie fermee) et au BACKTEST (toutes les
bougies) -> les statistiques annoncees correspondent exactement a ce qui est poste.
A la bougie i on n'utilise QUE les bougies <= i (aucun regard vers le futur).

STRATEGIE - 4 conditions obligatoires + 6 confirmations notees (score /6)
  Obligatoires :
    1. Tendance H4 alignee   : EMA50 > EMA200 et cloture > EMA200 (achat) / inverse (vente)
    2. Retest d'une zone     : Order Block ou Fair Value Gap frais, dans le sens de la tendance
    3. Bougie de rejet       : cloture dans le sens du trade, au-dela du milieu de la zone,
                               avec meche de rejet ou cloture au-dela de la bougie precedente
    4. Risque sain           : stop entre 0.6 et 2.5 ATR, spread <= 12% du risque
  Confirmations (1 point chacune) :
    - sweep    : balayage de liquidite (swing pris puis reprise) juste avant
    - stack    : zone empilee OB + FVG
    - volume   : zone sur le POC / un noeud de volume (Volume Profile)
    - momentum : RSI(14) sain et en retournement
    - discount : achat en zone Discount / vente en zone Premium (moitie du range)
    - session  : bougie cloturee pendant Londres / New York (07h-20h UTC)
  Publication a partir de min_score (defaut 4/6). Grade A+ a partir de 5/6.

GESTION : TP1 = 1R (40%), TP2 = 2R (30%), TP3 = 3R (30%). Apres TP1 le stop passe a
l'entree. Expiration apres 48 bougies. Egalite SL/TP dans la meme bougie => SL d'abord.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from volume_profile import compute_volume_profile

PARAMS = dict(
    swing_k=3,              # fractal : k bougies de chaque cote
    atr_len=14,
    impulse_atr=1.5,        # deplacement mini de la jambe qui casse la structure (OB)
    fvg_min_atr=0.25,       # taille mini d'un FVG
    fvg_body_atr=0.6,       # corps mini de la bougie centrale du FVG
    zone_max_age=120,       # duree de vie d'une zone (bougies)
    max_touches=2,          # retests toleres (1 = premier retest uniquement)
    wick_ratio=0.35,        # meche de rejet mini (part de la range)
    sl_buffer_atr=0.2,
    sl_min_atr=0.6,
    sl_max_atr=2.5,
    max_spread_risk=0.12,   # spread / risque
    min_score=4,            # sur 6
    a_plus_score=5,
    tp_rr=(1.0, 2.0, 3.0),
    tp_split=(0.4, 0.3, 0.3),
    expiry_bars=48,
    cooldown_bars=6,
    vp_lookback=200,
    vp_bins=30,
    warmup=250,
)

CHECK_LABELS = {
    "htf_trend": "Tendance H4 alignee",
    "zone_retest": "Retest d'une zone fraiche",
    "confirmation": "Bougie de rejet confirmee",
    "risk_ok": "Risque / spread sains",
    "sweep": "Balayage de liquidite",
    "stack": "Zone empilee OB + FVG",
    "volume": "Volume Profile (POC / noeud de volume)",
    "momentum": "Momentum RSI sain",
    "discount": "Zone Discount / Premium",
    "session": "Session Londres / New York",
}
SCORED = ("sweep", "stack", "volume", "momentum", "discount", "session")


@dataclass
class Signal:
    symbol: str                 # nom MT5 (ex: GOLD)
    display: str                # nom public (ex: XAUUSD)
    direction: str              # "LONG" | "SHORT"
    tf: str                     # "1h"
    bar_open: str               # ISO UTC ouverture de la bougie de signal
    signal_time: str            # ISO UTC cloture de la bougie (= instant de publication)
    entry: float
    sl: float
    tps: List[float]
    risk: float                 # |entry - sl|
    atr: float
    spread: float               # spread en prix au moment du signal
    score: int
    grade: str
    checks: Dict[str, bool]
    zone_kind: str              # "OB" | "FVG"
    zone_lo: float
    zone_hi: float
    zone_born: str              # ISO UTC
    rsi: float
    poc: Optional[float] = None
    id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Signal":
        return Signal(**d)


# ------------------------------------------------------------------ indicateurs
def _wilder(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).ewm(alpha=1.0 / n, adjust=False).mean().to_numpy()


def _rsi(c: np.ndarray, n: int = 14) -> np.ndarray:
    d = np.diff(c, prepend=c[0])
    up = np.where(d > 0, d, 0.0)
    dn = np.where(d < 0, -d, 0.0)
    au, ad = _wilder(up, n), _wilder(dn, n)
    rs = np.divide(au, ad, out=np.full_like(au, np.inf), where=ad > 0)
    return 100.0 - 100.0 / (1.0 + rs)


def htf_bias(df_htf: pd.DataFrame, entry_index: pd.DatetimeIndex, entry_minutes: int,
             htf_minutes: int = 240, fast: int = 50, slow: int = 200) -> np.ndarray:
    """Biais de tendance H4 aligne sur chaque bougie d'entree (+1 / -1 / 0).
    On n'utilise que la derniere bougie H4 DEJA CLOTUREE a l'instant de cloture de
    la bougie d'entree -> pas de regard vers le futur."""
    c = df_htf["close"]
    ef = c.ewm(span=fast, adjust=False).mean()
    es = c.ewm(span=slow, adjust=False).mean()
    b = np.where((ef > es) & (c > es), 1, np.where((ef < es) & (c < es), -1, 0)).astype(np.int8)
    b[: slow + 20] = 0
    htf_close = df_htf.index + pd.Timedelta(minutes=htf_minutes)
    entry_close = entry_index + pd.Timedelta(minutes=entry_minutes)
    pos = htf_close.searchsorted(entry_close, side="right") - 1
    return np.where(pos >= 0, b[np.clip(pos, 0, None)], 0).astype(np.int8)


# ------------------------------------------------------------------ moteur
def analyze(df: pd.DataFrame, bias: np.ndarray, point: float, symbol: str, display: str,
            tf_minutes: int = 60, asset_class: str = "fx", params: Optional[dict] = None,
            only_last: bool = False, min_score: Optional[int] = None,
            watch: Optional[list] = None) -> List[Signal]:
    """df : index = ouverture de bougie (UTC naif) ; colonnes open high low close
    tick_volume spread(points). bias : tableau aligne sur df (htf_bias)."""
    P = {**PARAMS, **(params or {})}
    ms = P["min_score"] if min_score is None else min_score
    n = len(df)
    if n < P["warmup"] + 10:
        return []
    K = P["swing_k"]
    o = df["open"].to_numpy(float).tolist()
    h = df["high"].to_numpy(float).tolist()
    l = df["low"].to_numpy(float).tolist()
    c = df["close"].to_numpy(float).tolist()
    vol = (df["tick_volume"] if "tick_volume" in df else df["volume"]).to_numpy(float)
    spr = ((df["spread"].to_numpy(float) * point) if "spread" in df else np.zeros(n)).tolist()
    atr = _wilder(np.maximum.reduce([
        np.array(h) - np.array(l),
        np.abs(np.array(h) - np.r_[c[0], c[:-1]]),
        np.abs(np.array(l) - np.r_[c[0], c[:-1]])]), P["atr_len"]).tolist()
    rsi = _rsi(np.array(c)).tolist()
    times = df.index
    tf_td = pd.Timedelta(minutes=tf_minutes)
    bias_l = bias.tolist()
    is_crypto = asset_class == "crypto"

    swing_hi: List[tuple] = []
    swing_lo: List[tuple] = []
    unb_hi = None
    unb_lo = None
    zones: List[dict] = []
    used_ob = set()
    last_sig_i = -10 ** 9
    out: List[Signal] = []
    emit_from = (n - 1) if only_last else P["warmup"]

    for i in range(2 * K + 2, n):
        a = atr[i]
        # 1) swings confirmes (fractals)
        j = i - K
        if j - K >= 0:
            if h[j] > max(h[j - K:j]) and h[j] >= max(h[j + 1:j + K + 1]):
                swing_hi.append((j, h[j]))
                unb_hi = (j, h[j])
            if l[j] < min(l[j - K:j]) and l[j] <= min(l[j + 1:j + K + 1]):
                swing_lo.append((j, l[j]))
                unb_lo = (j, l[j])

        # 2) cassure de structure -> Order Block
        if unb_hi is not None and c[i] > unb_hi[1]:
            sh_idx = unb_hi[0]
            seg = l[sh_idx:i + 1]
            m = sh_idx + seg.index(min(seg))
            if c[i] - l[m] >= P["impulse_atr"] * a:
                q_ob = None
                for q in range(m, max(m - 4, -1), -1):
                    if c[q] < o[q]:
                        q_ob = q
                        break
                if q_ob is None:
                    q_ob = m
                if (q_ob, 1) not in used_ob:
                    used_ob.add((q_ob, 1))
                    zones.append(dict(kind="OB", dir=1, lo=l[q_ob], hi=h[q_ob], born=i,
                                      touches=0, inz=False, dead=False, used=False, origin=q_ob))
            unb_hi = None
        if unb_lo is not None and c[i] < unb_lo[1]:
            sl_idx = unb_lo[0]
            seg = h[sl_idx:i + 1]
            m = sl_idx + seg.index(max(seg))
            if h[m] - c[i] >= P["impulse_atr"] * a:
                q_ob = None
                for q in range(m, max(m - 4, -1), -1):
                    if c[q] > o[q]:
                        q_ob = q
                        break
                if q_ob is None:
                    q_ob = m
                if (q_ob, -1) not in used_ob:
                    used_ob.add((q_ob, -1))
                    zones.append(dict(kind="OB", dir=-1, lo=l[q_ob], hi=h[q_ob], born=i,
                                      touches=0, inz=False, dead=False, used=False, origin=q_ob))
            unb_lo = None

        # 3) Fair Value Gap
        if i >= 2:
            if (l[i] > h[i - 2] and (c[i - 1] - o[i - 1]) >= P["fvg_body_atr"] * a
                    and (l[i] - h[i - 2]) >= P["fvg_min_atr"] * a):
                zones.append(dict(kind="FVG", dir=1, lo=h[i - 2], hi=l[i], born=i,
                                  touches=0, inz=False, dead=False, used=False, origin=i - 1))
            if (h[i] < l[i - 2] and (o[i - 1] - c[i - 1]) >= P["fvg_body_atr"] * a
                    and (l[i - 2] - h[i]) >= P["fvg_min_atr"] * a):
                zones.append(dict(kind="FVG", dir=-1, lo=h[i], hi=l[i - 2], born=i,
                                  touches=0, inz=False, dead=False, used=False, origin=i - 1))

        # 4) recherche de signal (AVANT la mise a jour des zones avec la bougie courante)
        if (i >= emit_from and bias_l[i] != 0 and (i - last_sig_i) >= P["cooldown_bars"]
                and _market_ok(times[i] + tf_td, asset_class)):
            d = int(bias_l[i])
            best = None
            rng = h[i] - l[i]
            if rng > 0:
                for z in zones:
                    if z["dead"] or z["used"] or z["dir"] != d or (i - z["born"]) < 2:
                        continue
                    mid = (z["lo"] + z["hi"]) / 2.0
                    if d == 1:
                        if not (l[i] <= z["hi"] and c[i] > z["lo"] and l[i] >= z["lo"] - 0.6 * a):
                            continue
                        wick = (min(o[i], c[i]) - l[i]) / rng
                        confirm = c[i] > o[i] and c[i] >= mid and (wick >= P["wick_ratio"] or c[i] > h[i - 1])
                    else:
                        if not (h[i] >= z["lo"] and c[i] < z["hi"] and h[i] <= z["hi"] + 0.6 * a):
                            continue
                        wick = (h[i] - max(o[i], c[i])) / rng
                        confirm = c[i] < o[i] and c[i] <= mid and (wick >= P["wick_ratio"] or c[i] < l[i - 1])
                    if not confirm:
                        continue
                    episode = z["touches"] + (0 if z["inz"] else 1)
                    if episode > P["max_touches"]:
                        continue
                    entry = c[i]
                    if d == 1:
                        sl = min(z["lo"], l[i]) - P["sl_buffer_atr"] * a
                        risk = entry - sl
                    else:
                        sl = max(z["hi"], h[i]) + P["sl_buffer_atr"] * a
                        risk = sl - entry
                    if not (P["sl_min_atr"] * a <= risk <= P["sl_max_atr"] * a):
                        continue
                    if spr[i] > P["max_spread_risk"] * risk:
                        continue
                    chk = _score(z, zones, d, i, h, l, c, o, rsi, swing_hi, swing_lo, K,
                                 times[i] + tf_td, df, vol, a, P)
                    sc = sum(chk[k] for k in SCORED)
                    if sc < ms:
                        continue
                    cand = (sc, -risk / a, z, chk, entry, sl, risk)
                    if best is None or cand[:2] > best[:2]:
                        best = cand
            if best is not None:
                sc, _, z, chk, entry, sl, risk = best
                z["used"] = True
                last_sig_i = i
                poc_v = chk.pop("_poc", None)
                tps = [entry + d * r * risk for r in P["tp_rr"]]
                allchk = {"htf_trend": True, "zone_retest": True, "confirmation": True,
                          "risk_ok": True, **chk}
                sig_close = times[i] + tf_td
                out.append(Signal(
                    symbol=symbol, display=display, direction="LONG" if d == 1 else "SHORT",
                    tf=f"{tf_minutes}m", bar_open=times[i].isoformat(), signal_time=sig_close.isoformat(),
                    entry=entry, sl=sl, tps=tps, risk=risk, atr=a, spread=spr[i], score=sc,
                    grade="A+" if sc >= P["a_plus_score"] else "A", checks=allchk,
                    zone_kind=z["kind"], zone_lo=z["lo"], zone_hi=z["hi"],
                    zone_born=times[z["born"]].isoformat(), rsi=rsi[i], poc=poc_v,
                    id=f"{display}-{sig_close:%Y%m%d%H%M}-{'L' if d == 1 else 'S'}"))

        # 5) mise a jour des zones avec la bougie courante
        for z in zones:
            if z["dead"]:
                continue
            if i - z["born"] > P["zone_max_age"]:
                z["dead"] = True
                continue
            if z["dir"] == 1:
                tapped = i > z["born"] and l[i] <= z["hi"]
                if tapped and not z["inz"]:
                    z["touches"] += 1
                z["inz"] = tapped
                if c[i] < z["lo"]:
                    z["dead"] = True
            else:
                tapped = i > z["born"] and h[i] >= z["lo"]
                if tapped and not z["inz"]:
                    z["touches"] += 1
                z["inz"] = tapped
                if c[i] > z["hi"]:
                    z["dead"] = True
        if len(zones) > 60:
            zones = [z for z in zones if not z["dead"]][-40:]

    # Zone d'interet proche (pour les posts "on surveille") : zone fraiche, dans le sens de la tendance, a moins de 1 ATR du prix
    if watch is not None and n > 0 and bias_l[-1] != 0:
        d, a, last_c = int(bias_l[-1]), atr[-1], c[-1]
        best = None
        for z in zones:
            if z["dead"] or z["used"] or z["dir"] != d:
                continue
            dist = max(0.0, (last_c - z["hi"]) if d == 1 else (z["lo"] - last_c))
            if dist <= 1.0 * a and (best is None or dist < best["dist_atr"] * a):
                best = {"symbol": symbol, "display": display, "kind": z["kind"], "dir": d, "dist_atr": round(dist / a, 2)}
        if best:
            watch.append(best)
    return out


def _market_ok(close_ts: pd.Timestamp, asset_class: str) -> bool:
    if asset_class == "crypto":
        return True
    wd, hr = close_ts.weekday(), close_ts.hour
    if wd >= 5 or (wd == 4 and hr >= 20):
        return False
    if hr >= 21 or hr < 1:      # rollover / spreads larges
        return False
    return True


def _score(z, zones, d, i, h, l, c, o, rsi, swing_hi, swing_lo, K, close_ts, df, vol, a, P) -> dict:
    mid = (z["lo"] + z["hi"]) / 2.0
    # sweep : un swing prealablement confirme est pris par une meche puis repris (6 dernieres bougies)
    sweep = False
    pool = swing_lo[-12:] if d == 1 else swing_hi[-12:]
    for jj in range(i, max(i - 6, 0) - 1, -1):
        for sidx, lvl in reversed(pool):
            if sidx > jj - K - 1:
                continue
            if i - sidx > 80:
                break
            if d == 1 and l[jj] < lvl < c[jj]:
                sweep = True
            elif d == -1 and h[jj] > lvl > c[jj]:
                sweep = True
            if sweep:
                break
        if sweep:
            break
    # stack : OB + FVG qui se chevauchent
    stack = any((not z2["dead"]) and z2["dir"] == d and z2["kind"] != z["kind"]
                and max(z["lo"], z2["lo"]) <= min(z["hi"], z2["hi"]) for z2 in zones)
    # volume profile : zone sur le POC ou un noeud de volume
    volume, poc = False, None
    lo_i = max(0, i + 1 - P["vp_lookback"])
    try:
        # pas de volume (ex. forex Yahoo) => pas de Volume Profile : on ne fabrique pas un faux POC
        if float(vol[lo_i:i + 1].sum()) > 0:
            seg = df.iloc[lo_i:i + 1]
            vp_df = pd.DataFrame({"low": seg["low"].to_numpy(), "high": seg["high"].to_numpy(),
                                  "volume": vol[lo_i:i + 1]})
            vp = compute_volume_profile(vp_df, bins=P["vp_bins"])
            poc = float(vp.poc)
            volume = abs(mid - vp.poc) <= 1.0 * a or any(abs(mid - x) <= 1.0 * a for x in vp.hvn)
    except Exception:
        pass
    # momentum RSI
    r0, r1 = rsi[i], rsi[i - 1]
    momentum = (28 <= r0 <= 60 and r0 > r1 + 0.5) if d == 1 else (40 <= r0 <= 72 and r0 < r1 - 0.5)
    # premium / discount sur les 100 dernieres bougies
    lo_i2 = max(0, i - 99)
    eq = (max(h[lo_i2:i + 1]) + min(l[lo_i2:i + 1])) / 2.0
    discount = (mid < eq) if d == 1 else (mid > eq)
    # session Londres / New York (07h-20h UTC)
    session = 7 <= close_ts.hour < 20 and close_ts.weekday() < 5
    return {"sweep": sweep, "stack": stack, "volume": volume, "momentum": momentum,
            "discount": discount, "session": session, "_poc": poc}


# ------------------------------------------------------------------ simulation d'un trade
class TradeSim:
    """Machine d'etat d'un trade signale, alimentee bougie par bougie
    (backtest : bougies H1 ; live : bougies M5). Regles conservatrices :
    - spread pris en compte (achat rempli au ask, vente evaluee sur le ask)
    - SL et TP touches dans la meme bougie => SL d'abord
    - le stop passe a l'entree A PARTIR DE LA BOUGIE SUIVANT TP1
    Resultat en R (1R = risque annonce). Partiels : 40% TP1 / 30% TP2 / 30% TP3."""

    def __init__(self, sig: Signal, params: Optional[dict] = None):
        P = {**PARAMS, **(params or {})}
        self.P = P
        self.long = sig.direction == "LONG"
        self.entry = sig.entry
        self.spread = sig.spread
        self.risk = sig.risk
        self.tps = list(sig.tps)
        self.entry_eff = sig.entry + (sig.spread if self.long else 0.0)
        self.sl_cur = sig.sl
        self.remaining = 1.0
        self.hit = [False, False, False]
        self.r = 0.0
        self.closed = False
        self.outcome: Optional[str] = None
        self.start = pd.Timestamp(sig.signal_time)
        self.expiry = pd.Timedelta(minutes=P["expiry_bars"] * int(sig.tf.rstrip("m")))
        self.close_time: Optional[str] = None
        self.be_armed = False        # stop deplace a l'entree (effectif des la bougie suivante)
        self.last_ts: Optional[str] = None

    # -- persistance
    def to_dict(self) -> dict:
        d = {k: getattr(self, k) for k in ("long", "entry", "spread", "risk", "tps", "entry_eff", "sl_cur",
                                          "remaining", "hit", "r", "closed", "outcome", "close_time",
                                          "be_armed", "last_ts")}
        d["start"] = self.start.isoformat()
        d["expiry_min"] = self.expiry.total_seconds() / 60
        return d

    @staticmethod
    def from_dict(d: dict, params: Optional[dict] = None) -> "TradeSim":
        t = TradeSim.__new__(TradeSim)
        t.P = {**PARAMS, **(params or {})}
        for k in ("long", "entry", "spread", "risk", "tps", "entry_eff", "sl_cur", "remaining", "hit", "r",
                  "closed", "outcome", "close_time", "be_armed", "last_ts"):
            setattr(t, k, d[k])
        t.start = pd.Timestamp(d["start"])
        t.expiry = pd.Timedelta(minutes=d["expiry_min"])
        return t

    def _pnl(self, price: float) -> float:
        return ((price - self.entry_eff) if self.long else (self.entry_eff - price)) / self.risk

    def feed(self, ts: pd.Timestamp, hi: float, lo: float, cl: float, bar_minutes: int = 60) -> List[dict]:
        if self.closed:
            return []
        if self.last_ts is not None and pd.Timestamp(self.last_ts) >= ts:
            return []
        self.last_ts = ts.isoformat()
        ev: List[dict] = []
        sp = self.spread
        h_e, l_e = (hi, lo) if self.long else (hi + sp, lo + sp)
        # 1) stop (SL d'abord)
        stop_hit = (l_e <= self.sl_cur) if self.long else (h_e >= self.sl_cur)
        if stop_hit:
            self.r += self.remaining * self._pnl(self.sl_cur)
            typ = "BE" if self.be_armed else "SL"
            ev.append({"type": typ, "price": self.sl_cur, "time": ts.isoformat()})
            self._close(ts, "TP%d" % max([k + 1 for k in range(3) if self.hit[k]]) if any(self.hit) else "SL")
            return ev
        # 2) objectifs (dans l'ordre)
        newly_tp1 = False
        for k in range(3):
            if self.hit[k]:
                continue
            reached = (h_e >= self.tps[k]) if self.long else (l_e <= self.tps[k])
            if not reached:
                break
            self.hit[k] = True
            part = self.P["tp_split"][k]
            self.r += part * self._pnl(self.tps[k])
            self.remaining -= part
            ev.append({"type": f"TP{k + 1}", "price": self.tps[k], "time": ts.isoformat()})
            if k == 0:
                newly_tp1 = True
        if newly_tp1:
            self.sl_cur = self.entry_eff           # stop a l'entree des la bougie suivante
            self.be_armed = True
        if self.remaining <= 1e-9:
            self._close(ts, "TP3")
            return ev
        # 3) expiration (a la cloture de la bougie qui atteint la duree max)
        if ts + pd.Timedelta(minutes=bar_minutes) - self.start >= self.expiry:
            self.r += self.remaining * self._pnl(cl)
            ev.append({"type": "EXPIRED", "price": cl, "time": ts.isoformat()})
            self._close(ts, ("TP%d" % max([k + 1 for k in range(3) if self.hit[k]])) if any(self.hit) else "EXPIRED")
        return ev

    def _close(self, ts: pd.Timestamp, outcome: str) -> None:
        self.closed = True
        self.remaining = 0.0
        self.outcome = outcome
        self.close_time = ts.isoformat()


# ------------------------------------------------------------------ statistiques
def summarize(trades: List[dict]) -> dict:
    """trades : liste de dict avec au moins 'r' et 'outcome'."""
    n = len(trades)
    if n == 0:
        return {"n": 0}
    r = np.array([t["r"] for t in trades], float)
    outc = [t["outcome"] for t in trades]
    wins = sum(o in ("TP1", "TP2", "TP3") for o in outc)
    pos, neg = r[r > 0].sum(), -r[r < 0].sum()
    eq = np.cumsum(r)
    dd = float((np.maximum.accumulate(eq) - eq).max()) if n else 0.0
    se = float(r.std(ddof=1) / np.sqrt(n)) if n > 1 else 0.0
    return {
        "n": n,
        "win_rate": wins / n * 100,
        "tp1": sum(o in ("TP1", "TP2", "TP3") for o in outc) / n * 100,
        "tp2": sum(o in ("TP2", "TP3") for o in outc) / n * 100,
        "tp3": sum(o == "TP3" for o in outc) / n * 100,
        "sl": sum(o == "SL" for o in outc) / n * 100,
        "expired": sum(o == "EXPIRED" for o in outc) / n * 100,
        "avg_r": float(r.mean()),
        "avg_r_se": se,
        "total_r": float(r.sum()),
        "profit_factor": float(pos / neg) if neg > 0 else float("inf"),
        "max_dd_r": dd,
    }
