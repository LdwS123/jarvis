"""Payer (coupé par défaut), les numéros de carte, les CAPTCHA, et ce qu'il publie pour faire connaître un projet.

    cd ~/Jervis && PYTHONPATH=. .venv/bin/python tests/test_paiements.py

Rien ne sort du Mac : la notification est remplacée par un faux, et ses publications vont dans un dossier temporaire.
"""

import asyncio
import json
import os
import tempfile
import threading
from pathlib import Path

threading.Timer(40, lambda: (print("⏰ bloqué", flush=True), os._exit(2))).start()

from jervis import comptes, config, outils  # noqa: E402
from jervis.permissions import (Decision, Gardien, est_lecture, lit_dehors, lit_le_presse_papiers, luhn,  # noqa: E402
                                motif_paiement, numero_de_carte, politique)

echecs: list[str] = []
total = 0


def verifier(nom: str, obtenu, attendu) -> None:
    global total
    total += 1
    if obtenu != attendu:
        echecs.append(nom)
        print(f"❌ {nom}\n     obtenu  : {obtenu!r}\n     attendu : {attendu!r}")


def texte(r: dict) -> str:
    return r["content"][0]["text"]


def carte(debut: str, longueur: int) -> str:
    """Une carte de test valide (le dernier chiffre est celui de contrôle)."""
    corps = debut + "0" * (longueur - len(debut) - 1)
    return next(corps + str(d) for d in range(10) if luhn(corps + str(d)))


TMP = Path(tempfile.mkdtemp())
comptes.PUBLICATIONS = TMP / "publications.jsonl"
lances: list[tuple] = []


async def faux_lancer(*cmd, entree=None, delai=15):
    lances.append(cmd)
    return 0, ""


comptes._lancer = faux_lancer

# --- Les numéros de carte --------------------------------------------------------------------------------------
visa, mc, amex = carte("497010", 16), carte("5132", 16), carte("37", 15)
verifier("carte : la carte de test classique", numero_de_carte("4111 1111 1111 1111"), True)
verifier("carte : Visa, Mastercard, Amex, collées", (numero_de_carte(visa), numero_de_carte(mc), numero_de_carte(amex)),
         (True, True, True))
verifier("carte : avec des tirets, dans un champ", numero_de_carte(str({"text": "-".join([visa[i:i + 4] for i in
                                                                                         range(0, 16, 4)])})), True)
verifier("carte : entourée d'autres nombres (« 12 … 12/28 »)", numero_de_carte(f"12 {visa} 12/28 123"), True)
verifier("carte : un chiffre de contrôle faux → pas une carte", numero_de_carte(visa[:-1] + str((int(visa[-1]) + 1) % 10)),
         False)
verifier("carte : une date en millisecondes, un SIRET, un téléphone → non",
         (numero_de_carte("1732464000000"), numero_de_carte("SIRET " + carte("4123", 14)),
          numero_de_carte("06 12 34 56 78")), (False, False, False))
verifier("carte : un numéro qui ne commence pas comme une carte → non", numero_de_carte(carte("9", 16)), False)
groupes = [visa[i:i + 4] for i in range(0, 16, 4)]
verifier("carte : espaces insécables, points, barres", [numero_de_carte(sep.join(groupes)) for sep in
                                                       ("\u00a0", "\u202f", ".", "/", " - ")], [True] * 5)

# --- Le Gardien ------------------------------------------------------------------------------------------------
class FauxContexte:
    pass


async def jamais(_question):
    raise AssertionError("il n'aurait pas dû demander")


async def passe(outil, entree):
    return type(await Gardien(jamais)(outil, entree, FauxContexte())).__name__


