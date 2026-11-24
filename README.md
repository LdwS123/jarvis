<p align="center"><img src="assets/banniere.png" alt="Jarvis, l'assistant vocal du Mac en français" width="100%"></p>

# Jarvis

<p align="center">
  <img alt="macOS Apple Silicon" src="https://img.shields.io/badge/macOS-Apple%20Silicon-000?logo=apple&logoColor=white">
  <img alt="Python 3.12" src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white">
  <img alt="Propulsé par Claude" src="https://img.shields.io/badge/propuls%C3%A9%20par-Claude-D97757">
  <img alt="Langue : français" src="https://img.shields.io/badge/langue-fran%C3%A7ais-2C7FC9">
  <img alt="Voix locale" src="https://img.shields.io/badge/voix-locale%20%C2%B7%20sans%20cloud-6FE0FF">
</p>

Le projet s'appelle Jarvis ; l'assistant, lui, s'appelle **Steve** (c'est le mot qui le réveille).
Le code vit dans le paquet Python `jervis`.

**En un coup d'œil :** assistant vocal français toujours à l'écoute (mot d'éveil, transcription et voix locales sur puce Apple), qui agit sur le Mac, le navigateur, le mail et l'agenda, lance plusieurs agents de fond en parallèle et les montre sur un tableau de bord. Mots-clés : assistant vocal, voice assistant, wake word, agent IA, multi-agents, Claude Agent SDK, MLX, macOS, français.

## Installer depuis GitHub

Il faut un Mac à puce Apple, Python 3.12 et un abonnement Claude (Claude Code connecté).

    git clone https://github.com/LdwS123/jarvis.git ~/Jervis && cd ~/Jervis
    python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
    ./installer.command

Ses modèles (voix, oreilles) se téléchargent tout seuls au premier lancement, dans `modeles/`.
Ta mémoire, vos conversations, son journal et ton empreinte vocale restent sur ton Mac : ils ne sont jamais dans le dépôt.

## Ce qu'il fait

Ton assistant vocal sur le Mac. Il réagit à son nom, « Steve », comprend le français et te répond à voix haute.
Il lit ton agenda Google et tes mails Gmail, ouvre tes apps, se souvient de toi et voit ton écran.
Il envoie des iMessages, crée des rappels et des notes, et pilote la maison via Raccourcis.
Il lit ton Google Drive, agit dans des milliers d'apps via Zapier, et clique ou tape dans n'importe quelle app du Mac.
Il fait les grosses tâches en arrière-plan pendant que tu continues à lui parler, te fait un point le matin,
te prévient à l'heure dite, et se souvient de vos conversations (et, avec screenpipe, de ce qui est passé sur ton écran).
Il travaille comme un bras droit : il fait sa ronde de lui-même (mails, agenda, rappels) et ne te dérange que si
ça vaut le coup, il tient un carnet de ce qu'il apprend en travaillant pour toi, relie ce qu'il sait de toi dans un graphe daté, et tu peux aussi lui écrire au lieu
de lui parler, depuis son tableau de bord.
Il tourne sur ton abonnement Claude, sans clé API.

## Installer dans la barre des menus (recommandé)

Double-clique `installer.command` (ou lance `./installer.command` dans le Terminal).

- Une icône apparaît en haut à droite : ⏳ démarrage, 🎙 veille, 👂 il t'écoute, 💭 il réfléchit, 🗣 il parle, ⏸ micro en pause, ⚠️ problème (voir le journal).
- Il redémarre tout seul à chaque ouverture de session.
- Le menu de l'icône permet de mettre le micro en pause (il ne te dérange plus : ses annonces attendent),
  d'ouvrir sa mémoire, vos conversations, ton briefing du matin, le journal technique (`jervis.log`), et de quitter.
- Après une mise à jour du code, relance `installer.command`.
- Pour qu'il ne démarre plus tout seul : `./installer.command --retirer`.

## Ou le lancer dans le Terminal

    cd ~/Jervis && .venv/bin/python -m jervis          # mode vocal
    cd ~/Jervis && .venv/bin/python -m jervis --texte  # au clavier, pour tester

Tu peux aussi double-cliquer `lancer.command`. Ne lance pas les deux modes en même temps : ils se disputeraient le micro.

## Les autorisations macOS

macOS les demande la première fois qu'il en a besoin. Clique « Autoriser » ou « OK ».
Elles se gèrent ensuite dans Réglages Système → Confidentialité et sécurité :

| Autorisation | Pour quoi |
|---|---|
| Micro | l'entendre |
| Accès complet au disque → Jervis | « efface ci, range ça » partout sur le Mac : Bureau, Documents, Téléchargements, Mail, Messages… Sans elle, macOS répond « Operation not permitted » |
| Accessibilité → Jervis | cliquer et taper dans tes apps (Peekaboo), appuyer sur Entrée dans Cursor |
| Enregistrement de l'écran → Jervis | « regarde mon écran » (sans elle, il ne voit que le fond d'écran) |
| Automatisation → Jervis → Finder, Messages, Contacts, Notes, Rappels… | piloter ces apps (vider la corbeille, iMessage, notes, rappels) |
| Contacts | retrouver « Léa » quand tu dis « envoie un message à Léa » |

L'accès complet au disque et l'Accessibilité, macOS ne les propose jamais tout seul : c'est toi qui allumes
l'interrupteur « Jervis ». Le plus simple, pour tout donner d'un coup :

    zsh ~/Jervis/outils/autorisations.command

