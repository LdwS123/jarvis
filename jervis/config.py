"""Réglages de l'assistant. Chaque valeur peut être surchargée par une variable d'environnement.

Ordre : la variable JERVIS_… si elle existe, sinon ce que tu as choisi dans la page Réglages (reglages.json,
à côté de ce dossier, jamais dans le dépôt), sinon la valeur par défaut écrite ici."""

import json
import os
import re
from pathlib import Path

from . import langues as _langues

DOSSIER = Path(__file__).resolve().parent.parent
# JERVIS_REGLAGES=non : ignore reglages.json (les tests le font, pour ne pas dépendre de tes choix) ;
# JERVIS_REGLAGES=/un/autre/fichier.json : le lit là.
SANS_REGLAGES = os.environ.get("JERVIS_REGLAGES", "").lower() in {"non", "off", "0"}
FICHIER_REGLAGES = (Path(os.environ["JERVIS_REGLAGES"]).expanduser()
                    if os.environ.get("JERVIS_REGLAGES") and not SANS_REGLAGES else DOSSIER / "reglages.json")


def _lire_reglages() -> dict[str, str]:
    if SANS_REGLAGES:
        return {}
    try:
        donnees = json.loads(FICHIER_REGLAGES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in donnees.items() if isinstance(v, (str, int, float))} if isinstance(donnees, dict) else {}


_REGLAGES = _lire_reglages()


def reglage(name: str) -> str:
    """Ce que tu as choisi dans la page Réglages pour ce nom (« » si rien)."""
    return _REGLAGES.get(name, "")


def _env(name: str, default: str) -> str:
    valeur = os.environ.get(f"JERVIS_{name}")
    return valeur if valeur is not None else _REGLAGES.get(name, default)


# --- Micro ----------------------------------------------------------------
SAMPLE_RATE = 16_000          # openWakeWord et Whisper attendent du 16 kHz mono
FRAME = 1_280                 # 80 ms par bloc

# --- Détection de la voix (Silero VAD, local) ------------------------------
SEUIL_VAD = float(_env("SEUIL_VAD", "0.5"))   # baisse (0.3) s'il ne t'entend pas, monte (0.7) s'il réagit au bruit

# --- Son nom ---------------------------------------------------------------
# Le nom auquel il répond (et qu'il dit). JERVIS_PRENOM=Jarvis pour revenir à l'ancien.
PRENOM = _env("PRENOM", "Steve")
# Tout ce qui ressemble à son nom dans ce que la transcription a écrit.
_VARIANTES = {
    # Jervis, Jarvis, Gervais, Jervice…
    "jervis": r"(?:\b(?:d?j|g)[ae]r\s?v(?:i|ai|é|y)[sc]?e?\b|贾维斯|\bჯარვის(?:ი)?\b)",  # + en chinois, en géorgien
    # Steve, Stève, Stive, Steeve, et « Стив » (sur 2 syllabes, Parakeet croit parfois entendre du russe).
    # Pas suivi d'un nom de famille (« Steve Jobs », « Steve Carell ») : là, on parle de quelqu'un d'autre.
    "steve": r"(?:\b(?:st[eéèi]{1,2}ve?s?|s[iy]ve?|стив)\b(?!\s+(?-i:[A-Z])\w)"  # + « Siv » (mal entendu)
             # Écrit dans tes autres langues (chinois, japonais, coréen : sans espaces, donc sans \b ; géorgien,
             # arabe, hébreu, grec : avec). Whisper écrit « 史蒂夫 » ou « სტივ » quand tu parles chinois ou géorgien.
             r"|史[蒂提]夫|斯[蒂提]夫|スティーブ|스티브|\b(?:სტივ(?:ი|ის|ს)?|ستيف|סטיב|στιβ)\b)",
}
_VARIANTES["jarvis"] = _VARIANTES["jervis"]
NOM = _env("NOM", _VARIANTES.get(PRENOM.casefold(), rf"\b{re.escape(PRENOM)}\b"))

# --- Langues ---------------------------------------------------------------------------------------------
# Ta langue principale (fr, en, zh, ka, es…) : celle de ses annonces, de sa voix de tous les jours.
LANGUE = _env("LANGUE", "fr")
# Toutes celles que tu lui parles : il reconnaît la langue de chaque phrase et te répond dans la même, avec une
# voix de cette langue. « fr,en,zh,ka », ou « toutes ». Par défaut, la principale seule : une vidéo en anglais
# ou en russe n'est alors jamais prise pour toi (avec plusieurs langues, ~0,3 s de plus par phrase).
LANGUES = _env("LANGUES", LANGUE)
# Ce qu'il dit quand il est prêt. (« Steve en ligne. », sans verbe, sa voix locale le disait de travers.)
ANNONCE = _langues.annonce(LANGUE, PRENOM)

# --- Enregistrement de ta phrase ------------------------------------------
SILENCE_END_S = float(_env("SILENCE_END_S", "0.8"))     # silence qui marque la fin de ta phrase
MAX_UTTERANCE_S = float(_env("MAX_UTTERANCE_S", "20"))
NO_SPEECH_TIMEOUT_S = float(_env("NO_SPEECH_TIMEOUT_S", "5"))
# Le son gardé juste avant le début de ta voix (la première syllabe, souvent douce, n'est pas perdue).
AVANT_PAROLE_S = float(_env("AVANT_PAROLE_S", "0.32"))
# En dessous de ce temps de parole, c'est un bruit (toux, porte, clavier) : il n'en fait pas une phrase.
MIN_PAROLE_S = float(_env("MIN_PAROLE_S", "0.35"))
# Conversation ouverte : tu dis son nom une fois, puis tu enchaînes sans lui. Le délai repart à chaque échange ;
# c'est le temps qu'il te laisse pour COMMENCER à parler (ensuite il attend que tu aies fini, même si tu cherches tes mots).
FOLLOW_UP_S = float(_env("FOLLOW_UP_S", "30"))
FOLLOW_UP_QUESTION_S = float(_env("FOLLOW_UP_QUESTION_S", "45"))  # … quand il vient de te poser une question
# « Steve, on discute » : il reste à l'écoute sans limite (jusqu'à « merci », « c'est bon », ou ce silence-là).
DISCUSSION_S = float(_env("DISCUSSION_S", "600"))

# --- Transcription ---------------------------------------------------------
WHISPER_MODEL = _env("WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo")

# --- Cerveau (Claude Code, sur ton abonnement) -----------------------------
MODELE_CLAUDE = _env("MODELE", "sonnet")
# Réflexion : il pense avant de répondre, autant que la question le demande (presque rien pour « mets de la
# musique », beaucoup plus pour « organise ma semaine »). Effort : low = plus rapide (le défaut : à l'oral, sa
# première phrase arrive 0,2 à 0,3 s plus tôt), medium ou high = plus réfléchi. Les grosses tâches, elles, partent
# chez l'expert (plus bas), qui garde son propre effort.
REFLEXION = _env("REFLEXION", "oui").lower() not in {"non", "off", "0"}
EFFORT = _env("EFFORT", "low")
# Il commence à parler sans attendre la fin de sa première phrase : dès qu'une virgule (ou « ; », « : ») tombe après
# au moins DEBUT_TOT_CARACTERES caractères, ce premier morceau part à la voix. 0 = il attend toujours le point.
DEBUT_TOT_CARACTERES = int(_env("DEBUT_TOT_CARACTERES", "35"))
# Son « expert » : un second agent, plus puissant, à qui il confie les grosses tâches en plusieurs étapes
# (coder, comparer, rédiger un long document…). Il travaille EN ARRIÈRE-PLAN : Steve reste à l'écoute pendant
# ce temps, et te prévient quand c'est fini. Mêmes outils, mêmes confirmations.
MODELE_EXPERT = _env("MODELE_EXPERT", "opus")
EFFORT_EXPERT = _env("EFFORT_EXPERT", "high")
TACHES_MAX = int(_env("TACHES_MAX", "5"))   # agents de fond en même temps
# Quand un agent a fini (ou une annonce tombe), il ne te coupe jamais : il attend un blanc, personne ne parle depuis
# ANNONCE_CALME_S. Si on parle sans arrêt à côté (télé, appel), il le dit quand même au bout d'ANNONCE_ATTENTE_MAX_S.
# En pleine conversation, il le dit entre deux de tes phrases, sans quitter la conversation.
ANNONCE_CALME_S = float(_env("ANNONCE_CALME_S", "1.2"))
ANNONCE_ATTENTE_MAX_S = float(_env("ANNONCE_ATTENTE_MAX_S", "20"))
# Une question d'un agent (« je peux envoyer ? ») sans réponse pendant ce temps : c'est non, l'agent continue.
QUESTION_FOND_S = float(_env("QUESTION_FOND_S", "1800"))
# Un agent attend au plus ce temps que les connecteurs claude.ai (Gmail, Agenda…) soient prêts avant de démarrer.
CONNECTEURS_FOND_S = float(_env("CONNECTEURS_FOND_S", "10"))
# Après ce temps sans lui parler, il repart sur une conversation neuve (il garde sa mémoire et vos 3 derniers
# échanges). Sinon toute la journée est relue à chaque phrase : plus lent, et ça mange ton abonnement.
OUBLI_MIN = float(_env("OUBLI_MIN", "15"))
# « 💰 2,1 cents · aujourd'hui 0,84 $ » après chaque réponse (équivalent prix API). JERVIS_COUT=non pour le masquer.
AFFICHER_COUT = _env("COUT", "oui").lower() not in {"non", "off", "0"}

