"""
signal_content.py - Textes du groupe PUBLIC : marketing HUMAIN et HONNETE.

Ton : comme si c'etait le trader lui-meme qui ecrivait au fil de la journee - "je" pour ce qu'il regarde et ce qu'il fait,
"on" pour l'equipe et le VIP, francais courant, emojis mesures. JAMAIS de vocabulaire de machine ("scan termine", "analyse en cours",
"UTC", "bougie H4", "filtres validés"...). Chaque type de message a plusieurs variantes qui TOURNENT (jamais deux fois la meme
d'affilee) pour que le groupe ne sonne pas robotique.

Regles d'ecriture (volontairement dans le code) :
- tout ce qui est annonce est REEL : signaux, resultats (gains ET pertes), chiffres de marche calcules
- aucune promesse de gain, aucun temoignage invente, aucune fausse urgence / rarete
- "deja dans le VIP" : vrai (le VIP est publie en premier) ; "deja en profit" : uniquement une fois un objectif reellement atteint
- le groupe public montre QUELQUES signaux en entier ; le VIP les recoit tous, avec l'analyse et le suivi complets
- le public ne voit JAMAIS combien ni quelles paires sont analysees / surveillees (demande du 2026-09-30) ; en revanche il voit
  combien de positions on a prises dans le VIP et combien sont encore ouvertes (demande du 2026-10-05)
- le lien pour nous ecrire est ajoute sous chaque image par le publieur (signal_publisher.Publisher.post_public)
"""
from __future__ import annotations
import html
from typing import Dict, List, Optional

from signal_market import NAMES, pct


# ------------------------------------------------------------------ rotation des variantes
def pick(st: dict, kind: str, variants: List[str]) -> str:
    """Variante suivante pour ce type de message (rotation persistante dans l'etat : pas de repetition immediate)."""
    idx = st.setdefault("copy_idx", {})
    i = idx.get(kind, 0) % len(variants)
    idx[kind] = i + 1
    return variants[i]


def _r(r: float) -> str:
    return f"{r:+.2f}".replace(".", ",") + " R"


def _handle(handle: str) -> str:
    return handle or "l'équipe GOTA"


# ------------------------------------------------------------------ signal montre en public et suivi
def public_signal(st: dict, pair: str, tf: str, direction: str) -> str:
    """Legende d'un signal montre en entier dans le groupe PUBLIC (le lien pour nous ecrire est ajoute sous l'image)."""
    long_ = direction == "LONG"
    d = dict(pair=html.escape(pair), tf=tf, dot="🟢" if long_ else "🔴", w="ACHAT" if long_ else "VENTE")
    v = [
        "{dot} <b>{w} {pair}</b> · {tf}\n\nJe viens de le prendre. Il est déjà dans le VIP, avec toute l'analyse. Je vous le montre ici et je vous dis où il en est à chaque étape 👀",
        "{dot} <b>{pair}</b> · {w} · {tf}\n\nLes membres du VIP l'ont reçu dès l'ouverture. Ici, je le suis avec vous : objectif atteint ou stop touché, je vous le dis.",
        "{dot} Nouveau trade : <b>{w} {pair}</b> ({tf}).\n\nDéjà envoyé dans le VIP. Je le suis ici, qu'il gagne ou qu'il perde 🙂",
        "{dot} Le voilà : <b>{w} {pair}</b> ({tf}).\n\nLe plan complet et l'analyse sont déjà dans le VIP. Regardez comment il évolue, je poste chaque étape.",
        "{dot} Je viens d'envoyer <b>{w} {pair}</b> ({tf}) au VIP : le plan complet y est déjà.\n\nIci, vous pouvez suivre le trade en direct 👀",
    ]
    return pick(st, "public_signal", v).format(**d)


def footer(kind: str, r: float = 0.0, cta: bool = True) -> str:
    """Texte de la barre en bas des images publiques. kind : 'new' ou le type d'evenement ; r : resultat cumule du trade (R) ;
    cta : ajoute l'invitation « écris-nous pour rejoindre » (1 message sur 7 seulement). 'Deja en profit' seulement si le
    resultat est reellement positif."""
    if kind == "new":
        base = "Déjà envoyé dans le groupe VIP"
    elif r > 0.05:
        base = "Déjà en profit côté VIP"
    else:
        base = "Tous les signaux sont suivis en direct dans le VIP"
    return base + ("  ·  écris-nous pour rejoindre" if cta else "")


