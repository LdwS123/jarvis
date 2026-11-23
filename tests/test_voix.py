"""Sa voix locale (jervis/voix_locale.py, voix_sures.py et leur branchement dans tts.py), sans le modèle :
il est remplacé par un faux. Ni Internet, ni carte graphique, ni son.

    cd ~/Jervis && PYTHONPATH=. .venv/bin/python tests/test_voix.py
"""

import asyncio
import os
import tempfile
import threading
from pathlib import Path

threading.Timer(40, lambda: (print("⏰ bloqué", flush=True), os._exit(2))).start()

import numpy as np  # noqa: E402

from jervis import config, ligne, oral, tts, voix_locale, voix_sures  # noqa: E402

echecs: list[str] = []
total = 0


def verifier(nom: str, obtenu, attendu) -> None:
    global total
    total += 1
    if obtenu != attendu:
        echecs.append(nom)
        print(f"❌ {nom}\n     obtenu  : {obtenu!r}\n     attendu : {attendu!r}")


class FauxSon:
    """Ce que rend le vrai modèle : un tenseur torch."""
    def __init__(self, valeurs): self.valeurs = valeurs
    def detach(self): return self
    def cpu(self): return self
    def numpy(self): return self.valeurs


class FauxModele:
    sample_rate = 24000

    def __init__(self):
        self.demandes: list[str] = []
        self.en_panne = False

    def generate_audio(self, etat, texte):
        self.demandes.append(texte)
        if self.en_panne:
            raise RuntimeError("plus de mémoire")
        t = np.arange(12000) / 24000      # une demi-seconde de la3, faible comme le vrai modèle
        return FauxSon((0.05 * np.sin(2 * np.pi * 440 * t)).astype(np.float32))


def ton(amplitude: float = 0.2, n: int = 4800) -> np.ndarray:
    return (amplitude * np.sin(2 * np.pi * 440 * np.arange(n) / 48000)).astype(np.float32)


modele = FauxModele()
config.VOIX_LOCALE, config.VOIX_LOCALE_LANGUE, config.VOIX_LOCALE_VOLUME = "essai", "french", 1.0
voix_locale._SURES = Path(tempfile.mkdtemp(prefix="jervis-test-voix-"))
voix_locale._modele, voix_locale._etat, voix_locale._echecs = modele, object(), 0

# --- 1. Le volume : toutes les phrases au même niveau ---------------------------------------------------------
def niveau(son: np.ndarray) -> float:
    parle = son[np.abs(son) > 0.1 * np.abs(son).max()]
    return round(float(np.sqrt(np.mean(parle ** 2))), 2)

verifier("une phrase chuchotée est remontée", niveau(voix_locale._niveler(ton(0.05))), 0.14)
verifier("mais pas plus de huit fois (on ne fabrique pas du souffle)",
         round(float(np.abs(voix_locale._niveler(ton(0.001))).max()), 3), 0.008)
verifier("une phrase trop forte est baissée", niveau(voix_locale._niveler(ton(0.9))), 0.14)
verifier("du silence reste du silence", float(np.abs(voix_locale._niveler(np.zeros(100, dtype=np.float32))).max()), 0.0)
clic = np.zeros(4800, dtype=np.float32)
clic[100] = 0.5                                        # un clic isolé : on ne monte pas au point de saturer
verifier("jamais au-dessus de la saturation", bool(np.abs(voix_locale._niveler(clic)).max() <= 0.951), True)

# --- 2. Les phrases vérifiées : rangées, relues, préférées ----------------------------------------------------
verifier("rien de rangé au départ", voix_locale.sure("Une seconde."), None)
voix_locale.garder("Une seconde.", ton())
relue = voix_locale.sure("Une seconde.")
verifier("rangée puis relue à l'identique", bool(relue is not None and np.allclose(relue, ton(), atol=1e-4)), True)
verifier("majuscules et espaces ne comptent pas", voix_locale.sure("une  SECONDE.") is not None, True)
verifier("une autre phrase n'est pas rangée", voix_locale.sure("Deux secondes."), None)
voix_locale.garder("Il est 14 h.", ton())
verifier("rangée sous sa forme en mots", voix_locale.sure("Il est quatorze heures.") is not None, True)

fabriquee = voix_locale.fabriquer("Je regarde.")
verifier("le modèle reçoit la phrase", modele.demandes[-1], "Je regarde.")
verifier("son rendu à 48 kHz (le modèle sort du 24 kHz)", len(fabriquee), 24000)
verifier("et au volume de référence", niveau(fabriquee), 0.14)
voix_locale.fabriquer("Il est 14 h 30.")
verifier("le modèle ne reçoit que des mots", modele.demandes[-1], "Il est quatorze heures trente.")
avant = len(modele.demandes)
verifier("« … » : rien à dire, le modèle n'est pas appelé",
         (len(voix_locale.fabriquer("…")), len(modele.demandes) - avant), (len(voix_locale._SILENCE), 0))

