"""
test_elite.py - Tests HORS CONNEXION du « groupe VIP 📊 » (nouveau groupe, positions 5/7 et plus) : reglages, textes, animation, et surtout que le groupe prive
et le groupe public ne sont JAMAIS touches (aucun reseau, aucun Telegram, aucun MT5).

    python test_elite.py
"""
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
TMP = Path(tempfile.mkdtemp(prefix="test_elite_"))
os.environ["SIGNALS_DATA_DIR"] = str(TMP)
os.environ["SIGNAL_BACKEND"] = "yahoo"
for v in ("SIGNALS_ELITE_MODE", "ELITE_CHAT_ID", "SIGNALS_ELITE_MIN_SCORE", "SIGNALS_NFP_MODE", "SIGNALS_OBSERVE", "SIGNALS_SCALP_PROFILES"):
    os.environ.pop(v, None)
sys.path.insert(0, str(HERE))

import pandas as pd  # noqa: E402

import signal_elite as el  # noqa: E402
import signal_events as ev  # noqa: E402
import signal_publisher as sp  # noqa: E402
from signal_engine import Signal, TradeSim  # noqa: E402

el.CONTROL_FILE = TMP / "control.json"
ev.CONTROL_FILE = TMP / "control_events.json"            # (les reglages NFP n'ont rien a voir avec ces tests)
ok = 0


def check(nom, cond, detail=""):
    global ok
    print(("  OK   " if cond else "  ECHEC ") + nom + (f"  [{detail}]" if detail and not cond else ""))
    ok += bool(cond)
    assert cond, nom


CHAT = "-1004392623650"


def reglages(**kw):
    el.CONTROL_FILE.write_text(__import__("json").dumps(kw), encoding="utf-8")


print("A) reglages : eteint par defaut, apercu avant direct")
check("par defaut : eteint (rien ne change pour les deux autres groupes)", el.settings()["mode"] == "off" and not el.wants(7) and el.publisher({}) is None)
reglages(elite_mode="live")
check("sans identifiant de groupe : eteint (jamais de publication au hasard)", el.settings()["mode"] == "off")
reglages(elite_mode="live", elite_chat_id=CHAT)
s = el.settings()
check("mode direct + identifiant : actif, seuil 5/7 par defaut", s == {"mode": "live", "chat_id": CHAT, "min_score": 5}, s)
check("le seuil : 5 et plus seulement", el.wants(5) and el.wants(6) and el.wants(7) and not el.wants(4) and not el.wants(0))
reglages(elite_mode="peut-etre", elite_chat_id=CHAT)
check("un mode illisible : eteint", el.settings()["mode"] == "off")
reglages(elite_mode="preview", elite_chat_id=CHAT, elite_min_score=6)
check("apercu et seuil 6 reglables", el.settings() == {"mode": "preview", "chat_id": CHAT, "min_score": 6} and not el.wants(5) and el.wants(6))
reglages(elite_mode="preview", elite_chat_id=CHAT, elite_min_score="abc")
check("seuil illisible : 5", el.settings()["min_score"] == 5)
el.CONTROL_FILE.write_bytes(b'\xef\xbb\xbf{"elite_mode": "live", "elite_chat_id": "' + CHAT.encode() + b'"}')
check("fichier avec BOM (editeur Windows) lu correctement", el.settings()["mode"] == "live")
el.CONTROL_FILE.unlink()
os.environ["ELITE_CHAT_ID"], os.environ["SIGNALS_ELITE_MODE"] = CHAT, "live"
check("sans fichier : variables d'environnement", el.settings()["mode"] == "live" and el.settings()["chat_id"] == CHAT)
os.environ.pop("ELITE_CHAT_ID")
os.environ.pop("SIGNALS_ELITE_MODE")

print("B) l'editeur du groupe VIP : sa propre cible, jamais celle des autres")
reglages(elite_mode="live", elite_chat_id=CHAT)
pe = sp.Publisher({"live": True, "vip_chat_id": "-1002816358889", "public_live": True, "channel_id": "-1003927074271", **el.elite_cfg({})}, audience="elite")
check("direct : l'editeur VIP vise le nouveau groupe (ni le groupe prive, ni le public)", pe.live and pe.dest == CHAT and pe.target == CHAT)
reglages(elite_mode="preview", elite_chat_id=CHAT)
pp = sp.Publisher({"live": True, "vip_chat_id": "-1002816358889", **el.elite_cfg({})}, audience="elite")
check("apercu : tout va dans le chat prive du proprietaire, rien dans le groupe", pp.live is False and pp.target == pp.owner_chat and pp.dest == CHAT)
check("etiquettes d'apercu : « groupe VIP 📊 » / « groupe privé » / « groupe public » (jamais confondus)",
      "groupe VIP 📊" in pp._tag() and "groupe privé" in sp.Publisher({"live": False, "vip_chat_id": "1"}, audience="vip")._tag()
      and "groupe public" in sp.Publisher({"public_live": False}, audience="public")._tag())