def progress(st: dict, kind: str, pair: str, r: float) -> str:
    """Suivi public d'un trade montre : TP1 / TP2 / TP3 / SL / BE / EXPIRED. Gains ET pertes annonces pareil.
    'deja en profit' n'apparait que sur un objectif reellement atteint."""
    p, rr = html.escape(pair), _r(r)
    bank: Dict[str, List[str]] = {
        "TP1": [
            "🎯 <b>TP1 touché sur {p}</b> — ceux qui ont suivi le signal dans le VIP sont déjà dans le vert.\n\nJ'ai remonté le stop à l'entrée : le reste du trade est protégé. On laisse courir 👀",
            "✅ <b>{p}</b> : premier objectif touché ! Côté VIP, c'est déjà dans le vert et le stop est passé à l'entrée. La suite, on la suit ensemble 🙂",
            "👏 <b>{p}</b> avance comme prévu : TP1 atteint. Dans le VIP, une première partie est déjà sécurisée et le reste est protégé (stop à l'entrée).",
        ],
        "TP2": [
            "🎯🎯 <b>{p}</b> : deuxième objectif atteint ! Deux objectifs sur trois validés côté VIP, le stop est déjà à l'entrée. Il reste la dernière partie, en route vers l'objectif final.",
            "🔥 TP2 sur <b>{p}</b> ! Le trade se déroule comme prévu — les membres VIP sont déjà bien dans le vert. On regarde la suite.",
        ],
        "TP3": [
            "🏆 <b>{p}</b> : objectif final atteint ! Trade bouclé de A à Z. Résultat : {rr}.\n\nBravo aux membres du VIP qui l'ont suivi depuis l'ouverture 🙌",
            "✨ Trade terminé sur <b>{p}</b> : les trois objectifs sont validés ({rr}). Il était dans le VIP dès l'ouverture — merci à celles et ceux qui nous font confiance 🤝",
        ],
        "BE": [
            "⏹ <b>{p}</b> : le prix est revenu à l'entrée après avoir donné une première partie du gain — je referme le reste sans perte. Résultat final : {rr}.\n\nC'est justement pour ça qu'on remonte le stop : on protège ce qui est déjà gagné.",
        ],
        "SL": [
            "🛑 <b>{p}</b> : le stop est touché ({rr}).\n\nÇa arrive, et c'est prévu dans le plan : la perte est limitée d'avance et on passe au trade suivant. Je vous le dis aussi quand ça ne passe pas — c'est ça, la transparence.",
            "📉 Petite perte sur <b>{p}</b> ({rr}) : le marché n'a pas suivi. Le stop a fait son travail, je garde la tête froide et j'avance.\n\nOn publie les gains comme les pertes — sinon ça n'aurait aucun sens.",
        ],
        "EXPIRED": [
            "⌛ <b>{p}</b> : le délai prévu est atteint, je clôture le reste du trade au marché. Résultat final : {rr}. Le plan est respecté, on passe à la suite.",
        ],
    }
    return pick(st, "progress_" + kind, bank.get(kind, bank["EXPIRED"])).format(p=p, rr=rr)


# ------------------------------------------------------------------ rendez-vous quotidiens (donnees reelles)
# NB (2026-09-30, demande explicite) : le groupe PUBLIC ne doit jamais voir combien ni quelles paires sont
# analysees/surveillees - seulement une indication qualitative. Le detail (comptes, noms) reste reserve au VIP.
def _trend_line(snap: dict) -> str:
    if not snap.get("n_pairs"):
        return ""
    maj = "plutôt haussière" if snap.get("up", 0) >= snap.get("down", 0) else "plutôt baissière"
    return f"La tendance de fond est {maj} sur l'ensemble du marché en ce moment."


