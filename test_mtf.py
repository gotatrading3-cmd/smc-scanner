"""
test_mtf.py - Tests HORS CONNEXION de l'analyse multi-timeframes (aucun reseau, aucun Telegram, aucun MT5).

    python test_mtf.py

Verifie : (1) les zones d'un timeframe superieur (duree de vie, sens, fraicheur, aucun regard vers le futur) ; (2) que l'analyse d'origine
H4 / H1 n'a PAS change d'un seul signal ; (3) le suivi des positions virtuelles (TP1 / TP2 / TP3 / stop, doublons, plafonds) ;
(4) que le mode observation ne peut ni planter le robot principal, ni publier quoi que ce soit.
"""
import io
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).parent
TMP = Path(tempfile.mkdtemp(prefix="test_mtf_"))
os.environ["SIGNALS_DATA_DIR"] = str(TMP)
os.environ["SIGNAL_BACKEND"] = "yahoo"
os.environ.pop("SIGNALS_OBSERVE", None)
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import signal_data as sd  # noqa: E402
import signal_engine as eng  # noqa: E402
from signal_engine import HtfZones, Signal, TradeSim, analyze, htf_bias, htf_zones_of  # noqa: E402
import signal_mtf as mtf  # noqa: E402

ok = 0


def check(nom, cond, detail=""):
    global ok
    print(("  OK   " if cond else "  ECHEC ") + nom + (f"  [{detail}]" if detail and not cond else ""))
    ok += bool(cond)
    assert cond, nom


def frame(n, minutes, start="2026-01-05", seed=1, spread=0.0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq=f"{minutes}min")
    close = 1.1 + np.cumsum(rng.normal(0, 0.0005, n))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + rng.uniform(0, 0.0003, n)
    low = np.minimum(open_, close) - rng.uniform(0, 0.0003, n)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "tick_volume": 0.0, "spread": spread}, index=idx)


def mk_signal(i_time, profile="scalp_m15", direction="LONG", entry=1.1000, risk=0.0010, tf="15m", symbol="EURUSD", spread=0.0, score=4):
    d = 1 if direction == "LONG" else -1
    tfm = int(tf.rstrip("m"))
    t = pd.Timestamp(i_time)
    close = t + pd.Timedelta(minutes=tfm)
    return Signal(symbol=symbol, display=symbol, direction=direction, tf=tf, bar_open=t.isoformat(), signal_time=close.isoformat(),
                  entry=entry, sl=entry - d * risk, tps=[entry + d * k * risk for k in (1, 2, 3)], risk=risk, atr=risk,
                  spread=spread, score=score, grade="A", checks={"htf_zone": True}, zone_kind="OB", zone_lo=entry - risk, zone_hi=entry,
                  zone_born=t.isoformat(), rsi=50.0, id=f"{symbol}-{close:%Y%m%d%H%M}-{'L' if d == 1 else 'S'}-{profile}",
                  profile=profile, ctx={"tf": "H4", "kind": "OB", "lo": entry - 2 * risk, "hi": entry + risk})


print("A) zones d'un timeframe superieur : duree de vie, sens, fraicheur")
idx = pd.date_range("2026-01-01", periods=100, freq="4h")
td = pd.Timedelta(hours=4)
log = [dict(kind="OB", dir=1, lo=1.0, hi=1.1, born=10, dead_i=None, touch_i=[]),
       dict(kind="FVG", dir=-1, lo=2.0, hi=2.1, born=10, dead_i=30, touch_i=[]),
       dict(kind="OB", dir=1, lo=1.5, hi=1.6, born=10, dead_i=None, touch_i=[15, 20, 25]),
       dict(kind="OB", dir=1, lo=3.0, hi=3.1, born=50, dead_i=40, touch_i=[])]            # incoherente (morte avant d'etre nee) : ignoree