check("le groupe prive et le public gardent leurs identifiants", sp.Publisher({"live": True, "vip_chat_id": "-1002816358889", "elite_chat_id": CHAT}, audience="vip").dest == "-1002816358889"
      and sp.Publisher({"public_live": True, "channel_id": "-1003927074271", "elite_chat_id": CHAT}, audience="public").dest == "-1003927074271")


class FakeEditeur:
    made = []

    def __init__(self, cfg, log=None, audience="vip"):
        self.cfg, self.audience, self.sent, self.pinned = cfg, audience, [], []
        self.live = bool(cfg.get("elite_live")) if audience == "elite" else bool(cfg.get("live"))
        FakeEditeur.made.append(self)

    def send_text(self, text, reply_to=None, notify=True, **k):
        self.sent.append(("text", text))
        return 100 + len(self.sent)

    def post_signal(self, sig, img, digits):
        self.sent.append(("signal", sig.id))
        return 555

    def post_update(self, sig, ev_, r, reply_to, img=None):
        self.sent.append(("update", ev_["type"], round(r, 2), reply_to))

    def _pin(self, mid):
        self.pinned.append(mid)


sp.Publisher = FakeEditeur
reglages(elite_mode="live", elite_chat_id=CHAT)
pe2 = el.publisher({"brand": "X"})
check("publisher() : un editeur « elite » avec le bon identifiant, en direct", pe2.audience == "elite" and pe2.cfg["elite_chat_id"] == CHAT and pe2.cfg["elite_live"] is True)

print("C) textes : vrais chiffres, honnetes, sans « 100 % » ni fausse anciennete")


def ligne(score, r, outcome, jour="2026-10-12", mode="live", display="EURUSD"):
    return {"display": display, "score": score, "r": r, "outcome": outcome, "mode": mode, "close_time": f"{jour}T10:00:00", "profile": ""}


hist = [ligne(5, 1.9, "TP3", "2026-10-01"), ligne(6, -1.0, "SL", "2026-10-02", display="GBPUSD"), ligne(5, 0.4, "TP1", "2026-10-05", display="USDJPY"),
        ligne(4, -1.0, "SL", "2026-10-06"), ligne(4, 1.0, "TP2", "2026-10-07"), ligne(7, 1.0, "TP2", "2026-10-08", mode="preview")]
check("positions A+ : score >= 5 ET en direct (les apercus ne comptent pas)", [r["score"] for r in el.a_plus(hist)] == [5, 6, 5])
w = el.welcome_text(hist)
check("bienvenue : ce qu'est le groupe, ce qu'il contient, 5 sur 7", "Groupe VIP" in w and "5 confirmations sur 7" in w and "fiche" in w and "analyse" in w and "suivi" in w)
check("bienvenue : l'historique REEL (A+ puis tous), avec le point de depart du 28 septembre", "28 septembre 2026" in w and "3 positions · TP1 67 %" in w and "5 positions" in w
      and "+1,30 R" in w, w)
check("bienvenue : ni '100 %', ni promesse, ni fausse anciennete ; une phrase honnete sur le 5 sur 7",
      "100 %" not in w and "garanti " not in w.replace("pas une garantie", "") and "depuis des années" not in w and "pas une garantie" in w)
check("balises HTML equilibrees", all(t.count("<b>") == t.count("</b>") for t in (w, el.evening_text([ligne(5, 1.0, "TP2")], 1))))
check("zones : rien a dire = aucun message", el.zones_text([]) is None)
z = el.zones_text([{"display": "EURUSD", "dir": 1, "kind": "FVG", "lo": 1.085, "hi": 1.087, "dist_atr": 0.6, "price": 1.088, "digits": 5}])
check("zones : niveaux reels et phrase d'usage", "EURUSD — achat : Fair Value Gap H4 1.08500 – 1.08700" in z and "avec son analyse" in z, z)
m = el.market_text({"ranked": [("EUR", 0.8), ("JPY", -0.6)]}, {"up": [("EURGBP", 0.3)], "down": [("USDJPY", -0.4)]})
check("point marche : devise en tete / en retrait, ce qui monte et ce qui baisse", "l'euro en tête" in m and "le yen en retrait" in m and "EURGBP +0,3 %" in m and "USDJPY −0,4 %" not in m and "−0,4 %" in m.replace("-", "−"), m)
check("point marche sans donnees : aucun message", el.market_text({}, {}) is None)
check("bilan du soir : rien de cloture et rien d'ouvert = aucun message (jamais de remplissage)", el.evening_text([], 0) is None)
ev_txt = el.evening_text([ligne(5, 1.9, "TP3"), ligne(6, -1.0, "SL", display="GBPUSD")], 2)
check("bilan du soir : gains ET pertes, positions ouvertes", "TP3 atteint (+1,90 R)" in ev_txt and "stop touché (−1,00 R)" in ev_txt and "Encore 2 positions ouvertes" in ev_txt and "gains comme pertes" in ev_txt, ev_txt)
check("bilan de la semaine : rien cette semaine = aucun message", el.weekly_text([], el.a_plus(hist)) is None)
check("libelle d'un resultat : « TP1 atteint » (le reste a pu se clore a l'entree OU au temps : le chiffre en R dit le net)",
      el._label("TP1") == "TP1 atteint" and el._label("SL") == "stop touché" and "stop à l'entrée" not in el._label("TP1"))
