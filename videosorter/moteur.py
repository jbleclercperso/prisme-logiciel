"""Le moteur de reparation PixMedic, partage avec le site pixmedic.com et le
futur logiciel : un seul moteur, dans son propre depot (pixmedic-moteur-
reparation), qui vit ici en sous-module git (dossier « moteur »).

Prisme ne l'appelle que par sa porte d'entree publique (reparateur.api) :
repair_file, diagnose.

Depuis les sources, le moteur s'importe directement. Le programme vendu
n'emporte que ses sources (dossier « moteur-src », quelques centaines de Ko) :
ses bibliotheques (numba, OpenCV, Pillow, PyAV : environ 150 Mo) ne se
telechargent qu'au premier besoin, dans le Python du Labo IA (iapython), et
le moteur y tourne, dans un processus a part (moteur_child). Comme pour le
labo, PRISME_IA_PYTHON fait prendre ce chemin depuis les sources.

Ses fichiers de passage et le cache de numba vont dans le dossier de Prisme,
jamais dans celui du programme (qui peut etre en lecture seule).
"""
from __future__ import annotations

import atexit
import os
import queue
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

from . import iapython

PHOTO_EXT = (".jpg", ".jpeg", ".jfif")
# Ce que le moteur importe : tant que l'un manque, il n'est pas installe.
MODULES = ("numpy", "numba", "cv2", "PIL", "av")
REQUIREMENTS = ("numpy", "numba", "opencv-python-headless", "Pillow", "av")
DOWNLOAD_SIZE = "environ 150 Mo"
# Le premier appel compile le decodeur JPEG (numba) : du temps, une fois.
START_TIMEOUT = 300.0
_ready = None
_error = ""


def external() -> bool:
    return iapython.external()


def engine_src() -> Path:
    """Le dossier qui contient le paquet `reparateur`."""
    if iapython.FROZEN:
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "moteur-src"
    return Path(__file__).resolve().parent.parent / "moteur"


def _env() -> None:
    from .config import APP_DIR
    os.environ.setdefault("NUMBA_CACHE_DIR", str(APP_DIR / "moteur-cache"))
    os.environ.setdefault("REPARATEUR_TRAVAIL_RACINE", str(APP_DIR / "moteur-travail"))


def _setup() -> bool:
    global _ready, _error
    if _ready is not None:
        return _ready
    _env()
    if not (engine_src() / "reparateur" / "api.py").is_file():
        _ready, _error = False, f"sources du moteur absentes ({engine_src()})"
        return _ready
    if external():
        _ready = installed()
        _error = "" if _ready else f"moteur de réparation pas encore installé ({DOWNLOAD_SIZE} à télécharger)"
        return _ready
    here = str(engine_src())
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        from reparateur import api  # noqa: F401
        _ready = True
    except Exception as exc:                                 # noqa: BLE001
        _ready = False
        _error = f"{type(exc).__name__} : {exc}"
    return _ready


def installed() -> bool:
    """Programme vendu : les bibliotheques du moteur sont-elles dans le Python
    du labo ? (Sans rien importer.)"""
    return iapython.prepared() and all(iapython.has(m) for m in MODULES)


def available() -> bool:
    """Le moteur peut-il reparer tout de suite ?"""
    return _setup()


def can_install() -> bool:
    """Programme vendu, moteur pas encore la : peut-on le telecharger ?"""
    _setup()
    return (not _ready and external() and os.name == "nt"
            and (engine_src() / "reparateur" / "api.py").is_file())


def offered() -> bool:
    """Le proposer dans les fenetres : pret, ou a telecharger au premier usage."""
    return available() or can_install()


def why_missing() -> str:
    _setup()
    return _error


