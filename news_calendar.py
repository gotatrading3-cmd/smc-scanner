"""
news_calendar.py - Calendrier economique du canal "Info du monde" : agenda du jour, alerte avant publication,
message "publication maintenant", agenda de la semaine - avec une explication pedagogique de chaque indicateur.

Source : le calendrier hebdomadaire gratuit de Forex Factory (nfs.faireconomy.media). Il donne l'heure, la devise,
l'impact, le chiffre PREVU et le PRECEDENT - mais PAS le chiffre reel : celui-ci arrive dans le canal via les titres
des agences (ForexLive, FXStreet, MarketWatch...), que news_runner.py met en avant. Source non officielle : peut
changer ou disparaitre, le bot fonctionne sans (il saute simplement la partie calendrier).

Les explications ("lecture") restent volontairement prudentes : "souvent", "en general". Aucune promesse de resultat.
"""
from __future__ import annotations
import html
import re
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

import requests

CAL_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
UA = {"User-Agent": "GOTA-NewsBot/1.0 (+https://github.com/gotatrading3-cmd/smc-scanner)"}

FLAGS = {"USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "🇬🇧", "JPY": "🇯🇵", "CAD": "🇨🇦", "AUD": "🇦🇺", "NZD": "🇳🇿",
         "CHF": "🇨🇭", "CNY": "🇨🇳"}
CCY_NAME = {"USD": "le dollar", "EUR": "l'euro", "GBP": "la livre", "JPY": "le yen", "CAD": "le dollar canadien",
            "AUD": "le dollar australien", "NZD": "le dollar néo-zélandais", "CHF": "le franc suisse", "CNY": "le yuan"}
IMPACT_DOT = {"High": "🔴", "Medium": "🟠", "Low": "🟡"}
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
        "novembre", "décembre"]

# ---- lecture type selon le sens de l'indicateur -------------------------------------------------------------
READ_UP = "Un chiffre plus fort que prévu soutient souvent {c} ; plus faible, il le pénalise souvent."
READ_DOWN = "Un chiffre plus élevé que prévu pèse souvent sur {c} ; plus bas, il le soutient souvent."
READ_INFLATION = ("Une inflation plus forte que prévue fait anticiper des taux plus hauts plus longtemps, ce qui soutient "
                  "souvent {c} ; plus faible, l'effet est souvent inverse.")
READ_RATE = ("Le marché compare la décision au taux attendu, mais surtout au ton du discours : plus ferme que prévu "
             "soutient souvent {c}, plus prudent le pénalise souvent.")
READ_TONE = "Ici, c'est le ton qui compte : plus ferme que prévu soutient souvent {c}, plus prudent le pénalise souvent."