rec = el.record_text(hist)
check("historique reel : seulement les positions A+ en direct (pas les 4/7, pas les apercus), de la plus ancienne a la plus recente",
      rec.count("•") == 3 and rec.index("EURUSD") < rec.index("GBPUSD") < rec.index("USDJPY"), rec)
check("historique reel : gains ET pertes, total reel, aucune promesse",
      "TP3 atteint (+1,90 R)" in rec and "stop touché (−1,00 R)" in rec and "TP1 atteint (+0,40 R)" in rec and "Total : 3 positions · TP1 67 %" in rec
      and "gains comme pertes" in rec and "100 %" not in rec and "depuis des années" not in rec, rec)
reel = [{"display": "GBPCAD", "direction": "LONG", "score": 5, "r": 0.948, "outcome": "TP2", "mode": "live", "signal_time": "2026-10-02T09:00:00", "close_time": "2026-10-07T10:45:00"},
        {"display": "CHFJPY", "direction": "SHORT", "score": 5, "r": -1.0, "outcome": "SL", "mode": "live", "signal_time": "2026-10-02T09:00:00", "close_time": "2026-10-02T11:55:00"}]
rec2 = el.record_text(reel)
check("historique reel : date de la prise de position, achat / vente, ordre par date",
      "2 oct. · GBPCAD achat — TP2 atteint (+0,95 R)" in rec2 and "2 oct. · CHFJPY vente — stop touché (−1,00 R)" in rec2, rec2)
check("historique reel : aucune position A+ close = aucun message", el.record_text([ligne(4, 1.0, "TP2")]) is None and el.record_text([]) is None)
check("historique reel : au plus 12 lignes (les plus recentes), le total reste celui de TOUTES les positions",
      el.record_text([ligne(5, 1.0, "TP2", f"2026-10-{d:02d}") for d in range(1, 21)]).count("•") == 12 and "Total : 20 positions" in el.record_text([ligne(5, 1.0, "TP2", f"2026-10-{d:02d}") for d in range(1, 21)]))
from datetime import datetime, timezone  # noqa: E402

EVS = [{"key": "a", "title": "Core CPI m/m", "ccy": "USD", "impact": "High", "time_utc": datetime(2026, 10, 14, 12, 30, tzinfo=timezone.utc), "forecast": "0.3%", "previous": "0.3%"},
       {"key": "b", "title": "Truc inconnu", "ccy": "EUR", "impact": "Medium", "time_utc": datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc), "forecast": "", "previous": ""},
       {"key": "c", "title": "Autre jour", "ccy": "GBP", "impact": "High", "time_utc": datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc), "forecast": "", "previous": ""},
       {"key": "d", "title": "Detail mineur", "ccy": "JPY", "impact": "Low", "time_utc": datetime(2026, 10, 14, 3, 0, tzinfo=timezone.utc), "forecast": "", "previous": ""}]
ag = el.agenda_text(pd.Timestamp("2026-10-14 09:10"), EVS)
check("agenda du jour : les rendez-vous du jour seulement (impact fort / moyen), heures UTC, prevu / precedent",
      "Agenda du jour — mercredi 14 octobre" in ag and "12:30" in ag and "09:00" in ag and "prévu 0.3%" in ag and "Autre jour" not in ag and "Detail mineur" not in ag, ag)
check("agenda du jour : le pied de page du canal d'actualites (alertes 15 min avant) n'est PAS repris ici ; le notre est la",
      "15 minutes avant" not in ag and "arrivent ici, avec leur analyse" in ag and "100 %" not in ag, ag)
check("agenda du jour : rien d'important ce jour-la / calendrier vide = aucun message",
      el.agenda_text(pd.Timestamp("2026-10-16 09:00"), EVS) is None and el.agenda_text(pd.Timestamp("2026-10-14 09:00"), []) is None and el.agenda_text(pd.Timestamp("2026-10-14 09:00"), None) is None)
t_cpi = datetime(2026, 10, 14, 12, 30, tzinfo=timezone.utc)
cpi = [{"key": str(i), "title": t, "ccy": "USD", "impact": "High", "time_utc": t_cpi, "forecast": p, "previous": ""}
       for i, (t, p) in enumerate((("CPI m/m", "0.2%"), ("Core CPI m/m", "0.3%"), ("CPI y/y", "2.5%"), ("Core CPI y/y", "3.1%")))]
