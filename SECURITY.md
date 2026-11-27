# Sécurité · Security

**Français** · [English](#english)

Jarvis agit sur ton Mac à ta place : il lit tes mails, tes messages, ton écran, lance des commandes et clique dans
tes apps. La sécurité fait donc partie du produit, pas d'une annexe.

## Signaler une faille

**N'ouvre pas d'issue publique.** Passe par
[« Report a vulnerability »](https://github.com/LdwS123/jarvis/security/advisories/new) (onglet Security du dépôt) :
le message reste privé. Décris l'attaque (par exemple : un mail piégé qui lui fait envoyer un fichier), ce qu'il
faut pour la reproduire, et son effet. Une réponse vient sous quelques jours.

## Comment il se protège

- **Ta voix reste sur le Mac** : mot d'éveil, transcription, reconnaissance de la langue et empreinte vocale sont
  calculés en local. Seul le texte de ta demande part vers Claude.
- **Un texte venu d'ailleurs est une donnée, jamais un ordre** : une page web, un mail, la sortie d'une commande,
  le message de quelqu'un. Juste après en avoir lu un, ce qui est risqué (un script qui pilote une app, un fichier
  qui se lance seul, un message avec un lien…) t'est demandé.
- **L'argent** : coupé par défaut (`JERVIS_PAIEMENTS`), et toujours demandé avec le montant quand tu l'allumes.
- **Interdits d'office**, même si tu dis oui : `sudo`, `rm` (il passe par la corbeille), formater un disque,
  éteindre le Mac, toucher à tes clés SSH ou à ses propres réglages de sécurité.
- **Tes mots de passe ne passent jamais par Claude** : tirés et rangés dans le Trousseau du Mac.
- **Tes règles 📌** (« autorise ça désormais ») sont écrites par le code, jamais par le modèle, et ne valent que
  dans une conversation où rien d'ailleurs n'a été lu.
- Tout se règle dans [`jervis/permissions.py`](jervis/permissions.py) ; le détail est dans le
  [README](README.md#ce-quil-te-demande-encore).

---

## English

Jarvis acts on your Mac on your behalf: it reads your mail and messages, sees your screen, runs commands and clicks
in your apps. Security is part of the product.

**Reporting a vulnerability:** please **don't open a public issue**. Use
[Report a vulnerability](https://github.com/LdwS123/jarvis/security/advisories/new) (Security tab), which stays
private. Describe the attack (e.g. a crafted email that makes it send a file), how to reproduce it, and its impact.

**How it protects you:**

- Your **voice never leaves the Mac** (wake word, transcription, language detection, voice print are local). Only
  the text of your request goes to Claude.
- **Text from elsewhere is data, never an instruction** (web pages, emails, command output, other people's
  messages). Right after reading such text, risky actions require your confirmation.
- **Money** is off by default and always confirmed with the amount when enabled.
- **Always refused**, even if you say yes: `sudo`, `rm` (it uses the Trash), formatting disks, shutting down,
  touching SSH keys or its own security settings.
- **Passwords never go through Claude**: generated and stored in the macOS Keychain.
- Rules you grant ("allow this from now on") are written by code, never by the model, and only apply in
  conversations where nothing from outside has been read.
