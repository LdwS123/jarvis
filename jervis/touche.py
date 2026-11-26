"""La touche fn (🌐) : un appui, et il t'écoute, sans dire « Steve ».

Deux façons :
  - tape fn (seule) puis parle : il fait son bip et attend ta demande, comme après « Salut Steve » ;
  - garde fn enfoncée pendant que tu parles : ta phrase est ta demande, telle quelle.

Un fn avec une autre touche (fn+F1, fn+flèche, fn+clic) ne compte pas : ce sont tes raccourcis.

Rien ne quitte le Mac : macOS lui dit seulement « fn descend », « fn remonte », « une autre touche ». Ce que tu
tapes n'est ni lu ni gardé (l'écoute ne regarde que les touches de modification et le code de fn).

Réglages :
  - JERVIS_TOUCHE_FN=non pour l'éteindre.
  - Réglages Système → Clavier → « Appuyer sur 🌐 » : « Ne rien faire », sinon macOS ouvre aussi la dictée ou les
    émojis à chaque appui.
  - macOS demande l'autorisation « Surveillance de l'entrée » pour Jervis (Confidentialité et sécurité).
"""

from __future__ import annotations

import ctypes
import math
import threading
import time
from typing import Callable

CODES_FN = {63, 179}            # kVK_Function, et la touche 🌐 de certains claviers
DRAPEAU_FN = 0x800000            # kCGEventFlagMaskSecondaryFn
AUTRES_MODIFICATEURS = 0x20000 | 0x40000 | 0x80000 | 0x100000  # maj, ctrl, option, cmd
TENUE_MAX_S = 30.0               # au-delà, fn est restée coincée (ou posée là) : ce n'est pas un appel

# Les genres d'événements de macOS (CGEventType)
SOURIS = {1, 3, 25}              # clic gauche, droit, autre
TOUCHE = 10                      # kCGEventKeyDown
MODIFICATEURS = 12               # kCGEventFlagsChanged
COUPEE = {0xFFFFFFFE, 0xFFFFFFFF}  # l'écoute coupée par macOS (trop lente, ou entrée sécurisée) : on la relance


class Fn:
    """Ce que fait la touche fn, d'après les événements du clavier. Testable sans clavier (evenement)."""

    def __init__(self, horloge: Callable[[], float] = time.monotonic) -> None:
        self._horloge = horloge
        self.tapee = threading.Event()   # fn tapée seule : sort de la veille (lu par le fil du micro)
        self._appui: float | None = None  # fn enfoncée depuis (seule jusqu'ici), sinon None
        self._autre = False               # une autre touche pendant qu'elle est enfoncée
        self._relachee = -math.inf        # la dernière fois qu'elle est remontée, seule
        self._appui_precedent = math.inf  # … et quand cet appui-là avait commencé
        self._ignorer_relache = False     # la phrase dite fn enfoncée est déjà prise : sa remontée ne compte pas

    def evenement(self, genre: int, code: int = 0, drapeaux: int = 0) -> None:
        maintenant = self._horloge()
        if genre in SOURIS or genre == TOUCHE:
            if self._appui is not None:
                self._autre = True  # fn+F1, fn+flèche, fn+clic : un raccourci
            return
        if genre != MODIFICATEURS:
            return
        if code not in CODES_FN:
            if self._appui is not None and drapeaux & AUTRES_MODIFICATEURS:
                self._autre = True  # fn+cmd… : un raccourci aussi
            return
        if drapeaux & DRAPEAU_FN:
            if self._appui is None:
                self._appui, self._autre = maintenant, bool(drapeaux & AUTRES_MODIFICATEURS)
            return
        if self._appui is None:
            return
        seule = not self._autre and maintenant - self._appui <= TENUE_MAX_S
        self._appui_fini, self._appui = self._appui, None
        if seule:
            self._relachee, self._appui_precedent = maintenant, self._appui_fini
            if self._ignorer_relache:
                self._ignorer_relache = False
            else:
                self.tapee.set()

    def tenue_depuis(self, depuis: float) -> bool:
        """fn a servi pendant une phrase qui a commencé à `depuis` : encore enfoncée (seule), ou remontée après."""
        if self._appui is not None and not self._autre and self._horloge() - self._appui <= TENUE_MAX_S:
            return True
        return self._relachee >= depuis

    def tenue_tout_du_long(self, debut: float, fin: float) -> bool:
        """fn enfoncée (seule) avant `debut` et jusqu'à `fin` au moins, sans lâcher : la preuve que c'est toi qui
        parlais pendant tout ce temps (pendant un appel, un fn tapé au vol pendant qu'un autre parle ne l'est pas)."""
        if fin <= debut:
            return False  # une phrase trop courte pour le prouver (sinon un fn tapé au vol suffirait)
        if self._appui is not None:
            return not self._autre and self._appui <= debut and self._horloge() - self._appui <= TENUE_MAX_S
        return self._appui_precedent <= debut and self._relachee >= fin

    def prendre(self) -> bool:
        """fn a-t-elle été tapée (et pas encore prise) ? La prend : la prochaine veille ne la verra plus."""
        tapee = self.tapee.is_set()
        self.tapee.clear()
        return tapee

    def phrase_prise(self) -> None:
        """La phrase dite fn enfoncée est ta demande : sa remontée (après coup) ne relance pas d'écoute."""
        self.tapee.clear()
        if self._appui is not None:
            self._ignorer_relache = True