ag2 = el.agenda_text(pd.Timestamp("2026-10-14 08:00"), cpi)
check("agenda : meme nom francais a la meme heure -> on distingue (mensuel / annuel / sous-jacent), plus de lignes identiques",
      all(s in ag2 for s in ("Inflation (CPI) : mensuel — prévu 0.2%", "Inflation (CPI) : sous-jacent, mensuel — prévu 0.3%", "Inflation (CPI) : annuel — prévu 2.5%",
                             "Inflation (CPI) : sous-jacent, annuel — prévu 3.1%")), ag2)
check("agenda : un rendez-vous seul garde son nom simple (pas de qualificatif inutile)", "Inflation (CPI) :" not in ag and "Inflation (CPI) — prévu 0.3%" in ag, ag)
chargee = [{"key": str(i), "title": f"Evenement {i}", "ccy": "EUR", "impact": "High" if i < 5 else "Medium", "time_utc": datetime(2026, 10, 14, 6 + i % 12, i % 60, tzinfo=timezone.utc),
            "forecast": "", "previous": ""} for i in range(30)]
ag3 = el.agenda_text(pd.Timestamp("2026-10-14 07:00"), chargee)
check("journee chargee : 18 lignes au plus, tous les impacts forts gardes, tries par heure",
      ag3.count("<b>") == 18 + 1 and all(f"Evenement {i}" in ag3 for i in range(5)), ag3.count("<b>"))
check("balises HTML equilibrees (agenda et historique)", all(t.count("<b>") == t.count("</b>") and t.count("<i>") == t.count("</i>") for t in (ag, ag2, ag3, rec, rec2)))

print("D) animation du groupe VIP : chaque message une seule fois, au bon moment")
M = lambda s: pd.Timestamp(s)  # noqa: E731
FakeEditeur.made.clear()
st = {}
logs = []
zones_vues, market_vues, cal_vues = [], [], []
zfn = lambda now: zones_vues.append(1) or [{"display": "EURUSD", "dir": 1, "kind": "OB", "lo": 1.08, "hi": 1.09, "dist_atr": 0.5, "price": 1.091, "digits": 5}]  # noqa: E731
mfn = lambda: market_vues.append(1) or ({"ranked": [("EUR", 0.5), ("JPY", -0.5)]}, {"up": [], "down": []})  # noqa: E731
cfn = lambda: cal_vues.append(1) or EVS  # noqa: E731
el.run_step(st, {}, M("2026-10-14 09:00"), hist, log=logs.append, zones_fn=zfn, market_fn=mfn)
pub = FakeEditeur.made[-1]
check("premier passage : message de bienvenue, epingle (groupe en direct)", len(pub.sent) == 1 and "mode d'emploi" in pub.sent[0][1] and pub.pinned, pub.sent)
check("... et rien d'autre ce passage-la", zones_vues == [] and st["elite"]["welcome"] == "2026-10-14")
el.run_step(st, {}, M("2026-10-14 09:05"), hist, log=logs.append, zones_fn=zfn, market_fn=mfn, calendar_fn=cfn)
pub = FakeEditeur.made[-1]
check("deuxieme passage : l'historique REEL des positions closes (une fois, juste apres la bienvenue), rien d'autre",
      len(pub.sent) == 1 and "Les positions déjà clôturées" in pub.sent[0][1] and zones_vues == [] and cal_vues == [] and st["elite"]["record"] == "2026-10-14", pub.sent)