def install(log) -> None:
    """Telecharge les bibliotheques du moteur dans le Python du labo (et ce
    Python, s'il n'est pas encore la). Quelques minutes : dans un fil."""
    global _ready
    iapython.prepare(log)
    log(f"Téléchargement du moteur de réparation ({DOWNLOAD_SIZE}, une fois)…")
    code = iapython._stream([str(iapython.python()), "-m", "pip", "install",
                             "--disable-pip-version-check", "--no-warn-script-location",
                             *REQUIREMENTS], log)
    _ready = None
    if code != 0 or not available():
        raise RuntimeError(f"le moteur ne s'est pas installé (pip : code {code})")
    log("Moteur de réparation prêt.")


class _Remote:
    """reparateur.api, vu de Prisme, quand le moteur tourne dans le Python du
    labo : memes noms (diagnose, repair_file), memes reponses."""

    def __init__(self):
        self._lock = threading.Lock()
        self._process = self._requests = self._answers = None

    def _wait(self, timeout: float):
        waited = 0.0
        while True:
            try:
                return self._answers.get(timeout=1)
            except queue.Empty:
                waited += 1
                if not self._process.is_alive():
                    self._process = None
                    raise RuntimeError("le moteur de réparation s'est arrêté "
                                       f"(voir {iapython.home() / 'prisme-moteur.log'})") from None
                if timeout and waited > timeout:
                    self.close()
                    raise RuntimeError("le moteur de réparation ne répond plus") from None

    def _start(self) -> None:
        if self._process is not None and self._process.is_alive():
            return
        from . import moteur_child
        _env()
        self._process, self._requests, self._answers = iapython.spawn(
            moteur_child.serve, (str(engine_src()),), "prisme-moteur")
        state, value = self._wait(START_TIMEOUT)
        if state != "ok":
            self.close()
            raise RuntimeError(f"le moteur de réparation n'a pas démarré : {value}")

    def _ask(self, kind: str, payload):
        with self._lock:
            self._start()
            self._requests.put((kind, payload))
            # Pas de limite : une grosse video peut demander de longues minutes.
            state, value = self._wait(0)
        if state != "ok":
            raise RuntimeError(value)
        return value

    def diagnose(self, path) -> dict:
        return self._ask("diagnose", str(path))

    def selftest(self) -> dict:
        return self._ask("selftest", None)

    def repair_file(self, path, target=None):
        try:
            return SimpleNamespace(**self._ask("repair", (str(path), str(target) if target else None)))
        except RuntimeError as exc:
            return SimpleNamespace(outcome="error", output_path=None, report={"detail": str(exc)},
                                   error_code="engine_exception",
                                   public_message=f"Le moteur de réparation a rencontré un problème : {exc}")

    def close(self) -> None:
        process, requests = self._process, self._requests
        self._process = self._requests = self._answers = None
        if process is None:
            return
        try:
            requests.put(("quit", None))
        except Exception:                                   # noqa: BLE001
            pass
        process.join(3)
        if process.is_alive():
            process.terminate()


_remote = None


def api():
    """reparateur.api (sources) ou son double dans le Python du labo."""
    global _remote
    if not _setup():
        raise RuntimeError(f"moteur de réparation indisponible : {_error}")
    if not external():
        from reparateur import api as engine_api
        return engine_api
    if _remote is None:
        _remote = _Remote()
        atexit.register(_remote.close)
    return _remote


def selftest() -> dict:
    """Une vraie reparation d'essai (moteur_child.selftest_photo), la ou le
    moteur tourne."""
    engine = api()
    if isinstance(engine, _Remote):
        return engine.selftest()
    from .moteur_child import selftest_photo
    return selftest_photo()


def is_photo(path) -> bool:
    return str(path).lower().endswith(PHOTO_EXT)


def repaired_name(path) -> Path:
    """« nom (réparée).ext » a cote de l'original, sans rien ecraser."""
    path = Path(path)
    target = path.with_name(f"{path.stem} (réparée){path.suffix}")
    count = 2
    while target.exists():
        target = path.with_name(f"{path.stem} (réparée {count}){path.suffix}")
        count += 1
    return target