avant = len(modele.demandes)
dite = voix_locale.dire("Une seconde.")
verifier("dire reprend la version vérifiée, sans le modèle",
         (bool(np.allclose(dite, ton(), atol=1e-4)), len(modele.demandes) - avant), (True, 0))
voix_locale.dire("Je regarde tes mails.")
verifier("une phrase pas rangée passe par le modèle", len(modele.demandes) - avant, 1)
config.VOIX_LOCALE_VOLUME = 0.5
verifier("le réglage de volume s'applique aussi aux phrases rangées",
         bool(np.allclose(voix_locale.dire("Une seconde."), ton() * 0.5, atol=1e-4)), True)
config.VOIX_LOCALE_VOLUME = 1.0

abime = voix_locale._fichier_sur(oral.en_mots("Voyons…"))
abime.write_bytes(b"pas un fichier son")
verifier("fichier abîmé : ignoré", voix_locale.sure("Voyons…"), None)
avant = len(modele.demandes)
verifier("et la phrase est refabriquée", (voix_locale.dire("Voyons…") is not None, len(modele.demandes) - avant), (True, 1))

config.VOIX_LOCALE = "autre"
voix_locale._en_memoire.clear()
verifier("chaque voix a ses propres phrases rangées", voix_locale.sure("Une seconde."), None)
config.VOIX_LOCALE = "essai"

# --- 3. La réécoute (voix_sures.py) ---------------------------------------------------------------------------
RECONNUES = [
    ("Pas de souci.", "pas de soucis", True),            # le « s » muet : Whisper écrit l'un ou l'autre
    ("Désolé.", "Désolée.", True),                        # pareil à l'oreille (une voix de femme)
    ("Bonne journée.", "Bonnes journées", True),
    ("Une seconde.", "Une seconde !", True),
    ("Une seconde.", "Une se conde", False),
    ("Une seconde.", "Une seconde de plus", False),
    ("Oui ?", "", False),
    ("Oui ?", "...", False),
    ("Il est 14 h.", "il est quatorze heures", True),     # écrit en mots…
    ("Il est 14 h.", "Il est 14 h", True),                # … ou en chiffres
    ("Très bien.", "Tres bien", True),
    ("Voyons…", "Voyant", False),
]
for phrase, entendu, attendu in RECONNUES:
    verifier(f"reconnue : {phrase} / {entendu!r}", voix_sures.reconnue(phrase, entendu), attendu)

entendus = iter(["Charabia", "Alors on", "Alors."])
verifier("refabriquée jusqu'à être comprise (3e essai)", voix_sures.verifier("Alors…", ecouter=lambda son: next(entendus)), 3)
verifier("et rangée", voix_locale.sure("Alors…") is not None, True)
verifier("jamais comprise : 0", voix_sures.verifier("Oui, vas-y.", essais=4, ecouter=lambda son: "Oui, va chier"), 0)
verifier("et rien n'est rangé", voix_locale.sure("Oui, vas-y."), None)

liste = voix_sures.phrases()
verifier("pas de doublon dans les phrases à vérifier", len(liste), len(set(liste)))
verifier("son annonce et ses relances y sont",
         [p in liste for p in (config.ANNONCE, "Oui ?", "Une seconde.", "Je t'écoute.")], [True] * 4)

# --- 4. Le branchement dans tts.py : la voix locale d'abord, l'autre en secours ---------------------------------
demandes_microsoft: list[str] = []


async def faux_synthetiser(phrase, en_tete=False):
    demandes_microsoft.append(phrase)
    return None


tts._synthetiser = faux_synthetiser
tts._duplex = lambda: False
boucle = asyncio.new_event_loop()

fichier, son = boucle.run_until_complete(tts._preparer("Je regarde tes mails."))
verifier("la phrase sort de la voix locale", (fichier.suffix, fichier.exists(), son is not None, demandes_microsoft),
         (".wav", True, True, []))

vrai_dire = voix_locale.dire
voix_locale.dire = lambda phrase: None                     # elle rate cette phrase
verifier("ratée : la voix Microsoft prend le relais",
         (boucle.run_until_complete(tts._preparer("Bonjour.")), demandes_microsoft), ((None, None), ["Bonjour."]))


def qui_plante(phrase):
    raise RuntimeError("boum")


voix_locale.dire = qui_plante
verifier("plantée : pareil, sans erreur", boucle.run_until_complete(tts._preparer("Salut.")), (None, None))
voix_locale.dire = vrai_dire

voix_locale.demarrer = lambda: None
voix_locale.attendre = lambda delai: True
tts._pretes.clear()
boucle.run_until_complete(tts.precharger(["Voyons…", "Une seconde."]))
verifier("ses petites phrases sont prêtes d'avance, dans sa voix locale",
         sorted((p, f.suffix) for p, (f, _) in tts._pretes.items()), [("Une seconde.", ".wav"), ("Voyons…", ".wav")])

