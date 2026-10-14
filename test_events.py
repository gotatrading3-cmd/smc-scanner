"""
test_events.py - Tests HORS CONNEXION du module NFP (aucun reseau, aucun Telegram, aucun MT5) et de son branchement dans le robot.

    python test_events.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).parent
TMP = Path(tempfile.mkdtemp(prefix="test_events_"))
os.environ["SIGNALS_DATA_DIR"] = str(TMP)
os.environ["SIGNAL_BACKEND"] = "yahoo"
for v in ("SIGNALS_NFP_MODE", "SIGNALS_OBSERVE", "SIGNALS_SCALP_PROFILES", "SIGNALS_SCALP_MODE"):
    os.environ.pop(v, None)
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import signal_data as sd  # noqa: E402
import signal_events as ev  # noqa: E402

ev.CONTROL_FILE = TMP / "control.json"
ok = 0


def check(nom, cond, detail=""):
    global ok
    print(("  OK   " if cond else "  ECHEC ") + nom + (f"  [{detail}]" if detail and not cond else ""))
    ok += bool(cond)
    assert cond, nom


T = ev.release_utc("2026-11-06")                       # NFP du 6 novembre 2026
M = lambda minutes: T + pd.Timedelta(minutes=minutes)  # noqa: E731

print("A) calendrier officiel : heure d'ete / d'hiver a New York")
check("6 nov. 2026 (heure d'hiver a New York) : 08h30 = 13h30 GMT", T == pd.Timestamp("2026-11-06 13:30"), T)
check("2 oct. 2026 (heure d'ete) : 08h30 = 12h30 GMT", ev.release_utc("2026-10-02") == pd.Timestamp("2026-10-02 12:30"))
check("5 mars 2027 (avant le passage a l'heure d'ete americaine) : 13h30 GMT ; 2 avril 2027 : 12h30 GMT",
      ev.release_utc("2027-03-05") == pd.Timestamp("2027-03-05 13:30") and ev.release_utc("2027-04-02") == pd.Timestamp("2027-04-02 12:30"))
check("le calendrier couvre jusqu'a fin 2027, mois de reference compris ; les dates a venir tombent un jeudi ou un vendredi",
      ev.NFP_RELEASES["2026-11-06"] == "2026-10" and ev.NFP_RELEASES["2027-12-03"] == "2027-11"
      and all(pd.Timestamp(d).weekday() in (3, 4) for d in ev.NFP_RELEASES if d >= "2026-11-01"))
n = ev.next_release(pd.Timestamp("2026-10-14 01:00"))
check("prochaine publication depuis le 14 octobre 2026 : le 6 novembre", n["date"] == "2026-11-06" and n["ref"] == "2026-10" and n["release"] == T)
check("ligne /etat lisible", "vendredi 6 novembre à 13h30 GMT" in ev.status_line(pd.Timestamp("2026-10-14 01:00")) and "dans 24 jours" in ev.status_line(pd.Timestamp("2026-10-14 01:00")),
      ev.status_line(pd.Timestamp("2026-10-14 01:00")))
check("... et le jour meme : en heures", "dans 5 h" in ev.status_line(pd.Timestamp("2026-11-06 08:30")), ev.status_line(pd.Timestamp("2026-11-06 08:30")))
check("apres la derniere date connue : rien (pas d'erreur)", ev.next_release(pd.Timestamp("2028-01-01")) is None and ev.status_line(pd.Timestamp("2028-01-01")) == "")

print("B) fenetres : veille, rappel, pause, chiffre reel, reprise")
check("hors fenetre (plus de 22 h avant) : rien", ev.window(M(-23 * 60)) is None and ev.due_kinds({}, M(-23 * 60)) == [])
check("a partir de 22 h avant : message de la veille", ev.due_kinds({}, M(-21 * 60)) == ["pre"])
check("de 70 a 30 minutes avant : rappel", ev.due_kinds({}, M(-70)) == ["t60"] and ev.due_kinds({}, M(-31)) == ["t60"] and ev.due_kinds({}, M(-71)) == ["pre"])
check("de 30 minutes avant a la publication : silence (pause)", ev.due_kinds({}, M(-30)) == [] and ev.due_kinds({}, M(-1)) == [])
check("a la publication : on attend le chiffre reel", ev.due_kinds({}, M(0)) == ["result"] and ev.due_kinds({}, M(29)) == ["result"])
check("a T+30 min : reprise (le chiffre reel reste attendu jusqu'a T+90)", ev.due_kinds({}, M(30)) == ["result", "resume"] and ev.due_kinds({}, M(91)) == ["resume"])
check("plus rien apres T+3 h", ev.due_kinds({}, M(181)) == [] and ev.window(M(181)) is None)
done = {"events": {"nfp": {"2026-11-06": {"pre_vip": "x", "pre_public": "x", "t60_vip": "x"}}}}
check("un message deja envoye aux DEUX groupes ne repart pas ; a moitie envoye, il reste du", ev.due_kinds(done, M(-21 * 60)) == [] and ev.due_kinds(done, M(-40)) == ["t60"])
check("pause de securite : de T-30 min a T+30 min, pas avant, pas apres",
      not ev.in_blackout(M(-31)) and ev.in_blackout(M(-30)) and ev.in_blackout(M(0)) and ev.in_blackout(M(29)) and not ev.in_blackout(M(30)))
ev.CONTROL_FILE.write_text('{"nfp_blackout": false}', encoding="utf-8")
check("la pause peut etre coupee (nfp_blackout=false)", not ev.in_blackout(M(0)))
ev.CONTROL_FILE.write_text('{"nfp_mode": "off"}', encoding="utf-8")
check("mode off : ni pause ni message", not ev.in_blackout(M(0)) and ev.nfp_mode() == "off")
ev.CONTROL_FILE.write_text("pas du json", encoding="utf-8")
check("reglage illisible : apercu par defaut, pause active", ev.nfp_mode() == "preview" and ev.in_blackout(M(0)))
ev.CONTROL_FILE.write_bytes(b'\xef\xbb\xbf{"nfp_mode": "live"}')
check("un fichier de reglages enregistre avec BOM (editeur Windows) est lu correctement", ev.nfp_mode() == "live")
ev.CONTROL_FILE.unlink()
check("par defaut : apercu prive", ev.nfp_mode() == "preview")

print("C) donnees : prevision (calendrier) et chiffre reel (BLS)")
feed = [{"title": "Non-Farm Employment Change", "country": "USD", "date": "2026-11-06T08:30:00-05:00", "impact": "High", "forecast": "70K", "previous": "29K"},
        {"title": "Unemployment Rate", "country": "USD", "date": "2026-11-06T08:30:00-05:00", "impact": "High", "forecast": "4.2%", "previous": "4.2%"},
        {"title": "Average Hourly Earnings m/m", "country": "USD", "date": "2026-11-06T08:30:00-05:00", "impact": "High", "forecast": "0.3%", "previous": "0.1%"},
        {"title": "Non-Farm Employment Change", "country": "EUR", "date": "2026-11-06T08:30:00-05:00", "forecast": "999K", "previous": "1K"},
        {"title": "Non-Farm Employment Change", "country": "USD", "date": "2026-11-13T08:30:00-05:00", "forecast": "888K", "previous": "1K"},
        {"title": "CPI m/m", "country": "USD", "date": "2026-11-06T08:30:00-05:00", "forecast": "0.2%", "previous": "0.1%"}]
c = ev.parse_consensus(feed, T)
check("prevision et precedent lus (emploi, chomage, salaires) ; autres pays, autres jours, autres indicateurs ignores",
      c == {"payrolls": {"forecast": 70.0, "previous": 29.0}, "unemployment": {"forecast": 4.2, "previous": 4.2}, "earnings": {"forecast": 0.3, "previous": 0.1}}, c)
check("flux vide ou casse : rien, jamais d'erreur", ev.parse_consensus([], T) == {} and ev.parse_consensus(None, T) == {} and ev.parse_consensus([{"x": 1}], T) == {})
check("nombres du calendrier : 70K, -12K, 4.1%, 1.2M, n/a", ev._num("70K") == 70.0 and ev._num("-12K") == -12.0 and ev._num("4.1%") == 4.1 and ev._num("1.2M") == 1200.0 and ev._num("") is None and ev._num(None) is None)


def bls(payroll, unemp, ahe):
    mk = lambda code, d: {"seriesID": code, "data": [{"year": str(y), "period": f"M{m:02d}", "periodName": "x", "value": str(v), "footnotes": [{}]} for (y, m), v in d.items()]}  # noqa: E731
    return {"status": "REQUEST_SUCCEEDED", "Results": {"series": [mk("CES0000000001", payroll), mk("LNS14000000", unemp), mk("CES0500000003", ahe)]}}


vrai = bls({(2026, 9): 159044, (2026, 8): 159015, (2026, 7): 158882, (2026, 6): 158892}, {(2026, 9): 4.2, (2026, 8): 4.1}, {(2026, 9): 38.40, (2026, 8): 38.35})
a = ev.parse_actual(vrai, "2026-09")
check("chiffres reels du 2 octobre 2026 : +29 000 emplois, mois precedent revise a +133 000, chomage 4,2 % (avant 4,1 %), salaires +0,13 %",
      a["change_k"] == 29 and a["previous_change_k"] == 133 and a["unemployment"] == 4.2 and a["unemployment_prev"] == 4.1 and abs(a["earnings_mom"] - 0.1304) < 0.001, a)
check("tant que le mois attendu n'est pas publie : None (on reessaie au cycle suivant)", ev.parse_actual(vrai, "2026-10") is None)
check("janvier : le mois precedent est decembre de l'annee d'avant", ev.parse_actual(bls({(2027, 1): 160000, (2026, 12): 159800, (2026, 11): 159700}, {}, {}), "2027-01")["change_k"] == 200)
check("reponse de l'API en erreur : rien, jamais d'erreur", ev.parse_actual({"status": "REQUEST_NOT_PROCESSED"}, "2026-09") is None and ev.parse_actual({}, "2026-09") is None)

print("D) reaction du marche : seulement des bougies reelles")
idx = pd.date_range(M(-60), periods=40, freq="5min")
b = pd.DataFrame({"open": 1.1000, "high": 1.1000, "low": 1.1000, "close": 1.1000}, index=idx)
b.loc[b.index >= M(0), ["open", "high", "low", "close"]] = 1.1035          # +35 pips des la publication (point 0.00001, pip 0.0001)
b2 = b.copy()
b2.loc[b2.index >= M(20), ["open", "high", "low", "close"]] = 1.0950       # puis retournement
usdjpy = pd.DataFrame({"open": 150.00, "high": 150.00, "low": 150.00, "close": 150.00}, index=idx)
usdjpy.loc[usdjpy.index >= M(0), ["open", "high", "low", "close"]] = 149.65   # -35 pips
fake = lambda sid, tf, count, closed_only=True: {"EURUSD": b2, "GBPUSD": b, "USDJPY": usdjpy}.get(sid)  # noqa: E731
r15 = ev.reaction(T, 15, get_rates=fake)
check("a +15 min : euro/dollar +35 pips, dollar/yen -35 pips (la derniere bougie de 5 min cloturee avant la publication sert de reference)",
      abs(r15["EURUSD"][0] - 35) < 0.5 and abs(r15["USDJPY"][0] + 35) < 0.5 and abs(r15["EURUSD"][1] - 15) < 0.1, r15)
r30 = ev.reaction(T, 30, get_rates=fake)
check("a +30 min : le retournement de la 20e minute compte (euro/dollar -50 pips)", abs(r30["EURUSD"][0] + 50) < 0.5, r30)
check("sans bougie apres la publication : rien (pas de chiffre invente)", ev.reaction(M(-120), 15, get_rates=lambda *a, **k: b.head(5)) == {})
check("source en panne : rien", ev.reaction(T, 15, get_rates=lambda *a, **k: None) == {})
check("lecture du dollar : 3 paires d'accord = 'en baisse' ; reaction partagee = rien",
      ev.usd_read({"EURUSD": (35, 15), "GBPUSD": (31, 15), "USDJPY": (-35, 15)}) == "en baisse" and ev.usd_read({"EURUSD": (35, 15), "GBPUSD": (-31, 15), "USDJPY": (-35, 15)}) == ""
      and ev.usd_read({"EURUSD": (-12, 15), "USDJPY": (20, 15)}) == "en hausse" and ev.usd_read({"EURUSD": (1, 15), "GBPUSD": (-2, 15)}) == "")

print("E) zones H4 proches du prix (calculees a la demande, jamais enregistrees)")
cache = sd.CACHE_DIR


def cached(name, tf):
    fs = sorted(cache.glob(f"{name}_{tf}_*.pkl"), key=lambda f: int(f.stem.rsplit("_", 1)[-1]))
    return pd.read_pickle(fs[-1]) if fs else None


d4, dd = cached("EURUSD", "4h"), cached("EURUSD", "1d")
if d4 is None or dd is None or not sd._meta_all().get("EURUSD"):
    print("  (donnees en cache absentes : tests E ignores)")
else:
    zs = ev.zones_to_watch(pd.Timestamp("2026-09-26"), symbols=("EURUSD",), max_atr=60.0, get_rates=lambda sid, tf, count, closed_only=True: {"4h": d4, "1d": dd}[tf])
    check("une zone H4 proche est trouvee, avec son sens, ses niveaux et sa distance en ATR",
          len(zs) == 1 and zs[0]["display"] == "EURUSD" and zs[0]["dir"] in (-1, 1) and zs[0]["lo"] < zs[0]["hi"] and zs[0]["dist_atr"] >= 0 and zs[0]["digits"] == 5, zs)
    near = ev.zones_to_watch(pd.Timestamp("2026-09-26"), symbols=("EURUSD",), max_atr=0.0001, get_rates=lambda sid, tf, count, closed_only=True: {"4h": d4, "1d": dd}[tf])
    check("seuil de distance respecte", all(z["dist_atr"] <= 0.0001 for z in near))
    check("donnees manquantes : liste vide, jamais d'erreur", ev.zones_to_watch(pd.Timestamp("2026-09-26"), symbols=("EURUSD",), get_rates=lambda *a, **k: None) == [])

print("F) messages : voix du trader, GMT, rien d'invente, le public ne voit jamais le detail des zones")
w = {"date": "2026-11-06", "ref": "2026-10", "release": T, "minutes": 0.0}
zones = [{"display": "EURUSD", "dir": 1, "kind": "FVG", "lo": 1.0850, "hi": 1.0870, "dist_atr": 0.6, "price": 1.0880, "digits": 5},
         {"display": "USDJPY", "dir": -1, "kind": "OB", "lo": 150.1, "hi": 150.4, "dist_atr": 1.1, "price": 149.9, "digits": 3}]
cons = {"payrolls": {"forecast": 70.0, "previous": 29.0}, "unemployment": {"forecast": 4.2, "previous": 4.2}}
st = {"copy_idx": {}}
pre = ev.build("pre", st, w, cons, zones=zones)
check("veille : date, heure GMT, explication du NFP, mesure sur 43 publications, plan de pause 13h00 -> 14h00",
      "Vendredi 6 novembre" in pre["public"] and "13h30 GMT" in pre["public"] and "nombre d'emplois créés" in pre["public"] and "2,8 fois" in pre["public"]
      and "de 13h00 à 14h00 GMT" in pre["public"], pre["public"])
check("la prevision est dans les deux versions ; les zones H4 seulement dans le VIP",
      "prévision +70 000" in pre["public"] and "prévision +70 000" in pre["vip"] and "EURUSD — achat : Fair Value Gap H4" in pre["vip"] and "EURUSD" not in pre["public"] and "Order Block" not in pre["public"])
check("le VIP recoit le conseil pour les trades ouverts, pas le public", "trade ouvert" in pre["vip"] and "trade ouvert" not in pre["public"])
t60 = ev.build("t60", st, w, cons, zones=zones)
check("rappel : prevision + pause + 'comme d'habitude' pour le VIP", "NFP" in t60["public"] and "+70 000" in t60["public"] and "13h00 à 14h00" in t60["public"] and "comme d'habitude" in t60["vip"])
check("sans prevision (flux indisponible) : le message part quand meme, sans chiffre invente", "prévision" not in ev.build("t60", {"copy_idx": {}}, w, {}, zones=None)["public"])
check("le chiffre reel n'existe pas encore : pas de message", ev.build("result", st, w, cons, actual=None) is None)
act = {"change_k": 29.0, "previous_change_k": 133.0, "unemployment": 4.2, "unemployment_prev": 4.1, "earnings_mom": 0.13}
res = ev.build("result", st, w, cons, actual=act, react={"EURUSD": (34.0, 15), "GBPUSD": (30.0, 15), "USDJPY": (-35.0, 15)})
check("resultat : emplois, ecart a la prevision, chomage, salaires, reaction, dollar en baisse, pas de trade avant 14h00",
      "+29 000" in res["public"] and "en dessous de la prévision : +70 000" in res["public"] and "4,2 %" in res["public"] and "+0,13 %" in res["public"]
      and "l'euro/dollar +34 pips" in res["public"] and "dollar/yen −35 pips" in res["public"] and "Le dollar est en baisse" in res["public"] and "avant 14h00 GMT" in res["public"], res["public"])
check("resultat identique pour le VIP et le public (rien de sensible)", res["vip"] == res["public"])
res2 = ev.build("result", st, w, {}, actual={"change_k": -12.0}, react={})
check("chiffre negatif et sans prevision ni reaction : '−12 000', aucun texte sur la reaction", "−12 000" in res2["public"] and "Réaction" not in res2["public"] and "prévision" not in res2["public"])
rsm = ev.build("resume", st, w, cons, react={"EURUSD": (34.0, 30)}, zones=zones)
check("reprise : annonce claire, avec la reaction mesuree", "je reprends" in rsm["public"] and "l'euro/dollar +34 pips" in rsm["public"])
check("reprise : le VIP a les zones, le public non", "Zones H4 à surveiller maintenant" in rsm["vip"] and "Zones H4" not in rsm["public"])
allt = [pre["public"], pre["vip"], t60["public"], t60["vip"], res["public"], rsm["public"], rsm["vip"]]
check("jamais de vocabulaire de machine ni de 'UTC' (regle des textes du groupe)", not any(x in t for t in allt for x in ("UTC", "blackout", "scan ", "analyse en cours", "filtres validés")))
check("jamais de promesse de gain", not any(x in t.lower() for t in allt for x in ("garanti", "100 %", "sans risque", "gagnant à coup sûr")))
check("balises HTML equilibrees", all(t.count("<b>") == t.count("</b>") for t in allt))
check("formats : 29 -> '+29 000', -12 -> '−12 000', pourcentages avec virgule", ev.fmt_k(29) == "+29 000" and ev.fmt_k(-12) == "−12 000" and ev.fmt_pct(4.2) == "4,2 %" and ev.fmt_pct2(0.13) == "+0,13 %")
check("rotation des variantes : deux veilles de suite ne sont pas identiques", ev.build("pre", st, w, cons)["public"] != ev.build("pre", st, w, cons)["public"])

print("G) envoi : apercu prive ou groupes, chaque groupe marque a part, jamais de doublon")


class FakePub:
    def __init__(self, ok=True, tag=""):
        self.ok, self.tag, self.sent = ok, tag, []

    def send_text(self, text, notify=True, **k):
        self.sent.append(("vip", text, notify))
        return 1 if self.ok else None

    def post_public(self, text, notify=False, cta=None, **k):
        self.sent.append(("public", text, notify, cta))
        return 1 if self.ok else None


class FakePublisher(FakePub):
    made = []

    def __init__(self, cfg, log=None, audience="vip"):
        super().__init__()
        self.cfg, self.audience = cfg, audience
        FakePublisher.made.append(self)


import signal_publisher as sp  # noqa: E402

sp.Publisher = FakePublisher
texts = {"vip": "VIP", "public": "PUBLIC"}
stg = {}
live_v, live_p = FakePub(), FakePub()
ev.deliver("pre", texts, stg, w, {"live": True}, (live_v, live_p), "live")
check("mode live : VIP et groupe public, sans sonnerie, sans invitation 'nous ecrire' dans ce message",
      live_v.sent == [("vip", "VIP", False)] and live_p.sent == [("public", "PUBLIC", False, False)], (live_v.sent, live_p.sent))
check("les deux sont marques (chacun a part)", set(stg["events"]["nfp"]["2026-11-06"]) == {"pre_vip", "pre_public"})
check("compte dans le plafond de posts publics du jour", sum(stg["pub_posts"].values()) == 1)
FakePublisher.made.clear()
stp = {}
ev.deliver("pre", texts, stp, w, {"live": True, "public_live": True, "vip_chat_id": "1"}, (live_v, live_p), "preview")
check("mode apercu : les DEUX versions vont dans le chat prive (editeurs non-live), RIEN dans les vrais groupes",
      len(FakePublisher.made) == 2 and all(p.cfg.get("live") is False or p.cfg.get("public_live") is False for p in FakePublisher.made)
      and FakePublisher.made[0].cfg["live"] is False and FakePublisher.made[1].cfg["public_live"] is False and len(live_v.sent) == 1 and len(live_p.sent) == 1)
fail_pub = FakePub(ok=False)
stf = {}
ev.deliver("t60", texts, stf, w, {}, (FakePub(), fail_pub), "live")
check("si le groupe public refuse : seul le VIP est marque, le public sera reessaye (et le VIP ne recevra pas de doublon)",
      set(stf["events"]["nfp"]["2026-11-06"]) == {"t60_vip"} and ev.due_kinds(stf, M(-40)) == ["t60"])
vip2, pub2 = FakePub(), FakePub()
ev.deliver("t60", texts, stf, w, {}, (vip2, pub2), "live")
check("... au cycle suivant : le VIP n'est pas reenvoye, le public part", vip2.sent == [] and len(pub2.sent) == 1 and ev.due_kinds(stf, M(-40)) == [])

print("H) passage complet a chaque cycle (run_step) : une seule fois chaque message")
sent_log = []
ev.fetch_consensus = lambda release: {"payrolls": {"forecast": 70.0, "previous": 29.0}}
publi = {"actual": None}
ev.fetch_actual = lambda ref: publi["actual"]
ev.reaction = lambda release, horizon_min=15, **k: {"EURUSD": (34.0, horizon_min)}
ev.zones_to_watch = lambda now, **k: zones
sp.Publisher = FakePublisher
sth = {"copy_idx": {}}
vv, pp = FakePub(), FakePub()
logs = []
ev.run_step(sth, {}, (vv, pp), M(-30 * 60), log=logs.append)
check("hors fenetre : rien", vv.sent == [] and pp.sent == [])
ev.CONTROL_FILE.write_text('{"nfp_mode": "live"}', encoding="utf-8")
ev.run_step(sth, {}, (vv, pp), M(-20 * 60), log=logs.append)
ev.run_step(sth, {}, (vv, pp), M(-19 * 60), log=logs.append)
check("la veille : un seul message par groupe (meme si le robot repasse 100 fois)", len(vv.sent) == 1 and len(pp.sent) == 1 and "zones" in vv.sent[0][1].lower() and sth["events"]["consensus"]["2026-11-06"])
ev.run_step(sth, {}, (vv, pp), M(-40), log=logs.append)
check("rappel 40 minutes avant", len(vv.sent) == 2 and len(pp.sent) == 2)
ev.run_step(sth, {}, (vv, pp), M(-10), log=logs.append)
check("pendant la pause (T-10) : aucun message", len(vv.sent) == 2)
ev.run_step(sth, {}, (vv, pp), M(3), log=logs.append)
check("chiffre reel pas encore publie a T+3 : rien, on reessaie", len(vv.sent) == 2)
publi["actual"] = {"change_k": 29.0, "unemployment": 4.2}
ev.run_step(sth, {}, (vv, pp), M(8), log=logs.append)
check("chiffre reel publie : message avec les chiffres et la reaction, une seule fois", len(vv.sent) == 3 and "+29 000" in vv.sent[2][1] and "euro/dollar +34 pips" in vv.sent[2][1])
ev.run_step(sth, {}, (vv, pp), M(13), log=logs.append)
check("pas de doublon du chiffre reel", len(vv.sent) == 3)
ev.run_step(sth, {}, (vv, pp), M(31), log=logs.append)
check("T+31 min : reprise (avec zones pour le VIP)", len(vv.sent) == 4 and "je reprends" in pp.sent[3][1] and "Zones H4 à surveiller maintenant" in vv.sent[3][1])
ev.run_step(sth, {}, (vv, pp), M(60), log=logs.append)
ev.run_step(sth, {}, (vv, pp), M(200), log=logs.append)
check("ensuite plus rien : 4 messages par groupe en tout", len(vv.sent) == 4 and len(pp.sent) == 4)
ev.CONTROL_FILE.write_text('{"nfp_mode": "off"}', encoding="utf-8")
vo, po = FakePub(), FakePub()
ev.run_step({"copy_idx": {}}, {}, (vo, po), M(-20 * 60), log=logs.append)
check("mode off : rien", vo.sent == [] and po.sent == [])


def boom(*a, **k):
    raise RuntimeError("panne de test")


ev.CONTROL_FILE.write_text('{"nfp_mode": "live"}', encoding="utf-8")
ev.zones_to_watch = boom
logs.clear()
ev.run_step({"copy_idx": {}}, {}, (FakePub(), FakePub()), M(-20 * 60), log=logs.append)
check("une panne interne n'arrive jamais jusqu'au robot (erreur journalisee seulement)", any("erreur ignoree" in m for m in logs), logs)
ev.CONTROL_FILE.unlink()

print("I) le robot principal : pause des nouveaux signaux, /etat")
import signal_runner as runner  # noqa: E402

check("le module NFP est branche dans le robot", runner.signal_events is ev)
check("pause autour de la publication : T-30 -> T+30, pas avant, pas apres", runner._nfp_blackout(M(-31)) is False and runner._nfp_blackout(M(-30)) and runner._nfp_blackout(M(29)) and runner._nfp_blackout(M(30)) is False)
scans = []


class Quiet:
    live = False

    def poll_owner_commands(self, st, fn):
        pass

    def __getattr__(self, name):
        return lambda *a, **k: None


runner.scan_symbol = lambda sid, cls, st, cfg, now: scans.append(sid) or []
runner.update_active = lambda *a, **k: None
runner.scheduled = lambda *a, **k: None
runner._observation = lambda *a, **k: None
runner.save_state = lambda s: None
ev.run_step = lambda *a, **k: None
runner.UNIVERSE = {"EURUSD": {"cls": "fx"}, "GBPUSD": {"cls": "fx"}}
sdum = {"sched": {}, "last_bar": {}, "next_check": {}, "active": {}, "per_day": {}, "last_signal": {}, "watch": {}, "per_day_scalp": {}, "active_scalp": {}, "pub_sig": {}}
runner.utc_now = lambda: M(-5)
runner._run_cycle(sdum, {"min_score": 4, "max_signals_per_day": 8, "max_active": 10}, (Quiet(), Quiet()), ["EURUSD", "GBPUSD"])
check("PENDANT la pause : le robot ne cherche AUCUN nouveau signal (aucun scan)", scans == [], scans)
runner.utc_now = lambda: M(45)
runner._run_cycle(sdum, {"min_score": 4, "max_signals_per_day": 8, "max_active": 10}, (Quiet(), Quiet()), ["EURUSD", "GBPUSD"])
check("apres la pause : les scans reprennent (les bougies manquees sont rattrapees)", scans == ["EURUSD", "GBPUSD"], scans)
ev.CONTROL_FILE.write_text('{"nfp_mode": "off"}', encoding="utf-8")
scans.clear()
runner.utc_now = lambda: M(-5)
runner._run_cycle(sdum, {"min_score": 4, "max_signals_per_day": 8, "max_active": 10}, (Quiet(), Quiet()), ["EURUSD"])
check("mode off : aucune pause, le robot scanne comme avant", scans == ["EURUSD"])
ev.CONTROL_FILE.unlink()
txt = runner.status_text({"active": {}, "per_day": {}, "sched": {}, "started": "2026-01-01"}, pd.Timestamp("2026-10-14 01:00"))
check("/etat : prochain NFP et mode apercu", "Prochain NFP : vendredi 6 novembre à 13h30 GMT" in txt and "aperçu dans ton chat privé" in txt, txt)

print(f"\n{ok} verifications reussies, aucune ne touche a Telegram, au reseau ni a MT5.")
