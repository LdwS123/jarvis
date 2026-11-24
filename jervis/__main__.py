"""Point d'entrée.

  python -m jervis           → vocal, dans le Terminal
  python -m jervis --barre   → vocal, avec une icône dans la barre des menus (c'est ce que lance Jervis.app)
  python -m jervis --texte   → au clavier
  python -m jervis --apprendre-ma-voix → enregistre l'empreinte de ta voix (il ignorera les autres)
"""

import argparse
import asyncio
import difflib
import re
import threading
import time
import unicodedata

from . import chat, compteur, config, oral, tts, voix_locale
from .brain import Cerveau
from .naturel import corriger_transcription, decoder_epellation, epellation_en_cours, hesite, hors_alphabet, recoller
from .fond import Evenement, Programmateur, Standard, Taches, outils_fond

NOM = re.compile(config.NOM, re.IGNORECASE)
# Ce qui précède son nom quand on l'appelle (avec ce que la transcription en fait : « Hey » → « Yes », « Dit », « І »…).
SALUTATION = re.compile(r"^(?:(?:salut|hey|hé|eh|et|ok|okay|dis|dit|yes|bonjour|coucou|yo|allô|і|эй|хей)\b\W*)+",
                        re.IGNORECASE)
# « Merci Steve », « non c'est bon », « ça ira merci » : la conversation est finie, il repasse en veille.
FIN_DE_CONVERSATION = re.compile(
    r"^(?:(?:non|ok|okay|bon|ah|bah)\W+)*(?:merci(?: beaucoup| bien)?|c'est tout|c'est bon|ça ira|ça va aller|"
    r"rien d'autre|rien|non|bonne nuit|à plus(?: tard)?|à tout à l'heure|stop|parfait|super)"
    r"(?:\W+(?:merci(?: beaucoup)?|c'est tout|c'est bon))*\W*$", re.IGNORECASE)
# « Steve, on discute » : il reste à l'écoute sans limite de temps, jusqu'à « merci » ou « c'est bon ».
DISCUSSION = re.compile(r"^(?:(?:allez|bon|viens)\W+)?(?:on (?:discute|papote|parle|cause)|discutons|"
                        r"mode (?:discussion|conversation)|reste (?:à l'écoute|avec moi|là))"
                        r"(?: un peu| un moment| ensemble)?\b\W*", re.IGNORECASE)
OUI = re.compile(r"\b(oui|ouais|ouep|yes|vas[- ]y|ok|okay|d'accord|confirme|go|bien sûr|carrément|exactement"
                 r"|évidemment|fais[- ]le|envoie|valide|parfait|c'est bon|je veux|allez)\b", re.IGNORECASE)
NON = re.compile(r"\b(non|nan|surtout pas|annule|arrête|stop|laisse tomber|attends|pas maintenant|pas ça"
                 r"|ne (?:le |l')?(?:fais|envoie) pas|n'envoie pas|ne fais rien)\b", re.IGNORECASE)
# « pas de souci », « n'hésite pas » : des oui qui contiennent « pas ».
OUI_MALGRE_PAS = re.compile(r"\b(pas de (?:souci|soucis|problème)|n'hésite pas|pourquoi pas)\b", re.IGNORECASE)


def lire_accord(reponse: str | None) -> bool | None:
    """Oui → True, non → False, rien de clair (« hein ? », une autre phrase, « non non vas-y ») → None."""
    if not reponse:
        return None
    sans_oui_pas = OUI_MALGRE_PAS.sub(" ", reponse)
    non = bool(NON.search(sans_oui_pas))
    oui = bool(OUI_MALGRE_PAS.search(reponse) or OUI.search(NON.sub(" ", sans_oui_pas)))  # « n'envoie pas » ≠ envoie
    if oui != non:
        return oui
    return None
def controle_voix(voix: float | None, voix_connue: bool, actif: bool = True) -> str:
    """Un « oui » peut-il déclencher une action sensible ? « ok » : c'est bien ta voix (ou aucune voix apprise,
    ou contrôle coupé). « courte » : trop bref pour reconnaître une voix (un « oui » seul) : il demande une
    phrase. « inconnue » : une autre voix, il n'y va pas."""
    if not actif or not voix_connue:
        return "ok"
    if voix is None:
        return "courte"
    return "ok" if voix >= config.SEUIL_VOIX else "inconnue"


# Ce qu'il te fait dire quand ton « oui » est trop bref pour reconnaître ta voix (moins de 0,8 s de parole :
# « oui, vas-y » tient en 0,7 s, il le redemandait donc en boucle). Chaque essai demande plus long que le précédent.
DEMANDES_PHRASE = [
    "Dis-le en une vraie phrase, que je reconnaisse ta voix : « oui, vas-y, je confirme, fais-le ».",
    "Encore plus long, s'il te plaît : « oui, je confirme que tu peux y aller, vas-y, fais-le maintenant ».",
]
REFUS_VOIX_COURTE = "Je n'arrive pas à reconnaître ta voix sur un oui si court, donc je n'y vais pas."


# Ce qui te permet de lui couper la parole (en plus de son nom).
STOP = re.compile(r"\b(stop|arrête|tais-toi|chut|silence|attends|laisse tomber|ça suffit|c'est bon)\b", re.IGNORECASE)
# Phrases que la transcription invente quand il n'entend que du bruit.
HALLUCINATIONS = re.compile(r"amara\.org|sous-titr|merci d'avoir regardé|abonnez-vous", re.IGNORECASE)
# « Mm-hmm », « euh », « hein » : tu l'écoutes ou tu réfléchis, ce n'est pas une demande. Il ne répond pas
# (« ouais » ou « ok » seuls peuvent répondre à sa question : eux, on les garde).
BRUITS = re.compile(r"^\W*(?:(?:m+|hm+|hum+|mm+-?hm+|euh+|heu+|ah+|oh+|hein|pf+|uh+|um+|uh-huh)\W*)+$", re.IGNORECASE)
# « Ouais », « d'accord », « ah oui c'est ça » dits PENDANT qu'il parle ou travaille : tu le suis, tu ne lui
# demandes rien. Il continue, sans y répondre. (Quand c'est à toi de parler, ces mots-là restent des réponses.)
ACQUIESCEMENT = re.compile(
    r"^\W*(?:(?:ah+|oh+|ben|bah|bon|et|m+|hm+|mm+-?hm+|ou+i+|ouais?|ouep|ok(?:ay)?|d['’]accord|dac|c['’]est ça"
    r"|c['’]est vrai|c['’]est clair|voilà|exactement|carrément|tout à fait|bien sûr|très bien|parfait|super|génial"
    r"|top|cool|nickel|je vois|ça marche|pas mal|trop bien|yes|yeah|yep)(?!\w)\W*)+$", re.IGNORECASE)


def extraire_commande(texte: str) -> str | None:
    """« Salut Steve, ouvre Cursor » → « ouvre Cursor ». Renvoie None si son nom n'y est pas."""
    m = NOM.search(texte)
    if not m:
        return None
    reste = f"{texte[:m.start()]} {texte[m.end():]}".strip(" ,.!?")
    return re.sub(r"\s+([,.!?])", r"\1", SALUTATION.sub("", reste)).strip(" ,.!?")