PW = "mcp__navigateur__"
config.PAIEMENTS = False
verifier("défaut : les paiements sont coupés", config.PAIEMENTS, False)
for nom, outil, entree, attendu in [
        ("taper sa carte dans la page", PW + "browser_type", {"ref": "e4", "text": visa}, "PermissionResultDeny"),
        ("l'écrire dans un fichier", "Write", {"file_path": "/tmp/x", "content": f"carte {amex}"}, "PermissionResultDeny"),
        ("une carte en espaces insécables, au fond d'un formulaire", PW + "browser_fill_form",
         {"fields": [{"name": "Nom", "value": "Inès"}, {"name": "x", "value": "\u00a0".join(groupes)}]},
         "PermissionResultDeny"),
        ("taper dans le champ « Numéro de carte », même 4 chiffres", PW + "browser_type",
         {"element": "Numéro de carte", "ref": "e7", "text": "4970"}, "PermissionResultDeny"),
        ("taper dans le champ CVC", "mcp__mac__type", {"query": "CVC", "text": "123"}, "PermissionResultDeny"),
        ("taper son nom dans le champ « Nom »", PW + "browser_type", {"element": "Nom sur la carte ?", "ref": "e8",
                                                                      "text": "Inès"}, "PermissionResultAllow"),
        ("cliquer « Payer 9,99 € »", PW + "browser_click", {"element": "Bouton Payer 9,99 €", "ref": "e9"},
         "PermissionResultDeny"),
        ("cliquer « Place order »", PW + "browser_click", {"element": "Place order button", "ref": "e2"},
         "PermissionResultDeny"),
        ("cliquer « S'abonner »", "mcp__mac__click", {"query": "S'abonner pour 5 €/mois"}, "PermissionResultDeny"),
        ("le bouton « Acheter » d'une boîte de dialogue", "mcp__mac__dialog", {"button": "Acheter"},
         "PermissionResultDeny"),
        ("un outil qui paie (checkout)", "mcp__producthunt__ads_prepare_checkout", {}, "PermissionResultDeny"),
        ("cliquer « Créer un compte »", PW + "browser_click", {"element": "Créer un compte", "ref": "e3"},
         "PermissionResultAllow"),
        ("cliquer « Se connecter »", PW + "browser_click", {"element": "Bouton Se connecter", "ref": "e5"},
         "PermissionResultAllow"),
        ("taper son adresse", PW + "browser_type", {"ref": "e1", "text": "ines@exemple.fr"}, "PermissionResultAllow"),
        ("se désabonner d'une liste (pas un paiement)", PW + "browser_navigate", {"url": "https://x.fr/unsubscribe"},
         "PermissionResultAllow")]:
    verifier(f"paiements coupés : {nom}", asyncio.run(passe(outil, entree)), attendu)
verifier("refus : il sait quoi dire", "dis-lui ce qu'il faudrait payer" in
         motif_paiement(PW + "browser_click", {"element": "Payer"}), True)
verifier("refus : la carte → le remplissage automatique", "remplissage automatique" in
         motif_paiement(PW + "browser_type", {"text": visa}), True)

config.PAIEMENTS = True
verifier("paiements permis : le bouton Payer n'est plus refusé d'office",
         motif_paiement(PW + "browser_click", {"element": "Bouton Payer 9,99 €"}), None)
verifier("paiements permis : la carte, toujours refusée", asyncio.run(passe(PW + "browser_type", {"text": visa})),
         "PermissionResultDeny")
config.PAIEMENTS = False

# --- Les CAPTCHA -----------------------------------------------------------------------------------------------
J = "mcp__jervis__"
r = asyncio.run(comptes.signaler_captcha.handler({"site": "https://www.Reddit.com/register"}))
verifier("captcha : il te prévient par une notification, avec le site", (lances[-1][0], "reddit.com" in lances[-1][-1]),
         ("osascript", True))
verifier("captcha : puis il s'arrête et attend ton « c'est bon »", ("Arrête-toi" in texte(r), "c'est bon" in texte(r)),
         (True, True))
verifier("captcha : seul, sans question", politique(J + "signaler_captcha", {"site": "reddit.com"}), Decision.OUI)
verifier("captcha : même pendant un collage de mot de passe", lit_le_presse_papiers(J + "signaler_captcha", {}), False)
verifier("captcha : il ne lui rend que son propre texte", lit_dehors(J + "signaler_captcha"), False)

