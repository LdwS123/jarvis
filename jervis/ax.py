"""L'accessibilité de macOS (AXUIElement), appelée directement depuis Python : trouver un bouton d'une app par son
identifiant, appuyer dessus, sortir une fenêtre du Dock.

Pourquoi pas AppleScript (System Events) ? Fouiller une fenêtre entière (« entire contents ») y prend des secondes ;
ici c'est le même appel que fait VoiceOver, en quelques millisecondes. Il faut l'autorisation Réglages →
Confidentialité → Accessibilité pour « Jervis » (la même que pour piloter les apps). Hors macOS, ou sans
l'autorisation, tout renvoie None / False : rien ne plante.
"""

from __future__ import annotations

import ctypes
import subprocess
from collections import deque
from typing import Callable, Iterator

_UTF8 = 0x08000100       # kCFStringEncodingUTF8
_ax = _cf = None
_vrai = _faux = None
_TYPES: dict[str, int] = {}


def _bibliotheques():
    global _ax, _cf, _vrai, _faux
    if _ax is None:
        ax = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        p = ctypes.c_void_p
        ax.AXIsProcessTrusted.restype = ctypes.c_bool
        ax.AXUIElementCreateApplication.argtypes = [ctypes.c_int32]
        ax.AXUIElementCreateApplication.restype = p
        ax.AXUIElementCopyAttributeValue.argtypes = [p, p, ctypes.POINTER(p)]
        ax.AXUIElementCopyAttributeValue.restype = ctypes.c_int32
        ax.AXUIElementSetAttributeValue.argtypes = [p, p, p]
        ax.AXUIElementSetAttributeValue.restype = ctypes.c_int32
        ax.AXUIElementPerformAction.argtypes = [p, p]
        ax.AXUIElementPerformAction.restype = ctypes.c_int32
        ax.AXUIElementSetMessagingTimeout.argtypes = [p, ctypes.c_float]
        ax.AXUIElementSetMessagingTimeout.restype = ctypes.c_int32
        cf.CFStringCreateWithCString.argtypes = [p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFStringCreateWithCString.restype = p
        cf.CFStringGetCString.argtypes = [p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        cf.CFStringGetCString.restype = ctypes.c_bool
        cf.CFArrayGetCount.argtypes = [p]
        cf.CFArrayGetCount.restype = ctypes.c_long
        cf.CFArrayGetValueAtIndex.argtypes = [p, ctypes.c_long]
        cf.CFArrayGetValueAtIndex.restype = p
        cf.CFGetTypeID.argtypes = [p]
        cf.CFGetTypeID.restype = ctypes.c_ulong
        for nom in ("CFStringGetTypeID", "CFArrayGetTypeID", "CFBooleanGetTypeID"):
            getattr(cf, nom).restype = ctypes.c_ulong
            _TYPES[nom] = getattr(cf, nom)()
        cf.CFRetain.argtypes = [p]
        cf.CFRetain.restype = p
        cf.CFRelease.argtypes = [p]
        cf.CFEqual.argtypes = [p, p]
        cf.CFEqual.restype = ctypes.c_bool
        _vrai = p.in_dll(cf, "kCFBooleanTrue")
        _faux = p.in_dll(cf, "kCFBooleanFalse")
        _ax, _cf = ax, cf
    return _ax, _cf


def _cle(texte: str):
    _, cf = _bibliotheques()
    return cf.CFStringCreateWithCString(None, texte.encode("utf-8"), _UTF8)


def _en_texte(ref) -> str | None:
    _, cf = _bibliotheques()
    if not ref or cf.CFGetTypeID(ref) != _TYPES["CFStringGetTypeID"]:
        return None
    tampon = ctypes.create_string_buffer(2048)
    return tampon.value.decode("utf-8", "replace") if cf.CFStringGetCString(ref, tampon, len(tampon), _UTF8) else None


class Element:
    """Un élément d'interface (une app, une fenêtre, un bouton). Libéré quand Python l'oublie."""

    def __init__(self, ref, garder: bool = False) -> None:
        _, cf = _bibliotheques()
        self.ref = cf.CFRetain(ref) if garder else ref

    def __del__(self) -> None:
        if self.ref and _cf is not None:
            _cf.CFRelease(self.ref)
            self.ref = None

    def __eq__(self, autre: object) -> bool:
        """Le même élément à l'écran (deux lectures du même bouton sont égales ; le bouton d'une autre
        discussion, non)."""
        return isinstance(autre, Element) and bool(self.ref and autre.ref) and bool(_cf.CFEqual(self.ref, autre.ref))

    __hash__ = None  # type: ignore[assignment]

    def _copier(self, attribut: str):
        ax, cf = _bibliotheques()
        cle, valeur = _cle(attribut), ctypes.c_void_p()
        try:
            erreur = ax.AXUIElementCopyAttributeValue(self.ref, cle, ctypes.byref(valeur))
        finally:
            cf.CFRelease(cle)
        return valeur.value if erreur == 0 else None

    def texte(self, attribut: str) -> str | None:
        """AXIdentifier, AXRole, AXDescription, AXTitle… (None si l'élément ne l'a pas)."""
        ref = self._copier(attribut)
        try:
            return _en_texte(ref)
        finally:
            if ref:
                _cf.CFRelease(ref)

    def booleen(self, attribut: str) -> bool | None:
        ref = self._copier(attribut)
        if not ref:
            return None
        try:
            return ref == _vrai.value if _cf.CFGetTypeID(ref) == _TYPES["CFBooleanGetTypeID"] else None
        finally:
            _cf.CFRelease(ref)

    def liste(self, attribut: str) -> list["Element"]:
        """AXChildren, AXWindows… : les éléments, chacun gardé pour lui."""
        ref = self._copier(attribut)
        if not ref:
            return []
        try:
            if _cf.CFGetTypeID(ref) != _TYPES["CFArrayGetTypeID"]:
                return []
            return [Element(_cf.CFArrayGetValueAtIndex(ref, i), garder=True) for i in range(_cf.CFArrayGetCount(ref))]
        finally:
            _cf.CFRelease(ref)

    def mettre(self, attribut: str, oui: bool) -> bool:
        ax, cf = _bibliotheques()
        cle = _cle(attribut)
        try:
            return ax.AXUIElementSetAttributeValue(self.ref, cle, (_vrai if oui else _faux).value) == 0
        finally:
            cf.CFRelease(cle)

    def appuyer(self) -> bool:
        ax, cf = _bibliotheques()
        cle = _cle("AXPress")
        try:
            return ax.AXUIElementPerformAction(self.ref, cle) == 0
        finally:
            cf.CFRelease(cle)


def autorise() -> bool:
    """macOS laisse-t-il ce programme lire et piloter les autres apps (Accessibilité) ?"""
    try:
        ax, _ = _bibliotheques()
        return bool(ax.AXIsProcessTrusted())
    except Exception:
        return False


def pid_de(processus: str) -> int | None:
    """Le pid de l'app (son nom de processus exact : « WhatsApp »), ou None si elle ne tourne pas."""
    try:
        sortie = subprocess.run(["pgrep", "-x", processus], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    pids = [int(p) for p in sortie.split() if p.isdigit()]
    return min(pids) if pids else None


def application(pid: int, patience: float = 2.0) -> Element | None:
    try:
        ax, _ = _bibliotheques()
    except Exception:
        return None
    ref = ax.AXUIElementCreateApplication(pid)
    if not ref:
        return None
    ax.AXUIElementSetMessagingTimeout(ref, patience)  # une app qui ne répond plus ne bloque pas Steve
    return Element(ref)


def parcourir(racine: Element, profondeur: int = 40, maxi: int = 6000) -> Iterator[Element]:
    """Tous les éléments sous `racine`, en largeur (les boutons de la barre du bas sont vite trouvés)."""
    file: deque[tuple[Element, int]] = deque([(racine, 0)])
    vus = 0
    while file and vus < maxi:
        element, niveau = file.popleft()
        vus += 1
        yield element
        if niveau < profondeur:
            file.extend((e, niveau + 1) for e in element.liste("AXChildren"))


def trouver(racine: Element, identifiant: str | None = None,
            condition: Callable[[Element], bool] | None = None) -> Element | None:
    """Le premier élément qui a cet AXIdentifier (ou qui remplit `condition`)."""
    for element in parcourir(racine):
        if identifiant is not None and element.texte("AXIdentifier") == identifiant:
            return element
        if condition is not None and condition(element):
            return element
    return None