# Une phrase qui finit sur un mot qui appelle une suite : tu cherchais tes mots, la fin de phrase a coupé trop tôt.
PHRASE_COUPEE = re.compile(
    r"(?:(?<![\w-])(?:de|du|des|le|la|les|un|une|à|au|aux|sur|pour|avec|dans|chez|vers|par|en|et|ou|mais|que|qui|si"
    r"|me|te|lui|mon|ma|mes|ton|ta|tes|son|sa|ses|ce|cette|quand|comme|parce|je|tu|il|on"
    r"|ouvrir|aller|mettre|dire|faire|envoyer|écrire|regarder|lancer|chercher|appeler|supprimer|effacer|trouver"
    r"|montrer|lire|prendre|noter|ranger|répondre|demander|peux|peut|veux|voudrais|pourrais|est-ce que"
    # « il faut que tu regardes », « j'aimerais que tu envoies » : il manque encore quoi
    r"|que tu (?:regardes|ouvres|envoies|mettes|cherches|fasses|dises|lances|écrives|appelles|ailles|prennes"
    r"|lises|trouves|supprimes|ranges|répondes|prépares|notes))|\b[ldjqst]u?')\W*$",
    re.IGNORECASE)
# « mets-moi un coup de f » : un mot coupé net, il ne reste qu'une consonne (en minuscule : « plan B » est fini).
MOT_TRONQUE = re.compile(r"(?<![\w'-])[bcdfghjkmnpqrtvwxz]\W*$")


def phrase_coupee(texte: str) -> bool:
    texte = texte.strip()
    return bool(PHRASE_COUPEE.search(texte) or MOT_TRONQUE.search(texte))


# La transcription met un point, un « ? » ou un « ! » quand ta voix retombe : la phrase est finie.
FIN_NETTE = re.compile(r"[.?!…][\s\"'»)]*$")


def attente_suite(texte: str, ponctuee: bool | None = None) -> float:
    """Combien de secondes il attend encore la suite de ta phrase (0 : elle est finie).
    `ponctuee` : la phrase d'origine finissait par un point (quand `texte` a déjà perdu sa ponctuation)."""
    texte = texte.strip()
    if ponctuee is None:
        ponctuee = bool(FIN_NETTE.search(texte))
    if hesite(texte):  # « envoie un mail à Paul, euh… », « du coup » : tu réfléchis à voix haute
        return config.SUITE_HESITATION_S
    if phrase_coupee(texte) or epellation_en_cours(texte):  # « est-ce que tu peux ouvrir », « T A » : il manque la suite
        return min(config.SUITE_PHRASE_S, 2.5) if ponctuee else config.SUITE_PHRASE_S
    if not ponctuee and len(re.findall(r"[\w'’-]+", NOM.sub(" ", texte))) >= 3 and not fin_de_conversation(texte):
        return config.SUITE_SANS_POINT_S  # « No parce que Scott » : pas de point, tu allais continuer
    return 0.0


ANGLAIS = frozenset("the you and is are this that these those it of to i they what was be have with just like know "
                    "don't gonna really but so if we do not my your how for in at from about all would could "
                    "much too still there their them he she his her it's that's i'm let's yeah oh can will been "
                    "think going right".split())
FRANCAIS = frozenset("le la les un une des de du et est je tu il on pas ça que qui en au mais pour ce mon ma "
                     "tes mes c'est j'ai t'as".split())


def en_anglais(texte: str) -> bool:
    """Une vidéo YouTube en anglais, pas toi : beaucoup de petits mots anglais, presque aucun français."""
    mots = re.findall(r"[\w']+", texte.casefold())
    if len(mots) < 4:
        return False
    anglais = sum(m in ANGLAIS for m in mots) / len(mots)
    francais = sum(m in FRANCAIS for m in mots) / len(mots)
    return anglais >= 0.25 and francais < 0.08


# Les mots qu'on trouve dans presque toutes tes phrases. Une phrase de quatre mots ou plus qui n'en contient
# presque aucun n'est pas du français : c'est du bruit que la transcription a pris pour des mots.
MOTS_COURANTS = frozenset("""
    le la les un une des de du d l au aux ce cet cette ces c ça cela ceci mon ma mes ton ta tes son sa ses notre nos
    votre vos leur leurs je j tu t il elle on nous vous ils elles me m te se s lui moi toi y en qui que qu quoi dont
    où quand comment pourquoi combien quel quelle quels quelles est es suis sont sommes êtes était étais sera serait
    a as ai ont avons avez avait avais aura aurait fait fais faire faut va vas vais vont aller peux peut pouvez
    pourrais pourrait veux veut voudrais dois doit sais sait dis dit dire mets met mettre prends prend ouvre ferme
    lance cherche trouve regarde montre envoie écris lis note rappelle appelle supprime efface enlève ajoute arrête
    attends continue donne passe change vide range et ou mais donc or ni car si alors puis ensuite parce comme pas
    ne n plus moins très trop bien mal aussi encore déjà jamais toujours souvent peut-être vraiment juste plutôt
    seulement même tout tous toute toutes rien quelque chose autre autres oui non ouais bah ben bon ok merci stp
    à dans sur sous avec sans pour par chez vers entre avant après pendant depuis jusqu contre ici là voilà voici
    maintenant aujourd hui demain hier matin soir midi nuit jour semaine mois heure heures minute minutes
    fois deux trois quatre cinq dix premier dernier nouveau nouvelle petit grand salut bonjour
    """.split())