def _watch_line(watch: List[str]) -> str:
    if not watch:
        return "Rien d'intéressant à proximité pour l'instant : la patience fait partie du plan."
    return "👀 Il y a des zones qui m'intéressent en ce moment. Le détail, c'est dans le VIP."


def morning(st: dict, snap: dict, watch: List[str], handle: str) -> str:
    ranked = snap.get("ranked") or []
    if len(ranked) < 2:
        return "☀️ Bonjour la team ! Je regarde les marchés ce matin et je vous préviens ici dès qu'il y a du mouvement 💪"
    (t, tp), (b, bp) = ranked[0], ranked[-1]
    d = dict(top=NAMES.get(t, t), top_pct=pct(tp), bot=NAMES.get(b, b), bot_pct=pct(bp),
             trend=_trend_line(snap), watch=_watch_line(watch), h=_handle(handle))
    v = [
        "☀️ Bonjour la team ! Petit tour d'horizon avant l'ouverture.\n\nCôté devises, {top} est la plus forte de la semaine ({top_pct}) et {bot} la plus faible ({bot_pct}). {trend}\n\n{watch}\n\nDès qu'un trade se met en place, vous le voyez ici — et le plan complet part dans le VIP.",
        "🌅 Salut tout le monde ! Ce que je vois ce matin : {top} en tête ({top_pct}), {bot} en queue de peloton ({bot_pct}). {trend}\n\n{watch}\n\nJe vous tiens au courant tout au long de la journée 💪",
        "☕ Café en main, j'ouvre mes graphiques.\n\n{top} domine cette semaine ({top_pct}), {bot} souffre ({bot_pct}). {trend}\n\n{watch}\n\nLe détail des trades, comme d'habitude, c'est dans le VIP.",
    ]
    return pick(st, "morning", v).format(**d)


def _p2(x: float) -> str:
    return f"{x:+.2f} %".replace(".", ",")


def scan_report(st: dict, hh: int, n_symbols: int, n_sig: int, n_active: int, watch: List[str]) -> str:
    """Petit point apres chaque cloture de 4 heures (donnees reelles) : combien de positions prises dans le VIP aujourd'hui et
    combien encore ouvertes, ecrit comme le ferait le trader. Ne revele ni combien ni quelles paires sont suivies (VIP uniquement)."""
    a = _activity(n_sig, n_active, len(watch))
    v = [
        "🧭 Je viens de refaire le point après la clôture de {hh}h (GMT). {a}",
        "👀 Petit point de {hh}h GMT : je viens de repasser mes graphiques en revue. {a}",
        "☕ J'ai jeté un œil aux graphiques à {hh}h GMT. {a}",
        "📋 Point de {hh}h GMT : tout est à jour de mon côté. {a}",
    ]
    return pick(st, "scan", v).format(hh=int(hh), a=a)


def ranking(st: dict, snap: dict) -> Optional[str]:
    """Classement des devises sur 5 jours (donnees reelles), pour le samedi. None si les donnees manquent."""
    ranked = snap.get("ranked") or []
    if len(ranked) < 4:
        return None
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (cur, p) in enumerate(ranked):
        name = NAMES.get(cur, cur)
        lines.append(f"{medals[i] if i < 3 else str(i + 1) + '.'} {name[0].upper() + name[1:]} {pct(p)}")
    head = pick(st, "ranking", ["📊 Classement des devises sur les 5 derniers jours :", "🏁 Force des devises cette semaine :",
                                "🌍 Qui a dominé la semaine ? Force des devises sur 5 jours :"])
    return head + "\n" + "\n".join(lines)


def movers(st: dict, mv: dict) -> str:
    """Plus gros mouvements du jour sur les paires FX (donnees reelles)."""
    def fmt(rows) -> str:
        return " · ".join(f"{s} {_p2(p)}" for s, p in rows)
    head = pick(st, "movers", ["📈 Ce qui bouge aujourd'hui :", "🔥 Les plus gros mouvements du jour :",
                               "👀 Les paires les plus actives aujourd'hui :"])
    lines = ([f"▲ {fmt(mv['up'])}"] if mv.get("up") else []) + ([f"▼ {fmt(mv['down'])}"] if mv.get("down") else [])
    return head + "\n" + "\n".join(lines)