# --- Faire connaître un projet ---------------------------------------------------------------------------------
verifier("publications : rien au départ", texte(asyncio.run(comptes.lister_publications.handler({}))),
         "Rien de publié pour l'instant.")
r = asyncio.run(comptes.noter_publication.handler({"projet": "Oko", "site": "https://www.ProductHunt.com/posts/oko",
                                                   "lien": "https://www.producthunt.com/posts/oko",
                                                   "resume": "Lancement d'Oko"}))
verifier("publications : notée avec le domaine exact", (texte(r), comptes.publications()[0]["site"]),
         ("Noté : Oko sur producthunt.com.", "producthunt.com"))
r = asyncio.run(comptes.noter_publication.handler({"projet": "oko", "site": "producthunt.com"}))
verifier("publications : jamais deux fois au même site (même en minuscules)",
         ("Déjà noté" in texte(r), len(comptes.publications())), (True, 1))
asyncio.run(comptes.noter_publication.handler({"projet": "Oko", "site": "betalist.com"}))
asyncio.run(comptes.noter_publication.handler({"projet": "Steve", "site": "producthunt.com"}))
verifier("publications : un autre projet peut aller sur le même site", len(comptes.publications()), 3)
verifier("publications : par projet", sorted(p["site"] for p in comptes.publications("oko")),
         ["betalist.com", "producthunt.com"])
liste = texte(asyncio.run(comptes.lister_publications.handler({"projet": "Oko"})))
verifier("publications : la liste montre le site et le lien",
         ("producthunt.com" in liste, "posts/oko" in liste, "Steve" in liste), (True, True, False))
verifier("publications : sans site ni projet → erreur",
         asyncio.run(comptes.noter_publication.handler({"projet": "Oko", "site": "pas un site"}))["is_error"], True)
comptes.PUBLICATIONS.write_text(comptes.PUBLICATIONS.read_text() + "pas du json\n" + json.dumps([1]) + "\n")
verifier("publications : une ligne abîmée est ignorée", len(comptes.publications()), 3)
verifier("publications : lister est une lecture ; noter, seul et interne",
         (est_lecture(J + "lister_publications"), politique(J + "noter_publication", {}), lit_dehors(J + "noter_publication"),
          lit_dehors(J + "lister_publications")), (True, Decision.OUI, False, True))

# --- Il sait ce qui est déjà fait, sans chercher -------------------------------------------------------------
from jervis import brain  # noqa: E402
bloc = comptes.publie()
verifier("déjà fait : la liste est dans ses consignes (publié ou bloqué)",
         ("producthunt.com" in bloc, "betalist.com" in bloc, "n'y retournes pas" in bloc, "ni sur le web" in bloc),
         (True, True, True, True))
verifier("déjà fait : elle arrive aussi par son carnet (conversation et agents de fond)", "producthunt.com" in brain.carnet(), True)
saved = comptes.PUBLICATIONS
comptes.PUBLICATIONS = TMP / "vide.jsonl"
verifier("déjà fait : rien à dire si rien n'est publié", comptes.publie(), "")
comptes.PUBLICATIONS = saved

# --- Ses consignes ---------------------------------------------------------------------------------------------
c = comptes.consigne()
verifier("consigne : un seul compte par site, à son nom", ("Un seul compte par site" in c, "faux profil" in c),
         (True, True))
verifier("consigne : faire connaître → règles du site, il dit qu'il est le créateur, accord avant de publier",
         ("lister_publications" in c, "règles" in c, "créateur" in c, "Publie sans demander" in c),
         (True, True, True, True))
verifier("consigne : pas de liens semés pour le référencement", "liens semés" in c, True)
verifier("consigne : aucun repère oublié", ("{payer}" in c, "{fiche}" in c), (False, False))
verifier("outils : les trois nouveaux sont à lui", {"signaler_captcha", "noter_publication", "lister_publications"}
         <= {t.name for t in outils.OUTILS}, True)

print(f"\n{'✅' if not echecs else '❌'} paiements, CAPTCHA et publications : {total - len(echecs)}/{total} vérifications OK",
      flush=True)
os._exit(0 if not echecs else 1)