hz = HtfZones(log, idx, 240, "H4", max_touches=2)
born = (idx[10] + td).value
check("une zone incoherente est ignoree", len(hz) == 3, len(hz))
check("la zone n'existe pas AVANT la cloture de la bougie qui la cree (aucun regard vers le futur)", hz.find(1, born - 1, 1.0, 1.1) is None)
check("... elle existe a cette cloture", hz.find(1, born, 1.05, 1.06) is not None and hz.find(1, born, 1.05, 1.06)["kind"] == "OB")
check("le sens compte : zone haussiere inutilisable pour une vente", hz.find(-1, born, 1.05, 1.06) is None)
check("la fourchette doit TOUCHER la zone", hz.find(1, born + 1, 1.2, 1.3) is None and hz.find(1, born + 1, 0.9, 1.0) is not None)
dead = (idx[30] + td).value
check("une zone invalidee disparait a la cloture de la bougie qui l'invalide", hz.find(-1, dead - 1, 2.05, 2.06) is not None and hz.find(-1, dead, 2.05, 2.06) is None)
exh = (idx[20] + td).value
check("apres le 2e retest, la zone n'est plus fraiche", hz.find(1, exh - 1, 1.55, 1.56) is not None and hz.find(1, exh, 1.55, 1.56) is None)
check("la zone la plus recente est retournee quand plusieurs correspondent", hz.find(1, born + 1, 1.0, 1.6)["lo"] == 1.5)
check("aucune zone : rien, jamais d'erreur", HtfZones([], idx, 240).find(1, 0, 0, 1) is None and len(HtfZones([], idx, 240)) == 0)
hz_age = HtfZones([dict(kind="OB", dir=1, lo=1.0, hi=1.1, born=10, dead_i=None, touch_i=[])], idx, 240, "H4", max_touches=2, max_age=5)
t_age = (idx[16] + td).value
check("age maximum (meme regle que le moteur) : la zone disparait a la cloture de sa 6e bougie apres sa naissance, meme sans fin de vie enregistree",
      hz_age.find(1, t_age - 1, 1.05, 1.06) is not None and hz_age.find(1, t_age, 1.05, 1.06) is None)

print("B) l'analyse d'origine n'a pas change, le contexte multi-timeframes n'ajoute que des champs")
cache = sd.CACHE_DIR


def cached(name, tf):
    fs = sorted(cache.glob(f"{name}_{tf}_*.pkl"), key=lambda f: int(f.stem.rsplit("_", 1)[-1]))
    return pd.read_pickle(fs[-1]) if fs else None


d4, dd, d1h = cached("EURUSD", "4h"), cached("EURUSD", "1d"), cached("EURUSD", "1h")
meta = sd._meta_all().get("EURUSD")
if d4 is None or dd is None or d1h is None or not meta:
    print("  (donnees en cache absentes : tests B ignores)")
else:
    b4 = htf_bias(dd, d4.index, 240, htf_minutes=1440)
    s_ref = analyze(d4, b4, meta["point"], "EURUSD", "EURUSD", 240, "fx", min_score=2)
    check("garde-fou de regression : EURUSD H4/D1 donne toujours exactement 314 signaux (min 2)", len(s_ref) == 314, len(s_ref))
    b1 = htf_bias(d4, d1h.index, 60, htf_minutes=240)
    check("garde-fou de regression : EURUSD H1/H4 donne toujours exactement 457 signaux (min 2)",
          len(analyze(d1h, b1, meta["point"], "EURUSD", "EURUSD", 60, "fx", min_score=2)) == 457)
    check("les signaux d'origine n'ont ni profil ni contexte et gardent leur identifiant", all(s.profile == "" and s.ctx == {} and not s.id.endswith("-") for s in s_ref[:50]))
    hz4 = htf_zones_of(d4, 240, "H4", meta["point"], "fx")
    check("les zones H4 sont detectees (OB et FVG, avec duree de vie)", hz4 is not None and len(hz4) > 100 and set(hz4.kind) == {"OB", "FVG"} and (hz4.t1 > hz4.t0).all(), len(hz4))
    s_ctx = analyze(d4, b4, meta["point"], "EURUSD", "EURUSD", 240, "fx", min_score=2, htf_zones=hz4)
    strip = lambda s: {k: v for k, v in s.to_dict().items() if k not in ("checks", "ctx")}          # noqa: E731
    check("fournir les zones SANS les exiger ne change aucun signal", [strip(s) for s in s_ctx] == [strip(s) for s in s_ref])
    check("... et ajoute seulement le champ htf_zone dans les confirmations", all("htf_zone" in s.checks for s in s_ctx) and all("htf_zone" not in s.checks for s in s_ref))
    d1h_bias = htf_bias(dd, d1h.index, 60, htf_minutes=1440)
    s_req = analyze(d1h, d1h_bias, meta["point"], "EURUSD", "EURUSD", 60, "fx", params={"require_htf_zone": True}, min_score=2, htf_zones=hz4, profile="intra_h1")
    check("exiger la zone H4 : des signaux existent, tous dans une zone H4, avec leur contexte et leur profil",
          len(s_req) > 20 and all(s.checks["htf_zone"] and s.ctx["tf"] == "H4" and s.profile == "intra_h1" and s.id.endswith("-intra_h1") for s in s_req), len(s_req))
    ok_overlap = True
    for s in s_req:
        row = d1h.loc[pd.Timestamp(s.bar_open)]
        ok_overlap &= row["low"] <= s.ctx["hi"] and row["high"] >= s.ctx["lo"]
    check("la bougie d'entree touche bien la zone H4 citee", ok_overlap)
    d_dir = {s.id: (1 if s.direction == "LONG" else -1) for s in s_req}
    check("le sens du signal est celui de la tendance journaliere", all(d_dir[s.id] == int(d1h_bias[d1h.index.get_loc(pd.Timestamp(s.bar_open))]) for s in s_req))

