"""Fait tourner les tests de Steve avec pytest (cd ~/Jervis && .venv/bin/python -m pytest).

Chaque fichier tests/test_*.py est un script autonome : il compte ses vérifications, les affiche, et quitte avec
os._exit (0 si tout passe). On ne peut donc pas l'importer dans pytest : il le tuerait. Ici, chaque script tourne
dans son propre processus Python, et pytest le compte comme un test — vert si le script sort à 0, rouge sinon,
avec la fin de ce qu'il a affiché.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
PATIENCE_S = 180   # un script qui tourne plus longtemps est bloqué (chacun a aussi sa propre minuterie)


def pytest_collect_file(parent, file_path: Path):
    if file_path.suffix == ".py" and file_path.name.startswith("test_"):
        return Script.from_parent(parent, path=file_path)
    return None


class Script(pytest.File):
    def collect(self):
        yield Lancement.from_parent(self, name=self.path.stem)


class Lancement(pytest.Item):
    def runtest(self) -> None:
        # JERVIS_REGLAGES=non : les tests ne dépendent pas de tes choix de la page Réglages (reglages.json).
        env = {**os.environ, "PYTHONPATH": str(RACINE), "JERVIS_REGLAGES": "non"}
        try:
            fini = subprocess.run([sys.executable, str(self.path)], cwd=RACINE, env=env, capture_output=True,
                                  text=True, timeout=PATIENCE_S)
        except subprocess.TimeoutExpired as e:
            raise EchecDuScript(f"bloqué au bout de {PATIENCE_S} s", (e.stdout or b"").decode(errors="replace")) from None
        sortie = fini.stdout + ("\n" + fini.stderr if fini.stderr.strip() else "")
        if fini.returncode != 0:
            raise EchecDuScript(f"code de sortie {fini.returncode}", sortie)
        print(sortie.strip().splitlines()[-1] if sortie.strip() else "")

    def repr_failure(self, excinfo):
        if isinstance(excinfo.value, EchecDuScript):
            raison, sortie = excinfo.value.args
            return f"{self.path.name} : {raison}\n" + "\n".join(sortie.strip().splitlines()[-60:])
        return super().repr_failure(excinfo)

    def reportinfo(self):
        return self.path, 0, f"script : {self.path.name}"


class EchecDuScript(Exception):
    pass