# (regex sur le titre en minuscules, nom FR, definition, lecture, sens)
GLOSSARY: List[Tuple[str, str, str, str]] = [
    (r"adp non-?farm", "Emplois privés ADP",
     "Estimation privée des créations d'emplois aux États-Unis, publiée avant le NFP : un avant-goût du grand rendez-vous.",
     READ_UP),
    (r"non-?farm (employment change|payrolls)", "Créations d'emplois non agricoles (NFP)",
     "Nombre d'emplois créés le mois dernier aux États-Unis, hors agriculture. C'est l'un des chiffres les plus suivis de la planète.",
     READ_UP),
    (r"unemployment rate", "Taux de chômage",
     "Part de la population active qui cherche un emploi sans en trouver.", READ_DOWN),
    (r"average hourly earnings", "Salaire horaire moyen",
     "Évolution des salaires : des salaires qui montent vite alimentent l'inflation, donc surveillés par les banques centrales.",
     READ_UP),
    (r"core pce|pce price", "Inflation PCE",
     "L'indicateur d'inflation préféré de la Fed. « Core » exclut l'énergie et l'alimentation, plus volatiles.", READ_INFLATION),
    (r"core cpi|cpi|consumer price", "Inflation (CPI)",
     "Évolution des prix à la consommation : la banque centrale s'en sert pour décider de ses taux. « Core » exclut l'énergie et l'alimentation.",
     READ_INFLATION),
    (r"ppi|producer price", "Prix à la production (PPI)",
     "Évolution des prix payés par les producteurs : souvent un signal avancé de l'inflation de demain.", READ_INFLATION),
    (r"retail sales", "Ventes au détail",
     "Dépenses des consommateurs dans les commerces, moteur principal de la croissance.", READ_UP),
    (r"gdp", "PIB (croissance)",
     "Valeur de tout ce que le pays produit : mesure la santé globale de l'économie.", READ_UP),
    (r"ism manufacturing|manufacturing pmi|flash manufacturing", "PMI manufacturier",
     "Enquête auprès des directeurs d'achat de l'industrie : au-dessus de 50 = expansion, en dessous = contraction.", READ_UP),
    (r"ism services|services pmi|flash services|non-manufacturing", "PMI services",
     "Enquête auprès des directeurs d'achat des services : au-dessus de 50 = expansion, en dessous = contraction.", READ_UP),
    (r"unemployment claims|jobless claims", "Inscriptions hebdomadaires au chômage",
     "Nombre de personnes qui demandent une allocation chômage cette semaine : un thermomètre rapide de l'emploi.", READ_DOWN),
    (r"jolts", "Offres d'emploi (JOLTS)",
     "Nombre de postes à pourvoir aux États-Unis : mesure la tension du marché du travail.", READ_UP),
    (r"claimant count", "Demandeurs d'emploi (Royaume-Uni)",
     "Variation du nombre de personnes qui touchent des allocations chômage.", READ_DOWN),
    (r"employment change", "Variation de l'emploi",
     "Nombre d'emplois créés ou perdus le mois dernier.", READ_UP),
    (r"federal funds rate", "Décision de taux de la Fed",
     "Le taux directeur américain, l'un des prix les plus importants du monde : il influence le dollar, les actions et l'or.",
     READ_RATE),
    (r"fomc statement", "Communiqué de la Fed",
     "Texte publié après la réunion de la Fed : chaque mot est scruté.", READ_TONE),
    (r"fomc press conference|fed chair .* speaks|fed chair", "Conférence de presse de la Fed",
     "Le président de la Fed explique la décision et répond aux journalistes : les marchés cherchent des indices sur la suite.",
     READ_TONE),
    (r"fomc meeting minutes|fomc minutes", "Compte rendu de la Fed",
     "Détail des discussions de la dernière réunion, publié environ trois semaines après : il révèle les divisions internes.",
     READ_TONE),
    (r"main refinancing rate|deposit facility rate|ecb interest rate", "Décision de taux de la BCE",
     "Le taux directeur de la zone euro : il influence l'euro et les marchés européens.", READ_RATE),
    (r"ecb press conference|monetary policy statement|monetary policy meeting", "Discours / communiqué de banque centrale",
     "Explication de la décision de politique monétaire : le ton compte autant que le taux.", READ_TONE),
    (r"official bank rate|boe", "Décision de taux de la Banque d'Angleterre",
     "Le taux directeur britannique : il influence la livre.", READ_RATE),
    (r"boj policy rate|boj outlook|boj monetary", "Décision de la Banque du Japon",
     "La politique de taux japonaise : elle influence fortement le yen.", READ_RATE),
    (r"cash rate|rba", "Décision de taux de la banque centrale d'Australie",
     "Le taux directeur australien : il influence le dollar australien.", READ_RATE),
    (r"overnight rate|boc", "Décision de taux de la Banque du Canada",
     "Le taux directeur canadien : il influence le dollar canadien.", READ_RATE),
    (r"snb policy rate|snb", "Décision de la Banque nationale suisse",
     "Le taux directeur suisse : il influence le franc suisse.", READ_RATE),
    (r"official cash rate|rbnz", "Décision de taux de la banque centrale de Nouvelle-Zélande",
     "Le taux directeur néo-zélandais : il influence le dollar néo-zélandais.", READ_RATE),
    (r"speaks|speech|testifies|testimony", "Prise de parole d'un responsable de banque centrale",
     "Le marché cherche des indices sur la future politique de taux dans chaque phrase.", READ_TONE),
    (r"consumer sentiment|consumer confidence|uom", "Confiance des consommateurs",
     "Enquête sur le moral des ménages : un moral élevé annonce souvent plus de consommation.", READ_UP),
    (r"crude oil inventories", "Stocks de pétrole brut (EIA)",
     "Variation hebdomadaire des réserves américaines : une baisse plus forte que prévue soutient souvent le pétrole.",
     "Un stock qui baisse plus que prévu soutient souvent le pétrole et les devises liées ; une hausse plus forte pèse souvent dessus."),
    (r"trade balance", "Balance commerciale",
     "Écart entre exportations et importations : un excédent qui grandit soutient souvent la monnaie.", READ_UP),
    (r"industrial production", "Production industrielle",
     "Production des usines, des mines et de l'énergie.", READ_UP),
    (r"durable goods", "Commandes de biens durables",
     "Commandes de produits conçus pour durer (machines, voitures...) : un indicateur de l'investissement.", READ_UP),
    (r"housing starts|building permits|home sales", "Immobilier",
     "Activité du secteur immobilier, très sensible aux taux d'intérêt.", READ_UP),
    (r"ifo|zew|sentix|business climate|business confidence|economic sentiment", "Climat des affaires",
     "Enquête sur le moral des entreprises ou des investisseurs : un signal avancé de l'activité.", READ_UP),
]
_GLOSSARY = [(re.compile(rx), name, what, reading) for rx, name, what, reading in GLOSSARY]