print("C) compatibilite des signaux enregistres")
sg = mk_signal("2026-01-05 10:00")
check("aller-retour (avec le contexte)", Signal.from_dict(sg.to_dict()) == sg)
ancien = {k: v for k, v in sg.to_dict().items() if k not in ("profile", "ctx")}
check("un signal enregistre AVANT cette version se relit (profil vide)", Signal.from_dict(ancien).profile == "" and Signal.from_dict(ancien).ctx == {})
check("un champ inconnu (ecrit par une autre version) est ignore", Signal.from_dict({**sg.to_dict(), "champ_futur": 1}) == sg)
check("une position M30 expire apres 48 bougies M30 (24 h)", TradeSim(mk_signal("2026-01-05 10:00", tf="30m")).expiry == pd.Timedelta(hours=24))
check("une position M15 expire apres 12 h", TradeSim(sg).expiry == pd.Timedelta(hours=12))

print("D) donnees : M30 = deux bougies M15 regroupees (:00 et :30)")
m15 = frame(8, 15, start="2026-01-05 10:00")
m30 = sd._resample(m15, 30)
check("4 bougies M30 alignees sur :00 / :30", len(m30) == 4 and list(m30.index.minute) == [0, 30, 0, 30], list(m30.index))
check("ouverture = 1re, cloture = derniere, extremes = max / min", m30.iloc[0]["open"] == m15.iloc[0]["open"] and m30.iloc[0]["close"] == m15.iloc[1]["close"]
      and m30.iloc[0]["high"] == m15.iloc[:2]["high"].max() and m30.iloc[0]["low"] == m15.iloc[:2]["low"].min())
check("les horloges M30 / M15 sont declarees", sd.TF_MIN["30m"] == 30 and sd.TF_LABEL[30] == "M30")

print("E) positions virtuelles : ouverture, garde-fous, suivi, resultats")
mtf.SHADOW_FILE = TMP / "signals_shadow.jsonl"
now = pd.Timestamp("2026-01-05 12:00")
st = {"sched": {}}
s1 = mk_signal("2026-01-05 11:45")
check("ouverture d'une position virtuelle", mtf.register(s1, st, now) and s1.id in st["mtf"]["active"])
check("pas de doublon (meme identifiant)", mtf.register(s1, st, now) is False)
s1b = mk_signal("2026-01-05 11:30", entry=1.1050)
check("une seule position a la fois par (symbole, profil)", mtf.register(s1b, st, now) is False)
check("un autre profil sur le meme symbole est accepte", mtf.register(mk_signal("2026-01-05 11:30", profile="scalp_m30", tf="30m"), st, now))
mtf.MAX_PER_DAY = 2
check("plafond du jour par profil", mtf.register(mk_signal("2026-01-05 11:15", symbol="GBPUSD"), st, now)
      and mtf.register(mk_signal("2026-01-05 11:00", symbol="USDJPY"), st, now) is False)
mtf.MAX_PER_DAY = 12
check("la fraicheur : un signal de plus d'une bougie (15 min apres sa cloture) est perime, sinon il reste valable",
      mtf.is_fresh(mk_signal("2026-01-05 11:45"), pd.Timestamp("2026-01-05 12:20")) is False
      and mtf.is_fresh(mk_signal("2026-01-05 11:45"), pd.Timestamp("2026-01-05 12:10")))


def serve(frames):
    return lambda sid, tf, count, closed_only=True: (frames[tf].tail(count).copy() if tf in frames else None)


