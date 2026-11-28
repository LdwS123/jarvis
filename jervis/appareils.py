"""Les appareils son du Mac, demandés directement à macOS (CoreAudio) : le micro et la sortie choisis par défaut,
et comment chacun est relié (intégré, Bluetooth, iPhone…).

Pourquoi pas sounddevice ? PortAudio garde la liste des appareils vue à son démarrage : il ne sait pas qu'un
casque vient d'être branché. CoreAudio, lui, le sait tout de suite. Ce ne sont que des questions posées au système
(sauf regler_micro, le temps d'un appel de Steve) : aucun micro n'est ouvert, rien ne sort du Mac. Hors macOS
(ou si CoreAudio ne répond pas), tout renvoie None / [].
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass

# Les appareils « loin de toi » : avec eux comme micro, on préfère celui du Mac (réglage MICRO = "auto").
#   blue / blea : Bluetooth (AirPods, casque) — leur micro les ferait passer en « qualité téléphone » ;
#   ccwd : le micro de ton iPhone (Continuité), peut-être dans ta poche.
LOIN = ("blue", "blea", "ccwd")
INTEGRE = "bltn"

_SYSTEME = 1                                   # kAudioObjectSystemObject
_UTF8 = 0x08000100                             # kCFStringEncodingUTF8


@dataclass
class Appareil:
    id: int
    nom: str
    transport: str        # "bltn" (intégré), "blue" (Bluetooth), "usb ", "ccwd" (iPhone)…
    entree: bool          # a un micro


class _Adresse(ctypes.Structure):
    _fields_ = [("selecteur", ctypes.c_uint32), ("portee", ctypes.c_uint32), ("element", ctypes.c_uint32)]


def _code(texte: str) -> int:
    return int.from_bytes(texte.encode("ascii"), "big")


def _texte(code: int) -> str:
    return code.to_bytes(4, "big").decode("ascii", "replace")


_ca = _cf = None


def _bibliotheques():
    global _ca, _cf
    if _ca is None:
        ca = ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        p, u32 = ctypes.c_void_p, ctypes.c_uint32
        ca.AudioObjectGetPropertyData.argtypes = [u32, p, u32, p, p, p]
        ca.AudioObjectGetPropertyData.restype = ctypes.c_int32
        ca.AudioObjectGetPropertyDataSize.argtypes = [u32, p, u32, p, p]
        ca.AudioObjectGetPropertyDataSize.restype = ctypes.c_int32
        ca.AudioObjectSetPropertyData.argtypes = [u32, p, u32, p, u32, p]
        ca.AudioObjectSetPropertyData.restype = ctypes.c_int32
        cf.CFStringGetCString.argtypes = [p, ctypes.c_char_p, ctypes.c_long, u32]
        cf.CFStringGetCString.restype = ctypes.c_bool
        cf.CFRelease.argtypes = [p]
        _ca, _cf = ca, cf
    return _ca, _cf


def _lire(objet: int, selecteur: str, type_c, portee: str = "glob"):
    """Une propriété CoreAudio d'une taille fixe (un identifiant, un code, un pointeur), ou None."""
    ca, _ = _bibliotheques()
    adresse = _Adresse(_code(selecteur), _code(portee), 0)
    valeur, taille = type_c(), ctypes.c_uint32(ctypes.sizeof(type_c))
    if ca.AudioObjectGetPropertyData(objet, ctypes.byref(adresse), 0, None, ctypes.byref(taille),
                                     ctypes.byref(valeur)) != 0:
        return None
    return valeur.value


def _taille(objet: int, selecteur: str, portee: str = "glob") -> int:
    ca, _ = _bibliotheques()
    adresse = _Adresse(_code(selecteur), _code(portee), 0)
    taille = ctypes.c_uint32(0)
    if ca.AudioObjectGetPropertyDataSize(objet, ctypes.byref(adresse), 0, None, ctypes.byref(taille)) != 0:
        return 0
    return taille.value