def crypto(st: dict, c: dict) -> str:
    """Point crypto du week-end : BTC / ETH, prix et variation sur 24 h (donnees reelles, indicatives)."""
    parts = []
    for name, sid in (("BTC", "BTCUSD"), ("ETH", "ETHUSD")):
        if sid in c:
            price, chg = c[sid]
            parts.append(f"{name} {price:,.0f} $".replace(",", " ") + f" ({_p2(chg)} sur 24 h)")
    head = pick(st, "crypto", ["₿ Le forex se repose, pas la crypto :", "🌐 Pendant que les devises dorment, la crypto continue :",
                               "🪙 Point crypto du week-end :"])
    return head + "\n" + " · ".join(parts)


def _activity(n_sig: int, n_active: int, n_watch: int) -> str:
    """Ce qui s'est passe dans le VIP aujourd'hui, en une phrase naturelle (chiffres reels : positions prises aujourd'hui, trades ouverts)."""
    if n_sig:
        s = f"On a pris {n_sig} position{'s' if n_sig > 1 else ''} dans le VIP aujourd'hui"
        if n_active:
            s += f" et {n_active} trade{'s sont' if n_active > 1 else ' est'} encore ouvert{'s' if n_active > 1 else ''}"
        return s + "."
    if n_active:
        return (f"Pas de nouvelle position aujourd'hui, mais {n_active} trade{'s' if n_active > 1 else ''} "
                f"toujours ouvert{'s' if n_active > 1 else ''} côté VIP.")
    if n_watch:
        return "Rien de validé pour l'instant, mais je garde quelques zones à l'œil."
    return "Rien de validé pour l'instant : j'attends le bon moment."


def new_york(st: dict, n_sig: int, n_active: int, n_watch: int) -> str:
    a = _activity(n_sig, n_active, n_watch)
    v = [
        "🇺🇸 New York vient d'ouvrir, c'est souvent là que ça s'anime. {a}\n\nJe garde les yeux ouverts et je vous préviens ici dès qu'il y a du mouvement 👀",
        "🗽 Ouverture de New York ! {a}\n\nLe marché ne dort jamais, nous non plus 😄 Toutes les nouveautés arrivent ici.",
        "🌎 New York vient de se réveiller. {a}\n\nRestez branchés, je vous tiens au courant.",
    ]
    return pick(st, "ny", v).format(a=a)


def evening(st: dict, outcomes: List[dict], n_sig: int, n_active: int) -> str:
    """outcomes : trades clotures aujourd'hui [{'display','label','r'}] (gains ET pertes)."""
    if outcomes:
        lines = "\n".join(f"• {o['display']} — {o['label']} ({_r(o['r'])})" for o in outcomes)
        tail = (f"Encore {n_active} trade{'s' if n_active > 1 else ''} ouvert{'s' if n_active > 1 else ''} : je vous tiens au courant.\n\n" if n_active else "")
        return f"🌙 Bilan de la journée (tous les trades du VIP) :\n{lines}\n\n{tail}On publie tout, gains et pertes, pour que chacun voie la réalité. À demain 👋"
    if n_sig or n_active:
        n = max(n_sig, n_active)
        return (f"🌙 Fin de journée : {n} trade{'s' if n > 1 else ''} ouvert{'s' if n > 1 else ''} dans le VIP, "
                "rien de clôturé pour l'instant. Je vous tiens au courant dès qu'il y a du nouveau.")
    v = [
        "🌙 Journée calme : rien n'a rempli mes critères aujourd'hui.\n\nJe ne force jamais un trade — je préfère attendre le bon moment plutôt que de trader pour trader. À demain 👋",
        "😴 Rien de validé aujourd'hui, et c'est très bien comme ça : un bon trader sait aussi ne rien faire.\n\nOn repart demain, toujours aussi attentifs 👀",
    ]
    return pick(st, "evening_none", v)


def weekend_saturday(st: dict, handle: str) -> str:
    h = _handle(handle)
    v = [
        "🌤 Week-end : le forex se repose, la crypto (BTC, ETH) continue de tourner et je garde un œil dessus. Profitez-en pour souffler — on revient en force lundi 💪",
        "☀️ Bon samedi à tous ! Les marchés de devises sont fermés jusqu'à dimanche soir. Petit rappel : les signaux, l'analyse et le suivi complet, c'est dans le VIP.",
    ]
    return pick(st, "sat", v).format(h=h)