b5 = frame(40, 5, start="2026-01-05 12:00", seed=3)
for c in ("open", "high", "low", "close"):
    b5[c] = 1.1005                                                  # prix a plat entre l'entree (1.1000) et TP1 : aucun evenement tant qu'on n'ecrit pas la suite
b5.loc[b5.index[2], "high"] = 1.1012                                # TP1 (1 R)
b5.loc[b5.index[4], "high"] = 1.1022                                # TP2 (2 R)
b5.loc[b5.index[6], "high"] = 1.1032                                # TP3 (3 R)
mtf.get_rates = serve({"5m": b5})
st2 = {"sched": {}}
mtf.register(mk_signal("2026-01-05 11:45"), st2, now)
mtf.update_shadow(st2, pd.Timestamp("2026-01-05 15:00"))
rows = mtf.load_shadow()
check("TP1 puis TP2 puis TP3 : la position se ferme avec +1.9 R (40 % a 1 R, 30 % a 2 R, 30 % a 3 R)",
      len(rows) == 1 and rows[0]["outcome"] == "TP3" and abs(rows[0]["r"] - 1.9) < 1e-6 and not st2["mtf"]["active"], rows)
check("le resultat enregistre contient le profil, le sens et l'heure", rows[0]["profile"] == "scalp_m15" and rows[0]["direction"] == "LONG" and rows[0]["close_time"])

b5s = b5.copy()
for c in ("open", "high", "low", "close"):
    b5s[c] = 1.1000
b5s.loc[b5s.index[3], "low"] = 1.0989                               # stop touche
mtf.get_rates = serve({"5m": b5s})
st3 = {"sched": {}}
mtf.register(mk_signal("2026-01-05 11:45", symbol="USDCAD"), st3, now)
mtf.update_shadow(st3, pd.Timestamp("2026-01-05 15:00"))
rows = mtf.load_shadow()
check("stop touche : -1 R", rows[-1]["outcome"] == "SL" and abs(rows[-1]["r"] + 1.0) < 1e-6, rows[-1])
n_before = len(mtf.load_shadow())
mtf.update_shadow(st3, pd.Timestamp("2026-01-05 15:05"))
check("une position deja fermee n'est jamais comptee deux fois", len(mtf.load_shadow()) == n_before)

st4 = {"sched": {}}
mtf.register(mk_signal("2026-01-05 11:45", symbol="EURJPY"), st4, now)
mtf.get_rates = serve({})                                            # plus aucune donnee
mtf.update_shadow(st4, pd.Timestamp("2026-01-05 15:00"))
check("sans donnees, la position reste ouverte (nouvel essai au cycle suivant)", len(st4["mtf"]["active"]) == 1)
mtf.update_shadow(st4, pd.Timestamp("2026-01-12 15:00"))
check("sans donnees depuis plus de 5 jours : abandonnee, jamais comptee", len(st4["mtf"]["active"]) == 0 and len(mtf.load_shadow()) == n_before)

print("F) recherche d'entrees (avec un moteur simule : on controle les signaux)")
m15f, d1f, h4f = frame(420, 15, start="2026-01-05"), frame(400, 1440, start="2024-01-01"), frame(400, 240, start="2025-06-01")
mtf.get_rates = serve({"15m": m15f, "1d": d1f, "4h": h4f})
mtf.symbol_meta = lambda sid: {"point": 0.0001, "digits": 5}
mtf.htf_zones_of = lambda *a, **k: object()
last = m15f.index[-1]
calls = []


def faux_analyze(df, bias, point, sym, disp, tfm, cls, **kw):
    calls.append(kw.get("profile"))
    return [mk_signal(df.index[-5], symbol=sym), mk_signal(df.index[-1], symbol=sym)]


mtf.analyze = faux_analyze
st5 = {"sched": {}}
now5 = last + pd.Timedelta(minutes=16)
new = mtf.scan_profile("EURUSD", "fx", "scalp_m15", st5, now5, 4)
check("premier passage : seulement le signal de la DERNIERE bougie (pas d'historique)", len(new) == 1 and new[0].bar_open == last.isoformat(), [s.bar_open for s in new])
check("le moteur recoit le profil et l'exigence de zone", calls[-1] == "scalp_m15")
check("prochain controle 1 minute apres la cloture de la bougie suivante", st5["mtf"]["next_check"]["EURUSD|scalp_m15"] == (last + pd.Timedelta(minutes=31)).isoformat())
n_calls = len(calls)
check("pas de nouveau passage tant que ce n'est pas l'heure (aucun calcul)", mtf.scan_profile("EURUSD", "fx", "scalp_m15", st5, now5 + pd.Timedelta(minutes=5), 4) == [] and len(calls) == n_calls)
m15g = frame(421, 15, start="2026-01-05")
mtf.get_rates = serve({"15m": m15g, "1d": d1f, "4h": h4f})
new = mtf.scan_profile("EURUSD", "fx", "scalp_m15", st5, m15g.index[-1] + pd.Timedelta(minutes=16), 4)
check("nouvelle bougie : seulement les signaux PLUS RECENTS que le dernier passage",
      len(new) == 1 and new[0].bar_open == m15g.index[-1].isoformat(), [s.bar_open for s in new])