el.run_step(st, {}, M("2026-10-14 09:10"), hist, log=logs.append, zones_fn=zfn, market_fn=mfn, calendar_fn=cfn)
pub = FakeEditeur.made[-1]
check("troisieme passage (mercredi 09h10) : l'agenda du jour, UN seul message par passage", len(pub.sent) == 1 and "Agenda du jour" in pub.sent[0][1] and cal_vues == [1] and zones_vues == [], pub.sent)
el.run_step(st, {}, M("2026-10-14 09:15"), hist, log=logs.append, zones_fn=zfn, market_fn=mfn, calendar_fn=cfn)
pub = FakeEditeur.made[-1]
check("passage suivant : les zones du matin, une fois", len(pub.sent) == 1 and "Zones à surveiller" in pub.sent[0][1] and zones_vues == [1])
el.run_step(st, {}, M("2026-10-14 09:20"), hist, log=logs.append, zones_fn=zfn, market_fn=mfn, calendar_fn=cfn)
check("pas de doublon (agenda, zones, historique : le passage suivant n'envoie rien)", zones_vues == [1] and cal_vues == [1] and len(FakeEditeur.made[-1].sent) == 0)
el.run_step(st, {}, M("2026-10-14 13:00"), hist, log=logs.append, zones_fn=zfn, market_fn=mfn, calendar_fn=cfn)
check("13h00 : le point marche, une fois", "Point marché" in FakeEditeur.made[-1].sent[0][1] and market_vues == [1])
el.run_step(st, {}, M("2026-10-14 22:00"), hist, n_open=1, log=logs.append, zones_fn=zfn, market_fn=mfn, calendar_fn=cfn)
check("22h00 : rien de cloture ce jour-la mais 1 position ouverte -> bilan du soir", "Encore 1 position ouverte" in FakeEditeur.made[-1].sent[0][1])
check("compteur de messages du jour tenu (animation limitee)", st["elite"]["posts"]["2026-10-14"] == 6, st["elite"]["posts"])
st2 = {"elite": {"welcome": "x", "record": "x"}}
FakeEditeur.made.clear()
el.run_step(st2, {}, M("2026-10-17 09:00"), hist, log=logs.append, zones_fn=zfn, market_fn=mfn)
check("samedi : pas de zones, pas de point marche", not FakeEditeur.made or not FakeEditeur.made[-1].sent)
ven = [ligne(5, 1.9, "TP3", "2026-10-16"), ligne(6, -1.0, "SL", "2026-10-15", display="GBPUSD")]
st3 = {"elite": {"welcome": "x", "record": "x", "agenda": "2026-10-16", "zones": "2026-10-16", "market": "2026-10-16"}}
FakeEditeur.made.clear()
el.run_step(st3, {}, M("2026-10-16 22:00"), ven, log=logs.append, zones_fn=zfn, market_fn=mfn)
textes = [t[1] for p in FakeEditeur.made for t in p.sent]
check("vendredi soir : bilan du jour PUIS bilan de la semaine (gains et pertes, depuis le 28 septembre)",
      any("Bilan du jour" in t and "TP3 atteint" in t for t in textes) and any("Bilan de la semaine" in t and "Depuis le 28 septembre" in t for t in textes), textes)
st3b = dict(st3)
n_avant = len([t for p in FakeEditeur.made for t in p.sent])
el.run_step(st3, {}, M("2026-10-16 22:30"), ven, log=logs.append, zones_fn=zfn, market_fn=mfn)
check("... une seule fois", len([t for p in FakeEditeur.made for t in p.sent]) == n_avant)
cap = {"elite": {"welcome": "x", "record": "x", "posts": {"2026-10-14": 8}}}
FakeEditeur.made.clear()
el.run_step(cap, {}, M("2026-10-14 09:00"), hist, log=logs.append, zones_fn=zfn, market_fn=mfn, calendar_fn=cfn)
check("plafond de 8 messages d'animation par jour", not FakeEditeur.made or not FakeEditeur.made[-1].sent)
FakeEditeur.made.clear()
el.run_step({"elite": {"welcome": "x", "record": "x"}}, {}, M("2026-10-14 09:00"), hist, t_cycle=__import__("time").time() - 500, log=logs.append, zones_fn=zfn, market_fn=mfn, calendar_fn=cfn)
check("cycle deja trop long : l'animation attend (le robot principal passe avant)", not FakeEditeur.made or not FakeEditeur.made[-1].sent)

print("D2) sources de donnees en panne : quelques essais, jamais de boucle, jamais d'exception, les autres messages partent quand meme")
cal_n = []
cal_none = lambda: cal_n.append(1)  # noqa: E731 - renvoie None : calendrier injoignable
st4 = {"elite": {"welcome": "x", "record": "x"}}
FakeEditeur.made.clear()
logs.clear()
zones_vues.clear()
el.run_step(st4, {}, M("2026-10-14 09:00"), hist, log=logs.append, zones_fn=zfn, market_fn=mfn, calendar_fn=cal_none)
textes4 = [t[1] for p in FakeEditeur.made for t in p.sent]
check("calendrier injoignable : pas d'agenda, mais les zones partent quand meme ; l'agenda n'est pas marque fait",
      len(textes4) == 1 and "Zones à surveiller" in textes4[0] and st4["elite"].get("agenda") != "2026-10-14" and cal_n == [1], textes4)
for h in ("09:05", "09:10", "09:15", "09:20"):
    el.run_step(st4, {}, M(f"2026-10-14 {h}"), hist, log=logs.append, zones_fn=zfn, market_fn=mfn, calendar_fn=cal_none)
check("calendrier injoignable : 3 essais dans la journee, pas un de plus", len(cal_n) == 3, cal_n)
check("... le lendemain, on reessaie (compteur du jour precedent oublie)", (el.run_step(st4, {}, M("2026-10-15 09:00"), hist, log=logs.append, zones_fn=zfn, market_fn=mfn, calendar_fn=cal_none), len(cal_n))[1] == 4)


def cal_panne():
    raise ValueError("calendrier illisible")