def explain(title: str) -> Optional[Tuple[str, str, str]]:
    """(nom FR, definition, lecture-modele) pour un titre d'evenement, ou None si inconnu."""
    t = title.lower()
    for rx, name, what, reading in _GLOSSARY:
        if rx.search(t):
            return name, what, reading
    return None


# ---- donnees ------------------------------------------------------------------------------------------------
def fetch_calendar(timeout: float = 15.0) -> Optional[List[dict]]:
    """Evenements de la semaine : [{key, title, ccy, impact, time_utc, forecast, previous}], ou None si injoignable."""
    try:
        r = requests.get(CAL_URL, headers=UA, timeout=timeout)
        if r.status_code != 200:
            return None
        out = []
        for e in r.json():
            try:
                t = datetime.fromisoformat(e["date"]).astimezone(timezone.utc)
            except Exception:
                continue
            if e.get("impact") not in IMPACT_DOT:        # "Holiday" etc. : pas d'horaire de publication
                continue
            out.append({"key": f"{t.isoformat()}|{e.get('country')}|{e.get('title')}", "title": e.get("title", ""),
                        "ccy": e.get("country", ""), "impact": e["impact"], "time_utc": t,
                        "forecast": (e.get("forecast") or "").strip(), "previous": (e.get("previous") or "").strip()})
        return out
    except Exception:
        return None


# ---- mise en forme ------------------------------------------------------------------------------------------
def _esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def _event_block(ev: dict, with_explanation: bool) -> str:
    ex = explain(ev["title"])
    name = ex[0] if ex else ev["title"]
    lines = [f"{IMPACT_DOT[ev['impact']]} {FLAGS.get(ev['ccy'], '')} <b>{_esc(name)}</b>"]
    nums = []
    if ev["forecast"]:
        nums.append(f"Prévu : <b>{_esc(ev['forecast'])}</b>")
    if ev["previous"]:
        nums.append(f"Précédent : {_esc(ev['previous'])}")
    if nums:
        lines.append(" · ".join(nums))
    if with_explanation and ex:
        ccy = CCY_NAME.get(ev["ccy"], ev["ccy"] or "la devise concernée")
        lines.append(f"📖 <i>{_esc(ex[1])}</i>")
        lines.append(f"📊 {_esc(ex[2].format(c=ccy))}")
    return "\n".join(lines)


def _hhmm(ev: dict) -> str:
    return ev["time_utc"].strftime("%H:%M")


def _group_by_time(evs: List[dict]) -> List[Tuple[datetime, List[dict]]]:
    groups: Dict[datetime, List[dict]] = {}
    for e in evs:
        groups.setdefault(e["time_utc"], []).append(e)
    return sorted(groups.items())


def agenda_message(evs: List[dict], day: datetime) -> Optional[str]:
    """Agenda d'une journee UTC (impact fort + moyen). None s'il n'y a rien d'important."""
    todays = [e for e in evs if e["time_utc"].date() == day.date() and e["impact"] in ("High", "Medium")]
    if not todays:
        return None
    head = (f"📅 <b>Agenda du jour — {JOURS[day.weekday()]} {day.day} {MOIS[day.month - 1]}</b>\n"
            f"<i>Heures UTC · 🔴 impact fort · 🟠 impact moyen</i>")
    body: List[str] = []
    for t, group in _group_by_time(todays)[:18]:
        for e in sorted(group, key=lambda x: x["impact"] != "High"):
            ex = explain(e["title"])
            name = ex[0] if ex else e["title"]
            nums = " · ".join(x for x in (f"prévu {_esc(e['forecast'])}" if e["forecast"] else "",
                                         f"précédent {_esc(e['previous'])}" if e["previous"] else "") if x)
            body.append(f"<b>{_hhmm(e)}</b> {IMPACT_DOT[e['impact']]} {FLAGS.get(e['ccy'], '')} {_esc(name)}"
                        + (f" — {nums}" if nums else ""))
    foot = "🔔 Les rendez-vous majeurs sont annoncés ici 15 minutes avant, puis à l'instant de leur publication."
    return head + "\n\n" + "\n".join(body) + "\n\n" + foot