def weekend_sunday(st: dict, snap: dict) -> str:
    ranked = snap.get("ranked") or []
    if len(ranked) < 2:
        return "🌙 Les marchés rouvrent ce soir ! Je regarde ça dès l'ouverture 👀"
    (t, tp), (b, bp) = ranked[0], ranked[-1]
    return (f"🌙 Les marchés rouvrent ce soir ! Tour d'horizon pour bien démarrer la semaine : {NAMES.get(t, t)} en tête ({pct(tp)}), "
            f"{NAMES.get(b, b)} en retrait ({pct(bp)}). {_trend_line(snap)}\n\nJe regarde ça dès l'ouverture 👀")


def cta(st: dict, handle: str) -> str:
    h = _handle(handle)
    v = [
        "Tu suis le groupe depuis un moment ? 😊\n\nIci, tu vois quelques trades. Dans le VIP, tu reçois tous les signaux en direct (entrée, stop, objectifs), l'analyse de chaque setup et le suivi jusqu'au bout. Écris-nous, on t'explique tout ➜ {h}",
        "Ici, tu vois le mouvement. Dans le VIP, tu vois tout : chaque signal, son analyse et son suivi.\n\nUne question, une curiosité ? Un message à {h} et on te répond avec plaisir 🤝",
        "On ne vend pas du rêve : on te montre chaque trade, gagnant ou perdant, avec le plan complet.\n\nLe groupe public en montre quelques-uns, le VIP les reçoit tous. Si ça te parle, viens nous en parler ➜ {h}",
        "Envie de comprendre pourquoi on prend un trade plutôt qu'un autre ? 🧠\n\nDans le VIP, chaque signal arrive avec son analyse : contexte, zone, confirmations, gestion. Rejoins-nous en écrivant à {h}.",
        "Petit message pour ceux qui hésitent 🙂 Le VIP, c'est simple : des signaux détaillés, un suivi transparent, des explications. Écris à {h}, on te dit tout.",
    ]
    return pick(st, "cta", v).format(h=h)


def promo(st: dict, perks: List[str], handle: str) -> str:
    bullets = "\n".join(f"• {html.escape(str(p))}" for p in perks)
    return (f"⭐ <b>Le groupe VIP GOTA TRADING</b>\n{bullets}\n\n"
            f"Pour nous rejoindre, il suffit de nous écrire ➜ {_handle(handle)}")


def account_block(link: str) -> str:
    """Bloc 'ouvrir un compte de trading' des messages epingles (lien partenaire, indique comme tel)."""
    if not str(link).startswith("http"):
        return ""
    return ("\n\n💼 <b>Ouvrir un compte de trading</b>\n"
            "Pour suivre les signaux, il faut un compte chez un courtier : tu peux ouvrir le tien ici (lien partenaire) ➜ "
            f'<a href="{html.escape(str(link), quote=True)}">Créer mon compte</a>')


def welcome(handle: str, account_link: str = "") -> str:
    """Message d'accueil du groupe public (a epingler) : ce qu'on y trouve, comment rejoindre le VIP, ouvrir un compte."""
    return ("👋 <b>Bienvenue sur GOTA TRADING !</b>\n\n"
            "Ici, on suit les paires de devises en continu et on partage ce qui bouge : le point du matin, quelques signaux en direct, "
            "le suivi de chaque trade (gains comme pertes) et un conseil par jour.\n\n"
            "⭐ Le groupe VIP reçoit <b>tous</b> les signaux (entrée, stop, objectifs), l'analyse détaillée et le suivi complet.\n"
            f"Pour le rejoindre, un simple message ➜ {_handle(handle)}" + account_block(account_link))