# --- Confirmations ------------------------------------------------------------
#   autonome     : (par défaut) il fait tout seul et en prend la responsabilité : messages, comptes, publications,
#                  mots de passe, fichiers, corbeille, Chrome. Il ne te demande que pour l'argent (et les paiements
#                  sont coupés tant que JERVIS_PAIEMENTS n'est pas « oui »). sudo, rm, kill restent refusés.
#   confiance    : si tu le lui demandes, c'est oui : il envoie le message, le mail, l'invitation sans redemander.
#                  Il ne confirme que l'argent, ce qui détruit pour de bon (rm, kill, supprimer), les mots de passe,
#                  le Terminal, et ce qu'une page ou un mail lui suggère sans que tu l'aies demandé.
#   essentielles : en plus, il confirme tout ce qui part chez quelqu'un d'autre (message, mail, partage).
#   toutes       : l'ancien réglage, prudent : il demande avant chaque création, modification ou commande inconnue.
CONFIRMATIONS = _env("CONFIRMATIONS", "autonome").lower()
# Méfiance (même en autonome) : juste après un texte venu d'ailleurs (une page, un mail, la sortie d'une commande),
# ce qui pilote tes apps (clics, frappes, scripts), lance un fichier ou le code d'un projet, arrête un programme,
# compose un numéro lu dans ce texte, te demande d'abord : ce texte a pu le lui dicter.
# JERVIS_MEFIANCE=non : plus aucune de ces questions ni de cage, seul l'argent passe par toi. À tes risques : une
# page ou un mail piégé qu'il lit peut alors lui faire écrire, cliquer ou taper en ton nom sans que tu le voies.
# Restent quoi qu'il arrive : l'argent, les numéros d'urgence, sudo/rm/kill (la corbeille à la place), Messages par
# un script.
MEFIANCE = _env("MEFIANCE", "oui").lower() not in {"non", "off", "0"}

# --- Cursor ----------------------------------------------------------------------
# Quand tu dis « écris ça dans Cursor » sans préciser, où va le prompt ?
#   claude_code : dans l'extension Claude Code de Cursor (collé dans l'onglet Claude Code déjà ouvert ;
#                 un nouvel onglet ou une nouvelle fenêtre seulement si tu le demandes).
#   agent       : dans le chat de l'agent de Cursor lui-même.
# Si tu précises à voix haute (« dans Claude Code », « à l'agent de Cursor »), c'est ce que tu dis qui compte.
CIBLE_CURSOR = _env("CURSOR_CIBLE", "claude_code").strip().lower()

# --- Mémoire ---------------------------------------------------------------------
# Après chaque conversation, un petit modèle relit ce que vous vous êtes dit et retient seul ce qui compte
# (en remplaçant ce qui n'est plus vrai). Chaque échange est aussi gardé dans espace/conversations.jsonl.
MEMOIRE_AUTO = _env("MEMOIRE_AUTO", "oui").lower() not in {"non", "off", "0"}
MODELE_MEMOIRE = _env("MODELE_MEMOIRE", "haiku")