st5 = {"elite": {"welcome": "x", "record": "x"}}
logs.clear()
FakeEditeur.made.clear()
el.run_step(st5, {}, M("2026-10-14 09:00"), hist, log=logs.append, zones_fn=zfn, market_fn=mfn, calendar_fn=cal_panne)
check("calendrier qui plante : erreur journalisee, jamais propagee, et les zones partent quand meme",
      any("agenda : erreur ignoree" in m for m in logs) and any("Zones à surveiller" in t[1] for p in FakeEditeur.made for t in p.sent), logs)
st6 = {"elite": {"welcome": "x", "record": "x"}}
FakeEditeur.made.clear()
el.run_step(st6, {}, M("2026-10-16 09:00"), hist, log=logs.append, zones_fn=zfn, market_fn=mfn, calendar_fn=cfn)      # vendredi : le calendrier (EVS) n'a rien ce jour-la
check("calendrier sans rendez-vous important ce jour-la : aucun message, marque fait (pas de nouvel essai)",
      st6["elite"].get("agenda") == "2026-10-16" and not any("Agenda" in t[1] for p in FakeEditeur.made for t in p.sent))


def zones_none(now):
    return None


st7 = {"elite": {"welcome": "x", "record": "x", "agenda": "2026-10-14"}}
z_n = []
for h in ("09:00", "09:05", "09:10", "09:15", "09:20", "09:25"):
    el.run_step(st7, {}, M(f"2026-10-14 {h}"), hist, log=logs.append, zones_fn=lambda now: z_n.append(1), market_fn=mfn, calendar_fn=cfn)
check("zones : source en echec = 4 essais au plus dans la journee", len(z_n) == 4, z_n)


class Refus(FakeEditeur):
    def send_text(self, text, reply_to=None, notify=True, **k):
        return None


sp.Publisher = Refus
sref = {}
el.run_step(sref, {}, M("2026-10-14 09:00"), hist, log=logs.append)
check("Telegram refuse : le message n'est PAS marque comme fait (nouvel essai au cycle suivant)", "welcome" not in sref.get("elite", {}))
sp.Publisher = FakeEditeur


def panne(*a, **k):
    raise RuntimeError("panne de test")


logs.clear()
el.run_step({"elite": {"welcome": "x", "record": "x", "agenda": "2026-10-14"}}, {}, M("2026-10-14 09:00"), hist, log=logs.append, zones_fn=panne, market_fn=mfn, calendar_fn=cfn)
check("une panne interne n'arrive jamais jusqu'au robot", any("erreur ignoree" in m for m in logs), logs)
logs.clear()
el.run_step({"elite": {"welcome": "x", "record": "x"}}, {}, M("2026-10-14 13:00"), hist, log=logs.append, zones_fn=zfn, market_fn=panne, calendar_fn=cfn)
check("... ni celle du point marche", any("market : erreur ignoree" in m for m in logs), logs)
el.CONTROL_FILE.unlink()
FakeEditeur.made.clear()
el.run_step({}, {}, M("2026-10-14 09:00"), hist, log=logs.append)
check("groupe eteint : aucun editeur, aucun message", FakeEditeur.made == [])

print("E) le robot : les positions 5/7 et plus partent AUSSI dans le groupe VIP, rien ne change pour les deux autres groupes")
import signal_runner as runner  # noqa: E402

check("le module est branche", runner.signal_elite is el)


def sig(score, ident, tf="240m", profile=""):
    t = pd.Timestamp("2026-10-14 08:00")
    return Signal(symbol="EURUSD", display="EURUSD", direction="LONG", tf=tf, bar_open=t.isoformat(), signal_time=(t + pd.Timedelta(hours=4)).isoformat(), entry=1.1, sl=1.099,
                  tps=[1.101, 1.102, 1.103], risk=0.001, atr=0.001, spread=0.0, score=score, grade="A+" if score >= 5 else "A", checks={}, zone_kind="OB", zone_lo=1.099,
                  zone_hi=1.1, zone_born=t.isoformat(), rsi=50.0, id=ident, profile=profile)


class PubPrive:
    live, handle = True, ""

    def __init__(self):
        self.posts = []

    def post_signal(self, s, img, digits):
        self.posts.append(("signal", s.id))
        return 9

    def post_update(self, s, e, r, reply_to, img=None):
        self.posts.append(("update", e["type"], reply_to))

    def cta_due(self):
        return False

    def post_public(self, text, img=None, notify=False, cta=None, **k):
        self.posts.append(("public", text))
        return 11


runner.render_signal_chart = lambda *a, **k: None
runner.OUT_DIR = TMP / "out"
runner.utc_now = lambda: pd.Timestamp("2026-10-14 09:00")
reglages(elite_mode="live", elite_chat_id=CHAT)
FakeEditeur.made.clear()
vp, pp_ = PubPrive(), PubPrive()
sn = {"pub_sig": {}, "active": {}, "per_day": {}, "last_signal": {}, "copy_idx": {}}
runner.publish_signal(sig(5, "A5"), None, {"digits": 5}, sn, (vp, pp_), {"brand": "X", "public_signals_per_day": 2})
e_pub = FakeEditeur.made[-1]
check("signal 5/7 : le groupe prive ET le groupe VIP reçoivent la fiche (le public garde son traitement habituel)",
      vp.posts[0] == ("signal", "A5") and e_pub.audience == "elite" and e_pub.sent == [("signal", "A5")] and sn["active"]["A5"]["elite_msg_id"] == 555 and len(pp_.posts) == 1)