def account(st: dict) -> str:
    """Rappel hebdomadaire (groupe public) : ouvrir un compte de trading. Le bouton 'Ouvrir un compte' est sous le message."""
    v = [
        "💼 Tu veux suivre les signaux mais tu n'as pas encore de compte de trading ? Tu peux ouvrir le tien juste en dessous 👇 (lien partenaire)",
        "🧭 Petit rappel pour les nouveaux : pour passer des trades, il faut un compte chez un courtier. Le lien pour en ouvrir un est juste en dessous 👇 (lien partenaire)",
        "🚀 Prêt à passer à l'action ? Ouvre ton compte de trading avec le lien qu'on partage avec la communauté 👇 (lien partenaire)",
    ]
    return pick(st, "account", v)


EDU_TIPS = [
    "💡 <b>L'Order Block (OB)</b>\nC'est la dernière bougie opposée avant un mouvement impulsif. Des ordres importants y sont souvent restés en attente : quand le prix revient, il réagit parfois. « Parfois » : on attend toujours une bougie de confirmation avant d'agir.",
    "💡 <b>Le Fair Value Gap (FVG)</b>\nUn vide sur 3 bougies : le prix est allé trop vite. Le marché revient souvent le combler, au moins en partie. C'est une zone d'intérêt, jamais une garantie.",
    "💡 <b>Le balayage de liquidité</b>\nLe prix perce un ancien plus bas (ou plus haut) pour déclencher les stops, puis repart dans l'autre sens. Une mèche qui reprend le niveau demande de l'attention — pas un achat automatique.",
    "💡 <b>Discount / Premium</b>\nOn coupe un mouvement en deux : sous le milieu = Discount (on cherche des achats), au-dessus = Premium (on cherche des ventes). Acheter cher dans une hausse dégrade le ratio gain / risque.",
    "💡 <b>Le risque avant tout</b>\nRisquer 1 % par trade, c'est pouvoir enchaîner 10 pertes de suite sans dépasser -10 %. Risquer 10 %, c'est être hors-jeu après quelques erreurs. Les gains viennent après la survie.",
    "💡 <b>Le « R »</b>\n1R = la perte prévue si le stop est touché. Un trade à +2R gagne deux fois ce qu'on risquait. Avec 40 % de trades gagnants à 2R on serait rentable… mais seule une centaine de trades mesurés le confirme, pas une impression.",
    "💡 <b>Pourquoi on saute des trades</b>\nSpread trop large, tendance contraire, stop trop serré : chaque filtre retire des setups. Moins de trades, mais des conditions réunies. Ne pas trader est une position.",
    "💡 <b>Le stop n'est pas négociable</b>\nLe reculer quand le prix s'en approche transforme une petite perte en grosse perte. On le place avant d'entrer, on ne l'éloigne jamais.",
    "💡 <b>Confluence ≠ certitude</b>\nPlusieurs confirmations augmentent la probabilité, jamais à 100 %. Un setup Grade A+ peut perdre. C'est pour ça que le risque par trade reste petit.",
    "💡 <b>Calculer sa taille de position</b>\nTaille = risque en $ ÷ (stop en pips × valeur du pip). Exemple : 1 000 $ de capital, risque 1 % = 10 $ ; stop à 20 pips, pip à 0,10 $ par micro-lot → 5 micro-lots (0,05 lot).",
    "💡 <b>Le stop à l'entrée (breakeven)</b>\nAprès TP1 on remonte le stop au prix d'entrée : le reste du trade ne peut plus perdre (hors frais). Contrepartie : on peut être sorti juste avant que le prix reparte. Chaque choix a un coût.",
    "💡 <b>Les sessions de marché</b>\nLondres et New York concentrent la liquidité. En dehors, les mouvements sont plus lents et les spreads plus larges : c'est pourquoi on note la session dans nos confirmations.",
    "💡 <b>Tenir un journal</b>\nNote chaque trade : raison, résultat, émotion. Sur 50 trades, ton journal t'apprend plus que n'importe quel « gourou » sur ce qui marche… ou pas pour toi.",
    "💡 <b>Démo d'abord</b>\nTester une stratégie en compte démo pendant plusieurs semaines coûte 0 $. Si tu ne sais pas la suivre en démo, tu ne la suivras pas en réel.",
]


def education(st: dict) -> str:
    return pick(st, "edu", EDU_TIPS)