same = mtf.scan_profile("EURUSD", "fx", "scalp_m15", st5, m15g.index[-1] + pd.Timedelta(minutes=60), 4)
check("aucune nouvelle bougie : rien, nouvel essai plus tard", same == [])
mtf.get_rates = serve({"15m": m15f.head(100), "1d": d1f, "4h": h4f})
st6 = {"sched": {}}
check("pas assez de donnees : rien, nouvel essai dans 5 minutes",
      mtf.scan_profile("GBPUSD", "fx", "scalp_m15", st6, now5, 4) == [] and st6["mtf"]["next_check"]["GBPUSD|scalp_m15"] == (now5 + pd.Timedelta(minutes=5)).isoformat())

print("G) le mode observation ne peut ni planter le robot ni publier")
mtf.get_rates = serve({"15m": m15f, "30m": frame(210, 30, start="2026-01-05"), "1h": frame(110, 60, start="2026-01-05"), "1d": d1f, "4h": h4f, "5m": b5})
logs = []
sd.UNIVERSE.setdefault("EURUSD", dict(mt5="EURUSD", yahoo="EURUSD=X", cls="fx"))
st7 = {"sched": {}}
mtf.observe(st7, {"min_score": 4}, now5, ["EURUSD", "GBPUSD"], log=logs.append, logp=lambda a, b: logs.append(a + b))
check("un passage complet ouvre des positions virtuelles (3 profils)", len(st7["mtf"]["active"]) >= 2, (len(st7["mtf"]["active"]), logs[-3:]))


def boom(*a, **k):
    raise RuntimeError("panne de test")


mtf.analyze = boom
logs.clear()
mtf.observe({"sched": {}}, {"min_score": 4}, now5, ["EURUSD"], log=logs.append)
check("une erreur dans l'analyse est ignoree (jamais d'exception vers le robot)", any("erreur ignoree" in m for m in logs), logs)
mtf.get_rates = boom
mtf.observe({"mtf": {"last_bar": {}, "next_check": {}, "active": {"x": {"signal": mk_signal("2026-01-05 11:45").to_dict(), "sim": TradeSim(mk_signal("2026-01-05 11:45")).to_dict(),
                                                                       "profile": "scalp_m15", "opened": "2026-01-05T12:00"}}, "per_day": {}}, "sched": {}},
            {"min_score": 4}, now5, ["EURUSD"], log=logs.append)
check("une panne de donnees dans le suivi est ignoree aussi", True)
mtf.get_rates = serve({"15m": m15f, "1d": d1f, "4h": h4f})
mtf.analyze = faux_analyze
mtf.BUDGET_S = -1.0
st8 = {"sched": {}}
mtf.observe(st8, {"min_score": 4}, now5, ["EURUSD", "GBPUSD"], log=logs.append)
check("budget de temps depasse : plus aucun scan (le robot principal passe avant)", not st8.get("mtf", {}).get("last_bar"), st8)
mtf.BUDGET_S = 110.0
os.environ["SIGNALS_OBSERVE"] = "off"
check("interrupteur SIGNALS_OBSERVE=off : rien du tout", mtf.enabled_profiles() == [])
os.environ["SIGNALS_OBSERVE"] = "scalp_m30, inconnu"
check("seuls les profils connus sont pris", mtf.enabled_profiles() == ["scalp_m30"])
os.environ.pop("SIGNALS_OBSERVE")
check("par defaut : M15, M30 et H1", mtf.enabled_profiles() == ["scalp_m15", "scalp_m30", "intra_h1"])

