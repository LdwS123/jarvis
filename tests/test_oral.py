"""Ce que sa voix locale lit vraiment (jervis/oral.py) : les chiffres, heures, prix, dates… écrits en mots.

    cd ~/Jervis && PYTHONPATH=. .venv/bin/python tests/test_oral.py
"""

import os
import threading

threading.Timer(40, lambda: (print("⏰ bloqué", flush=True), os._exit(2))).start()

from jervis import oral  # noqa: E402

echecs: list[str] = []
total = 0


def verifier(nom: str, obtenu, attendu) -> None:
    global total
    total += 1
    if obtenu != attendu:
        echecs.append(nom)
        print(f"❌ {nom}\n     obtenu  : {obtenu!r}\n     attendu : {attendu!r}")


NOMBRES = [(0, "zéro"), (1, "un"), (17, "dix-sept"), (21, "vingt et un"), (70, "soixante-dix"), (71, "soixante et onze"),
           (80, "quatre-vingts"), (81, "quatre-vingt-un"), (91, "quatre-vingt-onze"), (99, "quatre-vingt-dix-neuf"),
           (100, "cent"), (101, "cent un"), (200, "deux cents"), (1000, "mille"),
           (1999, "mille neuf cent quatre-vingt-dix-neuf"), (2026, "deux mille vingt-six"),
           (1_000_000, "un million"), (2_000_000, "deux millions")]
for n, attendu in NOMBRES:
    verifier(f"nombre {n}", oral.nombre(n), attendu)
verifier("une", oral.nombre(1, feminin=True), "une")
verifier("vingt et une", oral.nombre(21, feminin=True), "vingt et une")
for n, feminin, attendu in [(1, False, "premier"), (1, True, "première"), (2, False, "deuxième"), (9, False, "neuvième"),
                            (21, False, "vingt et unième")]:
    verifier(f"ordinal {n}", oral.ordinal(n, feminin), attendu)

# (ce que Claude écrit, ce que la voix reçoit)
PHRASES = [
    ("Il est 14 h 30.", "Il est quatorze heures trente."),
    ("Il est 9h.", "Il est neuf heures."),
    ("Ça coûte 12,50 €.", "Ça coûte douze euros cinquante."),
    ("1 € seulement.", "un euro seulement."),
    ("$5", "cinq dollars"),
    ("100 000 €", "cent mille euros"),
    ("Tu as 3 nouveaux mails.", "Tu as trois nouveaux mails."),
    ("Batterie à 90 %.", "Batterie à quatre-vingt-dix pour cent."),
    ("Le rendez-vous est le 25/11/2026 à 9 h.",
     "Le rendez-vous est le vingt-cinq novembre deux mille vingt-six à neuf heures."),
    ("C'est le 1er mai.", "C'est le premier mai."),
    ("Le 2e étage.", "Le deuxième étage."),
    ("Appelle le 06 12 34 56 78.", "Appelle le zéro six, douze, trente-quatre, cinquante-six, soixante-dix-huit."),
    ("Il fait 21 °C.", "Il fait vingt et un degrés."),
    ("Il y a 1 200 personnes.", "Il y a mille deux cents personnes."),
    ("2 km à pied.", "deux kilomètres à pied."),
    ("1,5 kg", "un virgule cinq kilo"),
    ("Version 3.5 du modèle.", "Version trois point cinq du modèle."),
    ("81 fois", "quatre-vingt-une fois"),          # l'accord au féminin
    ("1 heure", "une heure"),
    ("21 pages", "vingt et une pages"),
    ("Rien à changer ici.", "Rien à changer ici."),  # pas de chiffre : elle ne touche à rien
    ("…", "…"),
]
for ecrit, attendu in PHRASES:
    verifier(f"en mots : {ecrit}", oral.en_mots(ecrit), attendu)

verifier("plus aucun chiffre après le passage", [p for p, _ in PHRASES if any(c.isdigit() for c in oral.en_mots(p))], [])
verifier("abréviations", oral.abreviations("M. Dupont et Mme Martin, etc. Cf. le mail.").casefold(),
         "monsieur dupont et madame martin, et cetera. voir le mail.")

print(f"\n{'✅' if not echecs else '❌'} oral : {total - len(echecs)}/{total} vérifications OK", flush=True)
os._exit(0 if not echecs else 1)