FakeEditeur.made.clear()
vp, pp_ = PubPrive(), PubPrive()
sn = {"pub_sig": {}, "active": {}, "per_day": {}, "last_signal": {}, "copy_idx": {}}
runner.publish_signal(sig(4, "A4"), None, {"digits": 5}, sn, (vp, pp_), {"brand": "X", "public_signals_per_day": 2})
check("signal 4/7 : le groupe prive seulement, RIEN dans le groupe VIP", vp.posts[0] == ("signal", "A4") and not any(p.sent for p in FakeEditeur.made) and sn["active"]["A4"]["elite_msg_id"] is None)
reglages(elite_mode="preview", elite_chat_id=CHAT)
FakeEditeur.made.clear()
sn = {"pub_sig": {}, "active": {}, "per_day": {}, "last_signal": {}, "copy_idx": {}}
runner.publish_signal(sig(6, "A6"), None, {"digits": 5}, sn, (PubPrive(), PubPrive()), {"brand": "X", "public_signals_per_day": 2})
check("mode apercu du groupe VIP : la fiche va dans TON chat prive (editeur non-live)", FakeEditeur.made[-1].live is False and FakeEditeur.made[-1].sent == [("signal", "A6")])
el.CONTROL_FILE.unlink()
FakeEditeur.made.clear()
vp, pp_ = PubPrive(), PubPrive()
sn = {"pub_sig": {}, "active": {}, "per_day": {}, "last_signal": {}, "copy_idx": {}}
runner.publish_signal(sig(7, "A7"), None, {"digits": 5}, sn, (vp, pp_), {"brand": "X", "public_signals_per_day": 2})
check("groupe VIP eteint : comme avant (groupe prive + public), aucun message ailleurs", vp.posts[0] == ("signal", "A7") and len(pp_.posts) == 1 and not FakeEditeur.made and sn["active"]["A7"]["elite_msg_id"] is None)


class Casse(FakeEditeur):
    def post_signal(self, s, img, digits):
        raise RuntimeError("Telegram en panne")


reglages(elite_mode="live", elite_chat_id=CHAT)
sp.Publisher = Casse
vp, pp_ = PubPrive(), PubPrive()
sn = {"pub_sig": {}, "active": {}, "per_day": {}, "last_signal": {}, "copy_idx": {}}
ok_pub = runner.publish_signal(sig(5, "A5b"), None, {"digits": 5}, sn, (vp, pp_), {"brand": "X", "public_signals_per_day": 2})
check("le groupe VIP en panne ne casse RIEN : le signal est publie et enregistre dans le groupe prive", ok_pub and vp.posts[0] == ("signal", "A5b") and "A5b" in sn["active"] and sn["active"]["A5b"]["elite_msg_id"] is None)
sp.Publisher = FakeEditeur

print("F) le suivi (TP / stop) est repete dans le groupe VIP, en reponse a SA fiche ; l'historique garde la trace")
runner.HISTORY_FILE = TMP / "hist_elite.jsonl"
b5 = pd.DataFrame({"open": 1.1005, "high": 1.1005, "low": 1.1005, "close": 1.1005}, index=pd.date_range("2026-10-14 12:00", periods=30, freq="5min"))
b5.loc[b5.index[2], "high"], b5.loc[b5.index[4], "high"], b5.loc[b5.index[6], "high"] = 1.1012, 1.1022, 1.1032
runner.get_rates = lambda sid, tf, count, closed_only=True: b5
runner._result_image = lambda *a, **k: None
s5 = sig(5, "SUIVI5")
s5 = Signal.from_dict({**s5.to_dict(), "bar_open": "2026-10-14T08:00:00", "signal_time": "2026-10-14T12:00:00"})
st_ = {"active": {"SUIVI5": {"signal": s5.to_dict(), "sim": TradeSim(s5).to_dict(), "msg_id": 9, "public_msg_id": None, "digits": 5, "mode": "live", "elite_msg_id": 555}}}
FakeEditeur.made.clear()
vp = PubPrive()
runner.update_active(st_, (vp, PubPrive()), {"public_progress": True})
ups = [e for p in FakeEditeur.made for e in p.sent]
check("TP1, TP2, TP3 repetes dans le groupe VIP, en reponse a SA fiche (555) ; le groupe prive a les siens (reponse a la fiche 9)",
      [u[1] for u in ups] == ["TP1", "TP2", "TP3"] and all(u[3] == 555 for u in ups) and [p[1] for p in vp.posts if p[0] == "update"] == ["TP1", "TP2", "TP3"]
      and all(p[2] == 9 for p in vp.posts if p[0] == "update"), (ups, vp.posts))