print("H) bilans prives")
mtf.SHADOW_FILE = TMP / "bilan.jsonl"
check("sans aucun trade : pas de bilan du jour", mtf.digest_text({"mtf": {"active": {}}}, pd.Timestamp("2026-01-05 22:00")) is None)
for k, (p, r, o) in enumerate([("scalp_m15", 1.9, "TP3"), ("scalp_m15", -1.0, "SL"), ("scalp_m30", 0.4, "TP1"), ("intra_h1", -1.0, "SL")]):
    mtf.append_shadow({"id": f"x{k}", "profile": p, "r": r, "outcome": o, "close_time": "2026-01-05T14:00:00"})
txt = mtf.digest_text({"mtf": {"active": {}}}, pd.Timestamp("2026-01-05 22:00"))
check("bilan du jour : chaque profil, jamais de nom de paire", txt and "Scalp M15 : 2 trades" in txt and "+0.9 R" in txt and "Intraday H1 : 1 trades" in txt and "EURUSD" not in txt, txt)
lines = "\n".join(mtf.status_lines({"mtf": {"active": {}}}))
check("/etat : mention claire que rien n'est publie et qu'il est trop tot pour conclure", "rien n'est publié" in lines and "Trop tôt pour conclure" in lines, lines)

print("I) le robot principal")
import signal_runner as runner  # noqa: E402

check("le module d'observation est branche", runner.signal_mtf is mtf)
check("les timeframes M15 et M30 sont reconnus", runner.tf_key_of(mk_signal("2026-01-05 10:00")) == "15m" and runner.tf_key_of(mk_signal("2026-01-05 10:00", tf="30m")) == "30m")


class Pub:
    def __init__(self):
        self.calls = []

    def notify_owner(self, text, silent=False):
        self.calls.append(("notify_owner", silent, text))

    def __getattr__(self, name):                                    # toute autre methode (publication dans un groupe...) est INTERDITE
        raise AssertionError(f"l'observation ne doit jamais appeler {name}")


saved = []
runner.save_state = lambda s: saved.append(1)
seen_before = []
mtf.observe = lambda *a, **k: seen_before.append(len(saved))
runner._observation({"sched": {}}, {"min_score": 4}, (Pub(), Pub()), [], pd.Timestamp("2026-01-05 12:00"), time.time())
check("l'etat du robot principal est ENREGISTRE avant l'observation (un cycle coupe ne peut rien republier)", seen_before == [1], seen_before)
os.environ["SIGNALS_OBSERVE"] = "off"
saved.clear()
runner._observation({"sched": {}}, {"min_score": 4}, (Pub(), Pub()), [], pd.Timestamp("2026-01-05 12:00"), time.time())
check("observation desactivee : aucune sauvegarde supplementaire", saved == [])
os.environ.pop("SIGNALS_OBSERVE")
stx = {"sched": {}}
fichier_bilan = mtf.SHADOW_FILE
mtf.SHADOW_FILE = TMP / "echeance.jsonl"
mtf.get_rates = serve({"5m": b5})
mtf.register(mk_signal("2026-01-05 11:45"), stx, now)
mtf.update_shadow(stx, pd.Timestamp("2026-01-05 15:00"), deadline=time.time() - 1)
check("echeance de temps depassee : le suivi s'arrete (le reste au cycle suivant)", len(stx["mtf"]["active"]) == 1 and mtf.load_shadow() == [])
mtf.SHADOW_FILE = fichier_bilan
pv = Pub()
mtf.observe = lambda *a, **k: None
sti = {"sched": {}}
runner._observation(sti, {"min_score": 4}, (pv, Pub()), [], pd.Timestamp("2026-01-05 21:45"), time.time())
check("bilan prive envoye UNE fois par jour, en silence, seulement a un proprietaire", len(pv.calls) == 1 and pv.calls[0][1] is True and "Observation du jour" in pv.calls[0][2], pv.calls)
runner._observation(sti, {"min_score": 4}, (pv, Pub()), [], pd.Timestamp("2026-01-05 22:30"), time.time())
check("... pas deux fois le meme jour", len(pv.calls) == 1)
runner._observation({"sched": {}}, {"min_score": 4}, (Pub(), Pub()), [], pd.Timestamp("2026-01-05 12:00"), time.time())
check("... et rien en journee", True)
mtf.observe = boom
runner._observation({"sched": {}}, {"min_score": 4}, (Pub(), Pub()), [], pd.Timestamp("2026-01-05 12:00"), time.time())
check("une panne de l'observation n'arrive jamais jusqu'au cycle principal", True)

print(f"\n{ok} verifications reussies, aucune ne touche a Telegram, au reseau ni a MT5.")