class L_un_ou_l_autre:
    """Deux threading.Event lus comme un seul (le micro ne regarde que is_set)."""

    def __init__(self, *evenements: threading.Event) -> None:
        self._evenements = evenements

    def is_set(self) -> bool:
        return any(e.is_set() for e in self._evenements)


FN = Fn()

_CADRES = "/System/Library/Frameworks/"
_RAPPEL = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p)
_garde: list = []  # le rappel et l'écoute, gardés en vie tant que le fil tourne


def _graphiques():
    cg = ctypes.cdll.LoadLibrary(_CADRES + "CoreGraphics.framework/CoreGraphics")
    cg.CGPreflightListenEventAccess.restype = ctypes.c_bool
    cg.CGRequestListenEventAccess.restype = ctypes.c_bool
    return cg


def autorisee() -> bool:
    """macOS laisse-t-il Jervis voir la touche fn (« Surveillance de l'entrée ») ? Sans fenêtre."""
    try:
        return bool(_graphiques().CGPreflightListenEventAccess())
    except (OSError, AttributeError):
        return False


def _ecouter(fn: Fn, pret: threading.Event, resultat: list) -> None:
    """Le fil de la touche : une écoute du clavier, en lecture seule, et sa boucle macOS (ne revient jamais)."""
    try:
        cg = _graphiques()
        cf = ctypes.cdll.LoadLibrary(_CADRES + "CoreFoundation.framework/CoreFoundation")
        cg.CGEventTapCreate.restype = ctypes.c_void_p
        cg.CGEventTapCreate.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint64,
                                        _RAPPEL, ctypes.c_void_p]
        cg.CGEventTapEnable.argtypes = [ctypes.c_void_p, ctypes.c_bool]
        cg.CGEventGetFlags.restype = ctypes.c_uint64
        cg.CGEventGetFlags.argtypes = [ctypes.c_void_p]
        cg.CGEventGetIntegerValueField.restype = ctypes.c_int64
        cg.CGEventGetIntegerValueField.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        cf.CFMachPortCreateRunLoopSource.restype = ctypes.c_void_p
        cf.CFMachPortCreateRunLoopSource.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
        cf.CFRunLoopGetCurrent.restype = ctypes.c_void_p
        cf.CFRunLoopAddSource.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        ecoute: list = [None]

        def rappel(_proxy, genre, evenement, _info):
            try:
                if genre in COUPEE:
                    cg.CGEventTapEnable(ecoute[0], True)
                elif genre == MODIFICATEURS:  # seulement le code de la touche de modification, et ses drapeaux
                    fn.evenement(genre, cg.CGEventGetIntegerValueField(evenement, 9), cg.CGEventGetFlags(evenement))
                else:
                    fn.evenement(genre)  # une touche ou un clic : on ne regarde même pas laquelle
            except Exception:
                pass  # jamais d'exception dans un rappel de macOS
            return evenement

        masque = (1 << MODIFICATEURS) | (1 << TOUCHE) | sum(1 << s for s in SOURIS)
        c_rappel = _RAPPEL(rappel)
        # session (1), en tête (0), écoute seule (1) : il voit passer, il ne bloque ni ne change rien
        ecoute[0] = cg.CGEventTapCreate(1, 0, 1, masque, c_rappel, None)
        if not ecoute[0]:
            resultat.append(False)
            pret.set()
            return
        _garde.extend([c_rappel, ecoute])
        source = cf.CFMachPortCreateRunLoopSource(None, ecoute[0], 0)
        mode = ctypes.c_void_p.in_dll(cf, "kCFRunLoopCommonModes")
        cf.CFRunLoopAddSource(cf.CFRunLoopGetCurrent(), source, mode)
        cg.CGEventTapEnable(ecoute[0], True)
        resultat.append(True)
        pret.set()
        cf.CFRunLoopRun()
    except (OSError, AttributeError, ValueError):
        resultat.append(False)
        pret.set()


def demarrer(fn: Fn = FN) -> str | None:
    """Branche la touche fn. None si c'est fait, sinon ce qui manque (dit tel quel au démarrage)."""
    if not autorisee():
        try:
            _graphiques().CGRequestListenEventAccess()  # macOS ajoute Jervis à la liste (une fois)
        except (OSError, AttributeError):
            pass
        return ("Pour la touche fn : Réglages Système → Confidentialité et sécurité → Surveillance de l'entrée → "
                "allume Jervis, puis relance-moi.")
    pret, resultat = threading.Event(), []
    threading.Thread(target=_ecouter, args=(fn, pret, resultat), name="touche-fn", daemon=True).start()
    pret.wait(5)
    return None if resultat and resultat[0] else "La touche fn ne répond pas (macOS a refusé l'écoute du clavier)."


def reglage_systeme() -> str | None:
    """Le conseil à dire si macOS fait aussi autre chose de fn (dictée, émojis, sources de saisie)."""
    import subprocess
    try:
        fait = subprocess.run(["defaults", "read", "com.apple.HIToolbox", "AppleFnUsageType"],
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if fait == "0":  # (sans réglage : les émojis, sur les Mac récents)
        return None
    return ("Réglages Système → Clavier → « Appuyer sur 🌐 » : choisis « Ne rien faire », sinon macOS ouvre aussi "
            "autre chose à chaque appui sur fn.")