h = runner.load_history()
check("l'historique note que la position a aussi ete envoyee au groupe VIP", len(h) == 1 and h[0]["elite"] is True and h[0]["score"] == 5 and abs(h[0]["r"] - 1.9) < 1e-6, h)
st_ = {"active": {"SUIVI4": {"signal": Signal.from_dict({**s5.to_dict(), "id": "SUIVI4", "score": 4}).to_dict(), "sim": TradeSim(s5).to_dict(), "msg_id": 9, "public_msg_id": None,
                             "digits": 5, "mode": "live", "elite_msg_id": None}}}
FakeEditeur.made.clear()
runner.update_active(st_, (PubPrive(), PubPrive()), {"public_progress": True})
check("une position qui n'est pas dans le groupe VIP n'y envoie aucun suivi", not any(p.sent for p in FakeEditeur.made))

print("G) jours de NFP : le groupe VIP recoit aussi les messages, sans rien changer aux autres groupes")
T = ev.release_utc("2026-11-06")
check("groupe VIP eteint : les messages NFP se comptent comme avant (prive + public)", (el.CONTROL_FILE.unlink() if el.CONTROL_FILE.exists() else None) or
      ev.due_kinds({"events": {"nfp": {"2026-11-06": {"pre_vip": "x", "pre_public": "x"}}}}, T - pd.Timedelta(hours=20)) == [])
reglages(elite_mode="live", elite_chat_id=CHAT)
check("groupe VIP actif : le message de la veille reste du tant que le groupe VIP ne l'a pas eu",
      ev.due_kinds({"events": {"nfp": {"2026-11-06": {"pre_vip": "x", "pre_public": "x"}}}}, T - pd.Timedelta(hours=20)) == ["pre"]
      and ev.due_kinds({"events": {"nfp": {"2026-11-06": {"pre_vip": "x", "pre_public": "x", "pre_elite": "x"}}}}, T - pd.Timedelta(hours=20)) == [])


class Quel:
    def __init__(self):
        self.sent = []

    def send_text(self, text, notify=True, **k):
        self.sent.append(text)
        return 1

    def post_public(self, text, notify=False, cta=None, **k):
        self.sent.append(text)
        return 1


FakeEditeur.made.clear()
vq, pq = Quel(), Quel()
stg = {}
w_ = {"date": "2026-11-06", "ref": "2026-10", "release": T, "minutes": 0.0}
ev.deliver("pre", {"vip": "COMPLET", "public": "COURT"}, stg, w_, {"live": True}, (vq, pq), "live")
e_pub = FakeEditeur.made[-1]
check("la veille du NFP : groupe prive (COMPLET), public (COURT) et groupe VIP (COMPLET, dans SON groupe), chacun marque a part",
      vq.sent == ["COMPLET"] and pq.sent == ["COURT"] and e_pub.audience == "elite" and e_pub.sent == [("text", "COMPLET")]
      and set(stg["events"]["nfp"]["2026-11-06"]) == {"pre_vip", "pre_public", "pre_elite"}, (vq.sent, pq.sent, e_pub.sent))
class CasseTexte(FakeEditeur):
    def send_text(self, text, reply_to=None, notify=True, **k):
        raise RuntimeError("Telegram en panne")


sp.Publisher = CasseTexte
stg2 = {}
vq, pq = Quel(), Quel()
ev.deliver("pre", {"vip": "COMPLET", "public": "COURT"}, stg2, w_, {"live": True}, (vq, pq), "live")
check("groupe VIP en panne : les deux autres groupes ont leur message quand meme", vq.sent == ["COMPLET"] and pq.sent == ["COURT"] and "pre_elite" not in stg2["events"]["nfp"]["2026-11-06"])
sp.Publisher = FakeEditeur
el.CONTROL_FILE.unlink()

print("H) /etat")
reglages(elite_mode="preview", elite_chat_id=CHAT)
runner.HISTORY_FILE = TMP / "hist_elite.jsonl"
txt = runner.status_text({"active": {}, "per_day": {}, "sched": {}, "started": "2026-01-01"}, pd.Timestamp("2026-10-14 12:00"))
check("/etat : le groupe VIP, son mode et ses chiffres reels", "Groupe VIP 📊 : aperçu dans ton chat privé" in txt and "positions 5/7 et plus" in txt, txt)
el.CONTROL_FILE.unlink()
txt = runner.status_text({"active": {}, "per_day": {}, "sched": {}, "started": "2026-01-01"}, pd.Timestamp("2026-10-14 12:00"))
check("/etat : groupe VIP eteint = aucune ligne en plus", "Groupe VIP" not in txt)

print(f"\n{ok} verifications reussies, aucune ne touche a Telegram, au reseau ni a MT5.")