# --- Le graphe de ce qu'il sait (Graphiti, tout en local) ---------------------------
# Les faits retenus sont aussi reliés dans un graphe : les gens, les lieux, les projets, les liens entre eux,
# datés (ce qui n'est plus vrai reste, marqué « plus vrai depuis »). La base est un fichier dans espace/, les
# empreintes des phrases sont calculées par Ollama sur le Mac (ollama pull embeddinggemma). Sans Ollama ou
# sans Graphiti installé, il n'y a juste pas de graphe. JERVIS_GRAPHE=non pour le couper.
GRAPHE = _env("GRAPHE", "oui").lower() not in {"non", "off", "0"}
FICHIER_GRAPHE = DOSSIER / "espace" / "graphe.db"
OLLAMA_URL = _env("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
GRAPHE_EMBED = _env("GRAPHE_EMBED", "embeddinggemma")   # le modèle d'empreintes, dans Ollama
GRAPHE_DIM = int(_env("GRAPHE_DIM", "768"))             # leur taille (768 pour embeddinggemma)
MODELE_GRAPHE = _env("MODELE_GRAPHE", MODELE_MEMOIRE)   # qui lit « qui est qui, quel lien »
GRAPHE_PARALLELE = int(_env("GRAPHE_PARALLELE", "3"))   # lectures en même temps (chacune lance un petit Claude)

# --- Briefing du matin et annonces programmées ------------------------------------
# Le premier moment où tu es devant ton Mac après cette heure (et avant midi), il te fait le point.
# Ce qu'il dit est dans briefing.md (modifie-le comme tu veux). JERVIS_BRIEFING=non pour le couper.
BRIEFING = _env("BRIEFING", "oui").lower() not in {"non", "off", "0"}
BRIEFING_HEURE = _env("BRIEFING_HEURE", "07:30")
# Le bilan du soir : à cette heure, il te fait le point de la journée (ce qu'il a fait, ce qui attend, demain).
# Devant le Mac : à voix haute. Pas là au bout de BILAN_SOIR_ATTENTE_MIN minutes : un message vocal, dit avec sa
# voix, sur ton iPhone (là où tu lui as écrit en dernier ; WhatsApp d'abord), et tu lui réponds là-bas. Sans iPhone
# branché, il attend que tu reviennes devant le Mac. Plus tard que BILAN_SOIR_RETARD_MIN après l'heure (le Mac
# était éteint), il laisse tomber pour ce soir. Ce qu'il dit est dans bilan_soir.md. JERVIS_BILAN_SOIR=non pour le
# couper ; JERVIS_BILAN_SOIR_IPHONE=non pour qu'il ne te l'envoie jamais sur l'iPhone.
BILAN_SOIR = _env("BILAN_SOIR", "oui").lower() not in {"non", "off", "0"}
BILAN_SOIR_HEURE = _env("BILAN_SOIR_HEURE", "22:00")
BILAN_SOIR_ATTENTE_MIN = float(_env("BILAN_SOIR_ATTENTE_MIN", "10"))
BILAN_SOIR_RETARD_MIN = float(_env("BILAN_SOIR_RETARD_MIN", "90"))
BILAN_SOIR_IPHONE = _env("BILAN_SOIR_IPHONE", "oui").lower() not in {"non", "off", "0"}
# Un vrai appel au lieu du vocal (telephone.py) : ton numéro ou ton adresse Apple, que FaceTime (connecté sur le Mac
# à un AUTRE identifiant Apple que le tien : on ne s'appelle pas soi-même) appelle en FaceTime audio. Sa voix passe
# dans l'appel par le câble audio APPEL_CABLE (BlackHole 2ch, gratuit) ; il t'entend par les haut-parleurs du Mac.
# Tu décroches et dis « allô » (dans les BILAN_SOIR_SONNERIE_S secondes) ; pas de réponse : le vocal, comme avant.
# Vide (par défaut) : pas d'appel.
BILAN_SOIR_APPEL = _env("BILAN_SOIR_APPEL", "").strip()
BILAN_SOIR_SONNERIE_S = float(_env("BILAN_SOIR_SONNERIE_S", "45"))
APPEL_CABLE = _env("APPEL_CABLE", "BlackHole")
# Avant une réunion : BRIEFING_REUNION_MIN minutes avant un rendez-vous avec d'autres personnes, il te résume tes
# derniers mails avec elles, tes comptes rendus et ce qu'il sait d'elles (un agent en lecture seule). Il relève
# ton agenda toutes les RELEVE_AGENDA_MIN minutes, entre INITIATIVE_DEBUT et INITIATIVE_FIN, quand tu es devant
# le Mac. JERVIS_BRIEFING_REUNION=non pour le couper.
BRIEFING_REUNION = _env("BRIEFING_REUNION", "oui").lower() not in {"non", "off", "0"}
BRIEFING_REUNION_MIN = float(_env("BRIEFING_REUNION_MIN", "5"))
RELEVE_AGENDA_MIN = float(_env("RELEVE_AGENDA_MIN", "60"))
# Ses documents à toi (ton CV, tes papiers) : il en connaît la liste et les lit quand ça sert (une candidature,
# un formulaire). Ils restent sur le Mac ; seul le passage qu'il lit part chez Claude, comme un fichier ouvert.
DOCUMENTS = Path(_env("DOCUMENTS", str(DOSSIER / "documents"))).expanduser()
DOCUMENTS_VUS = int(_env("DOCUMENTS_VUS", "30"))   # combien il en liste dans ses consignes, les plus récents d'abord
# Créer des comptes à ta place (comptes.py) : tes infos sont dans documents/fiche.md ; chaque mot de passe est
# tiré au hasard, rangé dans ton Trousseau macOS et passe par le presse-papiers, jamais par Claude.
FICHE = DOCUMENTS / "fiche.md"
PRESSE_PAPIERS_S = int(_env("PRESSE_PAPIERS_S", "90"))   # après ce délai, le mot de passe quitte le presse-papiers
MOT_DE_PASSE_LONGUEUR = int(_env("MOT_DE_PASSE_LONGUEUR", "20"))
CODE_SMS_MINUTES = int(_env("CODE_SMS_MINUTES", "10"))   # jusqu'où il remonte pour trouver un code reçu par SMS
# Payer à ta place. Par défaut non : il s'arrête avant tout paiement (carte, PayPal, essai gratuit qui demande une
# carte) et te dit ce qu'il faudrait payer, et ses clics sur « Payer », « Acheter », « S'abonner »… sont refusés.
# JERVIS_PAIEMENTS=oui : il peut aller jusqu'au bouton qui paie, après ton accord (montant et marchand).
PAIEMENTS = _env("PAIEMENTS", "non").lower() in {"oui", "on", "1"}
# Son coffre (coffre.py) : ton IBAN, ton numéro de sécu, tes identifiants de l'État, tes mots de passe… rangés dans
# ton Trousseau macOS, et masqués partout (ce que tu dis, ce qu'il lit) avant de partir chez Claude, qui ne voit que
# « [🔒 iban] ». « non » : plus de masquage (à tes risques : ce qu'il lit part tel quel chez Claude).
COFFRE = _env("COFFRE", "oui").lower() not in {"non", "off", "0"}
# Pendant combien de secondes la dernière page vue (son navigateur) sert à vérifier où il colle un secret.
COFFRE_PAGE_S = int(_env("COFFRE_PAGE_S", "600"))
# Un IBAN ou un numéro de sécu qu'il lit (ta fiche de paie, un relevé, un mail) et qu'il ne connaît pas encore :
# rangé tout seul dans le coffre (« iban trouvé », d'où il vient), pour qu'il puisse s'en servir sans que tu le
# copies ; COFFRE_TROUVES_MAX au plus (au-delà : seulement masqué). « non » : seulement masqué, comme avant.
COFFRE_TROUVES = _env("COFFRE_TROUVES", "oui").lower() not in {"non", "off", "0"}
COFFRE_TROUVES_MAX = int(_env("COFFRE_TROUVES_MAX", "20"))
# Le temps qu'un script qui écrit tes secrets dans un document (remplir_document) a pour finir.
COFFRE_DOCUMENT_S = int(_env("COFFRE_DOCUMENT_S", "180"))
# Son propre code (proprio.py) : quand tu lui dis « t'as le droit », il le modifie lui-même, après que macOS t'a
# demandé le mot de passe de ta session (jamais vu ni par lui ni par Claude). L'accord reste ouvert tant de minutes.
# « non » : plus de mot de passe (comme avant : il modifie son code sans rien demander, sauf après une page lue).
CODE_MOT_DE_PASSE = _env("CODE_MOT_DE_PASSE", "oui").lower() not in {"non", "off", "0"}
CODE_ACCES_MIN = int(_env("CODE_ACCES_MIN", "30"))

FICHIER_BRIEFING = DOSSIER / "briefing.md"
FICHIER_BILAN_SOIR = DOSSIER / "bilan_soir.md"
PRESENT_S = float(_env("PRESENT_S", "120"))   # tu es « devant le Mac » si tu as touché clavier ou souris depuis ça

# --- Son initiative : une ronde discrète pendant la journée ------------------------
# Toutes les INITIATIVE_MIN minutes (entre INITIATIVE_DEBUT et INITIATIVE_FIN, et seulement si tu es devant le
# Mac), un agent de fond fait la ronde : mails importants pas lus, rendez-vous qui approchent, rappels dus.
# Il ne te dit rien s'il n'y a rien d'important, et jamais deux fois la même chose dans la journée. Il ne fait
# que regarder (jamais d'envoi ni d'effacement). Ce qu'il surveille est dans initiative.md (modifie-le).
# JERVIS_INITIATIVE=non pour la couper.
INITIATIVE = _env("INITIATIVE", "oui").lower() not in {"non", "off", "0"}
INITIATIVE_MIN = float(_env("INITIATIVE_MIN", "60"))
INITIATIVE_DEBUT = _env("INITIATIVE_DEBUT", "09:00")
INITIATIVE_FIN = _env("INITIATIVE_FIN", "20:00")
FICHIER_INITIATIVE = DOSSIER / "initiative.md"
MODELE_INITIATIVE = _env("MODELE_INITIATIVE", "sonnet")   # un modèle léger : la ronde ne fait que lire

# --- Ses processus de fond (« runners ») -------------------------------------------
# Un programme qu'il lance et qui tourne en continu, même quand il redémarre : un bot, un script qui surveille un
# dossier, un scraper, une file de travail. Sa sortie va dans un journal (espace/processus/). Avec « relancer », il
# le relance s'il s'arrête, au plus PROCESSUS_RELANCES_MAX fois par heure (au-delà, il te prévient). PROCESSUS_MAX
# en même temps au plus.
PROCESSUS_MAX = int(_env("PROCESSUS_MAX", "8"))
PROCESSUS_RELANCES_MAX = int(_env("PROCESSUS_RELANCES_MAX", "5"))

# --- Son carnet de leçons ----------------------------------------------------------
# Ce qu'il apprend en travaillant (une correction, une façon de faire qui a marché) va dans lecons.md, relu à
# chaque session par lui et ses agents. Au-delà de LECONS_MAX lignes, les plus anciennes s'en vont.
FICHIER_LECONS = DOSSIER / "lecons.md"
LECONS_MAX = int(_env("LECONS_MAX", "60"))

# --- Ce qu'il apprend de toi en plus du carnet ---------------------------------------
# Son lexique (lexique.md) : les mots que la reconnaissance vocale écorche chez toi (« au crow » → « Oqro »). Il les
# corrige avant de lire ta phrase. LEXIQUE=non pour ne plus corriger (le fichier reste).
LEXIQUE = _env("LEXIQUE", "oui").lower() not in {"non", "off", "0"}
FICHIER_LEXIQUE = DOSSIER / "lexique.md"
LEXIQUE_MAX = int(_env("LEXIQUE_MAX", "80"))
# Ses savoir-faire (espace/savoir-faire/) : la marche à suivre d'une tâche en plusieurs étapes qui a fini par marcher,
# relue la fois suivante au lieu de tout redécouvrir.
DOSSIER_SAVOIR_FAIRE = DOSSIER / "espace" / "savoir-faire"
SAVOIR_FAIRE_MAX = int(_env("SAVOIR_FAIRE_MAX", "40"))
# Une tâche de fond qui a demandé au moins RECETTE_ETAPES étapes : il en garde la méthode (une recette), pour que la
# fois suivante aille plus vite. (0 = il ne le propose plus de lui-même.)
RECETTE_ETAPES = int(_env("RECETTE_ETAPES", "12"))
# Ses chantiers en cours (objectifs.md) : ce que tu lui as confié pour plus tard (« il faut qu'on fasse connaître
# Oko »), où il en est et la prochaine étape. Relus à chaque session, par lui et ses agents ; au plus OBJECTIFS_MAX.
FICHIER_OBJECTIFS = DOSSIER / "objectifs.md"
OBJECTIFS_MAX = int(_env("OBJECTIFS_MAX", "12"))
# Son catalogue d'alternatives gratuites (gratuit.py) : ce qu'il sait déjà, plus ce qu'il a trouvé lui-même
# (alternatives.json), pour ne pas chercher à chaque fois.
FICHIER_ALTERNATIVES = DOSSIER / "alternatives.json"

# --- Le chat écrit (tableau de bord) -----------------------------------------------
# Tu peux lui écrire au lieu de parler : il répond par écrit, sans voix. Une question oui/non dans le chat
# (« je peux envoyer ? ») sans clic pendant QUESTION_ECRITE_S vaut non.
# Après ECRIT_RELECTURE_S secondes sans message tapé, il relit l'échange pour sa mémoire et son carnet.
QUESTION_ECRITE_S = float(_env("QUESTION_ECRITE_S", "300"))
ECRIT_RELECTURE_S = float(_env("ECRIT_RELECTURE_S", "120"))

# --- Steve partout : depuis ton iPhone, par iMessage (distance.py) ----------------------------------------
# Tu t'écris à toi-même dans Messages (ou « Dis Siri, Steve ») : il lit, fait, et te répond dans le même fil.
# auto = tes adresses sont trouvées dans la base de Messages ; non = coupé ; ou la liste : « +33612345678,moi@icloud.com ».
IMESSAGE = _env("IMESSAGE", "auto")
IMESSAGE_RELEVE_S = float(_env("IMESSAGE_RELEVE_S", "2"))       # il relit tes messages toutes les 2 s
IMESSAGE_PATIENCE_S = float(_env("IMESSAGE_PATIENCE_S", "20"))  # pas fini en 20 s : « Je m'en occupe… » (0 = jamais)
# Un iMessage arrivé pendant qu'il lit une page ou un mail, ou moins de 20 s après : il te demande si c'est bien toi.
IMESSAGE_MEFIANCE_S = float(_env("IMESSAGE_MEFIANCE_S", "20"))
QUESTION_IMESSAGE_S = float(_env("QUESTION_IMESSAGE_S", "600"))  # une question sans réponse en 10 min vaut non
IMESSAGE_VOCAL = _env("IMESSAGE_VOCAL", "oui")              # un vocal de toi : il te répond par un vocal (non = écrit)
IMESSAGE_VOCAUX_DOSSIER = _env("IMESSAGE_VOCAUX_DOSSIER", "")  # où ranger ses vocaux avant l'envoi (vide = auto)
# Par WhatsApp, dans ta discussion « Message à moi-même » (distance.BoiteWhatsApp) : une seule bulle par message.
# auto = ton numéro est lu dans WhatsApp ; non = coupé ; ou ton numéro : « +33612345678 ». IMESSAGE_RELEVE_S,
# IMESSAGE_PATIENCE_S, QUESTION_IMESSAGE_S et IMESSAGE_VOCAL valent aussi pour lui.
WHATSAPP_MOI = _env("WHATSAPP_MOI", "auto")
# Une question qui t'attend (un CAPTCHA, une double vérification, la question d'un agent de fond) et à laquelle tu
# ne réponds pas à voix haute, alors que tu n'as pas touché le Mac depuis PRESENT_S : elle part sur ton iPhone
# (WhatsApp ou iMessage, là où tu lui as écrit en dernier), et ton « ok » le fait continuer. non = coupé.
RELAIS_IPHONE = _env("RELAIS_IPHONE", "oui").lower() not in {"non", "off", "0"}

# --- Répondre à ta place (reponses.py) ------------------------------------------------------------------
# « Steve, réponds à tous mes messages » : iMessage et WhatsApp, dans ton style avec chaque personne.
REPONSES_HEURES = float(_env("REPONSES_HEURES", "72"))   # jusqu'où il remonte pour trouver ceux qui attendent
REPONSES_MAX = int(_env("REPONSES_MAX", "15"))           # au plus 15 conversations à la fois
MODELE_REPONSES = _env("MODELE_REPONSES", "sonnet")      # le modèle qui rédige les réponses du répondeur
# Le répondeur (« réponds à ma place pendant 2 h ») : il attend 90 s que tu répondes toi-même, puis répond.
REPONDEUR_DELAI_S = float(_env("REPONDEUR_DELAI_S", "90"))
REPONDEUR_DUREE_H = float(_env("REPONDEUR_DUREE_H", "3"))      # sans durée dite : 3 h (0 = jusqu'à ce que tu l'arrêtes)
REPONDEUR_MAX_PAR_H = int(_env("REPONDEUR_MAX_PAR_H", "4"))    # au plus 4 réponses par personne et par heure
REPONDEUR_RELEVE_S = float(_env("REPONDEUR_RELEVE_S", "10"))   # il regarde les nouveaux messages toutes les 10 s
REPONDEUR_IPHONE = _env("REPONDEUR_IPHONE", "oui").lower() not in {"non", "off", "0"}  # chaque réponse racontée sur l'iPhone

# --- Appels par l'iPhone (appels.py) ------------------------------------------------------------------------
# « Steve, appelle maman » : il compose sur le Mac, l'appel passe par ton iPhone, et toi tu parles.
# APPEL_AUTO=non : il ouvre la bulle « Appeler » et c'est toi qui cliques.
APPELS = _env("APPELS", "oui").lower() not in {"non", "off", "0"}
APPEL_AUTO = _env("APPEL_AUTO", "oui").lower() not in {"non", "off", "0"}

# --- Ses outils « infos » (infos.py) --------------------------------------------------
# Actualité, météo, taux de change, Wikipédia, jours fériés, cryptos : des services gratuits, sans clé.
# Un service qui ne répond pas dans ce délai : il le dit et passe à WebSearch.
INFOS_DELAI_S = float(_env("INFOS_DELAI_S", "8"))
# Toutes les API publiques (apis.py) : une même réponse resservie pendant ce temps (0 = jamais), et le catalogue
# retéléchargé en fond (dans espace/) quand il a plus de tant de jours (0 = jamais).
APIS_CACHE_S = float(_env("APIS_CACHE_S", "120"))
APIS_CATALOGUE_JOURS = int(_env("APIS_CATALOGUE_JOURS", "30"))

# --- Mémoire de l'écran (screenpipe, optionnel) -----------------------------------
# screenpipe enregistre en local ce qui passe sur ton écran (texte lu par OCR) et dans ton micro.
# auto = branché seulement s'il tourne (npx screenpipe record). oui / non pour forcer.
SCREENPIPE = _env("SCREENPIPE", "auto").lower()
SCREENPIPE_URL = _env("SCREENPIPE_URL", "http://localhost:3030")
SCREENPIPE_MCP = _env("SCREENPIPE_MCP", "screenpipe-mcp@0.18.14")

# --- Fluidité de la conversation -------------------------------------------------
# Pendant qu'il réfléchit, s'il n'a encore rien dit au bout de PATIENCE_S secondes, il lâche un petit
# « Voyons… » (préparé à l'avance : il part sans délai). JERVIS_PATIENCE=non pour le couper.
PATIENCE = _env("PATIENCE", "oui").lower() not in {"non", "off", "0"}
PATIENCE_S = float(_env("PATIENCE_S", "1.8"))
# Tu lui as coupé la parole (« stop », ou une autre question) : pendant REPRISE_MIN minutes, « continue »,
# « reprends », « tu disais ? » lui fait reprendre sa réponse à la phrase coupée, sans redire le début.
# JERVIS_REPRISE_MIN=0 pour le couper.
REPRISE_MIN = float(_env("REPRISE_MIN", "10"))
PHRASES_PATIENCE = [p.strip() for p in _env("PHRASES_PATIENCE", _langues.patiences(LANGUE)).split("|")
                    if p.strip()]
# Quand tu commences à lui parler pendant qu'il parle, il baisse la voix (à ce volume, de 0 à 1) le temps
# de comprendre si tu lui coupes la parole. 1 = ne baisse pas.
VOLUME_SI_TU_PARLES = float(_env("VOLUME_SI_TU_PARLES", "0.35"))
# Tu continues de parler par-dessus lui (COUPURE_S secondes de voix, pas un « hm » ni une toux) : sa voix
# s'arrête net, comme quelqu'un qui t'écoute. Ta phrase comprise, il se tait pour de bon et te répond, ou, si ce
# n'était pas pour lui (son écho, la télé), il reprend au mot où il s'était arrêté. 0 = il baisse seulement la voix.
COUPURE_S = float(_env("COUPURE_S", "0.5"))
# Chrono : après chaque réponse, une ligne grise dit combien de temps il a mis entre la fin de ta phrase et
# le premier son de sa voix, et où ce temps est passé. JERVIS_CHRONO=non pour la masquer.
CHRONO = _env("CHRONO", "oui").lower() not in {"non", "off", "0"}

# Tableau de bord : un écran holographique (son état, vos échanges, ses temps de réponse, ce qu'il coûte, le Mac).
# Menu « Tableau de bord », ou « Steve, affiche ton tableau de bord ». Il ne répond qu'à cette machine.
# JERVIS_TABLEAU=non pour ne pas le servir ; JERVIS_TABLEAU_AU_DEMARRAGE=oui pour l'ouvrir à chaque lancement.
TABLEAU = _env("TABLEAU", "oui").lower() not in {"non", "off", "0"}
TABLEAU_PORT = int(_env("TABLEAU_PORT", "8765"))
TABLEAU_AU_DEMARRAGE = _env("TABLEAU_AU_DEMARRAGE", "non").lower() in {"oui", "on", "1"}

# --- Voix -----------------------------------------------------------------
# Sa voix de tous les jours : Pocket TTS (Kyutai), un modèle qui tourne sur le Mac (jervis/voix_locale.py).
# Rien ne part sur Internet et une phrase sort en 0,1 à 0,5 s. Le nom est celui d'une des voix fournies :
# giovanni (homme), estelle (femme, la seule enregistrée par une Française), charles, rafael, javert…
# `outils/choisir_voix.command` te les fait écouter et note ton choix dans voix_choisie.txt.
# JERVIS_VOIX_LOCALE=non : il reprend la voix Microsoft ci-dessous.
def _voix_choisie() -> str:
    try:
        return (DOSSIER / "voix_choisie.txt").read_text(encoding="utf-8").strip() or "giovanni"
    except OSError:
        return "giovanni"


VOIX_LOCALE = _env("VOIX_LOCALE", _voix_choisie())
# Le modèle de cette langue (french, english, german, spanish, italian, portuguese, dutch). Une langue
# principale qu'il n'a pas (chinois, géorgien…) : pas de voix locale, il parle avec la voix Microsoft.
VOIX_LOCALE_LANGUE = _env("VOIX_LOCALE_LANGUE", _langues.modele_local(LANGUE))
VOIX_LOCALE_VOLUME = float(_env("VOIX_LOCALE_VOLUME", "1.0"))
# Voix neuronale Microsoft (gratuite, très naturelle, demande Internet).
# Autres : fr-FR-HenriNeural, fr-FR-VivienneMultilingualNeural (femme), fr-FR-DeniseNeural (femme).
# Mets JERVIS_VOIX_NEURONALE=non pour rester 100 % local avec la voix macOS.
# Pour tes autres langues, chacune a la sienne (voir langues.py) ; JERVIS_VOIX_ZH, JERVIS_VOIX_KA… pour en changer.
VOIX_NEURONALE = _env("VOIX_NEURONALE", _langues.LANGUES[LANGUE].neuronale if LANGUE in _langues.LANGUES else
                      "fr-FR-RemyMultilingualNeural")
VITESSE = _env("VITESSE", "+8%")
# La ligne directe (jervis/ligne.py) : la connexion au service de voix est ouverte d'avance et ressert d'une
# phrase à l'autre, et le début d'une réponse est demandé deux fois pour garder la demande la plus rapide.
# JERVIS_VOIX_DIRECTE=non pour revenir à une connexion neuve par phrase.
VOIX_DIRECTE = _env("VOIX_DIRECTE", "oui").lower() not in {"non", "off", "0"}
VOIX = _env("VOIX", (_langues.LANGUES[LANGUE].mac or ("Thomas",))[0] if LANGUE in _langues.LANGUES
           else "Thomas")   # voix macOS de secours (sans Internet)
DEBIT = _env("DEBIT", "195")    # mots par minute, pour la voix macOS

# Connecteurs claude.ai que Jervis garde (les autres sont coupés au démarrage pour aller plus vite).
# Chacun doit être connecté sur claude.ai → Paramètres → Connecteurs. Zapier ouvre 9 000 apps (Notion, Slack…).
_CONNECTEURS = "claude.ai Gmail,claude.ai Google Calendar,claude.ai Google Drive,claude.ai Zapier"
CONNECTEURS = [c.strip() for c in _env("CONNECTEURS", _CONNECTEURS).split(",") if c.strip()]
# Ceux qu'il garde s'ils sont branchés sur claude.ai, sans les attendre au démarrage : brancher Notion sur
# claude.ai → Paramètres → Connecteurs, relancer Steve, et c'est tout.
_FACULTATIFS = "claude.ai Notion"
CONNECTEURS_FACULTATIFS = [c.strip() for c in _env("CONNECTEURS_FACULTATIFS", _FACULTATIFS).split(",") if c.strip()]
# Dossier de travail de la session Claude (isolé, pour ne pas toucher aux réglages de tes autres projets).
ESPACE = DOSSIER / "espace"

# --- Navigateur (Chrome piloté par Jervis, via Playwright) ------------------
# Un Chrome à lui, avec son propre profil : tes comptes et ton Chrome perso restent à part.
# Tu te connectes une fois dans sa fenêtre aux sites que tu veux qu'il utilise, et il s'en souvient.
NAVIGATEUR = _env("NAVIGATEUR", "oui").lower() not in {"non", "off", "0"}
NAVIGATEUR_INVISIBLE = _env("NAVIGATEUR_INVISIBLE", "non").lower() in {"oui", "on", "1"}
PROFIL_NAVIGATEUR = DOSSIER / "navigateur"
PLAYWRIGHT_MCP = _env("PLAYWRIGHT_MCP", "@playwright/mcp@0.0.86")

# --- Serveurs MCP gratuits en plus (jervis/extras.py) ---------------------------------
# La doc de tes outils de dev (DeepWiki, Context7, Microsoft, AWS, Cloudflare, Hugging Face, Apple, Next.js), les
# sous-titres YouTube, Wikipédia et arXiv (ces deux-là avec uv : brew install uv). Sans compte ni clé.
# « tous », « aucun », ou une liste : « deepwiki,context7,wikipedia ».
MCP_GRATUITS = _env("MCP_GRATUITS", "tous")
# Tes serveurs à toi, au format de Claude Code ({"mcpServers": {...}}), écrits à la main.
MCP_PERSO = DOSSIER / "mcp.json"
# Quand il a beaucoup d'outils, Claude Code ne lui montre d'abord que leurs noms et va chercher le reste au besoin
# (ToolSearch) : ses outils à lui restent toujours chargés. Vide = le réglage de Claude Code ; sinon « true »,
# « false » ou « auto:10 » (le seuil, en % du contexte), passé tel quel à ENABLE_TOOL_SEARCH.
RECHERCHE_OUTILS = _env("RECHERCHE_OUTILS", "")

# --- Ton Chrome à toi (extension « Claude in Chrome ») ------------------------------
# Il pilote TON Chrome, avec tes comptes déjà connectés, comme Claude Code : ouvrir des onglets, lire,
# cliquer, remplir. Il faut l'extension Claude (claude.ai/chrome) installée et connectée au même compte.
# Son Chrome à lui (ci-dessus) reste là en secours si l'extension ne répond pas.
CHROME_PERSO = _env("CHROME_PERSO", "oui").lower() not in {"non", "off", "0"}

# --- Les apps du Mac (Peekaboo) ----------------------------------------------
# Il voit et pilote n'importe quelle app (Finder, Notes, Spotify, Réglages…) par l'accessibilité de macOS,
# en arrière-plan : il ne te vole ni la souris ni le clavier. Demande l'autorisation « Accessibilité ».
MAC = _env("MAC", "oui").lower() not in {"non", "off", "0"}
PEEKABOO_MCP = _env("PEEKABOO_MCP", "@steipete/peekaboo@4.9.8")

# --- Détection de la voix, version « Jarvis » (voir jervis/voix.py) --------------------------
# Mot-clé : un petit modèle openWakeWord qui reconnaît une formule précise, en continu.
# Pour « Hey Steve », entraîne le tien (outils/entrainer_hey_steve.command) : dès que modeles/voix/hey_steve.onnx
# existe, il est utilisé tout seul. Sans lui, il se fie à son nom dans la phrase transcrite.
# Valeurs : un modèle fourni (hey_jarvis, alexa, hey_mycroft…) ou le chemin d'un .onnx que tu as entraîné
# (openWakeWord ou livekit-wakeword, reconnu tout seul).
_JARVIS = PRENOM.casefold() in {"jervis", "jarvis"}
_PERSO = DOSSIER / "modeles" / "voix" / f"hey_{re.sub(r'\W+', '_', PRENOM.casefold())}.onnx"
MOTCLE_MODELE = _env("MOTCLE_MODELE", "hey_jarvis" if _JARVIS else (str(_PERSO) if _PERSO.exists() else ""))
PHRASE_MOTCLE = _env("PHRASE_MOTCLE", "Hey Jarvis" if MOTCLE_MODELE == "hey_jarvis" else f"Hey {PRENOM}")
# Comment il sait que tu lui parles :
#   hybride : le mot-clé détecté par le petit modèle, OU son nom dans la phrase transcrite (« Salut Steve »)
#   motcle  : seulement le mot-clé (rien n'est transcrit tant que tu ne l'as pas dit : moins de calcul)
#   whisper : seulement son nom dans la phrase transcrite (le choix par défaut quand il n'y a pas de mot-clé)
DETECTION = _env("DETECTION", "hybride" if MOTCLE_MODELE else "whisper").lower()
SEUIL_MOTCLE = float(_env("SEUIL_MOTCLE", "0.5"))       # baisse (0.3) s'il rate le mot-clé, monte (0.7) s'il se réveille seul
# La touche fn (🌐) le réveille sans dire son nom (touche.py) : tape-la puis parle, ou garde-la enfoncée pendant
# ta phrase. Une autre touche avec elle (fn+F1…) ne compte pas. JERVIS_TOUCHE_FN=non pour l'éteindre.
TOUCHE_FN = _env("TOUCHE_FN", "oui").lower() not in {"non", "off", "0"}
# Fin de phrase intelligente (smart-turn) : après une courte pause, il devine si tu as fini.
FIN_INTELLIGENTE = _env("FIN_INTELLIGENTE", "oui").lower() not in {"non", "off", "0"}
# Il se repose la question tout au long de ta pause, en étant de moins en moins exigeant : juste après un
# blanc il lui faut être sûr à SEUIL_FIN (« Quelle heure il est ? » part tout de suite), puis il se contente
# de moins en moins, jusqu'à SEUIL_FIN_TARD à la fin de SILENCE_MAX_S. Tu cherches tes mots ? Il attend.
SILENCE_COURT_S = float(_env("SILENCE_COURT_S", "0.35"))  # pause après laquelle il se pose la question
SILENCE_MAX_S = float(_env("SILENCE_MAX_S", "2.5"))       # pause après laquelle il coupe quoi qu'il arrive
SEUIL_FIN = float(_env("SEUIL_FIN", "0.8"))               # monte-le (0.9) s'il te coupe encore
SEUIL_FIN_TARD = float(_env("SEUIL_FIN_TARD", "0.3"))
# Pendant ta pause, il se repose la question toutes les ENTRE_QUESTIONS_S secondes.
ENTRE_QUESTIONS_S = float(_env("ENTRE_QUESTIONS_S", "0.25"))
# Phrase qui finit en l'air (« Steve, est-ce que tu peux ouvrir… ») : il t'écoute encore ce temps-là
# pour avoir la suite, en silence, au lieu de te répondre « ouvrir quoi ? » (moitié moins si la transcription
# a quand même mis un point ou un « ? » au bout).
SUITE_PHRASE_S = float(_env("SUITE_PHRASE_S", "5"))
# Phrase de trois mots ou plus sans point final (« No parce que Scott ») : la transcription ne met un point
# que quand ta voix retombe. Sans point, tu allais sûrement continuer : il attend encore ce temps-là.
SUITE_SANS_POINT_S = float(_env("SUITE_SANS_POINT_S", "1.5"))
# Tu réfléchis à voix haute (« Steve, mets-moi… euh… », « … et du coup ») : il attend la suite ce temps-là.
SUITE_HESITATION_S = float(_env("SUITE_HESITATION_S", "4"))
# Tu parlais encore quand il a repris ta phrase : il écoute au moins ce temps-là pour avoir la fin.
ENCORE_S = float(_env("ENCORE_S", "1.2"))
# Tu reprends la parole alors qu'il n'a encore rien répondu (il a cru ta phrase finie) : il lâche ce qu'il
# commençait et repart avec ta phrase entière. Valable pendant FUSION_S secondes après ta phrase, et au plus
# FUSIONS_MAX fois de suite (après, ce que tu ajoutes est traité juste après sa réponse).
FUSION_S = float(_env("FUSION_S", "8"))
FUSIONS_MAX = int(_env("FUSIONS_MAX", "3"))
# Tant que tu parles, il ne commence pas à parler : il se retient RETENUE_S après le début de ta voix (le temps
# d'un morceau d'écoute), et jamais plus de RETENUE_MAX_S en tout (une télé allumée ne doit pas le rendre muet).
RETENUE_S = float(_env("RETENUE_S", "3.2"))
RETENUE_MAX_S = float(_env("RETENUE_MAX_S", "6"))
# Tu parles par-dessus sa réponse (au moins ce nombre de mots, pas juste « hm ») : il se tait et te répond.
COUPER_SI_TU_PARLES = _env("COUPER_SI_TU_PARLES", "oui").lower() not in {"non", "off", "0"}
MOTS_POUR_COUPER = int(_env("MOTS_POUR_COUPER", "3"))
# Tu lui parles pendant qu'il travaille sans rien dire (mails, navigateur, entre deux phrases) : il t'écoute tout
# de suite. En plein travail, ta phrase est lue à la fin du geste en cours et il change de cap s'il faut ; sinon il
# s'arrête et te répond. (Avant, elle attendait la fin de tout son travail.) ECOUTE_PENDANT_TRAVAIL=non pour revenir
# à l'ancien comportement.
ECOUTE_PENDANT_TRAVAIL = _env("ECOUTE_PENDANT_TRAVAIL", "oui").lower() not in {"non", "off", "0"}
# … et quand sa voix s'est déjà arrêtée pour t'écouter (COUPURE_S), ta phrase entendue sans elle : moins de mots.
MOTS_POUR_COUPER_ARRETE = int(_env("MOTS_POUR_COUPER_ARRETE", "2"))
# Un « stop » entendu n'est pris pour l'écho de sa voix que s'il a dit ce mot dans les ECHO_MARGE_S secondes
# d'avant (pas trois phrases plus tôt). Un « stop » net passe même quand ta voix est mal reconnue
# (STOP_TOUTE_VOIX=non pour l'interdire : seule ta voix pourra alors le faire taire).
ECHO_MARGE_S = float(_env("ECHO_MARGE_S", "1.5"))
STOP_TOUTE_VOIX = _env("STOP_TOUTE_VOIX", "oui").lower() not in {"non", "off", "0"}
# Transcription : parakeet (NVIDIA Parakeet v3, très rapide) ou whisper.
STT = _env("STT", "parakeet").lower()
PARAKEET_MODEL = _env("PARAKEET_MODEL", "mlx-community/parakeet-tdt-0.6b-v3")
# Une phrase courte que Parakeet écrit en anglais (« Skip the mail. ») est réécoutée par Whisper, en français
# imposé (0,4 s de plus, et 2 à 3 s la toute première fois). JERVIS_DEUXIEME_AVIS=non pour t'en passer.
DEUXIEME_AVIS = _env("DEUXIEME_AVIS", "oui").lower() not in {"non", "off", "0"}
# Quand Parakeet ne trouve pas son nom du premier coup, il réécoute en le lui soufflant (indispensable pour
# « Jervis », qu'il ne connaît pas ; un simple filet de sécurité pour « Steve », qu'il connaît).
# Monte (6) s'il rate son nom, baisse (3) ou 0 s'il croit l'entendre dans des phrases qui ne le contiennent pas.
# Par défaut : 4 pour « Jervis », 0 (coupé) pour un prénom courant comme Steve, qui ferait entendre « steve » dans « steak ».
BOOST_NOM = float(_env("BOOST_NOM", "4" if _JARVIS else "0"))
# Annulation d'écho : il n'entend plus sa propre voix dans le haut-parleur.
ANTI_ECHO = _env("ANTI_ECHO", "oui").lower() not in {"non", "off", "0"}
# Le micro quand tu branches un casque ou tes AirPods. « auto » : celui que macOS choisit, sauf un micro loin de
# toi (AirPods, casque Bluetooth, iPhone) : alors celui du Mac, et tes AirPods gardent leur bon son (leur micro les
# ferait passer en qualité téléphone). « systeme » : toujours celui de macOS. Ou un bout de son nom (« Yeti »).
MICRO = _env("MICRO", "auto")
# Toutes les combien de secondes il vérifie qu'un casque n'a pas été branché (ou que le micro n'est pas tombé).
SURVEILLANCE_SON_S = float(_env("SURVEILLANCE_SON_S", "1.5"))
# Débruitage (DPDFNet) : « detection » = seulement pour savoir si tu parles, « tout » = aussi pour la transcription.
DEBRUITAGE = _env("DEBRUITAGE", "detection").lower()
# Ta voix : si voix.npy existe (python -m jervis --apprendre-ma-voix), il ignore les autres voix.
SEUIL_VOIX = float(_env("SEUIL_VOIX", "0.5"))   # baisse-le (0.4) s'il ignore ta propre voix
# Une phrase courte (moins de VOIX_LONGUE_S de parole) donne une empreinte moins nette : le seuil descend en douceur
# jusqu'à SEUIL_VOIX_COURTE pour les plus courtes (MIN_VERIF_VOIX_S). En dessous de MIN_VERIF_VOIX_S, trop court
# pour juger : la phrase passe (une confirmation sensible, elle, demande toujours une vraie phrase).
SEUIL_VOIX_COURTE = float(_env("SEUIL_VOIX_COURTE", "0.42"))
VOIX_LONGUE_S = float(_env("VOIX_LONGUE_S", "2"))
MIN_VERIF_VOIX_S = float(_env("MIN_VERIF_VOIX_S", "0.8"))
# Le mot-clé (« Hey Steve ») bien entendu par son modèle : la télé ne le dit pas. Le seuil descend alors jusque-là.
SEUIL_VOIX_REVEIL = float(_env("SEUIL_VOIX_REVEIL", "0.42"))
# Ta voix n'a pas une seule empreinte mais jusqu'à VOIX_CENTRES : voix.npy, plus d'autres dans voix_centres.npy
# (ta voix du matin, fatiguée, au micro du casque…). Une phrase est à toi si elle ressemble à l'une d'elles.
# Une nouvelle se crée quand tu dis son nom d'une voix qu'il reconnaissait mal. Garde-fous contre la dérive :
# chacune reste proche de ta voix de départ (ressemblance ≥ ECART_CENTRE avec voix_depart.npy), et une phrase
# reconnue seulement par ces empreintes-là doit aussi ressembler un minimum (VOIX_PLANCHER) à ta voix de départ.
# JERVIS_VOIX_CENTRES=1 : une seule empreinte, comme avant.
VOIX_CENTRES = int(_env("VOIX_CENTRES", "4"))
ECART_CENTRE = float(_env("ECART_CENTRE", "0.5"))
VOIX_PLANCHER = float(_env("VOIX_PLANCHER", "0.3"))
# JERVIS_DIAGNOSTIC_VOIX=oui : chaque phrase écrit son score de voix dans jervis.log (pour régler SEUIL_VOIX).
DIAGNOSTIC_VOIX = _env("DIAGNOSTIC_VOIX", "non").lower() in {"oui", "on", "1"}
# Un « oui » qui déclenche une action sensible (paiement, ordre venu d'une page…) doit être dit de ta voix :
# une télé, un autre ou une vidéo ne peuvent pas le dire à ta place. (Sans voix apprise, ça ne change rien.)
CONFIRMATION_VOIX = _env("CONFIRMATION_VOIX", "oui").lower() not in {"non", "off", "0"}
# Il apprend ta voix tout seul (les phrases où tu dis son nom), puis l'affine à chaque phrase de toi.
APPRENDRE_VOIX = _env("APPRENDRE_VOIX", "oui").lower() not in {"non", "off", "0"}
SEUIL_APPRENDRE = float(_env("SEUIL_APPRENDRE", "0.65"))  # il n'apprend que des phrases où il est sûr que c'est toi
# Tu chuchotes (des gens dorment) : il t'entend quand même, et te répond plus bas. Le son du micro est monté tout seul
# quand tu parles bas (jamais quand tu parles normalement, jamais pendant que lui parle : l'anti-écho reste intact),
# le détecteur de voix écoute aussi le son non débruité (le débruitage prend un chuchotement pour du souffle), et une
# phrase chuchotée n'est pas jugée sur ta voix comme une autre : l'empreinte d'un chuchotement ne ressemble plus à ta
# voix (un « oui » sensible, lui, se dit toujours à voix normale). JERVIS_CHUCHOTEMENT=non pour revenir à avant.
CHUCHOTEMENT = _env("CHUCHOTEMENT", "oui").lower() not in {"non", "off", "0"}
GAIN_MAX = float(_env("GAIN_MAX", "10"))           # le son est monté jusqu'à ×10 ; baisse (4) s'il réagit au moindre bruit
BRUIT_MAX_DB = float(_env("BRUIT_MAX_DB", "-45"))  # … sans que le bruit de fond dépasse ce niveau (dB, 0 = le maximum)
NIVEAU_VOIX_DB = float(_env("NIVEAU_VOIX_DB", "-26"))  # … ni qu'une voix soit poussée plus fort qu'une voix normale
SEUIL_VAD_BRUT = float(_env("SEUIL_VAD_BRUT", "0.6"))  # le détecteur sur le son non débruité (un peu plus exigeant)
# Une phrase est « chuchotée » quand ta voix y vibre moins que cette part du temps (parler : 0,7 à 0,9 ; chuchoter : ~0).
SEUIL_CHUCHOTE = float(_env("SEUIL_CHUCHOTE", "0.15"))
SEUIL_VOIX_CHUCHOTE = float(_env("SEUIL_VOIX_CHUCHOTE", "0.3"))  # ressemblance qu'il faut à une phrase chuchotée
VOLUME_CHUCHOTE = float(_env("VOLUME_CHUCHOTE", "0.35"))  # sa voix quand tu viens de chuchoter (1 = comme d'habitude)
# Taille max d'un message de Claude Code : une capture d'écran (outil « see ») dépasse le 1 Mo par défaut.
TAMPON_MAX = 64 * 1024 * 1024

# --- Réunions (reunion.py) ---------------------------------------------------
# Mode réunion : il voit que tu es en appel (Zoom, Meet, Teams, FaceTime, WhatsApp…) parce qu'une de ces apps
# écoute ton micro (macOS le dit, sans rien ouvrir). Pendant l'appel il ne parle plus du tout : ses annonces
# attendent la fin, et si tu l'appelles par son nom, il répond par écrit (notification + chat). JERVIS_MODE_REUNION=non
# pour le couper.
MODE_REUNION = _env("MODE_REUNION", "oui").lower() not in {"non", "off", "0"}
# Les apps d'appel (le début de leur identifiant macOS). Les navigateurs y sont : un onglet Meet qui prend le micro.
APPS_APPEL = [a.strip() for a in _env(
    "APPS_APPEL", "us.zoom.xos,com.microsoft.teams,com.apple.FaceTime,com.apple.avconferenced,com.google.Chrome,"
                  "com.apple.Safari,com.apple.WebKit,org.mozilla.firefox,company.thebrowser,com.brave.Browser,"
                  "com.microsoft.edgemac,net.whatsapp.WhatsApp,com.hnc.Discord,com.tinyspeck.slackmacgap,"
                  "com.skype,com.cisco.webex,ru.keepcoder.Telegram,org.whispersystems.signal-desktop").split(",")
              if a.strip()]
APPEL_SURVEILLANCE_S = float(_env("APPEL_SURVEILLANCE_S", "2"))  # il regarde toutes les 2 s
APPEL_FIN_S = float(_env("APPEL_FIN_S", "8"))  # le micro libéré depuis 8 s : l'appel est fini (pas un simple « muet »)
# Pendant un appel, quand tu dis son nom : "ecrit" (il répond par écrit), "rien" (il ne réagit qu'à « compte rendu »).
APPEL_REPONSE = _env("APPEL_REPONSE", "ecrit").lower()
# Ce qu'il entend pendant un appel peut être la voix d'un participant (les haut-parleurs). Pour agir (un message, un
# rappel, un fichier…) : "voix" (par défaut) = sans clic quand c'est ta voix (voix.npy), un clic dans le chat sinon ;
# "non" = toujours un clic ; "oui" = jamais de clic (tu es seul au casque). Lire reste libre dans tous les cas.
APPEL_CONFIANCE = _env("APPEL_CONFIANCE", "voix").lower()
# Compte rendu : il transcrit la réunion en local, morceau par morceau, en détectant la langue de chaque morceau
# (Whisper, 99 langues ; Parakeet s'il n'est pas là : 25 langues européennes).
DOSSIER_REUNIONS = Path(os.path.expanduser(_env("DOSSIER_REUNIONS", str(DOSSIER / "espace" / "reunions"))))
REUNION_STT = _env("REUNION_STT", "auto").lower()          # auto | whisper | parakeet
REUNION_LANGUE = _env("REUNION_LANGUE", "auto").lower()    # auto = chaque morceau dans sa langue ; sinon "fr", "en"…
LANGUE_COMPTE_RENDU = _env("LANGUE_COMPTE_RENDU", "français")  # la langue du résumé, quelle que soit celle de la réunion
REUNION_MORCEAU_S = float(_env("REUNION_MORCEAU_S", "30"))  # transcrit par morceaux de ~30 s (coupés dans un blanc)
REUNION_MAX_MIN = float(_env("REUNION_MAX_MIN", "180"))     # au-delà, il s'arrête seul
REUNION_GARDER_SON = _env("REUNION_GARDER_SON", "non").lower() in {"oui", "on", "1"}  # garder le .wav à côté
# Le son du Mac (ce que disent les autres) quand tu es au casque : le nom d'un appareil « boucle » (BlackHole,
# Loopback…) à enregistrer en plus du micro. Vide : seulement le micro (qui les entend sur les haut-parleurs).
REUNION_SON_DU_MAC = _env("REUNION_SON_DU_MAC", "")
REUNION_AUTO = _env("REUNION_AUTO", "non").lower() in {"oui", "on", "1"}  # compte rendu de chaque appel, sans le demander

# --- Le vocal WhatsApp (vocal.py) --------------------------------------------
# Le temps maximal d'un enregistrement : au-delà, tout est rétabli quoi qu'il arrive (écoute, barge-in, appels).
VOCAL_WHATSAPP_MAX_S = float(_env("VOCAL_WHATSAPP_MAX_S", "60"))
# Par où sort sa voix pendant le vocal : vide = les enceintes (le micro du Mac l'entend, comme toi). Le nom d'un
# appareil virtuel (« BlackHole 2ch ») = directement dans WhatsApp, sans bruit de la pièce ; il faut alors choisir ce
# même appareil comme micro dans WhatsApp (menu Appel → Micro).
VOCAL_WHATSAPP_SORTIE = _env("VOCAL_WHATSAPP_SORTIE", "")

# --- L'atelier de code (dev.py) ------------------------------------------------
# Où il cherche tes projets (les dossiers qui ont un .git), séparés par « : », et jusqu'à quelle profondeur.
PROJETS = [Path(p.strip()).expanduser() for p in _env("PROJETS", "~").split(":") if p.strip()]
PROJETS_PROFONDEUR = int(_env("PROJETS_PROFONDEUR", "4"))
EDITEUR = _env("EDITEUR", "Cursor")                     # « ouvre Jervis » : dans cette app
TESTS_MAX_MIN = float(_env("TESTS_MAX_MIN", "20"))      # des tests plus longs sont arrêtés
TESTS_ATTENTE_S = float(_env("TESTS_ATTENTE_S", "40"))  # au-delà, il te rend la main et te dit la fin plus tard
CI_MAX_MIN = float(_env("CI_MAX_MIN", "60"))            # il surveille une CI au plus une heure
DOSSIER_DEV = DOSSIER / "espace" / "dev"                # la sortie des tests et des serveurs de dev

# --- Le bureau (bureau.py) ---------------------------------------------------
FICHIER_MODES = DOSSIER / "modes.json"            # tes modes (« mode travail ») : apps, liens, volume, raccourci
FICHIER_RANGEMENT = DOSSIER / "rangement.json"    # tes règles de rangement (« les factures vont dans… »)
DOSSIER_ANNULER_RANGEMENT = DOSSIER / "espace" / "rangements"  # de quoi défaire le dernier rangement
# Santé du Mac : il te prévient de lui-même (une fois par jour et par souci) quand le disque, la batterie ou la
# mémoire coincent. JERVIS_SANTE_INTERVALLE_MIN=0 pour ne jamais vérifier seul.
SANTE_INTERVALLE_MIN = float(_env("SANTE_INTERVALLE_MIN", "30"))
SANTE_DISQUE_MIN_GO = float(_env("SANTE_DISQUE_MIN_GO", "15"))
SANTE_BATTERIE_MIN = int(_env("SANTE_BATTERIE_MIN", "10"))
SANTE_CPU_MAX = float(_env("SANTE_CPU_MAX", "90"))   # une app à plus de 90 % du processeur pendant 10 min

# --- Recherche dans tes fichiers, messages vocaux, règles -----------------------
RECHERCHE_MAX = int(_env("RECHERCHE_MAX", "15"))      # résultats de chercher_fichiers
VOCAL_MAX_S = float(_env("VOCAL_MAX_S", "300"))       # un message vocal iPhone plus long : coupé à 5 min
LECONS_PERMANENTES_MAX = int(_env("LECONS_PERMANENTES_MAX", "40"))  # tes règles « pour toujours » (📌)