def week_message(evs: List[dict], start: datetime) -> Optional[str]:
    """Les rendez-vous a impact fort de la semaine qui commence."""
    end = start + timedelta(days=7)
    week = sorted((e for e in evs if start <= e["time_utc"] < end and e["impact"] == "High"), key=lambda x: x["time_utc"])
    if not week:
        return None
    lines = [f"🗓️ <b>La semaine à venir — rendez-vous à impact fort</b>", "<i>Heures UTC</i>"]
    last_day = None
    for e in week[:25]:
        d = e["time_utc"].date()
        if d != last_day:
            lines.append(f"\n<b>{JOURS[e['time_utc'].weekday()].capitalize()} {e['time_utc'].day} {MOIS[e['time_utc'].month - 1]}</b>")
            last_day = d
        ex = explain(e["title"])
        lines.append(f"{_hhmm(e)} 🔴 {FLAGS.get(e['ccy'], '')} {_esc(ex[0] if ex else e['title'])}")
    lines.append("\n🔔 Chaque rendez-vous sera rappelé ici avant sa publication.")
    return "\n".join(lines)


def alert_message(group: List[dict], minutes: int) -> str:
    t = _hhmm(group[0])
    head = f"⏰ <b>Dans {minutes} minutes</b> · {t} UTC"
    return head + "\n\n" + "\n\n".join(_event_block(e, True) for e in group)


def release_message(group: List[dict]) -> str:
    t = _hhmm(group[0])
    head = f"⚡ <b>PUBLICATION MAINTENANT</b> · {t} UTC"
    foot = "👇 Le chiffre officiel s'affiche juste en dessous dès que les agences le publient."
    return head + "\n\n" + "\n\n".join(_event_block(e, True) for e in group) + "\n\n" + foot


# ---- orchestration (appelee par news_runner a chaque cycle) -------------------------------------------------
def run_calendar(st: dict, cache: dict, now: datetime, send: Callable[[str, bool], bool], log: Callable[[str], None],
                 first_run: bool = False) -> int:
    """Poste ce qui est du : agenda (a partir de 06h30 UTC), alerte T-15 min, publication, agenda de semaine (dimanche soir).
    send(texte, sonore) -> True si envoye. Retourne le nombre de messages envoyes. Ne leve jamais d'exception."""
    try:
        if now.timestamp() - cache.get("cal_fetched", 0) > 600 or "cal" not in cache:
            data = fetch_calendar()
            if data is not None:
                # le cache est ecrit en JSON entre deux cycles : dates en texte ISO
                cache["cal"] = [dict(e, time_utc=e["time_utc"].isoformat()) for e in data]
                cache["cal_fetched"] = now.timestamp()
            else:
                cache["cal_fetched"] = now.timestamp() - 480       # on reessaie dans ~2 min
        evs: List[dict] = [dict(e, time_utc=datetime.fromisoformat(e["time_utc"])) for e in (cache.get("cal") or [])]
        if not evs:
            return 0
        done: Dict[str, List[str]] = st.setdefault("cal_done", {})
        sent = 0

        # 1) agenda du jour (une fois par jour, des 06h30 UTC)
        dkey = f"agenda|{now.date().isoformat()}"
        if dkey not in done and now.weekday() < 5 and (now.hour, now.minute) >= (6, 30):
            msg = agenda_message(evs, now)
            if msg and send(msg, False):
                sent += 1
            done[dkey] = ["x"]

        # 2) agenda de la semaine (dimanche a partir de 16h UTC, une fois)
        if now.weekday() == 6 and now.hour >= 16:
            nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            wkey = f"week|{nxt.date().isoformat()}"
            if wkey not in done:
                msg = week_message(evs, nxt)
                if msg and send(msg, False):
                    sent += 1
                done[wkey] = ["x"]

        # 3) alertes + publications des evenements a impact fort (regroupes par heure et devise)
        highs = [e for e in evs if e["impact"] == "High"]
        groups: Dict[Tuple[datetime, str], List[dict]] = {}
        for e in highs:
            groups.setdefault((e["time_utc"], e["ccy"]), []).append(e)
        for (t, ccy), group in sorted(groups.items()):
            gkey = f"{t.isoformat()}|{ccy}"
            stages = done.setdefault(gkey, [])
            delta = (t - now).total_seconds() / 60.0
            if "alert" not in stages and 0 < delta <= 15.5:
                if not first_run and send(alert_message(group, max(1, round(delta))), True):
                    sent += 1
                stages.append("alert")
            if "now" not in stages and -10 <= delta <= 0.5:
                if not first_run and send(release_message(group), True):
                    sent += 1
                stages.append("now")
        # menage de l'etat (on garde 10 jours)
        limit = (now - timedelta(days=10)).isoformat()
        for k in [k for k in done if k[:10] < limit[:10] and not k.startswith(("agenda|", "week|"))]:
            del done[k]
        return sent
    except Exception as ex:                                   # jamais bloquant pour le reste du bot
        log(f"[CAL] erreur ignoree : {ex}")
        return 0