lignes_ouvertes: list[int] = []
ligne.prechauffer = lambda: lignes_ouvertes.append(1)
config.VOIX_NEURONALE = "fr-FR-RemyMultilingualNeural"
tts.prechauffer()
verifier("voix locale prête : aucune connexion ouverte d'avance", lignes_ouvertes, [])

modele.en_panne = True
verifier("trois phrases ratées de suite…", [voix_locale.fabriquer(f"Phrase {n}.") for n in "abc"], [None] * 3)
verifier("… elle se met de côté", (voix_locale.prete(), voix_locale.dire("Une seconde.")), (False, None))
tts.prechauffer()
verifier("et la ligne Microsoft est rouverte", lignes_ouvertes, [1])
verifier("tts ne lui demande plus rien", boucle.run_until_complete(tts._en_local("Bonjour.")), None)

config.VOIX_LOCALE = "non"
verifier("JERVIS_VOIX_LOCALE=non la coupe", (voix_locale.active(), voix_sures.construire()), (False, True))

# --- Il apprend ta voix tout seul, et un redémarrage ne remet pas le compteur à zéro --------------------------------
from jervis import voix as ecoute  # noqa: E402

dossier_voix = Path(tempfile.mkdtemp())
ecoute.Empreinte.FICHIER = dossier_voix / "voix.npy"
ecoute.Empreinte.DEPART = dossier_voix / "voix_depart.npy"
ecoute.Empreinte.DEBUT = dossier_voix / "voix_debut.npy"
hasard = np.random.default_rng(7)


def direction() -> np.ndarray:
    v = hasard.standard_normal(192)
    return v / np.linalg.norm(v)


def phrase_de(personne: np.ndarray) -> np.ndarray:
    """Une phrase de cette personne : sa voix, plus ce qui change d'une phrase à l'autre."""
    v = personne + 0.3 * direction()
    return (v / np.linalg.norm(v)).astype(np.float32)


def oreille_neuve():
    """Ce que fait Empreinte au démarrage, sans charger le modèle qui écoute."""
    e = ecoute.Empreinte.__new__(ecoute.Empreinte)
    e.reference = np.load(e.FICHIER) if e.FICHIER.exists() else None
    e.depart = np.load(e.DEPART) if e.DEPART.exists() else e.reference
    e._debut = e._relire_debut()
    e.affinages = 0
    return e


toi, tele = direction(), direction()
e = oreille_neuve()
verifier("ta voix : une phrase sans son nom ne compte pas", (e.apprendre(phrase_de(toi), sure=False), e.en_attente), (None, 0))
verifier("ta voix : première phrase avec son nom", e.apprendre(phrase_de(toi), sure=True), "1 phrase sur 4")
verifier("ta voix : deuxième", e.apprendre(phrase_de(toi), sure=True), "2 phrases sur 4")
e = oreille_neuve()   # Steve est relancé
verifier("ta voix : après un redémarrage, il a gardé ses deux phrases", (e.reference is None, e.en_attente), (True, 2))
verifier("ta voix : une autre voix qui dit son nom ne compte pas, et n'efface rien",
         e.apprendre(phrase_de(tele), sure=True), "2 phrases sur 4")
verifier("ta voix : troisième", e.apprendre(phrase_de(toi), sure=True), "3 phrases sur 4")
e = oreille_neuve()   # encore relancé
message = e.apprendre(phrase_de(toi), sure=True)
verifier("ta voix : à la quatrième, l'empreinte est créée", message.startswith("j'ai appris ta voix (4 phrases)"), True)
verifier("ta voix : c'est bien la tienne, pas celle de la télé",
         (float(e.reference @ toi) > 0.9, float(e.reference @ tele) < 0.5), (True, True))
verifier("ta voix : empreinte et point de départ écrits, brouillon effacé",
         (e.FICHIER.exists(), e.DEPART.exists(), e.DEBUT.exists()), (True, True, False))
e = oreille_neuve()
verifier("ta voix : au redémarrage suivant elle est là, plus rien en attente", (e.reference is not None, e.en_attente), (True, 0))
e.apprendre(phrase_de(toi), sure=False)
e.apprendre(phrase_de(tele), sure=True)
verifier("ta voix : elle s'affine sur tes phrases, jamais sur une autre voix", e.affinages, 1)
e.FICHIER.unlink()
e.DEPART.unlink()
for abime in (b"", b"pas un tableau", b"\x93NUMPY\x01\x00"):
    e.DEBUT.write_bytes(abime)
    verifier(f"ta voix : brouillon abîmé ({len(abime)} octets) → il repart de zéro sans planter", oreille_neuve().en_attente, 0)

print(f"\n{'✅' if not echecs else '❌'} voix : {total - len(echecs)}/{total} vérifications OK", flush=True)
os._exit(0 if not echecs else 1)