def _sans_accents(mot: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", mot) if not unicodedata.combining(c))


def _en_mots_simples(texte: str) -> list[str]:
    """Les mots d'une phrase, en minuscules et sans accents (« Géorgien » et « georgien » : le même mot)."""
    return [_sans_accents(m) for m in re.findall(r"\w+", texte.casefold())]


def _meme_mot(a: str, b: str) -> bool:
    """Le même mot, à une lettre près pour les mots longs (« mail » et « mails »)."""
    if a == b:
        return True
    if min(len(a), len(b)) < 4 or a[0] != b[0] or abs(len(a) - len(b)) > 2:
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.8


def charabia(entendu: str) -> bool:
    """Quatre mots ou plus, presque aucun mot français courant : du bruit pris pour une phrase
    (« Og det spikes sjovt base præskel », « Gamla fed le plussoeau Ernesto »)."""
    mots = re.findall(r"\w+", entendu.casefold())
    return len(mots) >= 4 and sum(m in MOTS_COURANTS for m in mots) / len(mots) < 0.25


def echo_de_sa_voix(entendu: str, dit: str) -> bool:
    """Le micro a réentendu ce que lui-même vient de dire ? La transcription de son écho est approximative
    (« tes mails » → « était mail », « 995 » → « neuf cent quatre-vingt-quinze ») : on compare donc avec ce
    qu'il a écrit ET avec ce qu'il a prononcé, à une lettre près. C'est son écho si les trois quarts des
    mots entendus sont les siens, ou la moitié avec deux vrais mots (cinq lettres ou plus : « tu peux me… »
    ne prouve rien), ou si quatre mots de suite reprennent sa phrase dans le même ordre."""
    mots = _en_mots_simples(entendu)
    if not mots or not dit.strip():
        return False
    versions = [_en_mots_simples(dit), _en_mots_simples(oral.en_mots(dit))]
    vocabulaire = set(versions[0]) | set(versions[1])
    connus = {m: m in vocabulaire or any(_meme_mot(m, s) for s in vocabulaire) for m in set(mots)}
    part = sum(connus[m] for m in mots) / len(mots)
    if part >= 0.75 or (part >= 0.5 and sum(connus[m] and len(m) >= 5 for m in set(mots)) >= 2):
        return True
    for siens in versions:  # quatre mots de suite, dans son ordre à lui, dont un vrai mot (cinq lettres ou plus)
        suites: dict[int, tuple[int, bool]] = {}  # position dans sa phrase → (longueur de la suite, un mot long ?)
        for m in mots:
            suivantes: dict[int, tuple[int, bool]] = {}
            if connus[m]:
                for j, s in enumerate(siens):
                    if _meme_mot(m, s):
                        longueur, long_mot = suites.get(j - 1, (0, False))
                        suivantes[j] = (longueur + 1, long_mot or len(m) >= 5)
            suites = suivantes
            if any(longueur >= 4 and long_mot for longueur, long_mot in suites.values()):
                return True
    return False


def ajout_probable(entendu: str, dit: str) -> bool:
    """Ce que le micro a entendu pendant qu'il travaillait ou parlait : toi qui continues (vrai),
    ou l'écho de sa propre voix, un bruit, une vidéo en anglais (faux) ?"""
    mots = re.findall(r"\w+", entendu.casefold())
    if len(mots) < 2 or HALLUCINATIONS.search(entendu) or BRUITS.match(entendu) or en_anglais(entendu):
        return False
    if ACQUIESCEMENT.match(entendu) or charabia(entendu):
        return False
    return not echo_de_sa_voix(entendu, dit)


# Ce qu'il reçoit quand tu as continué ta phrase alors qu'il avait déjà commencé à travailler sur le début.
MARQUE_SUITE = ("(Il a continué sa phrase pendant que tu commençais. La voici en entier ; "
                "ne refais pas ce qui est déjà fait :)")


def bout_en_l_air(ajout: str) -> bool:
    """Trois mots au plus et qui ne finissent pas (« et le », « et aussi à ») : un bout de phrase attrapé au vol
    pendant qu'il parlait. Y répondre donnerait « et le quoi ? » : il le laisse passer et t'écoute ensuite."""
    return len(re.findall(r"[\w'’-]+", ajout)) <= 3 and phrase_coupee(ajout) and not fin_de_conversation(ajout)


def fin_de_conversation(texte: str) -> bool:
    sans_nom = NOM.sub(" ", texte)
    return bool(FIN_DE_CONVERSATION.match(re.sub(r"\s+", " ", SALUTATION.sub("", sans_nom.strip(" ,.!?"))).strip()))


def est_interruption(entendu: str, dit: str) -> bool:
    """Le micro entend aussi sa propre voix : un mot ne compte que s'il n'est pas dans sa phrase à lui."""
    dit = dit.casefold()
    return any(m.group().casefold() not in dit for rx in (NOM, STOP) for m in rx.finditer(entendu))


class Etat:
    """Ce qu'il fait, pour l'icône de la barre des menus."""
    ICONES = {"demarre": "⏳", "veille": "🎙", "ecoute": "👂", "reflechit": "💭", "parle": "🗣",
              "pause": "⏸", "erreur": "⚠️"}

    def __init__(self) -> None:
        self.nom = "demarre"
        self.oreilles = None
        self.taches = None  # les agents de fond (fond.Taches), pour le tableau de bord

    @property
    def icone(self) -> str:
        return self.ICONES[self.nom]

    def __call__(self, nom: str) -> None:
        if not (self.oreilles and self.oreilles.en_pause):
            self.nom = nom


def _equipe(journal=print) -> tuple[Standard, Taches, Programmateur, list]:
    """Le standard, les tâches de fond, le programmateur, et les outils qui les pilotent."""
    standard = Standard()
    taches = Taches(standard, journal)
    programmateur = Programmateur(standard, journal, taches)
    return standard, taches, programmateur, outils_fond(taches, programmateur)


async def mode_texte(muet: bool) -> None:
    boucle = asyncio.get_running_loop()
    entrees: asyncio.Queue[str | None] = asyncio.Queue()

    def lire_clavier() -> None:  # un fil à part : le clavier ne bloque pas les annonces
        while True:
            try:
                ligne = input()
            except (EOFError, KeyboardInterrupt):
                boucle.call_soon_threadsafe(entrees.put_nowait, None)
                return
            boucle.call_soon_threadsafe(entrees.put_nowait, ligne)

    async def parler(texte: str) -> None:
        print(f"🤖 {config.PRENOM} : {texte}", flush=True)
        if not muet:
            await tts.parler(texte)

    async def confirmer(question: str) -> bool:
        await parler(question)
        print("   (oui/non) > ", end="", flush=True)
        reponse = await entrees.get()
        return lire_accord(reponse) is True

    standard, taches, programmateur, outils_en_plus = _equipe()
    cerveau = Cerveau(parler, confirmer, outils_en_plus=outils_en_plus, anticiper=None if muet else tts.anticiper)
    await cerveau.demarrer()
    horloge = asyncio.create_task(programmateur.tourner())
    threading.Thread(target=lire_clavier, daemon=True).start()
    print(f"{config.PRENOM} est prêt. Tape ta demande (Ctrl+D pour quitter).")

    async def traiter(ev: Evenement) -> None:
        print()
        if ev.genre == "question":
            ok = await confirmer(ev.texte)
            if ev.futur and not ev.futur.done():
                ev.futur.set_result(ok)
        elif ev.genre == "annonce":
            await parler(ev.texte)
        else:
            await cerveau.demander(ev.texte)

    try:
        while True:
            while ev := standard.prendre():
                await traiter(ev)
            print("🧑 Toi : ", end="", flush=True)
            lecture = asyncio.ensure_future(entrees.get())
            sonnerie = asyncio.ensure_future(standard.attendre())
            fini, _ = await asyncio.wait({lecture, sonnerie}, timeout=120, return_when=asyncio.FIRST_COMPLETED)
            sonnerie.cancel()
            if lecture in fini:
                texte = lecture.result()
                if texte is None:
                    break
                if texte.strip():
                    await cerveau.demander(texte.strip())
            else:
                lecture.cancel()  # rien n'est perdu : une ligne pas encore lue reste dans la file
                if not fini:      # deux minutes sans rien : la conversation est finie, il met sa mémoire à jour
                    print()
                    cerveau.fin_de_conversation()
    finally:
        horloge.cancel()
        await taches.arreter()
        await cerveau.arreter()


async def mode_vocal(etat: Etat | None = None) -> None:
    from . import outils, stt
    from .audio import Oreilles

    etat = etat or Etat()
    motcle = f", « {config.PHRASE_MOTCLE} »" if config.MOTCLE_MODELE and config.DETECTION != "whisper" else ""
    print(f"⏳ Démarrage du micro (anti-écho, débruitage{motcle}, fin de phrase)…", flush=True)
    oreilles = await asyncio.to_thread(Oreilles)
    etat.oreilles = oreilles
    tts.brancher(oreilles.haut_parleur)  # sa voix passe par le flux du micro : l'anti-écho la connaît
    print(f"⏳ Chargement de {stt.nom_moteur()}…", flush=True)
    await asyncio.to_thread(stt.prechauffer)
    micro = asyncio.Lock()  # une seule écoute à la fois (veille, réponse oui/non, guet pendant qu'il parle)
    chrono: dict = {"texte_pret": None}  # les étapes de ta dernière phrase, pour mesurer son temps de réponse
    delais: list[float] = []

    async def entendre(attente: float | None, veille: bool = False,
                       arret: threading.Event | None = None, garder: bool = False) -> tuple[str | None, bool]:
        """(ce que tu as dit, mot-clé entendu). Le texte vaut None si personne n'a parlé à temps,
        ou si `arret` sonne (jamais au milieu de ta phrase). `garder` : garde le son arrivé depuis la
        phrase précédente (tu continuais à parler pendant la transcription)."""
        async with micro:
            chrono["voix"] = None
            phrase = await asyncio.to_thread(oreilles.ecouter_phrase, attente, arret, veille=veille,
                                             laisser_finir=arret is not None, repartir_a_zero=not garder)
            if phrase is None:
                return None, False
            # Le score de ta voix principale (sans les empreintes apprises seules) : confirmer s'en sert pour les oui sensibles
            chrono["voix"] = getattr(phrase, "voix_stricte", None)
            entendue = time.monotonic()
            texte = corriger_transcription(await asyncio.to_thread(stt.transcrire, phrase.audio, veille) or "")
            chrono.update(fin=entendue - phrase.silence, silence=phrase.silence,
                          transcription=time.monotonic() - entendue, texte_pret=time.monotonic())
        if not texte or HALLUCINATIONS.search(texte) or BRUITS.match(texte) or hors_alphabet(texte):
            texte = ""  # (hors_alphabet : une vidéo en russe, en arabe…)
        elif not en_anglais(texte):
            # Une vraie phrase de toi : il affine ta voix. Avec son nom dedans, c'est sûr que c'est toi.
            if appris := oreilles.apprendre_voix(phrase, sure=phrase.reveil or bool(NOM.search(texte))):
                print(f"   🎙  Ta voix : {appris}.", flush=True)
        return texte, phrase.reveil

    # "texte" : ce que tu as dit en lui coupant la parole. "suite" : la suite de ta phrase, arrivée alors qu'il
    # n'avait encore rien répondu ("encore" : tu parlais toujours). "fusions" : combien de fois de suite.
    coupure: dict = {"texte": None, "suite": None, "encore": False, "fusions": 0}

    async def completer(texte: str, ponctuee: bool | None = None, encore: bool = False) -> str:
        """« Steve, est-ce que tu peux ouvrir… » : il attend la suite, sans rien dire, au lieu de répondre
        « ouvrir quoi ? ». Pareil pour une phrase sans point final (ta voix n'était pas retombée). Il colle
        la suite au début ; rien ne vient : Claude fera au mieux avec ce qu'il a.
        `ponctuee` : voir attente_suite. `encore` : tu parlais encore, il écoute quoi qu'il arrive."""
        for tour in range(4):
            attente = attente_suite(texte, ponctuee if tour == 0 else None)
            if encore and tour == 0:
                attente = max(attente, config.ENCORE_S)
            if not attente:
                break
            if attente > config.SUITE_SANS_POINT_S:
                print("   \033[2m(tu n'as pas fini ta phrase : je t'écoute…)\033[0m", flush=True)
            etat("ecoute")
            suite, _ = await entendre(attente, garder=True)
            if suite == "":
                continue  # un « euh », une toux : tu cherches tes mots, il attend encore un peu
            if not suite or (en_anglais(suite) and not NOM.search(suite)) or charabia(suite):
                break
            print(f"🧑 Toi (suite) : {suite}")
            texte = decoder_epellation(recoller(texte, suite))  # « T A » + « double L » → « TALL »
        return texte

    async def guetter(guet: dict, arret: threading.Event) -> None:
        """Pendant qu'il travaille ou parle, on écoute par petits morceaux : son nom ou « stop » le fait taire
        et abandonne la demande en cours (réflexion et outils compris)."""
        hp = oreilles.haut_parleur

        def baisser(tu_parles: bool) -> None:
            """Dès que tu commences à parler, il baisse sa voix (comme quelqu'un qui t'entend). Et s'il
            allait prendre la parole, il se retient : il attend que tu aies fini."""
            maintenant = time.monotonic()
            if tu_parles and guet["parle"]:
                guet["coupe_sa_voix"] = True  # tu as commencé à parler pendant qu'il parlait
            elif tu_parles and maintenant - guet["fin_voix"] > 0.6:  # (juste après sa voix : c'est son écho)
                guet["retiens"] = maintenant + config.RETENUE_S
            elif not tu_parles:
                guet["retiens"] = 0.0
            if hp is not None:
                hp.volume = config.VOLUME_SI_TU_PARLES if tu_parles else 1.0

        def a_toi(phrase) -> bool | None:
            """Ta voix reconnue (vrai), pas encore apprise (None), ou phrase trop courte pour le dire (faux)."""
            if not getattr(oreilles, "voix_connue", False):
                return None
            voix = getattr(phrase, "voix", None)
            seuil = getattr(phrase, "seuil_voix", None) or config.SEUIL_VOIX
            return voix is not None and voix >= seuil

        def suite_de_ta_phrase(entendu: str, debut: float, par_dessus: bool, toi: bool | None) -> bool:
            """Tu as repris la parole alors qu'il n'avait encore rien répondu : c'est la suite de ta demande,
            pas une autre demande. (Par-dessus son « Voyons… », seulement si c'est bien ta voix.)"""
            return (guet["fusion"] and not guet["vrai"] and coupure["fusions"] < config.FUSIONS_MAX
                    and len(re.findall(r"\w+", entendu)) >= 2 and debut - guet["depart"] <= config.FUSION_S
                    and (not par_dessus or toi is True))

        async with micro:
            premier = not guet.pop("garder", False)  # après ta demande : on garde ce que tu disais déjà
            try:
                while not arret.is_set():
                    phrase = await asyncio.to_thread(oreilles.ecouter_phrase, None, arret, 2.5, premier,
                                                     quand_tu_parles=baisser)
                    premier = False
                    if phrase is None:
                        return
                    debut = time.monotonic() - len(phrase.audio) / config.SAMPLE_RATE
                    par_dessus = (guet.pop("coupe_sa_voix", False) or bool(guet["parle"])
                                  or debut < guet["fin_voix"] + 0.3)
                    entendu = corriger_transcription(
                        await asyncio.to_thread(stt.transcrire, phrase.audio, oreilles.anti_echo) or "")
                    if hors_alphabet(entendu):
                        entendu = ""  # le bruit d'une vidéo dans une autre écriture : pas toi
                    pour_lui = False
                    if entendu and not arret.is_set() and est_interruption(entendu, guet["dit"]):
                        print(f"✋ Toi : {entendu}")
                        tts.taire()
                        cerveau.couper()
                        coupure["texte"] = entendu
                        return
                    if entendu and not arret.is_set() and ajout_probable(entendu, guet["dit"]):
                        toi = a_toi(phrase)
                        if par_dessus and coupe_la_parole(entendu) and toi is not False:
                            # Tu parles par-dessus sa réponse : comme une vraie personne, il se tait et t'écoute.
                            print(f"✋ Toi (par-dessus) : {entendu}")
                            tts.taire()
                            cerveau.couper()
                            coupure["texte"] = entendu
                            return
                        if suite_de_ta_phrase(entendu, debut, par_dessus, toi):
                            # Il avait cru ta phrase finie : il lâche ce qu'il commençait et repart avec tout.
                            print(f"🧑 Toi (suite) : {entendu}")
                            tts.taire()
                            cerveau.couper()
                            coupure["suite"], coupure["encore"] = entendu, bool(getattr(phrase, "coupee", False))
                            return
                        pour_lui = True
                        guet["ajout"].append(entendu)  # tu continuais à parler : il le traitera juste après
                        print(f"   \033[2m(noté pour juste après : {entendu})\033[0m", flush=True)
                    baisser(False)  # ce n'était pas pour lui (ou c'était son écho) : il reprend sa voix normale
                    if pour_lui and getattr(phrase, "coupee", False):
                        guet["retiens"] = time.monotonic() + 1.0  # tu parles encore : il ne démarre pas maintenant
            finally:
                baisser(False)

    # Le guet de la demande en cours : il écoute du moment où tu as fini de parler jusqu'à la fin de sa réponse.
    # "parle" : la phrase qu'il est en train de dire (None s'il travaille en silence).
    # "retiens" : jusqu'à quand il se retient de parler (tu parles). "fin_voix" : quand il s'est tu pour la
    # dernière fois. "vrai" : il a déjà vraiment répondu (pas juste « Voyons… »). "depart" : début de la demande.
    # "fusion" : ce que tu dis avant sa réponse peut être la suite de ta demande (pas pour une tâche de fond).
    guet: dict = {"tache": None, "arret": None, "dit": "", "ajout": [], "parle": None,
                  "retiens": 0.0, "fin_voix": 0.0, "vrai": False, "depart": 0.0, "fusion": False}

    def coupe_la_parole(entendu: str) -> bool:
        """Ce que tu dis par-dessus sa voix le fait taire ? Pas un « hm », ni pendant un « Voyons… »
        (là il travaille encore : ce que tu ajoutes sera traité juste après)."""
        return (config.COUPER_SI_TU_PARLES and guet["parle"] not in config.PHRASES_PATIENCE
                and len(re.findall(r"\w+", entendu)) >= config.MOTS_POUR_COUPER)

    def prendre_ajout() -> str:
        """Ce que tu as dit pendant qu'il travaillait ou répondait (« » si rien)."""
        ajout = " ".join(guet["ajout"]).strip()
        guet["ajout"] = []
        if ajout and bout_en_l_air(ajout):
            print(f"   \033[2m(bout de phrase laissé de côté : {ajout})\033[0m", flush=True)
            return ""
        if ajout:
            print(f"🧑 Toi (pendant sa réponse) : {ajout}")
        return ajout

    def comme_ajout(ajout: str) -> str | None:
        """L'ajout, prêt à servir de demande suivante."""
        if not ajout:
            return None
        return ajout if fin_de_conversation(ajout) else f"(Pendant que tu répondais, il a ajouté :) {ajout}"

    def lancer_guet() -> None:
        guet["arret"] = threading.Event()
        guet["tache"] = asyncio.create_task(guetter(guet, guet["arret"]))

    async def suspendre_guet() -> bool:
        """Arrête le guet et libère le micro. Renvoie vrai s'il tournait."""
        tache = guet["tache"]
        if tache is None:
            return False
        guet["arret"].set()
        await tache
        guet["tache"] = None
        return True

    async def demander(texte: str, fusion: bool = True) -> None:
        """Envoie ta demande à Claude ; « stop » ou son nom l'arrête à tout moment, même pendant qu'il réfléchit.
        `fusion` : c'est toi qui viens de parler, ce que tu ajoutes avant sa réponse est la suite de ta phrase."""
        coupure["texte"] = coupure["suite"] = None
        guet["dit"] = ""
        guet["ajout"] = []
        guet.update(vrai=False, retiens=0.0, fusion=fusion, garder=fusion)
        depart = guet["depart"] = time.monotonic()
        mesure = dict(chrono)  # un oui/non entendu pendant la réponse ne doit pas fausser la mesure
        chrono["texte_pret"] = None
        tts.premier_son = None
        tts.prechauffer()  # la ligne avec la voix s'ouvre pendant que Claude réfléchit
        lancer_guet()
        try:
            await cerveau.demander(texte)
        finally:
            await suspendre_guet()
        afficher_chrono(depart, mesure)

    def afficher_chrono(depart: float, chrono: dict) -> None:
        """« ⏱ 1,6 s » : de ta dernière syllabe au premier son de sa voix, étape par étape."""
        pret, son, phrase = chrono["texte_pret"], tts.premier_son, cerveau.premiere_phrase
        if not config.CHRONO or pret is None or son is None or depart - pret > 1.0:
            return  # pas une réponse à ta voix (tâche de fond, reprise après coupure…), ou rien dit
        total = son - chrono["fin"]
        delais.append(total)
        s = lambda x: f"{x:.1f}".replace(".", ",")
        etapes = [f"fin de phrase {s(chrono['silence'])}", f"texte {s(chrono['transcription'])}"]
        if phrase is not None and phrase <= son:
            etapes += [f"Claude {s(phrase - depart)}", f"voix {s(son - phrase)}"]
        else:  # le « Voyons… » est parti avant sa vraie réponse
            etapes += [f"« Voyons… » à {s(son - depart)}", f"vraie réponse à {s(phrase - depart) if phrase else '?'}"]
        print(f"   \033[2m⏱ {s(total)} s  ({' · '.join(etapes)})\033[0m", flush=True)

    async def parler(texte: str, interruptible: bool = True) -> None:
        patience = texte in config.PHRASES_PATIENCE
        if interruptible and guet["tache"] is not None:
            # Tant que tu parles, il ne prend pas la parole : il attend que tu aies fini (jamais plus de
            # RETENUE_MAX_S : une télé allumée ne doit pas le rendre muet). Un « Voyons… » ne vaut pas l'attente.
            limite = time.monotonic() + config.RETENUE_MAX_S
            while time.monotonic() < min(guet["retiens"], limite):
                if patience:
                    return
                await asyncio.sleep(0.05)
            if coupure["texte"] or coupure["suite"]:
                return  # entre-temps tu as dit autre chose : cette phrase-là ne sert plus
        print(f"🤖 {config.PRENOM} : {texte}")
        etat("parle")
        if guet["tache"] is not None or not interruptible:  # déjà guetté par la demande (ou à ne pas couper)
            guet["dit"] += " " + texte  # sa propre voix, pour ne pas la prendre pour la tienne
            guet["parle"] = texte if interruptible else None
            guet["vrai"] = guet["vrai"] or not patience
            try:
                await tts.parler(texte)
            finally:
                guet["parle"] = None
                guet["fin_voix"] = time.monotonic()
            if guet["tache"] is not None:
                etat("reflechit")  # il continue peut-être à travailler
            return
        guet["dit"] = texte
        guet["parle"] = texte
        guet.update(vrai=True, retiens=0.0, fusion=False)
        lancer_guet()
        try:
            await tts.parler(texte)
        finally:
            guet["parle"] = None
            guet["fin_voix"] = time.monotonic()
            await suspendre_guet()  # libère le micro avant qu'on l'écoute à nouveau

    async def confirmer(question: str) -> bool:
        reprendre = await suspendre_guet()  # le micro sert à entendre ton oui ou non
        try:
            await parler(question, interruptible=False)
            for essai in range(3):
                tts.bip()
                etat("ecoute")
                reponse, _ = await entendre(12)
                print(f"🧑 Toi : {reponse or '(rien)'}")
                accord = lire_accord(reponse)
                if accord:
                    controle = controle_voix(chrono.get("voix"), oreilles.voix_connue, config.CONFIRMATION_VOIX)
                    if controle == "inconnue":
                        print("   \033[2m(ce « oui » n'est pas de ta voix : je n'y vais pas)\033[0m", flush=True)
                        await parler("Ce n'est pas ta voix, je n'y vais pas.", interruptible=False)
                        return False
                    if controle == "courte":  # un « oui » seul est trop bref pour reconnaître ta voix
                        if essai < 2:
                            await parler(DEMANDES_PHRASE[min(essai, len(DEMANDES_PHRASE) - 1)],
                                         interruptible=False)
                            continue
                        await parler(REFUS_VOIX_COURTE, interruptible=False)
                        return False
                if accord is not None:
                    return accord
                if reponse and len(re.findall(r"\w+", reponse)) >= 3 and not en_anglais(reponse):
                    # Une vraie phrase qui n'est ni oui ni non : tu parles d'autre chose. Il ne fait pas
                    # l'action (pas de oui, pas d'action) et ta phrase lui arrive juste après.
                    guet["ajout"].append(reponse)
                    return False
                if essai < 2:  # pas compris : on redemande au lieu de prendre ça pour un refus
                    await parler("Pardon, c'est oui ou non ?" if reponse else "Oui ou non ?", interruptible=False)
            return False
        finally:
            if reprendre and not coupure["texte"]:
                etat("reflechit")
                lancer_guet()

    def apres_coupure(ajout: str = "") -> str | None:
        """Tu lui as coupé la parole : ta nouvelle demande (« » si tu as juste dit stop), ou None.
        `ajout` : ce que tu avais dit juste avant de le couper (« en fait non »… « Steve, mets du jazz »)."""
        if not coupure["texte"]:
            return None
        reste = STOP.sub("", extraire_commande(coupure["texte"]) or coupure["texte"]).strip(" ,.!?")
        coupure["texte"] = None
        if len(reste) < 3:
            return ""  # juste « stop » : il oublie aussi le reste
        return f"(interrompu) {f'{ajout} ' if ajout else ''}{reste}"

    discussion = {"active": False}

    def avec_annonce(demande: str, annonce: str | None) -> str:
        """Pour qu'il sache de quoi tu parles (« décale de dix minutes ») juste après une annonce."""
        return f"(Tu viens de lui annoncer : « {annonce} ».) {demande}" if annonce and demande else demande

    async def attendre_un_blanc(en_conversation: bool) -> tuple[str | None, bool]:
        """Il a quelque chose à te dire de lui-même (tâche finie, annonce, question d'une tâche) : il attend un
        blanc (ANNONCE_CALME_S sans que personne parle) pour ne jamais te couper. Si tu lui parles entre-temps,
        ta phrase passe d'abord : renvoie (ta phrase, mot-clé), ou (None, False) quand c'est le moment de parler.
        En veille, seule une phrase avec son nom compte (le reste, ce sont des gens qui parlent à côté).
        Au bout d'ANNONCE_ATTENTE_MAX_S de bavardage continu (une télé), il le dit quand même."""
        fin = time.monotonic() + config.ANNONCE_ATTENTE_MAX_S
        prevenu = False
        if not en_conversation:
            etat("veille")
        while (reste := fin - time.monotonic()) > 0:
            texte, reveil = await entendre(min(config.ANNONCE_CALME_S, reste), veille=not en_conversation, garder=True)
            if texte is None:
                return None, False  # un blanc : c'est le moment
            if not texte and not reveil:
                continue  # un bruit
            if en_conversation:
                if texte and not (en_anglais(texte) and not NOM.search(texte)) and not charabia(texte):
                    return texte, reveil
            elif reveil or NOM.search(texte):
                return texte, reveil
            if not prevenu:
                print("   \033[2m(on parle à côté : j'attends un blanc pour te le dire)\033[0m", flush=True)
                prevenu = True
        return None, False

    async def dire_evenements() -> tuple[str | None, str | None]:
        """Dit ce qu'il a à te dire de lui-même : questions de tes tâches de fond, annonces, tâches finies (celles
        qui ont fini en même temps : un seul compte rendu), actions programmées.
        Renvoie (ce que tu as répondu par-dessus, la dernière annonce dite). Ta réponse vaut None si tu n'as rien
        dit, « » si tu as dit stop ; ce qui reste à dire attend le prochain blanc."""
        annonce = None
        while ev := standard.prendre():
            if ev.genre == "question":
                if ev.futur is not None and ev.futur.done():
                    continue  # la tâche n'attend plus la réponse (annulée, ou trop tard)
                tts.bip()
                ok = await confirmer(ev.texte)
                if ev.futur and not ev.futur.done():
                    ev.futur.set_result(ok)
                if ajout := prendre_ajout():  # tu as parlé d'autre chose à la place d'un oui ou d'un non
                    return comme_ajout(ajout), None
                continue
            coupure["texte"] = None
            if ev.genre == "annonce":
                tts.bip()
                await parler(ev.texte)
                annonce = ev.texte
            else:  # tâche finie, action programmée : c'est Steve (Claude) qui s'en charge et te le dit
                texte = ev.texte
                if ev.genre == "fini" and standard.en_attente(("fini",)):
                    textes = [texte]
                    while autre := standard.prendre(("fini",)):
                        textes.append(autre.texte)
                    texte = (f"({len(textes)} tâches de fond viennent de finir : fais-lui un seul compte rendu, "
                             "une ou deux phrases par tâche.)\n\n" + "\n\n".join(textes))
                etat("reflechit")
                annonce = None
                await demander(texte, fusion=False)
            ajout = prendre_ajout()
            reprise = apres_coupure(ajout)
            if reprise is not None:
                return avec_annonce(reprise, annonce), None
            if ajout:
                return avec_annonce(comme_ajout(ajout), annonce), None
        return None, annonce

    async def ecouter_suite(annonce: str | None = None) -> str | None:
        """La suite de la conversation, sans son nom. Toux, bruit ou phrase vide : il continue d'écouter.
        Une tâche qui finit pendant ce temps : il te le dit au premier blanc, puis la conversation continue.
        Rien pendant le délai : petit son doux, il repasse en veille. `annonce` : ce qu'il vient de t'annoncer."""
        tts.bip()
        etat("ecoute")
        if discussion["active"]:
            delai = config.DISCUSSION_S
        elif cerveau.derniere_reponse.rstrip().endswith("?"):  # il t'a posé une question : plus de temps
            delai = config.FOLLOW_UP_QUESTION_S
        else:
            delai = config.FOLLOW_UP_S
        fin = time.monotonic() + delai
        while (reste := fin - time.monotonic()) > 0:
            suite, _ = await entendre(reste, arret=standard.sonnerie)
            if suite is None:
                if not standard.sonnerie.is_set():
                    break
                if not standard.en_attente() or oreilles.en_pause:
                    standard.sonnerie.clear()  # sonnerie restée levée sans rien à dire : on continue d'écouter
                    continue
                # Il a quelque chose à te dire : au premier blanc, sans finir la conversation.
                texte, _ = await attendre_un_blanc(en_conversation=True)
                if texte:
                    print(f"🧑 Toi : {texte}")  # tu parlais : ta phrase d'abord, l'annonce attendra
                    return avec_annonce(await completer(texte), annonce)
                reponse, annonce = await dire_evenements()
                if reponse:
                    return reponse
                tts.bip()
                etat("ecoute")
                fin = time.monotonic() + delai  # et il t'écoute à nouveau, comme après une réponse
                continue
            if suite and en_anglais(suite) and not NOM.search(suite):
                print(f"   \033[2m(en anglais, sûrement une vidéo : ignoré : {suite})\033[0m", flush=True)
                continue
            if suite and charabia(suite) and not NOM.search(suite):
                print(f"   \033[2m(du bruit pris pour une phrase : ignoré : {suite})\033[0m", flush=True)
                continue
            if suite:
                print(f"🧑 Toi : {suite}")
                return avec_annonce(await completer(suite), annonce)
        print(f"   \033[2m(fin de la conversation : rien entendu en {delai:.0f} s)\033[0m", flush=True)
        discussion["active"] = False
        tts.bip_fin()
        return None

    async def repondre_questions() -> None:
        """Les questions de tes tâches de fond, posées dès qu'il y a un blanc dans la conversation."""
        while ev := standard.prendre(("question",)):
            ok = await confirmer(ev.texte)
            if ev.futur and not ev.futur.done():
                ev.futur.set_result(ok)

    async def reprendre_avec_suite(commande: str) -> str:
        """Ta demande en entier : le début (sur lequel il s'était lancé), ce que tu as ajouté, et la fin
        qu'il écoute encore si tu n'avais pas fini."""
        suite, coupure["suite"] = coupure["suite"], None
        coupure["fusions"] += 1
        entiere = commande.removeprefix(MARQUE_SUITE).strip()
        for morceau in [*guet["ajout"], suite]:
            if morceau:  # (la reconnaissance réentend souvent la fin d'un morceau au début du suivant)
                entiere = recoller(entiere, morceau)
        guet["ajout"] = []
        entiere = await completer(decoder_epellation(entiere), encore=coupure["encore"])
        return f"{MARQUE_SUITE} {entiere}"

    async def converser(commande: str | None) -> None:
        """Après chaque réponse, tu peux enchaîner sans redire son nom."""
        while commande:
            if fin_de_conversation(commande):
                discussion["active"] = False
                await parler("À plus tard.", interruptible=False)
                return
            if m := DISCUSSION.match(commande):
                discussion["active"] = True
                if len(commande[m.end():].strip(" ,.!?")) < 3:  # juste « on discute » : pas besoin de Claude
                    await parler("Je t'écoute.", interruptible=False)
                    commande = await ecouter_suite()
                    continue
            etat("reflechit")
            await demander(commande)
            if coupure["suite"]:  # tu as continué ta phrase avant sa réponse : il repart avec la phrase entière
                commande = await reprendre_avec_suite(commande)
                continue
            coupure["fusions"] = 0
            ajout = prendre_ajout()
            nette = bool(coupure["texte"] and FIN_NETTE.search(coupure["texte"]))
            if (reprise := apres_coupure(ajout)) is not None:
                if reprise:  # tu lui as coupé la parole au milieu d'une phrase ?
                    commande = await completer(reprise, ponctuee=nette)
                    continue
                ajout = ""  # tu as dit stop : on oublie le reste
            await repondre_questions()
            ajout = ajout or prendre_ajout()  # ce que tu as répondu à la place d'un oui ou d'un non
            commande = comme_ajout(ajout) or await ecouter_suite()

    async def traiter_evenements() -> None:
        """Ce qu'il a à te dire de lui-même (tâche finie, question d'une tâche, annonce, action programmée),
        puis la conversation que tu enchaînes. Ce qui arrive pendant ce temps est dit aux blancs suivants."""
        reponse, annonce = await dire_evenements()
        await converser(reponse if reponse is not None else await ecouter_suite(annonce))
        discussion["active"] = False
        cerveau.fin_de_conversation()

    async def repondre_par_ecrit() -> None:
        """Le chat du tableau de bord : tes messages tapés, un par un. Il répond par écrit, sans un mot à voix
        haute, et ses questions oui/non s'affichent avec des boutons. Ta voix et le chat se relaient (verrou)."""
        messagerie, a_relire = chat.MESSAGERIE, False
        while True:
            try:
                texte = await (asyncio.wait_for(messagerie.prochain(), config.ECRIT_RELECTURE_S) if a_relire
                               else messagerie.prochain())
            except asyncio.TimeoutError:  # tu ne lui écris plus : il relit l'échange pour sa mémoire et son carnet
                if not discussion["active"]:
                    cerveau.fin_de_conversation()
                a_relire = False
                continue
            print(f"🧑 Toi (écrit) : {texte}", flush=True)
            n = messagerie.ajouter("steve", "", fini=False)
            try:
                reponse = await cerveau.demander(
                    texte, ecrit=lambda morceau: messagerie.completer(n, morceau),
                    confirmer_ecrit=lambda question: messagerie.demander(question, config.QUESTION_ECRITE_S))
            except Exception as e:
                reponse = f"⚠️ Je n'ai pas pu répondre ({e!r}). Réessaie, ou dis-le-moi à voix haute."
            reponse = reponse or "OK."
            lignes = " ⏎ ".join(l.strip() for l in reponse.splitlines() if l.strip())
            print(f"🤖 {config.PRENOM} (écrit) : {lignes}", flush=True)  # le journal d'abord : la page le relit aussitôt
            messagerie.fermer(n, reponse)
            a_relire = True

    standard, taches, programmateur, outils_en_plus = _equipe()
    etat.taches = taches
    cerveau = Cerveau(parler, confirmer, outils_en_plus=outils_en_plus,
                      anticiper=tts.anticiper, patience=config.PATIENCE)
    attente = [cerveau.demarrer()]
    # Les « Voyons… » sont fabriqués d'avance : ils partent sans aucun délai.
    attente.append(tts.precharger(config.PHRASES_PATIENCE if config.PATIENCE else []))
    await asyncio.gather(*attente)
    if voix_locale.prete():
        print(f"   🗣  Voix locale « {config.VOIX_LOCALE} » prête (sans Internet).", flush=True)
    elif voix_locale.active():
        print(f"   ⚠️  Voix locale indisponible ({voix_locale.erreur or 'trop longue à charger'}) : "
              "il parle avec la voix Microsoft.", flush=True)
    horloge = asyncio.create_task(programmateur.tourner())
    chat.MESSAGERIE.brancher(asyncio.get_running_loop())
    par_ecrit = asyncio.create_task(repondre_par_ecrit())
    n = config.PRENOM
    if manquantes := outils.autorisations_manquantes():  # sans elles, « efface ci, fais ça » échoue en silence
        noms = ", ".join(outils.VOLETS[m][1] for m in manquantes)
        print(f"   🔐 Autorisations de macOS à me donner : {noms}. Menu {n} → « Autorisations… », "
              f"ou dis « {n}, vérifie tes autorisations ».", flush=True)
    await parler(config.ANNONCE, interruptible=False)
    print(f"👂 Parle-moi en disant « {n} » : « Hey {n} », « Salut {n} », « {n}, ouvre Cursor »… "
          "(Ctrl+C pour quitter)", flush=True)

    try:
        while True:
            if standard.en_attente():
                if oreilles.en_pause:  # micro en pause = ne pas déranger : ça attendra que tu le rallumes
                    await asyncio.sleep(1)
                    continue
                # Il a quelque chose à te dire : il attend un blanc. Si tu l'appelles entre-temps, toi d'abord.
                texte, reveil = await attendre_un_blanc(en_conversation=False)
                if texte is None:
                    await traiter_evenements()
                    print("💤 En veille.")
                    continue
            else:
                # Veille : on réagit si le modèle a entendu le mot-clé, ou si son nom est dans la phrase transcrite.
                # Une annonce ou une tâche finie la réveille aussi (entre deux phrases, jamais au milieu).
                etat("veille")
                texte, reveil = await entendre(None, veille=True, arret=standard.sonnerie)
            if texte is None or (not texte and not reveil):
                continue
            commande = extraire_commande(texte)
            if commande is None and reveil:  # mot-clé bien entendu, mais nom mal écrit par la transcription
                commande = SALUTATION.sub("", texte).strip(" ,.!?")
            if commande is None:
                print(f"   \033[2m(entendu sans « {n} » : {texte})\033[0m", flush=True)
                continue
            print(f"🧑 Toi : {texte}" + (f"  \033[2m(« {config.PHRASE_MOTCLE} »)\033[0m" if reveil else ""), flush=True)
            tts.bip()

            if len(commande) < 3:  # juste « Salut Steve » : on attend la suite
                etat("ecoute")
                commande, _ = await entendre(config.NO_SPEECH_TIMEOUT_S, garder=True)
                if commande:
                    print(f"🧑 Toi : {commande}")
            if commande:  # (extraire_commande a retiré le point final : on regarde la phrase d'origine)
                commande = await completer(commande, ponctuee=bool(FIN_NETTE.search(texte)))

            await converser(commande)
            discussion["active"] = False
            cerveau.fin_de_conversation()  # il met sa mémoire à jour pendant que tu fais autre chose
            print("💤 En veille.")
    finally:
        if delais:
            delais.sort()
            print(f"⏱ Temps de réponse sur {len(delais)} échanges : médiane {delais[len(delais) // 2]:.1f} s, "
                  f"le plus lent {delais[-1]:.1f} s.".replace(".", ",", 2))
        print(f"💰 Aujourd'hui : {compteur.dollars(compteur.aujourdhui())} en équivalent prix API "
              "(sur ton abonnement, ça compte dans tes limites, tu ne le paies pas en plus).")
        horloge.cancel()
        chat.MESSAGERIE.debrancher()
        par_ecrit.cancel()
        await taches.arreter()
        await cerveau.arreter()
        oreilles.fermer()


PHRASES_A_LIRE = [
    f"Salut {config.PRENOM}, qu'est-ce que j'ai dans mon agenda demain matin ?",
    "Envoie un message à Léa pour lui dire que j'arrive dans dix minutes.",
    "Rappelle-moi d'appeler le garage vendredi vers quatorze heures.",
    "Quel temps va-t-il faire ce week-end, et est-ce que je dois prendre un parapluie ?",
    "Ouvre Cursor, mets un peu de musique et baisse le volume à trente pour cent.",
    "Lis-moi les trois derniers mails importants que j'ai reçus aujourd'hui.",
]


async def apprendre_ma_voix() -> None:
    """Enregistre l'empreinte de ta voix : ensuite, il ignore les phrases dites par quelqu'un d'autre."""
    from .audio import Oreilles
    from .voix import Empreinte

    print("⏳ Préparation…", flush=True)
    oreilles = await asyncio.to_thread(Oreilles)
    empreinte = await asyncio.to_thread(Empreinte)
    print("\nLis chaque phrase à voix haute, normalement, à ta place habituelle devant le Mac.\n"
          f"(Mets {config.PRENOM} en pause dans la barre des menus pendant ce temps, sinon il va te répondre.)\n")
    enregistrements = []
    try:
        for i, texte in enumerate(PHRASES_A_LIRE, 1):
            print(f"  {i}/{len(PHRASES_A_LIRE)}  « {texte} »", flush=True)
            for _ in range(3):
                phrase = await asyncio.to_thread(oreilles.ecouter_phrase, 15, verifier_voix=False)
                if phrase and phrase.duree_parole >= 1.5:
                    enregistrements.append(phrase.audio)
                    print(f"        ✓ ({phrase.duree_parole:.1f} s de voix)", flush=True)
                    break
                print("        … je n'ai pas bien entendu, relis-la.", flush=True)
    finally:
        oreilles.fermer()
    if len(enregistrements) < 3:
        print("\n⚠️  Pas assez de phrases enregistrées : rien n'est changé.")
        return
    scores = await asyncio.to_thread(empreinte.enregistrer, enregistrements)
    print(f"\n✅ Ta voix est enregistrée dans {Empreinte.FICHIER.name}.")
    print(f"   Tes phrases ressemblent à ton empreinte entre {min(scores):.2f} et {max(scores):.2f} "
          f"(seuil actuel : {config.SEUIL_VOIX}).")
    if min(scores) - 0.1 < config.SEUIL_VOIX:
        conseil = max(0.3, round(min(scores) - 0.15, 2))
        print(f"   ⚠️  C'est proche du seuil : relance-le avec JERVIS_SEUIL_VOIX={conseil} "
              "s'il ignore parfois ta voix.")
    print(f"   Relance {config.PRENOM} (installer.command, ou Quitter puis rouvrir) pour qu'il en tienne compte.\n"
          f"   Pour revenir en arrière : supprime le fichier {Empreinte.FICHIER}.")


def mode_barre() -> None:
    """Il vit dans la barre des menus. L'interface tourne sur le fil principal (obligatoire sur macOS),
    et tout le reste (micro, transcription, Claude) dans un fil à côté."""
    import os
    import subprocess

    import rumps
    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

    from . import fond, lecons, outils, tableau

    etat = Etat()

    def vivre() -> None:
        pannes: list[float] = []
        while True:
            try:
                asyncio.run(mode_vocal(etat))
                return
            except Exception as e:  # visible dans l'icône et dans jervis.log
                print(f"⚠️  {config.PRENOM} s'est arrêté : {e!r}", flush=True)
                etat.nom = "erreur"
                if etat.oreilles is not None:
                    try:
                        etat.oreilles.fermer()  # libère le micro avant de le rouvrir
                    except Exception:
                        pass
                    etat.oreilles = None
                pannes = [t for t in pannes if time.monotonic() - t < 600] + [time.monotonic()]
                if len(pannes) > 3:  # trop de pannes d'affilée : on n'insiste pas
                    print("   Trop de pannes en 10 minutes : relance-le avec installer.command.", flush=True)
                    return
                print("   🔁 Je redémarre tout seul dans 3 secondes…", flush=True)
                time.sleep(3)

    app = rumps.App(config.PRENOM, title=etat.icone, quit_button=None)

    def basculer_pause(_item=None) -> None:
        if etat.oreilles is None:
            return
        pause = not etat.oreilles.en_pause
        etat.oreilles.pause(pause)
        etat.nom = "pause" if pause else "veille"

    def ouvrir(chemin) -> None:
        chemin.touch(exist_ok=True)
        subprocess.Popen(["open", "-t", str(chemin)])

    # Le tableau de bord a les mêmes boutons que ce menu (il les appelle depuis son propre fil).
    ecran = tableau.Tableau(etat, {
        "pause": basculer_pause,
        "memoire": lambda: ouvrir(outils.MEMOIRE),
        "conversations": lambda: ouvrir(outils.JOURNAL),
        "briefing": lambda: (fond.lire_briefing(), ouvrir(config.FICHIER_BRIEFING)),
        "ronde": lambda: (fond.lire_initiative(), ouvrir(config.FICHIER_INITIATIVE)),
        "carnet": lambda: ouvrir(lecons.preparer()),
        "journal": lambda: ouvrir(config.DOSSIER / "jervis.log"),
    })
    if config.TABLEAU and ecran.demarrer() and config.TABLEAU_AU_DEMARRAGE:
        ecran.ouvrir()

    bouton_pause = rumps.MenuItem("Mettre le micro en pause", callback=basculer_pause)
    app.menu = [
        bouton_pause,
        rumps.MenuItem("Tableau de bord", callback=lambda _: ecran.ouvrir()),
        None,
        rumps.MenuItem("Ce qu'il sait de moi (mémoire)", callback=lambda _: ouvrir(outils.MEMOIRE)),
        rumps.MenuItem("Nos conversations", callback=lambda _: ouvrir(outils.JOURNAL)),
        rumps.MenuItem("Mon briefing du matin", callback=lambda _: (fond.lire_briefing(),
                                                                     ouvrir(config.FICHIER_BRIEFING))),
        rumps.MenuItem("Ce qu'il surveille pour moi (sa ronde)", callback=lambda _: (fond.lire_initiative(),
                                                                                    ouvrir(config.FICHIER_INITIATIVE))),
        rumps.MenuItem("Ce qu'il a appris (son carnet)", callback=lambda _: ouvrir(lecons.preparer())),
        rumps.MenuItem("Voir le journal technique", callback=lambda _: ouvrir(config.DOSSIER / "jervis.log")),
        rumps.MenuItem("Autorisations…", callback=lambda _: subprocess.Popen(
            ["open", str(config.DOSSIER / "outils" / "autorisations.command")])),  # le guide, dans le Terminal
        None,
        rumps.MenuItem(f"Quitter {config.PRENOM}", callback=lambda _: os._exit(0)),
    ]

    def rafraichir(_timer) -> None:
        if app.title != etat.icone:
            app.title = etat.icone
        titre = "Rallumer le micro" if etat.oreilles and etat.oreilles.en_pause else "Mettre le micro en pause"
        if bouton_pause.title != titre:  # la pause peut aussi venir du tableau de bord
            bouton_pause.title = titre

    rumps.Timer(rafraichir, 0.3).start()
    threading.Thread(target=vivre, daemon=True).start()
    NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)  # pas d'icône dans le Dock
    app.run()


def main() -> None:
    p = argparse.ArgumentParser(prog="jervis", description="Ton assistant vocal personnel.")
    p.add_argument("--texte", action="store_true", help="tape au clavier au lieu de parler")
    p.add_argument("--muet", action="store_true", help="en mode texte, ne parle pas à voix haute")
    p.add_argument("--barre", action="store_true", help="icône dans la barre des menus (lancé par Jervis.app)")
    p.add_argument("--apprendre-ma-voix", action="store_true",
                   help="enregistre ta voix : il ignorera ensuite les phrases dites par quelqu'un d'autre")
    args = p.parse_args()
    if args.barre:
        mode_barre()
        return
    try:
        if args.apprendre_ma_voix:
            asyncio.run(apprendre_ma_voix())
        else:
            asyncio.run(mode_texte(args.muet) if args.texte else mode_vocal())
    except (KeyboardInterrupt, EOFError):
        print("\n👋 À plus tard.")


if __name__ == "__main__":
    main()