(ou menu Steve → « Autorisations… »). Il ouvre chaque volet des Réglages l'un après l'autre, tu allumes « Jervis »,
tu appuies sur Entrée pour le suivant, et il propose de relancer Steve à la fin (macOS ne tient compte d'une
nouvelle autorisation qu'au lancement suivant). Si « Jervis » n'est pas dans une liste : bouton « + », puis choisis
`~/Jervis/Jervis.app` (le script te la montre dans le Finder : tu peux aussi la glisser dans la liste).

Si macOS refuse une app (erreur -1743 : « Contacts », Messages, Notes ou Rappels n'ont pas l'autorisation
Automatisation), Steve ne répond pas « erreur » : il te dit laquelle, ouvre le bon volet des Réglages et te
guide (« allume Jervis dans Automatisation, puis redis-moi la même chose »).

Au démarrage, une ligne `🔐 Autorisations de macOS à me donner : …` dans le journal dit ce qui manque encore. Tu peux
aussi lui demander « Steve, vérifie tes autorisations » : il te dit lesquelles manquent et ouvre le bon volet. Il ne
peut pas les allumer à ta place, et c'est voulu.

## Parler à Steve

Son nom se change avec `JERVIS_PRENOM` (voir les réglages) ; `JERVIS_PRENOM=Jarvis` remet l'ancien, avec « Hey Jarvis ».

- Dis son nom dans ta phrase, d'une traite : « Steve, ouvre Cursor », « Salut Steve, j'ai rendez-vous avec qui demain ? ».
  Le nom peut être au début ou à la fin.
- Tu peux aussi dire juste « Salut Steve » ou « Hey Steve », attendre le bip, puis parler.
- Il comprend qu'on parle *d'un autre* Steve quand un nom de famille suit (« Steve Jobs a fondé Apple ») et ne réagit pas.
  Mais « Steve » reste un prénom courant : si un ami s'appelle Steve, il risque de répondre à sa place.
- **Il parle pendant qu'il réfléchit** : il dit sa première phrase dès qu'elle est écrite, sans attendre la fin de
  sa réponse (souvent moins d'une seconde). S'il réfléchit longtemps, il lâche un petit « Voyons… » pour que tu
  saches qu'il t'a entendu.
- **Son nom, une seule fois par conversation.** Après sa réponse, il y a un bip : tu as 30 secondes pour
  commencer ta phrase suivante sans redire son nom (45 s'il t'a posé une question), et le compteur repart à
  chaque échange. Une toux, un bruit ou un mot incompris ne coupent rien : il continue d'écouter.
  Quand tu ne dis plus rien, un petit « pop » doux : il est reparti en veille, il faudra redire « Steve ».
- **« Steve, on discute »** (ou « mode discussion », « reste à l'écoute ») : il reste avec toi même si tu fais de
  longues pauses (jusqu'à 10 minutes de silence), le temps de vraiment papoter. « Merci » ou « c'est bon » pour finir.
- « Merci », « merci Steve », « c'est bon », « ça ira » ou « rien d'autre » le remet en veille.
- **Il parle comme quelqu'un, pas comme un service client** : réponses courtes en français parlé, pas de
  « Bien sûr ! » ni de « N'hésite pas si… », une commande simple est confirmée en trois mots (« C'est lancé. »).
  Pour bavarder, il réagit, donne son avis et te relance, sans aller chercher d'outil. Ses consignes le lui
  demandent, et un filet (`jervis/naturel.py`) enlève ces formules s'il les dit quand même.
- **Tant que tu parles, il t'écoute** : il ne répond pas par-dessus toi, et ne te coupe pas en plein milieu.
  - Phrase qui finit en l'air (« Steve, est-ce que tu peux… » puis tu réfléchis) : il attend la suite en silence
    (5 secondes), sans dire « Oui ? », et répond à la phrase entière.
  - Phrase sans point final (ta voix n'est pas retombée) : il attend encore 1,5 seconde avant de la croire finie.
  - Tu réfléchis à voix haute (« Steve, envoie un mail à Paul, euh… », « … et du coup ») : il attend la suite
    4 secondes. Une toux ou un « euh » tout seul pendant ce temps ne compte pas comme la suite.
  - **Les morceaux sont recollés proprement** : la transcription réentend souvent la fin d'un morceau au début
    du suivant (« ouvre le fichier » + « fichier budget » → « ouvre le fichier budget »), et quand tu te reprends
    (« envoie un mail à Paul, euh » + « envoie un mail à Pierre pour… »), c'est ta correction qui reste. Dans le
    doute, il garde les deux morceaux : rien de ce que tu as dit n'est perdu.
  - Tu reprends la parole alors qu'il commençait à réfléchir : il lâche ce qu'il préparait et repart avec ta phrase
    complète, bout à bout (jusqu'à 3 fois de suite, dans les 8 secondes).
  - Sa réponse est prête mais tu parles encore : il la retient jusqu'à ce que tu te taises (6 secondes au plus,
    pour qu'une télé allumée ne le rende pas muet).
  - Ce qui aide le plus : qu'il connaisse ta voix (voir « Apprendre ta voix » plus bas). Sans ça, la télé ou
    quelqu'un d'autre peut finir ta phrase à ta place.
- **Lui couper la parole** : pendant qu'il parle, dis « Steve », « stop », « tais-toi » ou « attends ».
  Si tu enchaînes avec une demande (« Steve, plutôt la météo »), il y répond tout de suite.
  Tu peux aussi simplement parler par-dessus lui (« en fait mets plutôt du jazz ») : à partir de 3 mots, il se tait
  et répond à ta nouvelle phrase, sans s'excuser. Un « ouais » ou « hmm » ne le coupe pas.
  Dès que tu commences à parler, il baisse sa voix (comme quelqu'un qui t'entend) ; si ce n'était pas pour lui,
  il reprend normalement. Il retire sa propre voix du micro (anti-écho), mais avec un casque ou des AirPods ça
  reste encore plus fiable.
- Il fait presque tout sans demander : ouvrir, créer, modifier, ranger, cliquer, coder. Il ne pose la question
  que pour ce qui ne se rattrape pas (voir « Ce qu'il te demande encore » plus bas). Réponds « oui » ou « non ».
- **Il travaille en fond pendant que vous parlez** : jusqu'à 5 agents à la fois (des tâches indépendantes
  tournent en parallèle). Tu continues de lui parler normalement pendant ce temps, et une nouvelle demande est
  traitée tout de suite, sans attendre qu'une tâche finisse.
- **Ce qu'il a à te dire de lui-même** (tâche finie, annonce programmée, question d'un agent) attend un blanc :
  1,2 seconde sans que personne parle. Si tu parles, ta phrase passe d'abord, et en pleine conversation il te le
  dit entre deux échanges, sans y mettre fin : tu enchaînes ensuite sans redire son nom. En veille, les gens qui
  parlent à côté le font aussi patienter ; au bout de 20 secondes de bavardage continu (une télé), il le dit quand
  même. Des tâches qui finissent en même temps : un seul compte rendu. Juste après une annonce, il sait à quoi tu
  réponds (« décale de dix minutes »).
- Un agent qui attend ta réponse est marqué sur le tableau de bord (« attend ta réponse : … »). Une question
  restée 30 minutes sans réponse vaut non, et une question dont l'agent a été arrêté n'est plus posée.

## Comment il t'entend

Tout tourne en local sur le Mac, avec de petits modèles open source (téléchargés une fois dans `modeles/`) :

| Brique | Rôle | Modèle |
|---|---|---|
| Son nom dans la phrase | « Salut Steve… », « …, Steve » : Parakeet connaît ce prénom, rien à lui apprendre | Parakeet v3 |
| Mot de réveil (optionnel) | une formule détectée en continu, même mal transcrite. Fourni seulement pour « Hey Jarvis » ; pour « Hey Steve », il faut entraîner un modèle (voir plus bas) | openWakeWord |
| Fin de phrase | après une courte pause, il devine si tu as fini ou si tu cherches tes mots | pipecat smart-turn v3 |
| Transcription | ~0,1 s par phrase (Whisper : ~0,35 s) | NVIDIA Parakeet v3 (MLX) |
| Anti-écho | retire sa propre voix du micro, pour pouvoir lui couper la parole | WebRTC AEC3 (LiveKit) |
| Débruitage | ignore le bruit de fond (ventilateur, rue) pour savoir si tu parles | DPDFNet (sherpa-onnx) |
| Ta voix | ignore les phrases dites par quelqu'un d'autre (télé, collègues) | CAM++ (sherpa-onnx) |
| Deuxième avis | deux ou trois mots sortis en anglais (« Skip the mail. ») : il les réécoute en français | Whisper large-v3-turbo (MLX) |
| Sa voix | fabrique chaque phrase sur le Mac, sans Internet (voir « Sa voix » plus bas) | Pocket TTS (Kyutai) |

Au démarrage, une ligne indique ce qui est actif (`✓`) ou pas (`·`). Si une brique ne se charge pas, il marche sans elle.

**Apprendre ta voix** (facultatif) : lance une fois

    .venv/bin/python -m jervis --apprendre-ma-voix

et lis les 6 phrases. Il crée `voix.npy` et, ensuite, ignore les autres voix. Pour revenir en arrière, supprime `voix.npy`, `voix_depart.npy`, `voix_debut.npy` et `voix_centres.npy`.

Sans ça, il l'apprend tout seul : après 4 phrases qui se ressemblent où tu dis « Steve » (la télé ne l'appelle pas), il crée l'empreinte. Ces phrases sont gardées dans `voix_debut.npy` : relancer Steve ne remet pas le compteur à zéro, et une autre voix qui dit son nom ne compte pas. Ensuite chaque phrase où il est sûr que c'est toi l'affine un peu, sans jamais trop s'éloigner de l'empreinte de départ (`voix_depart.npy`).

**Ta voix fatiguée aussi.** Ta voix change d'un jour à l'autre (fatigue, rhume, casque, autre pièce) : une seule
empreinte la reconnaît mal ces jours-là. Quand tu dis « Steve » d'une voix qu'il ne reconnaît que de justesse, il en
garde une empreinte de plus dans `voix_centres.npy` (4 empreintes en tout, `voix.npy` comprise ; au-delà, les deux plus
proches fusionnent). Chacune s'affine sur les phrases qui lui ressemblent le plus, et toutes restent proches de ta voix
de départ : la télé ne peut pas s'en faire une. `voix.npy` et `voix_depart.npy` gardent leur rôle ; supprimer
`voix_centres.npy` revient à une seule empreinte.

**Le seuil s'adapte à la phrase.** Une phrase courte a une empreinte moins nette : le seuil descend en douceur de
0,5 (2 secondes de parole et plus) à 0,42 (0,8 seconde). Sous 0,8 seconde, c'est trop court pour juger : la phrase
passe. Quand le mot de réveil a été entendu par son modèle, le seuil est de 0,42 au plus. Le « oui » d'une
confirmation sensible reste jugé au seuil plein.

**Pour régler les seuils**, lance Steve avec `JERVIS_DIAGNOSTIC_VOIX=oui` : chaque phrase écrit une ligne dans
`jervis.log`, par exemple `🔎 voix : 0.61 pour un seuil de 0.50 (2.4 s de parole ; empreintes 0.58 / 0.61, départ 0.55) → acceptée`.
Regarde les scores de tes phrases et ceux de la télé, puis place `JERVIS_SEUIL_VOIX` entre les deux. Rien ne sort du Mac.
C'est un filtre contre la télé et les conversations autour, pas un verrou de sécurité : une voix très proche de la tienne peut passer.

**Un « oui » qu'on ne peut pas te voler.** Quand il te demande confirmation (paiement, ordre venu d'une page web…), ta voix apprise doit être reconnue sur la réponse : une autre voix, une vidéo ou la télé qui dit « oui » ne déclenche rien (« Ce n'est pas ta voix, je n'y vais pas »). Un « oui » tout seul est trop bref pour être signé : il te demande de le dire en une phrase (« oui, vas-y »). Sans voix apprise, rien ne change. Coupe-le avec `JERVIS_CONFIRMATION_VOIX=non`.

**Un vrai mot de réveil « Hey Steve »** (facultatif) : double-clique `outils/entrainer_hey_steve.command`.
Il utilise [livekit-wakeword](https://github.com/livekit/livekit-wakeword) pour fabriquer des milliers d'exemples de
« hey steve » en voix de synthèse, et des phrases qui y ressemblent pour apprendre à ne pas réagir. Il entraîne ensuite
un petit modèle et le range dans `modeles/voix/hey_steve.onnx`. Il faut un compte gratuit [LiveKit Cloud](https://cloud.livekit.io),
environ 4 Go de téléchargement et une heure sur une puce M. Relance ensuite `installer.command` : Steve passe tout seul en
détection hybride (mot de réveil **ou** son nom dans la phrase). Sans ce modèle, il marche déjà : il repère « Steve »
dans la phrase transcrite.

## Sa voix

Steve parle avec [Pocket TTS](https://github.com/kyutai-labs/pocket-tts) (Kyutai), un petit modèle de 220 Mo qui tourne
sur le processeur du Mac. Une phrase est prête en 0,1 à 0,4 s, sans Internet. Avant, il passait par la voix Microsoft
« Rémy » : 1,4 s par phrase en moyenne, et rien sans connexion. Elle reste là en secours, puis la voix du Mac.

**Choisir sa voix** : double-clique `outils/choisir_voix.command`. Il te fait écouter la même phrase avec quatre voix
locales (giovanni, estelle, javert, juergen) et avec Rémy, puis note ton choix dans `voix_choisie.txt`. Relance Steve
ensuite. Fais-le quand il ne t'écoute pas : il entendrait les essais.

Ce qu'il faut savoir :

- Il ne lit que des mots. « 14 h 30 », « 12,50 € », « 25/11/2026 » ou « 06 12 34 56 78 » sont d'abord écrits en toutes
  lettres par `jervis/oral.py` (« quatorze heures trente »).
- Son point faible, ce sont les phrases de un à trois mots : il en rate à peu près une sur six (« Une seconde. » sort
  en charabia). Les 40 qu'il dit le plus (« Oui ? », « Voyons… », « C'est fait. »…) sont donc fabriquées une fois,
  réécoutées par Whisper, et rangées dans `voix_sures/` quand elles sont comprises mot pour mot. Il reprend ces
  versions-là. Si tu changes ses phrases d'attente, relance `.venv/bin/python -m jervis.voix_sures` (une minute).
  Les autres réponses très courtes ne sont pas vérifiées.
- Revenir à Rémy : choisis-le dans `outils/choisir_voix.command`, ou lance avec `JERVIS_VOIX_LOCALE=non`.
- Les noms étrangers passent moins bien qu'avec Rémy (« GitHub » peut sortir « guitare »).
- Si tu refais le `.venv` : `.venv/bin/pip install pocket-tts`. Le modèle (220 Mo, dans `modeles/`) se retélécharge
  tout seul au premier démarrage s'il manque.

## Ce qu'il sait faire en plus

| Tu dis… | Il fait |
|---|---|
| « Retiens que ma sœur s'appelle Inès » | il le note dans `memoire.md`, rangé par rubrique (Toi, Proches, Travail…) et daté |
| « J'ai déménagé à Paris » | il range l'ancienne adresse dans « Ce qui n'est plus vrai (jusqu'au …) » au lieu de garder les deux |
| « Oublie ce que je t'ai dit sur Inès » | il efface ces lignes |
| « Tu te souviens du resto qu'on a cherché la semaine dernière ? » | il fouille vos conversations passées (`espace/conversations.jsonl`) |
| « Fais une recherche sur les vols pour Lisbonne en mars, et compare » | il lance un **agent de fond** (Opus, qui réfléchit longtemps) et te rend la main tout de suite. Tu peux lui demander autre chose ; quand c'est fini, il te prévient et te résume le résultat. Plusieurs travaux d'un coup (« ajoute des fondateurs sur LinkedIn et trouve-moi des vols ») = un agent chacun, jusqu'à 5 en parallèle. « Où en sont mes agents ? » (il dit ce que chacun fait là, tout de suite), « annule la tâche 2 ». Les agents ont leur propre Chrome, vide : pas connectés à tes comptes |
| « À 18 h 30, dis-moi de partir à la gare » | une annonce programmée : à l'heure dite, il te le dit à voix haute (et une notification s'affiche) |
| « Tous les jours à 9 h, regarde si mon train a du retard » | il le fera lui-même chaque jour à 9 h, puis te dira le résultat. « Qu'est-ce que tu as de programmé ? », « annule le 3 » |
| « Qu'est-ce que je faisais hier vers 15 h ? », « retrouve l'article sur l'IA que j'ai lu ce matin » | avec screenpipe (voir plus bas), il retrouve ce qui est passé sur ton écran |
| « Regarde mon écran, c'est quoi cette erreur ? » | il fait une capture et l'analyse |
| « Envoie un message à Léa : j'arrive dans 10 minutes » | il cherche Léa dans Contacts, puis te lit le message avant de l'envoyer |
| « Rappelle-moi d'appeler maman demain à 18 h » | il crée un rappel dans Rappels, qui sonne aussi sur l'iPhone |
| « Note mes idées : … » | il crée une note dans Notes |
| « Éteins les lumières du salon » | il lance le raccourci correspondant de l'app Raccourcis |
| « Va sur le site de la SNCF et trouve-moi un train pour Lyon vendredi » | il ouvre **son propre Chrome** et fait la tâche lui-même : il clique, tape et lit les pages. Avant de payer ou commander quoi que ce soit, il te demande « Je peux ? » |
| « Dans Notes, crée une liste de courses avec du pain et du lait » | il pilote l'app elle-même : il regarde ses boutons, clique et tape, **en arrière-plan** (ta souris et ton clavier restent à toi). Marche avec Finder, Spotify, Pages, Réglages… |
| « Trouve mon CV dans le Drive et résume-le » | il cherche dans ton Google Drive et lit le document |
| « Garde mon CV » (avec le chemin du fichier), puis « aide-moi à postuler chez Mistral » | il le copie dans ses documents (`documents/`) et s'en sert ensuite tout seul : candidature, formulaire, lettre (voir plus bas) |
| « Inscris-moi sur Canva avec mon adresse perso » | il prend tes infos dans ta fiche (`documents/fiche.md`), remplit le formulaire, **tire un mot de passe solide sur le Mac, le range dans ton Trousseau et le colle sans jamais le voir**, puis va chercher le code de vérification dans Gmail ou dans tes SMS. Il te demande « Je crée ton compte sur canva.com avec … ? » avant, et te passe la main pour un CAPTCHA (voir plus bas) |
| « Connecte-toi à mon compte Canva » | pour un compte qu'il a créé, il ressort le mot de passe du Trousseau et le colle (« Je sors ton mot de passe de canva.com… ? ») ; « quels comptes tu m'as créés ? » |
| « Ajoute ça à ma page Notion "Idées" » | avec Notion branché sur claude.ai, il cherche la page, la lit et l'écrit directement (sans Zapier) |
| « Ajoute une carte "Facture EDF" dans mon Trello » | il passe par Zapier, qui relie des milliers d'apps (Notion, Slack, Trello, Sheets…). Les actions s'activent sur zapier.com |
| « Mets un prompt dans Claude Code sur Jervis pour ajouter un mode silencieux » | il lit d'abord ton code, **rédige lui-même un vrai prompt** (contexte, fichiers, contraintes, comment vérifier), le colle dans **l'onglet Claude Code déjà ouvert** dans Cursor (palette de commandes → « Claude Code: Focus input », puis cmd+v) et appuie sur Entrée. Un nouvel onglet ou une nouvelle fenêtre Claude Code seulement si tu le demandes (« dans un nouvel onglet Claude Code », « dans une nouvelle fenêtre »). Le prompt passe par le presse-papiers. « Écris-le sans l'envoyer » : il le laisse dans le champ. « Dans l'agent de Cursor » (ou « Composer ») : il le met dans le chat de Cursor, qui te montre toujours une fenêtre de confirmation. Si tu dis juste « dans Cursor », c'est `JERVIS_CURSOR_CIBLE` qui décide. Pour un nouvel onglet ou l'agent, la première fois, Cursor peut demander « Ouvrir ce lien ? » : coche « ne plus demander » |
| « Mets à la corbeille les captures d'écran du Bureau » | il les trouve, puis les met **à la corbeille** (jamais de suppression définitive : tu peux les reprendre). « Vide la corbeille » : là il te demande « Je peux ? » |
| « Libère de la mémoire », « le Mac rame » | il mesure la mémoire vive (part occupée, pression, débordement sur le disque), te dit quelles apps pèsent le plus et quitte celles dont tu ne te sers pas (jamais Cursor, Chrome ni l'app au premier plan). Si la pression est normale, il te le dit au lieu de fermer des apps pour rien |
| « Efface ta mémoire », « oublie tout ce que tu sais sur moi », « efface nos conversations » | il met `memoire.md` et/ou l'historique à la corbeille, puis repart de zéro tout de suite, sans redémarrage. « Oublie que j'aime le jazz » n'enlève que ça |
| « Vérifie tes autorisations » | il te dit ce que macOS ne lui laisse pas encore faire, et ouvre le bon volet des Réglages |
| « Renomme / déplace / ouvre / range… » n'importe où sur le Mac | il a accès à tout le disque avec les chemins complets (`~/Desktop/…`, `/Applications/…`), pas seulement à son dossier |
Il réfléchit avant de répondre, autant que la question le demande : presque pas pour « mets de la musique »,
beaucoup plus pour « organise ma semaine ». Il enchaîne seul les étapes et vérifie que ça a marché.

### Ce qu'il retient tout seul

Après chaque conversation, un petit modèle (Haiku) relit l'échange et garde ce qui sera encore utile dans un mois :
un prénom, une habitude, un projet. Les banalités et ce qui ne vaut que pour aujourd'hui sont ignorés.
Il le sait dès la conversation suivante ; demande-lui « qu'est-ce que tu sais de moi ? » pour vérifier.
Chaque échange est aussi écrit dans `espace/conversations.jsonl`, où il cherche quand tu lui parles du passé.
`memoire.md` est un simple fichier texte : tu peux le lire et le corriger toi-même. `JERVIS_MEMOIRE_AUTO=non`
coupe la mémoire automatique, mais « retiens que… » marche toujours.

### Il relie ce qu'il sait (son graphe, facultatif)

La mémoire est une liste de phrases ; le graphe ([Graphiti](https://github.com/getzep/graphiti)) les relie. Chaque
fait que la relecture vient de retenir y entre aussi : « ta sœur s'appelle Léa » et « Léa habite à Lyon » deviennent
des personnes, des lieux et des liens. Il peut alors répondre à « où habite ma sœur ? » sans que la phrase existe
telle quelle (outil `chercher_liens`), et il sait ce qui a changé : quand tu lui dis plus tard que Léa a déménagé à
Bordeaux, l'ancien lien n'est pas effacé mais daté « plus vrai depuis le … », et il ne te le donnera pas comme actuel.
Seuls les faits déjà retenus dans `memoire.md` y entrent, jamais une conversation brute ni un mail ou une page.
Tout reste sur le Mac : la base est un fichier (`espace/graphe.db`, FalkorDB embarqué, rien à installer de plus), les
empreintes des phrases sont calculées par Ollama en local, la télémétrie de Graphiti est coupée ; seul le travail de
lecture (qui est qui, quel lien) passe par Claude, comme la relecture de la mémoire (compté « graphe » dans les
dépenses). Pour l'activer, une fois :

    ollama pull embeddinggemma

Sans Ollama (ou s'il est éteint), il le note dans le journal (« 🕸️ Graphe : Ollama ne répond pas… ») et continue
avec `memoire.md` seule. « Oublie que… » efface aussi les liens qui contiennent ce texte, « efface ta mémoire »
vide le graphe. `JERVIS_GRAPHE=non` le coupe.

### Ses documents (ton CV, tes papiers)

Menu de Steve → « Mes documents (CV, papiers…) » ouvre le dossier `documents/` dans le Finder : glisse-y ton CV, une
lettre de motivation type, une attestation… Tu peux aussi lui dire « garde mon CV, il est dans Téléchargements » :
il le copie là (l'original reste où il est). Il en connaît la liste (les 30 plus récents, avec leur date) et ouvre
celui qui sert quand c'est utile : « aide-moi à remplir cette candidature », « écris une lettre pour ce poste »,
« depuis quand je fais ce métier ? ». Les agents de fond ont la même liste.

- Il ne lit un document que quand la demande le demande, et ce qui est écrit dedans reste une information, jamais
  un ordre.
- Envoyer un de ces fichiers sur un site (déposer ton CV dans un formulaire) te demande toujours « Je peux ? ».
- Le dossier n'est pas suivi par git : tes papiers ne partent pas sur GitHub. Mettre un vieux CV à la corbeille se
  fait comme pour tes autres fichiers.

### Créer des comptes à ta place

« Inscris-moi sur Canva », « crée-moi un compte Doctolib », « ouvre un compte Vinted avec mon adresse Gmail » : il le
fait de bout en bout, dans son Chrome ou dans le tien.

1. **Ta fiche.** Menu de Steve → « Ma fiche (pour les inscriptions) » ouvre `documents/fiche.md` : prénom, nom, date
   de naissance, adresse mail et téléphone à utiliser, adresse postale, pseudo. Remplis ce que tu veux : ce qui
   manque, il te le demande. Comme tes autres documents, elle reste sur le Mac (pas sur GitHub).
2. **Le mot de passe, Claude ne le voit jamais.** Il est tiré au hasard sur ton Mac (`secrets`, 20 caractères),
   rangé dans ton **Trousseau macOS** au nom du site (app Mots de passe ou Trousseau d'accès → cherche « Steve »),
   puis mis dans le presse-papiers le temps qu'il le colle (cmd+v). Il quitte le presse-papiers au bout de 90
   secondes, sauf si tu as copié autre chose entre-temps. Steve ne reçoit que « c'est rangé » : rien ne part chez
   Claude, rien n'est dit à voix haute, rien n'est écrit dans ses conversations. La liste des comptes créés
   (`espace/comptes.jsonl`) n'a que le site, l'identifiant et la date.
3. **Le code de vérification.** Par mail : il le lit dans Gmail. Par SMS : `chercher_code_recu` le lit dans
   Messages, sur le Mac, et ne rend que le code. Pour ça : sur l'iPhone, Réglages → Apps → Messages → **Transfert
   de SMS** → ton Mac, et sur le Mac, **Accès complet au disque** pour Jervis (Réglages → Confidentialité et
   sécurité).
4. **Ce qui reste à toi.** Il te demande « Je peux ? » en te disant le site exact (« canva.com », pas
   « canva-connexion.xyz ») avant de créer le compte, avant de sortir un mot de passe du Trousseau et avant de lire
   un SMS : une page piégée ne peut pas lui faire voler un de tes comptes en le réinitialisant. Un **CAPTCHA** (« je
   ne suis pas un robot », images à cocher), c'est toi : il ne le contourne jamais, il te le dit et reprend ensuite.

**Payer** (un abonnement à l'inscription, une commande) : ta carte n'est jamais dans un fichier ni dans ses
messages. Range-la une fois dans Chrome (Paramètres → Saisie automatique → Modes de paiement) : dans **ton Chrome**
(voir « Ton Chrome à toi »), le remplissage automatique la propose, il te demande « Je paie 12,99 € à Canva, je
peux ? » avant le bouton, et la validation de ta banque (3-D Secure) reste dans ton app bancaire. Pour aller plus
loin : une **carte virtuelle** (Revolut, ta banque) que tu peux bloquer à tout moment.

Pour tes comptes existants (ceux qu'il n'a pas créés) : il te demande de te connecter, ou laisse ton gestionnaire
de mots de passe remplir la page. **1Password for Claude** (1password.com/blog/1password-for-claude) fait
exactement ça avec l'extension Claude in Chrome : il remplit après ton Touch ID, sans que Claude voie le mot de passe.

<details><summary>Pourquoi pas browser-use, Skyvern ou Stagehand ?</summary>

Ces projets GitHub font un agent navigateur complet, avec sa propre boucle d'IA. Steve a déjà Claude pour réfléchir,
et deux navigateurs pour agir (le sien via Playwright, le tien via Claude in Chrome) : en ajouter un troisième
doublerait les coûts et les pannes. Ce qu'ils apportent vraiment, c'est de **ne jamais montrer les mots de passe au
modèle** (browser-use `sensitive_data`, Skyvern et ses coffres Bitwarden/1Password, Stagehand qui remplit par le
code) : c'est ce que font le Trousseau et le presse-papiers ici. Skyvern (AGPL, serveur + Postgres) vaut le coup si
tu veux un jour confier des centaines d'inscriptions d'un coup ; pour ton usage, Steve suffit.
</details>

### Notion, branché directement

Notion a son propre connecteur sur claude.ai : **claude.ai → Paramètres → Connecteurs → Notion → Connecter**, choisis
les pages qu'il peut voir, puis relance Steve. C'est tout : il le garde s'il est branché, et ne l'attend pas au
démarrage s'il ne l'est pas (`JERVIS_CONNECTEURS_FACULTATIFS`). « Cherche dans mon Notion ce que j'ai noté sur le
budget », « crée une page "Réunion lundi" avec ces points », « ajoute ça à ma liste d'idées ». Lire et chercher se
font seuls ; créer ou modifier une page qu'il demande aussi (sauf avec `JERVIS_CONFIRMATIONS=toutes`). Le tableau
de bord le montre avec les autres connecteurs dès qu'il l'a vu.

### Il apprend de son travail (son carnet de leçons)

La mémoire parle de toi ; son carnet, `lecons.md`, parle de sa façon de travailler pour toi : « ses mails pro sont
sur le compte Gmail du boulot », « sur Doctolib, filtrer par secteur 1 d'abord », « quand il dit "le projet",
c'est Oqro ». Il y écrit de lui-même quand tu le corriges ou quand une tâche a coincé puis marché autrement
(outil `noter_lecon`), et la relecture de fin de conversation y ajoute ce qu'elle remarque : elle voit quels outils
ont servi et lesquels ont échoué. Il relit le carnet à chaque session, ses agents de fond aussi, donc il ne refait pas
deux fois la même erreur. Une leçon devenue fausse est remplacée, pas empilée ; au-delà de `JERVIS_LECONS_MAX` lignes,
les plus anciennes s'en vont. C'est un simple fichier texte : menu → « Ce qu'il a appris (son carnet) », ou le
bouton **Carnet** du tableau de bord, pour le lire et rayer ce que tu veux. « Efface ta mémoire, tout » le vide aussi.
Comme le carnet entre dans ses consignes, il est protégé contre les textes piégés : une leçon vient de ce que tu lui
dis ou de ce qu'il constate, jamais du contenu d'un mail ou d'une page ; ses agents de fond et sa ronde (qui lisent
seuls) ne peuvent pas y écrire, et une leçon qui contient un lien, une adresse mail ou un numéro est refusée.
Quand il vient de lire un texte écrit par quelqu'un d'autre (un mail, une page, un document, un fichier, une commande,
l'écran : tout outil qui n'est pas sur sa courte liste d'outils « internes »), il te demande avant d'écrire quoi que
ce soit pour plus tard : une leçon (« Je note dans mon carnet : … Je peux ? »), un fait sur toi (« Je retiens sur
toi : … »), un oubli, une consigne programmée (« Je programme chaque jour à partir de 08:00 : je ferai « … » ») ou le brief
d'un agent de fond (« Je confie à un agent « … » : … »). La relecture de fin de
conversation, le fil qu'il reprend après une pause et ses recherches dans vos conversations passées ne lui remontent
pas ce texte-là, seulement ce que tu as dit et quels outils ont marché (le journal, lui, garde tout pour toi). Chaque nouvelle leçon apparaît dans le journal (« 📒 Leçon : … »).

### Sa ronde (il prend l'initiative)

Une fois par heure environ, entre 9 h et 20 h, quand tu es devant le Mac et après le briefing du matin, il lance un
petit agent qui fait le tour de ce que tu lui as confié : mails importants non lus, prochain rendez-vous, rappels
en retard. S'il n'y a rien, il se tait (une ligne « 👀 Ronde : rien d'important. » dans le journal). S'il y a
quelque chose, il te le dit en une ou deux phrases et te propose la suite (« Marc attend ta réponse sur le devis
depuis ce matin, je te prépare un brouillon ? »). Il retient ce qu'il t'a déjà signalé dans la journée pour ne pas
le répéter.

La ronde ne fait que **regarder** : elle peut chercher, lister et lire, mais tout envoi, suppression, création ou
commande du terminal lui est refusé, et elle n'a pas accès au web (un mail piégé ne peut donc pas lui faire ouvrir un
lien pour faire sortir tes données). Agir reste ta décision : c'est lui, après t'avoir parlé, qui fait la suite si tu
dis oui. Elle ne prend jamais la dernière place d'agent de fond : il en reste toujours une pour toi. Quand il te rapporte ce
qu'elle (ou une tâche de fond) a trouvé, il se méfie : ce compte rendu peut répéter un mail piégé, alors pendant ce
tour-là il ne fait que lire et te parler, et toute action (même un brouillon) attend ton « oui ».

Ce qu'elle surveille est écrit dans `initiative.md` (menu → « Ce qu'il surveille pour moi (sa ronde) », ou le
bouton **Ronde**) : « regarde aussi les mails de l'URSSAF », « ignore les newsletters »… `JERVIS_INITIATIVE=non` la
coupe, `JERVIS_INITIATIVE_MIN=120` l'espace.

### Le briefing du matin

Le premier matin où tu es devant le Mac (clavier ou souris touchés depuis moins de 2 minutes) après 7 h 30, il te fait
un point de 4 ou 5 phrases : météo, agenda du jour, mails importants non lus, rappels. Une seule fois par jour, et jamais
après midi. Tu choisis ce qu'il contient en écrivant dans `briefing.md` (menu → « Mon briefing du matin ») :
« commence par les mails de mon boss », « dis-moi le cours du bitcoin »… `JERVIS_BRIEFING_HEURE=08:15` change l'heure,
`JERVIS_BRIEFING=non` le coupe.

### Sa mémoire de l'écran (screenpipe, facultatif)

[screenpipe](https://github.com/screenpipe/screenpipe) enregistre en continu ton écran (texte lu par OCR) et ton micro,
**en local**, dans `~/.screenpipe`. Steve peut alors répondre à « qu'est-ce que je faisais hier après-midi ? » ou
« retrouve ce que Paul m'a écrit dans Slack ce matin ». Pour l'activer, lance dans un Terminal :

    npx screenpipe record

et autorise l'**Enregistrement de l'écran** pour le Terminal. Ensuite, Steve le trouve tout seul au démarrage
(`JERVIS_SCREENPIPE=auto`). Il s'en sert pour relire ce qui est passé ; l'export de vidéos lui est coupé.
Licence : gratuit pour un usage personnel, payant pour une entreprise.

### Ce qu'il te demande encore

Si tu lui demandes quelque chose, c'est oui : il envoie le message WhatsApp, le mail ou l'iMessage sans
redemander, et il en prend la responsabilité. Il ne demande « Je peux ? » que dans ces cas :

| Ce qui reste confirmé | Exemples |
|---|---|
| L'argent | payer, acheter, commander, réserver ou s'abonner (Steve ne paie jamais lui-même) |
| Tes comptes | créer un compte (il te dit le site et l'identifiant), sortir un mot de passe du Trousseau, lire un code reçu par SMS |
| Ce qui détruit pour de bon | `kill`, `git push`, vider la corbeille, supprimer un rendez-vous ou un brouillon, un menu « Supprimer » ou « Vider » (mettre à la corbeille se rattrape : il le fait seul, y compris pour sa propre mémoire) |
| Ce qui part chez tout le monde | publier, partager un fichier « à tous ceux qui ont le lien » |
| Les secrets et le système | lire, écrire ou jeter `.ssh`, `.env`, clés, trousseau, 1Password, toucher au Terminal, lancer du code dans ton Chrome connecté |
| Ce qui ne vient pas de toi | une action suggérée par une page web, un mail ou une app, que tu n'as pas demandée |

`sudo`, `rm -rf`, formater un disque, éteindre ou redémarrer le Mac restent interdits, même si tu dis oui.
Pareil pour **tout `rm` direct** (`rm`, `rmdir`, `unlink`, `shred`, `find … -delete`) : il n'efface jamais un fichier,
il le met à la corbeille avec son outil dédié, et te le dit s'il a dû renoncer à une commande (« je ne supprime pas :
je mets à la corbeille »). Le reste du terminal lui est ouvert : lire les journaux, lancer un script, `git status`,
`git commit`, installer un outil (`brew`, `pip`, `npm`), tant que ça ne touche pas aux secrets ni au système.
La corbeille refuse aussi, quoi qu'il arrive : les dossiers du système, ton dossier personnel entier, un grand
dossier en bloc (Bureau, Documents, Bibliothèque… : ce qu'il y a dedans part, pas le dossier), un disque ou un compte
entier, et Steve lui-même (son code, sa mémoire : pour elle, c'est « efface ta mémoire »). Une tâche de fond qui a besoin de ton accord
te pose la question à voix haute (« Pour la tâche "vols Lisbonne" : je peux réserver ? »). Sans réponse dans les
30 minutes, c'est non. `JERVIS_CONFIRMATIONS=essentielles` lui fait aussi confirmer chaque message et chaque
mail ; `toutes` remet l'ancien mode, qui demande avant chaque création ou modification. Tout se règle dans
`jervis/permissions.py`.

### Ton Chrome à toi

Il pilote **ton** Chrome, avec tes comptes déjà connectés, grâce à l'extension Claude in Chrome (la même que
Claude Code) : « Steve, va sur Doctolib et trouve-moi un créneau », « ouvre-moi le dernier article du Monde »,
« remplis ce formulaire ». Il travaille dans son propre groupe d'onglets et ne ferme jamais les tiens.

- Il faut l'extension [Claude in Chrome](https://claude.ai/chrome) installée et connectée au même compte Claude.
- Si Chrome est fermé, il l'ouvre tout seul.
- Il demande avant de lancer du code dans une page ou d'envoyer un fichier du Mac sur un site, et avant tout ce
  qui engage (payer, commander, réserver, envoyer). Il ne tape jamais de mot de passe.
- Si l'extension ne répond pas, il se rabat sur son Chrome à lui (profil à part, dossier `navigateur/`) et te le
  dit. `JERVIS_CHROME_PERSO=non` le fait toujours travailler dans son Chrome à lui.

Pour la maison : crée dans l'app Raccourcis un raccourci par action (« Lumières salon off », « Scène film »…) avec les actions Maison.
Il les retrouve par leur nom.

## Son tableau de bord

Un écran façon hologramme, dans une fenêtre à part : clique sur son icône dans la barre des menus →
« Tableau de bord », ou dis « Steve, affiche ton tableau de bord ».

- **Au centre** : un anneau qui vit avec lui (en veille, il t'écoute, il réfléchit, il parle) et qui bouge avec
  sa voix et la tienne, l'heure et le calendrier du mois.
- **À gauche** : le Mac, expliqué en clair, et ses dernières actions (« Gmail · mail à la corbeille ×12 »).
  Chaque jauge dit ce que le chiffre veut dire (« 17 sur 24 Go », « 49 Go libres ») et donne un verdict avec un
  signe : ✓ ça va, ▲ à surveiller, ✕ problème. Survole-la (ou passe dessus au clavier) pour l'explication
  complète et les apps qui en prennent le plus. En dessous, Steve résume en une ou deux phrases.
- **À droite** : ses systèmes (micro, voix, réseau avec sa courbe, activité du processeur sur les 3 dernières
  minutes, depuis quand le Mac est allumé ; ils s'allument un par un à l'ouverture), puis **ses outils connectés**
  (Chrome, Gmail, Agenda, Drive, Zapier : ✓ connecté, ▲ à reconnecter, ✕ ne répond pas, tel que Claude Code
  l'a dit à la dernière vérification), **ses dernières commandes** du terminal (survole-en une pour son but et la
  commande entière ; les mots de passe, jetons et clés y sont toujours masqués), puis vos derniers échanges.
- **En bas** : ses temps de réponse (survole une barre : fin de phrase, texte, Claude, voix), ce qu'il sait sur
  toi, et son usage de Claude : la part utilisée de ta fenêtre de 5 heures et de ta semaine d'abonnement, et ses
  jetons jour par jour (pas de prix). Il te prévient à voix haute à 80 % puis 95 % d'une limite ; quand elle est
  atteinte, il te dit en une phrase à quelle heure ça se libère (« Tu as atteint la limite de ton abonnement, ça
  se libère à 21 h 50. ») et ne peut plus rien faire d'ici là.
- **Ses agents** (à gauche) : chaque agent de fond qu'il a lancé, avec le temps qu'il y passe, l'outil qu'il utilise en
  ce moment (« cherche sur le web : … », « Navigateur · ouvre une page », ou en jaune « attend ta réponse : … » quand il
  t'a posé une question) et son nombre d'étapes ; les derniers finis restent
  quelques minutes, grisés (✓ fini, ✕ échec) avec ce qu'ils ont consommé chez Claude, en jetons. Un **×** à droite d'un
  agent en cours l'arrête. Au-dessus, le bilan depuis son démarrage : « 5 lancés · 1 en cours · 2 finis · 1 échec ·
  1 annulé ». Ni prix ni dollars : ce qui compte, c'est ta part d'abonnement, pas la facture API.
- **Lui écrire** (sous vos échanges) : tape ta demande et Entrée (Maj+Entrée pour aller à la ligne). Il te
  répond par écrit, sans un mot à voix haute, et sa réponse s'affiche au fur et à mesure, en entier (listes et
  retours à la ligne compris). Pratique en réunion, dans le métro, ou pour une adresse, un code, un texte à
  coller. S'il a besoin de ton accord (« j'envoie le mail ? »), la question apparaît dans le chat avec deux
  boutons **Oui** / **Non** au lieu de passer par le micro ; sans clic au bout de 5 minutes, c'est non. Si tu lui
  parles pendant qu'il répond à un message écrit, il te dit « je finis ce que tu m'as écrit » et enchaîne : ta voix
  ne coupe pas une demande écrite. Ce que vous vous écrivez entre dans la même conversation, la même mémoire et le
  même carnet que ce que vous vous dites.
- **Les boutons** : couper ou rallumer le micro, ouvrir sa mémoire, ses conversations, son briefing, sa ronde,
  son carnet de leçons, son journal.

La page est servie par Steve lui-même sur `http://127.0.0.1:8765` : elle n'existe que sur ce Mac, et tant qu'elle
n'est pas ouverte elle ne lui coûte rien. Le chat passe par la même porte : rien de ce que tu tapes ne sort du Mac
avant d'arriver à Claude, comme ce que tu dis. Écrire, lire le chat et répondre Oui / Non demandent le jeton secret
que seule la page reçoit : un autre site ouvert dans ton navigateur ne peut ni lire ni écrire à ta place.

**Ce que veulent dire les chiffres du Mac :**

- **Mémoire 90 %** : c'est normal. macOS remplit la mémoire avec tes apps pour qu'elles se rouvrent tout de
  suite. Ce qui compte, c'est la **pression** (le signal de macOS lui-même) : « sans pression » = tout va
  bien ; « sous pression » = il compresse et écrit sur le disque (le *swap*), le Mac ralentit : ferme des apps
  ou des onglets.
- **Processeur** : des pics sont normaux. Le verdict regarde les dernières mesures, pas un pic d'une seconde :
  « à fond » seulement s'il reste au-dessus de 85 %.
- **Disque** : garde 10 à 15 % de libre (▲ en dessous de 15 %, ✕ en dessous de 5 %). Pour faire de la place :
  Réglages Système › Général › Stockage.
- **Batterie** : ▲ sous 20 %, ✕ sous 10 %. Le temps restant n'apparaît que quand macOS a une vraie estimation. Les phrases de l'anneau (« je t'écoute », « je réfléchis »…) se changent
dans `ETATS`, dans `jervis/tableau.html`. Test : `PYTHONPATH=. .venv/bin/python tests/test_tableau.py`.

## Réglages

Tu peux changer chaque réglage avec une variable d'environnement, par exemple :

    JERVIS_VOIX_NEURONALE=fr-FR-HenriNeural JERVIS_SEUIL_VAD=0.3 .venv/bin/python -m jervis

| Variable | Défaut | Rôle |
|---|---|---|
| `JERVIS_VOIX_LOCALE` | giovanni (ou ton choix dans `voix_choisie.txt`) | sa voix fabriquée sur le Mac : `giovanni`, `estelle`, `javert`, `juergen`… ; `non` = la voix Microsoft ci-dessous |
| `JERVIS_VOIX_LOCALE_VOLUME` | 1.0 | volume de sa voix locale (0.8 = un peu moins fort) |
| `JERVIS_VOIX_LOCALE_LANGUE` | french | le modèle de sa voix locale |
| `JERVIS_VOIX_NEURONALE` | fr-FR-RemyMultilingualNeural | voix Microsoft, en secours ou si la voix locale est coupée : `fr-FR-HenriNeural`, `fr-FR-VivienneMultilingualNeural` (femme)… ; `non` = voix macOS |
| `JERVIS_VITESSE` | +8% | débit de la voix Microsoft (la voix locale a le sien) |
| `JERVIS_VOIX_DIRECTE` | oui | garde une connexion ouverte avec la voix Microsoft pendant qu'il réfléchit (sa première phrase part plus vite) ; `non` = une connexion neuve par phrase |
| `JERVIS_SEUIL_VAD` | 0.5 | baisse-le (0.3) s'il ne t'entend pas, monte-le (0.7) s'il réagit au bruit |
| `JERVIS_PRENOM` | Steve | le nom auquel il répond et qu'il se donne (`Jarvis` = l'ancien) |
| `JERVIS_MOTCLE_MODELE` | (aucun) | modèle de mot de réveil : `hey_jarvis` (fourni) ou le chemin de ton `.onnx` « Hey Steve » |
| `JERVIS_DETECTION` | whisper | `whisper` = son nom dans la phrase transcrite ; avec un modèle de mot de réveil : `hybride` (par défaut alors) = l'un ou l'autre, `motcle` = seulement le mot de réveil |
| `JERVIS_SEUIL_MOTCLE` | 0.5 | baisse-le (0.3) s'il rate le mot de réveil, monte-le (0.7) s'il se réveille tout seul |
| `JERVIS_BOOST_NOM` | 0 (4 pour Jarvis) | souffle son nom à Parakeet quand il ne l'a pas trouvé ; inutile pour Steve (il ferait entendre « steve » dans « steak ») |
| `JERVIS_FIN_INTELLIGENTE` | oui | `non` = fin de phrase après un silence fixe (`JERVIS_SILENCE_END_S`, 0.8 s) |
| `JERVIS_SILENCE_COURT_S` | 0.35 | pause après laquelle il commence à se demander si tu as fini (puis toutes les 0,25 s) |
| `JERVIS_SILENCE_MAX_S` | 2.5 | pause après laquelle il coupe quoi qu'il arrive |
| `JERVIS_SEUIL_FIN` | 0.8 | certitude qu'il lui faut juste après un blanc : monte-le (0.9) s'il te coupe encore |
| `JERVIS_SEUIL_FIN_TARD` | 0.3 | certitude qui lui suffit à la fin de `SILENCE_MAX_S` (il devient moins exigeant au fil de ta pause) |
| `JERVIS_SUITE_PHRASE_S` | 5 | phrase qui finit en l'air (« tu peux ouvrir… ») : le temps qu'il attend la suite, en silence |
| `JERVIS_SUITE_SANS_POINT_S` | 1.5 | phrase de 3 mots ou plus sans point final : le temps qu'il attend encore (monte-le à 2.5 s'il te coupe) |
| `JERVIS_SUITE_HESITATION_S` | 4 | tu réfléchis à voix haute (« euh », « du coup », « en fait »… en fin de phrase) : le temps qu'il attend la suite |
| `JERVIS_ENCORE_S` | 1.2 | tu parlais encore quand il a repris ta phrase : il écoute au moins ce temps-là pour avoir la fin |
| `JERVIS_AVANT_PAROLE_S` | 0.32 | le son gardé juste avant que ta voix soit détectée (pour ne pas manger ta première syllabe) |
| `JERVIS_MIN_PAROLE_S` | 0.35 | en dessous de ce temps de parole, c'est un bruit (clic, toux), pas une phrase |
| `JERVIS_ENTRE_QUESTIONS_S` | 0.25 | pendant ta pause, tous les combien il se redemande si tu as fini |
| `JERVIS_FUSION_S` | 8 | tu reprends la parole avant qu'il ait répondu : pendant ce temps-là, il recolle les deux bouts et repart de la phrase entière |
| `JERVIS_FUSIONS_MAX` | 3 | combien de fois de suite il recolle (après, ce que tu ajoutes passe juste après sa réponse) |
| `JERVIS_RETENUE_S` | 3.2 | tant que tu parles, il retient sa réponse : ce temps-là après le début de ta voix |
| `JERVIS_RETENUE_MAX_S` | 6 | et jamais plus que ça en tout (une télé allumée ne doit pas le rendre muet) |
| `JERVIS_COUPER_SI_TU_PARLES` | oui | `non` = parler par-dessus lui ne le coupe plus (seulement son nom ou « stop ») |
| `JERVIS_MOTS_POUR_COUPER` | 3 | nombre de mots qu'il te faut dire par-dessus lui pour qu'il se taise |
| `JERVIS_STT` | parakeet | `whisper` pour l'ancien moteur de transcription |
| `JERVIS_DEUXIEME_AVIS` | oui | `non` = il ne réécoute plus en français les deux ou trois mots sortis en anglais (0,4 s de gagné sur ces phrases-là) |
| `JERVIS_ANTI_ECHO` | oui | `non` pour le couper (par ex. si ton micro a déjà le sien) |
| `JERVIS_DEBRUITAGE` | detection | `tout` = débruite aussi ce qui est transcrit ; `non` = coupé |
| `JERVIS_SEUIL_VOIX` | 0.5 | si ta voix est apprise : baisse-le (0.4) s'il t'ignore, monte-le (0.6) s'il écoute les autres (phrases de `VOIX_LONGUE_S` et plus, et les « oui » sensibles) |
| `JERVIS_SEUIL_VOIX_COURTE` | 0.42 | le seuil des phrases les plus courtes (`MIN_VERIF_VOIX_S`) ; entre les deux, il descend en douceur |
| `JERVIS_VOIX_LONGUE_S` | 2 | à partir de ce temps de parole, le seuil plein (`SEUIL_VOIX`) |
| `JERVIS_MIN_VERIF_VOIX_S` | 0.8 | en dessous, trop court pour juger la voix : la phrase passe |
| `JERVIS_SEUIL_VOIX_REVEIL` | 0.42 | seuil au plus quand le mot de réveil a été entendu par son modèle |
| `JERVIS_VOIX_CENTRES` | 4 | nombre d'empreintes de ta voix (`1` = une seule, comme avant) |
| `JERVIS_ECART_CENTRE` | 0.5 | une empreinte en plus doit ressembler au moins autant à ta voix de départ |
| `JERVIS_VOIX_PLANCHER` | 0.3 | une phrase reconnue seulement par une empreinte en plus doit ressembler au moins autant à ta voix de départ |
| `JERVIS_DIAGNOSTIC_VOIX` | non | `oui` = chaque phrase écrit son score de voix dans `jervis.log` (pour régler les seuils) |
| `JERVIS_APPRENDRE_VOIX` | oui | il apprend ta voix tout seul (4 phrases où tu dis son nom), puis l'affine à chaque phrase de toi |
| `JERVIS_SEUIL_APPRENDRE` | 0.65 | il n'affine ta voix qu'avec les phrases où il est au moins aussi sûr que c'est toi |
| `JERVIS_CONFIRMATION_VOIX` | oui | un « oui » sensible doit être de ta voix (si elle est apprise) ; `non` pour couper |
| `JERVIS_MODELE` | sonnet | modèle Claude (`haiku` pour encore plus rapide) |
| `JERVIS_REFLEXION` | oui | `non` = il ne réfléchit plus avant de répondre (un peu plus rapide, moins malin) |
| `JERVIS_EFFORT` | medium | `low` = plus rapide (surtout pour bavarder), `high` = réfléchit plus |
| `JERVIS_FOLLOW_UP_S` | 30 | secondes pour commencer ta phrase suivante sans redire son nom (repart à chaque échange) |
| `JERVIS_FOLLOW_UP_QUESTION_S` | 45 | pareil, quand il vient de te poser une question |
| `JERVIS_DISCUSSION_S` | 600 | en mode « on discute » : combien de silence avant qu'il repasse en veille |
| `JERVIS_CHRONO` | oui | après chaque réponse, une ligne grise « ⏱ 1,6 s (fin de phrase · texte · Claude · voix) » : où passe son temps de réponse |
| `JERVIS_PATIENCE` | oui | `non` = plus de « Voyons… » quand il réfléchit longtemps |
| `JERVIS_PATIENCE_S` | 1.8 | après combien de secondes de silence il dit « Voyons… » |
| `JERVIS_PHRASES_PATIENCE` | Voyons…\|Alors…\|Une seconde.\|Je regarde. | ses petites phrases d'attente, séparées par `\|` |
| `JERVIS_VOLUME_SI_TU_PARLES` | 0.35 | volume de sa voix quand tu commences à parler (1 = il ne baisse pas) |
| `JERVIS_CURSOR_CIBLE` | claude_code | « mets ça dans Cursor » sans préciser : `claude_code` = l'extension Claude Code (l'onglet déjà ouvert), `agent` = le chat de l'agent de Cursor |
| `JERVIS_CHROME_PERSO` | oui | `non` = il n'utilise plus ton Chrome, seulement le sien |
| `JERVIS_MODELE_EXPERT` | opus | modèle de son expert, pour les grosses tâches |
| `JERVIS_EFFORT_EXPERT` | high | effort de l'expert (`xhigh` ou `max` pour les tâches très dures) |
| `JERVIS_CONNECTEURS` | Gmail, Google Calendar, Google Drive, Zapier | connecteurs claude.ai à garder (chacun se connecte sur claude.ai → Paramètres → Connecteurs) |
| `JERVIS_MAC` | oui | `non` = il ne pilote plus les apps du Mac (Peekaboo) |
| `JERVIS_PEEKABOO_MCP` | @steipete/peekaboo@4.9.8 | version de Peekaboo |
| `JERVIS_CONFIRMATIONS` | confiance | `essentielles` = il confirme aussi chaque message envoyé ; `toutes` = il redemande avant chaque création ou modification |
| `JERVIS_OUBLI_MIN` | 15 | après cette pause, conversation neuve (il garde sa mémoire et vos 3 derniers échanges) : moins cher et plus rapide |
| `JERVIS_COUT` | oui | `non` = ne plus afficher « 💰 … » après chaque réponse |
| `JERVIS_TACHES_MAX` | 5 | nombre d'agents de fond en même temps |
| `JERVIS_ANNONCE_CALME_S` | 1.2 | le blanc qu'il attend (personne ne parle) pour te dire ce qu'il a à te dire : tâche finie, annonce, question |
| `JERVIS_ANNONCE_ATTENTE_MAX_S` | 20 | au bout de ce temps de bavardage continu (une télé), il le dit quand même |
| `JERVIS_QUESTION_FOND_S` | 1800 | une question d'un agent restée sans réponse ce temps-là : c'est non |
| `JERVIS_CONNECTEURS_FACULTATIFS` | Notion | connecteurs claude.ai gardés s'ils sont branchés, sans les attendre au démarrage |
| `JERVIS_DOCUMENTS` | `documents/` | le dossier de tes documents (CV, papiers) qu'il connaît et lit quand ça sert |
| `JERVIS_DOCUMENTS_VUS` | 30 | combien de documents il liste dans ses consignes (les plus récents d'abord) |
| `JERVIS_PRESSE_PAPIERS_S` | 90 | au bout de combien de secondes un mot de passe qu'il a collé quitte le presse-papiers |
| `JERVIS_MOT_DE_PASSE_LONGUEUR` | 20 | la longueur des mots de passe qu'il crée (de 12 à 64 ; il s'adapte si un site impose moins) |
| `JERVIS_CODE_SMS_MINUTES` | 10 | jusqu'où il remonte dans tes SMS pour trouver un code de vérification |
| `JERVIS_CONNECTEURS_FOND_S` | 10 | le temps qu'un agent attend ses connecteurs (Gmail, Agenda…) avant de démarrer sans eux |
| `JERVIS_MEMOIRE_AUTO` | oui | `non` = il ne retient que ce que tu lui demandes |
| `JERVIS_MODELE_MEMOIRE` | haiku | modèle qui relit les conversations pour la mémoire |
| `JERVIS_GRAPHE` | oui | relie ce qu'il retient dans un graphe (`espace/graphe.db`) ; `non` = mémoire seule |
| `JERVIS_OLLAMA_URL` | http://127.0.0.1:11434 | où trouver Ollama, qui calcule les empreintes des phrases |
| `JERVIS_GRAPHE_EMBED` | embeddinggemma | le modèle d'empreintes dans Ollama (un autre : change aussi `JERVIS_GRAPHE_DIM` et repars d'un graphe vide) |
| `JERVIS_GRAPHE_DIM` | 768 | taille de ces empreintes |
| `JERVIS_MODELE_GRAPHE` | (comme `JERVIS_MODELE_MEMOIRE`) | modèle qui lit « qui est qui, quel lien » pour le graphe |
| `JERVIS_GRAPHE_PARALLELE` | 3 | lectures en même temps pour le graphe (chacune lance un petit Claude) |
| `JERVIS_BRIEFING` | oui | `non` = pas de briefing du matin |
| `JERVIS_BRIEFING_HEURE` | 07:30 | à partir de quelle heure il fait le briefing |
| `JERVIS_INITIATIVE` | oui | `non` = pas de ronde : il ne te dit plus rien de lui-même (hors briefing et rappels) |
| `JERVIS_INITIATIVE_MIN` | 60 | minutes entre deux rondes |
| `JERVIS_INITIATIVE_DEBUT` | 09:00 | pas de ronde avant cette heure |
| `JERVIS_INITIATIVE_FIN` | 20:00 | ni après celle-ci |
| `JERVIS_MODELE_INITIATIVE` | sonnet | modèle de la ronde (elle ne fait que lire : un modèle léger suffit) |
| `JERVIS_LECONS_MAX` | 60 | lignes gardées dans son carnet de leçons (les plus anciennes s'en vont) |
| `JERVIS_QUESTION_ECRITE_S` | 300 | une question Oui / Non du chat restée sans clic ce temps-là : c'est non |
| `JERVIS_ECRIT_RELECTURE_S` | 120 | après ce temps sans message tapé, il relit l'échange écrit pour sa mémoire et son carnet |
| `JERVIS_PRESENT_S` | 120 | tu es « devant le Mac » si tu as touché clavier ou souris depuis ce nombre de secondes |
| `JERVIS_SCREENPIPE` | auto | `auto` = s'il tourne ; `oui` = toujours ; `non` = jamais |
| `JERVIS_SCREENPIPE_MCP` | screenpipe-mcp@0.18.14 | version du connecteur screenpipe |
| `JERVIS_TABLEAU` | oui | `non` = pas de tableau de bord |
| `JERVIS_TABLEAU_PORT` | 8765 | son port, si un autre programme l'utilise déjà |
| `JERVIS_TABLEAU_AU_DEMARRAGE` | non | `oui` = le tableau de bord s'ouvre à chaque lancement |

Les mots que la reconnaissance vocale écorche à coup sûr (« load code » → Claude Code, « whatsap » → WhatsApp,
« ouvre curseur » → Cursor, « link inn » → LinkedIn, « Frisco » → San Francisco…) sont corrigés dans
`jervis/naturel.py` ; ajoute les tiens dans `_CORRECTIONS`. Ce qui reste ambigu (« Watt » = WhatsApp ?), il le devine
avec le contexte et ne redemande que s'il est vraiment bloqué (ses consignes, dans `jervis/brain.py`).

**Ce qu'il comprend de mieux :**

- **Les lettres épelées** : « Jessica T, A, double L » devient « Jessica TALL » (au moins trois lettres, en
  majuscules ; « plan B » ne bouge pas). Tant que tu épelles (« T A… »), il attend la suite au lieu de répondre.
- **Une phrase coupée puis reprise** (« interrompu », « il a continué sa phrase ») : il recolle les morceaux, et si
  tu l'as coupé, il répond à ta nouvelle phrase sans reprendre la tâche à zéro ni s'excuser.
- **Le bruit d'une vidéo** en russe, en arabe, en chinois… (plus de la moitié des lettres hors de l'alphabet latin)
  n'est jamais pour lui : il l'ignore, et ta tâche en cours n'est pas perdue.
Test sans micro : `PYTHONPATH=. .venv/bin/python tests/test_naturel.py`. La ligne directe de la voix, sans
Internet : `PYTHONPATH=. .venv/bin/python tests/test_ligne.py`. Sa voix locale, sans le modèle :
`tests/test_voix.py` (l'apprentissage de ta voix, ses empreintes et le diagnostic). Les chiffres écrits en mots : `tests/test_oral.py`.
Cursor, la corbeille, effacer sa mémoire et les autorisations, sans rien ouvrir ni jeter : `tests/test_mac.py`.
Les agents de fond (plusieurs en parallèle, leur état, leur histoire) : `tests/test_fond.py`.
Le chat écrit, le carnet de leçons et la ronde (sans Claude ni réseau) : `tests/test_bras_droit.py`.
Le graphe (sans Claude ni Ollama, la base embarquée dans un dossier temporaire) : `tests/test_graphe.py`.
Ses documents et Notion (dossier temporaire, faux client Claude) : `tests/test_documents.py`.
Créer des comptes (faux Trousseau, faux presse-papiers, fausse base de Messages) : `tests/test_comptes.py`.
Toute la suite d'un coup : `.venv/bin/python -m pytest` (chaque fichier tourne à part, comme ci-dessus).

Ce qu'il fait seul, ce qu'il te demande et ce qui est interdit se règle dans `jervis/permissions.py`.
Ses outils maison (mémoire, écran, iMessage, Cursor, corbeille, autorisations…) sont dans `jervis/outils.py`.

## Vie privée

- Ta voix ne quitte jamais le Mac : détection, transcription et empreinte vocale (`voix.npy`) sont calculées en local.
- Ce que tu dis (en texte), tes mails, ton agenda, tes documents Drive, tes pages Notion, ta mémoire, les captures d'écran et ce qu'il
  lit dans tes apps partent vers Claude pour qu'il réponde.
- Ce que tu lui écris sur le tableau de bord suit le même chemin que ce que tu dis : la page et Steve se parlent sur
  ce Mac (127.0.0.1), puis le texte part vers Claude pour qu'il réponde, comme une phrase dite.
- Vos conversations (`espace/conversations.jsonl`), sa mémoire, son carnet (`lecons.md`), son graphe (`espace/graphe.db`,
  dont les empreintes sont calculées par Ollama en local) et les enregistrements de screenpipe (`~/.screenpipe`)
  restent sur le Mac. Il n'en envoie à Claude que les morceaux utiles à la question. Pareil pour tes documents
  (`documents/`) : seule leur liste est dans ses consignes, et il n'en lit un que quand ta demande le demande.
- Les mots de passe qu'il crée ne partent jamais chez Claude : tirés et rangés sur le Mac (Trousseau), ils passent
  par le presse-papiers 90 secondes au plus. Si tu as un historique du presse-papiers (Raycast, Paste, Maccy),
  exclus-en Jervis ou les mots de passe. D'un SMS, seul le code (et l'expéditeur) part chez Claude, pas le message.
- Sa voix est fabriquée sur le Mac : le texte de ses réponses ne part nulle part pour être lu. Si elle rate une
  phrase ou ne se charge pas, cette phrase-là passe par le service de synthèse de Microsoft Edge ; c'est aussi le cas
  de toutes ses phrases si tu choisis Rémy. Mets `JERVIS_VOIX_NEURONALE=non` pour que rien n'y parte jamais.
