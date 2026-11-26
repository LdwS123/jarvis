#!/bin/zsh
# Donner à Steve l'accès à tout le Mac. Double-clique ce fichier, ou : zsh outils/autorisations.command
#
# macOS ne laisse personne cocher ces cases à ta place : ce script ouvre les bons écrans de Réglages, un par
# un, et te dit quel interrupteur allumer. Les autorisations vont à « Jervis » (l'app de Steve).
set -euo pipefail
cd "$(dirname "$0")/.."

APP="$PWD/Jervis.app"
REGLAGES="x-apple.systempreferences:com.apple.preference.security?Privacy_"

etape() {  # etape <volet> <nom dans Réglages> <à quoi ça sert>
  print -P "\n%F{cyan}$2%f : $3"
  open "$REGLAGES$1"
  print "  Allume l'interrupteur « Jervis ». S'il n'est pas dans la liste : bouton +, puis choisis"
  print "  $APP (la fenêtre du Finder qui vient de s'ouvrir te la montre : tu peux aussi la glisser dans la liste)."
  read "?  Entrée quand c'est fait (ou Entrée tout de suite pour passer)… "
}

print -P "%F{cyan}Les accès de Steve%f : six écrans, une minute."
open -R "$APP"

etape AllFiles      "Accès complet au disque"  "lire, ranger et mettre à la corbeille n'importe quel fichier du Mac."
etape Accessibility "Accessibilité"            "cliquer et taper à ta place (envoyer un prompt dans Cursor, piloter une app)."
etape ScreenCapture "Enregistrement de l'écran et de l'audio système"  "voir ce qu'il y a à l'écran."
etape ListenEvent   "Surveillance de l'entrée"  "t'écouter dès que tu appuies sur fn (🌐), sans dire son nom."
etape Automation    "Automatisation"           "piloter Finder, Messages, Notes, Rappels, Contacts, Chrome. Déplie « Jervis » et allume tout. (La liste se remplit à mesure que Steve s'en sert : c'est normal si elle est courte.)"
etape Contacts      "Contacts"                 "retrouver un numéro quand tu dis « écris à Marie »."

print -P "\n%F{cyan}C'est fait.%f macOS n'applique certaines autorisations qu'au redémarrage de l'app."
read "reponse?Relancer Steve maintenant ? (o/n) "
if [[ "$reponse" == [oOyY]* ]]; then
  pkill -f "Jervis.app/Contents" || true
  pkill -f "python -m jervis" || true
  sleep 2
  open -a "$APP"
  print "Steve redémarre (30 secondes). Dis-lui ensuite : « Steve, vérifie tes autorisations. »"
else
  print "\nPour le relancer plus tard :"
  print '  pkill -f "Jervis.app/Contents"; pkill -f "python -m jervis"; sleep 2; open -a ~/Jervis/Jervis.app'
fi
