"""
news_edu.py - petites notes pedagogiques "💡 À savoir" glissees sous certaines actualites du canal "Info du monde".

Chaque note explique UN terme ou indicateur ; les textes sont rediges a la main (factuels, prudents : "souvent", "peut",
jamais de conseil d'investissement ni de promesse). Cadence : au plus une note par message, chaque terme au plus une fois
toutes les 8 h, et au plus une note toutes les 12 minutes - pour eduquer sans saturer le canal.
Les termes les plus precis sont testes en premier (NFP avant "inflation", par exemple).
"""
from __future__ import annotations
import re
from typing import List, Optional, Tuple

KEY_PAUSE = 8 * 3600          # un meme terme n'est explique qu'une fois toutes les 8 h
GLOBAL_PAUSE = 12 * 60        # et pas plus d'une note toutes les 12 minutes

_RAW: List[Tuple[str, str, str]] = [
    ("nfp", r"\bNFP\b|non-?farm|emplois non agricoles|cr[ée]ations? d['’]emplois",
     "Le NFP (Non-Farm Payrolls) est le nombre d'emplois créés aux États-Unis hors agriculture, publié en général le premier "
     "vendredi du mois. C'est l'un des chiffres les plus suivis : il oriente les attentes sur les taux de la Fed, donc le dollar, "
     "l'or et les indices."),
    ("fomc", r"\bFOMC\b|r[ée]serve f[ée]d[ée]rale",
     "La Fed est la banque centrale américaine. Son comité de politique monétaire (FOMC) se réunit huit fois par an pour fixer le "
     "taux directeur ; le communiqué, les projections et la conférence de presse sont scrutés par les marchés."),
    ("pce", r"\bPCE\b",
     "Le PCE est l'indicateur d'inflation préféré de la Fed ; le « PCE core » (hors énergie et alimentation) est le plus suivi."),
    ("ppi", r"\bPPI\b|prix [àa] la production",
     "Le PPI mesure l'évolution des prix à la production : les entreprises répercutent souvent ces coûts sur leurs prix de vente, "
     "c'est pourquoi il est vu comme un signal avancé de l'inflation."),
    ("pmi", r"\bPMI\b|\bISM\b",
     "Le PMI (ISM aux États-Unis) est un indice d'enquête auprès des directeurs d'achat : au-dessus de 50, l'activité progresse ; "
     "en dessous de 50, elle recule. Publié tôt dans le mois, il donne un aperçu rapide de la santé de l'économie."),
    ("ventes", r"ventes au d[ée]tail|retail sales",
     "Les ventes au détail mesurent la consommation des ménages, moteur majeur de l'économie américaine (plus des deux tiers du "
     "PIB). Un chiffre supérieur aux attentes est souvent lu comme un signe de solidité."),
    ("claims", r"jobless claims|inscriptions hebdomadaires|initial claims|allocations? ch[ôo]mage",
     "Chaque jeudi, le nombre d'inscriptions hebdomadaires au chômage aux États-Unis donne une lecture rapide de la santé du "
     "marché du travail."),
    ("halving", r"halving|halvening",
     "Le halving divise par deux la récompense des mineurs de bitcoin, environ tous les quatre ans : l'arrivée de nouveaux "
     "bitcoins ralentit. Des mouvements de prix ont souvent suivi, sans aucune garantie."),
    ("etf", r"\bETF\b",
     "Un ETF est un fonds coté en bourse qui réplique un indice ou un actif (or, bitcoin…). Un ETF « spot » détient réellement "
     "l'actif ; les flux d'achats et de ventes de ces fonds sont suivis de près."),
    ("stablecoin", r"stablecoins?",
     "Un stablecoin est une cryptomonnaie conçue pour garder une valeur stable, souvent adossée au dollar. Sa solidité dépend des "
     "réserves de l'émetteur."),
    ("qe", r"assouplissement quantitatif|resserrement quantitatif|quantitative (?:easing|tightening)|\bQE\b|\bQT\b",
     "Dans un assouplissement quantitatif (QE), la banque centrale achète des obligations pour injecter des liquidités et faire "
     "baisser les taux ; dans un resserrement quantitatif (QT), elle laisse son bilan se réduire, ce qui retire des liquidités."),
    ("faucon", r"hawkish|dovish|faucons?|colombes?|restrictifs?|restrictive|accommodante?s?",
     "On parle de ton « restrictif » (faucon, « hawkish ») quand une banque centrale penche pour des taux élevés afin de freiner "
     "l'inflation, et « accommodant » (colombe, « dovish ») quand elle penche pour des taux bas afin de soutenir l'économie."),
    ("courbe", r"courbe des taux|yield curve",
     "La courbe des taux compare les rendements à court et à long terme. Quand les taux courts dépassent les taux longs (courbe "
     "« inversée »), cela a souvent précédé les récessions par le passé, sans être un signal infaillible."),
    ("dxy", r"\bDXY\b|dollar index|indice dollar",
     "L'indice dollar (DXY) mesure le billet vert face à un panier de six devises, l'euro en tête. Un dollar fort pèse souvent sur "
     "l'or et les matières premières, cotées en dollars."),
    ("eurusd", r"EUR/USD|euro/dollar",
     "L'EUR/USD indique combien de dollars il faut pour acheter un euro : c'est la paire de devises la plus échangée au monde. "
     "S'il monte, l'euro s'apprécie face au dollar."),
    ("boj", r"\bBoJ\b|banque du japon",
     "Le Japon a longtemps pratiqué des taux très bas, ce qui a nourri le « carry trade » : emprunter en yen pour investir "
     "ailleurs. Une hausse des taux de la Banque du Japon peut inciter à dénouer ces positions et faire monter le yen."),
    ("bce", r"\bBCE\b|\bECB\b|banque centrale europ[ée]enne",
     "La BCE, basée à Francfort, fixe les taux de la zone euro et vise une inflation de 2 % à moyen terme. Ses décisions et le ton "
     "de ses responsables font bouger l'euro et les taux européens."),
    ("rendement", r"rendements? (?:obligataires?|des obligations|des bons)|bond yields?|treasury yields?|\bbunds?\b|\bOAT\b|bons du tr[ée]sor",
     "Le rendement d'une obligation évolue à l'inverse de son prix : si le prix baisse, le rendement monte. Les taux d'emprunt "
     "d'État servent de référence au coût du crédit et pèsent sur la valorisation des actions."),
    ("spread", r"\bspread\b|[ée]cart de taux",
     "Le spread est un écart de rendement entre deux obligations (par exemple France/Allemagne) : plus il s'élargit, plus le "
     "marché demande une prime pour le risque supplémentaire."),
    ("notation", r"agences? de notation|notation souveraine|\bMoody|\bFitch\b|S&P Global Ratings|credit rating",
     "Les agences de notation (S&P, Moody's, Fitch) évaluent la solvabilité d'un État ou d'une entreprise. Une dégradation de "
     "note peut faire monter ses coûts d'emprunt."),
    ("dette", r"dette publique|dette souveraine|d[ée]ficit public|national debt|budget deficit",
     "La dette publique est l'ensemble des emprunts d'un État. Plus elle est élevée, plus les investisseurs peuvent exiger un "
     "rendement élevé pour la détenir, ce qui renchérit le coût de financement du pays."),
    ("douane", r"droits? de douane|tarifs? douaniers?|\btariffs?\b",
     "Les droits de douane sont des taxes sur les produits importés. Ils renchérissent ces produits, peuvent alimenter "
     "l'inflation et pèsent sur les entreprises exposées au commerce international."),
    ("vix", r"\bVIX\b|volatilit[ée]",
     "Le VIX mesure la volatilité attendue de l'indice S&P 500 sur 30 jours ; on l'appelle l'« indice de la peur ». Il grimpe "
     "quand les investisseurs craignent des variations brutales."),
    ("ipo", r"introduction en bourse|\bIPO\b",
     "Une introduction en bourse (IPO) est la première mise en vente des actions d'une entreprise au public. Elle permet de lever "
     "des fonds ; le prix d'introduction est fixé avant la cotation et le cours peut ensuite beaucoup varier."),
    ("opa", r"\bOPA\b|offre publique d['’]achat",
     "Une OPA (offre publique d'achat) est la proposition d'une société d'acheter les actions d'une autre à un prix fixé, "
     "généralement supérieur au cours, pour en prendre le contrôle."),
    ("dividende", r"dividendes?|dividends?",
     "Le dividende est la part des bénéfices qu'une entreprise reverse à ses actionnaires. Après son détachement, le cours de "
     "l'action baisse en général du montant versé."),
    ("resultats", r"r[ée]sultats? (?:trimestriels?|annuels?|semestriels?)|b[ée]n[ée]fices? par action|\bBPA\b|\bEPS\b|corporate results",
     "Les résultats trimestriels sont comparés aux attentes des analystes (bénéfice par action, chiffre d'affaires). Le cours "
     "réagit surtout à l'écart avec ces attentes et aux perspectives annoncées, pas seulement au chiffre lui-même."),
    ("petrole", r"\bOPEP\b|\bOPEC\b|brent|\bWTI\b|baril|p[ée]trole",
     "Le Brent (mer du Nord) et le WTI (États-Unis) sont les deux références du prix du pétrole. L'OPEP+ ajuste sa production "
     "pour influencer les cours ; un pétrole cher alimente l'inflation."),
    ("or", r"l['’]or\b|once d['’]or|\bgold\b|\bXAU\b",
     "L'or est une valeur refuge : il tend à monter quand les taux réels baissent, quand le dollar recule ou quand l'incertitude "
     "grandit. N'offrant aucun intérêt, il souffre plutôt quand les taux montent."),
    ("ethereum", r"ethereum|[ée]ther\b|\bETH\b",
     "Ethereum est une blockchain qui exécute des contrats intelligents ; son jeton, l'ether (ETH), sert notamment à payer les "
     "frais du réseau."),
    ("bitcoin", r"bitcoin|\bBTC\b",
     "Le bitcoin est limité à 21 millions d'unités. Il se négocie 24 h/24, week-ends compris, et son prix peut varier fortement "
     "en peu de temps."),
    ("forex", r"\bforex\b|march[ée] des changes",
     "Le forex (marché des changes) est le marché où s'échangent les devises : ouvert 24 h/24 du lundi au vendredi, c'est le "
     "marché le plus liquide au monde."),
    ("taux", r"taux directeurs?|rate (?:decision|cut|hike)|baisses? des taux|hausses? des taux|d[ée]cision de taux|politique mon[ée]taire",
     "Le taux directeur est le taux auquel la banque centrale prête aux banques. Le baisser rend le crédit moins cher et soutient "
     "l'économie ; le relever freine l'inflation mais pèse sur l'activité. Le marché réagit autant aux mots du communiqué qu'au "
     "chiffre."),
    ("pib", r"\bPIB\b|\bGDP\b|produit int[ée]rieur brut|croissance [ée]conomique",
     "Le PIB (produit intérieur brut) mesure la richesse produite par un pays sur une période. Deux trimestres consécutifs de "
     "baisse correspondent à la définition courante d'une récession « technique »."),
    ("recession", r"r[ée]cession|recession",
     "Une récession est un net recul de l'activité économique, souvent définie par deux trimestres consécutifs de baisse du PIB. "
     "Elle s'accompagne en général d'une hausse du chômage et d'une baisse des bénéfices des entreprises."),
    ("chomage", r"ch[ôo]mage|unemployment|jobless",
     "Le taux de chômage mesure la part des actifs qui cherchent un emploi sans en trouver. Un marché du travail très tendu peut "
     "faire monter les salaires, donc l'inflation, ce que surveillent les banques centrales."),
    ("inflation", r"\bCPI\b|\bIPC\b|prix [àa] la consommation|inflation",
     "L'inflation mesure la hausse générale des prix ; l'indice des prix à la consommation (CPI aux États-Unis, IPC en France) en "
     "est la référence. Quand elle dépasse l'objectif des banques centrales (autour de 2 %), celles-ci hésitent à baisser leurs taux."),
]
_TIPS = [(k, re.compile(rx, re.I), txt) for k, rx, txt in _RAW]


def tip_for(text: str, state: dict, now_ts: float) -> Optional[Tuple[str, str]]:
    """(cle, note) a ajouter sous ce message, ou None. Ne modifie rien : l'appelant valide avec mark_tip() une fois le message parti."""
    if now_ts - state.get("tip_last", 0) < GLOBAL_PAUSE:
        return None
    used = state.get("tips", {})
    for key, rx, txt in _TIPS:
        if now_ts - used.get(key, 0) >= KEY_PAUSE and rx.search(text):
            return key, txt
    return None


def mark_tip(state: dict, key: str, now_ts: float) -> None:
    state.setdefault("tips", {})[key] = now_ts
    state["tip_last"] = now_ts
    state["tips"] = {k: v for k, v in state["tips"].items() if now_ts - v < 2 * 86400}