def _nom(objet: int) -> str:
    return _chaine(objet, "lnam")


def _chaine(objet: int, selecteur: str) -> str:
    """Une propriété texte (CFString) : le nom d'un appareil (lnam), l'identifiant d'une app (pbid)…"""
    _, cf = _bibliotheques()
    ref = _lire(objet, selecteur, ctypes.c_void_p)
    if not ref:  # (un pointeur nul ferait planter CoreFoundation)
        return ""
    tampon = ctypes.create_string_buffer(512)
    try:
        ok = cf.CFStringGetCString(ref, tampon, len(tampon), _UTF8)
    finally:
        cf.CFRelease(ref)
    return tampon.value.decode("utf-8", "replace") if ok else ""


def _appareil(objet: int) -> Appareil:
    transport = _lire(objet, "tran", ctypes.c_uint32)
    return Appareil(objet, _nom(objet), _texte(transport) if transport else "",
                    _taille(objet, "stm#", "inpt") > 0)


def par_defaut() -> tuple[Appareil | None, Appareil | None]:
    """(le micro, la sortie) que macOS utilise en ce moment."""
    try:
        micro, sortie = _lire(_SYSTEME, "dIn ", ctypes.c_uint32), _lire(_SYSTEME, "dOut", ctypes.c_uint32)
        return (_appareil(micro) if micro else None), (_appareil(sortie) if sortie else None)
    except Exception:
        return None, None


def tous() -> list[Appareil]:
    """Tous les appareils son branchés en ce moment."""
    try:
        n = _taille(_SYSTEME, "dev#") // 4
        if not n:
            return []
        ca, _ = _bibliotheques()
        ids, taille = (ctypes.c_uint32 * n)(), ctypes.c_uint32(4 * n)
        adresse = _Adresse(_code("dev#"), _code("glob"), 0)
        if ca.AudioObjectGetPropertyData(_SYSTEME, ctypes.byref(adresse), 0, None, ctypes.byref(taille), ids) != 0:
            return []
        return [_appareil(i) for i in ids[: taille.value // 4]]
    except Exception:
        return []


def regler_micro(objet: int) -> bool:
    """Fait de cet appareil le micro de macOS (celui que prennent les apps, FaceTime compris). Le seul réglage que
    ce module change : pendant que Steve t'appelle (telephone.py), remis comme avant à la fin de l'appel."""
    try:
        ca, _ = _bibliotheques()
        adresse = _Adresse(_code("dIn "), _code("glob"), 0)
        valeur = ctypes.c_uint32(objet)
        return ca.AudioObjectSetPropertyData(_SYSTEME, ctypes.byref(adresse), 0, None, ctypes.sizeof(valeur),
                                             ctypes.byref(valeur)) == 0
    except Exception:
        return False


def choisir_micro(reglage: str, defaut: Appareil | None, appareils: list[Appareil]) -> str | None:
    """Le nom du micro à ouvrir, ou None pour celui de macOS.
    "systeme" : toujours celui de macOS. "auto" : celui de macOS, sauf s'il est loin de toi (AirPods, casque
    Bluetooth, iPhone) : alors celui du Mac, et tes AirPods gardent leur bon son. Autre chose : un bout du nom
    du micro voulu (« MacBook », « Yeti »…) ; s'il n'est pas branché, celui de macOS."""
    reglage = (reglage or "auto").strip()
    micros = [a for a in appareils if a.entree and a.nom]
    if reglage.casefold() == "systeme":
        return None
    if reglage.casefold() == "auto":
        if defaut is None or defaut.transport not in LOIN:
            return None
        integre = next((a for a in micros if a.transport == INTEGRE), None)
        return integre.nom if integre else None
    voulu = next((a for a in micros if reglage.casefold() in a.nom.casefold()), None)
    return voulu.nom if voulu else None
