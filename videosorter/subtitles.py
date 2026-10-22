"""Les sous-titres automatiques, cote Prisme : ce qu'on demande, ce qu'on
garde, ce qu'on montre.

Le calcul (Whisper, puis la traduction) tourne dans le Python du labo
(`subs_model.subs_child`), en tache de fond : Prisme lui demande des
morceaux de son en avance sur la lecture -- douze secondes d'abord, pour que
le premier sous-titre arrive vite, trente ensuite --, et toujours d'abord la
video qu'on regarde, puis le debut de la suivante.

Ce qui est fait est garde, par fichier (chemin, taille, date), dans le dossier
« sous-titres » du cache, a cote des vignettes : revoir une video, ou la voir
depuis l'autre PC qui partage le cache, affiche tout de suite.

Trois modes, pour tous les lecteurs : « off », « orig » (la langue parlee),
« fr » (traduit en francais quand ce n'est pas du francais).
"""
from __future__ import annotations

import hashlib
import json
import os
import queue as _queue
import subprocess
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QRect, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QWidget

from . import config, iapython, subs_model, voice
from .floatguard import FloatGuard

MODES = ("off", "orig", "fr")
LABELS = {"off": "Pas de sous-titres", "orig": "Sous-titres (langue parlée)",
          "fr": "Sous-titres en français"}
FIRST_S = 10.0          # le premier morceau : court, pour qu'il arrive vite
CHUNK_S = 30.0          # les suivants : la fenetre de Whisper
AHEAD_S = 75.0          # l'avance qu'on garde sur la lecture
NEXT_S = FIRST_S        # le debut de la video suivante, prepare d'avance
CACHE_VERSION = 1


def folder() -> Path:
    return config.APP_DIR / "sous-titres"


# -- ce qu'il faut sur la machine ------------------------------------------------------

def available() -> bool:
    """Les paquets du Labo IA (torch, transformers) sont-ils la ?"""
    return voice.available()


def translator_on_disk() -> bool:
    base = voice._hub_cache() / ("models--" + subs_model.TRANSLATOR.replace("/", "--")) / "snapshots"
    try:
        return any(weights.stat().st_size > 100 * 2**20
                   for weights in base.glob("*/model.safetensors"))
    except OSError:
        return False


def missing(mode: str) -> list:
    """Ce qu'il faut encore telecharger pour ce mode : [(quoi, taille)]."""
    needed = []
    if mode in ("orig", "fr") and not voice.model_on_disk():
        needed.append(("le modèle de reconnaissance vocale (Whisper)", voice.MODEL_SIZE))
    if mode == "fr":
        if not iapython.has("sentencepiece"):
            needed.append(("le découpeur de texte de la traduction (sentencepiece)", "2 Mo"))
        if not translator_on_disk():
            needed.append(("le modèle de traduction anglais vers français",
                           subs_model.TRANSLATOR_SIZE))
    return needed


def prepare(mode: str) -> None:
    """Telecharge ce qui manque (long : hors du fil de l'interface)."""
    if mode in ("orig", "fr") and not voice.model_on_disk():
        voice.download()
    if mode != "fr":
        return
    if not iapython.has("sentencepiece"):
        from .ia import console_python
        from .media import NO_WINDOW
        done = subprocess.run([console_python(), "-m", "pip", "install",
                               "--disable-pip-version-check", "sentencepiece"],
                              stdin=subprocess.DEVNULL, capture_output=True,
                              creationflags=NO_WINDOW)
        if done.returncode != 0 or not iapython.has("sentencepiece"):
            tail = (done.stderr or done.stdout or b"").decode("utf-8", "replace").strip()
            raise RuntimeError("sentencepiece ne s'installe pas"
                               + (f" ({tail.splitlines()[-1]})" if tail else ""))
    if not translator_on_disk():
        iapython.call(subs_model.fetch_translator, (), name="prisme-sous-titres-modele")
        if not translator_on_disk():
            raise RuntimeError("le modèle de traduction n'est pas arrivé en entier")


# -- ce qui est fait, par video ----------------------------------------------------------

class Track:
    """Les sous-titres d'un fichier : les morceaux faits et leurs phrases."""

    def __init__(self, path: str):
        self.path = path
        self.key = self._key(path)
        self.language = None
        self.chunks: list = []          # [debut, fin, tache, francais fait]
        self.segments: list = []        # [debut, fin, texte, francais]
        self.eof = None                 # la fin du son, une fois atteinte
        self._load()

    @staticmethod
    def _key(path: str) -> str:
        try:
            stat = os.stat(path)
            stamp = f"{stat.st_size}|{int(stat.st_mtime)}"
        except OSError:
            stamp = "?"
        name = os.path.normcase(os.path.abspath(path))
        return hashlib.sha1(f"{name}|{stamp}".encode("utf-8", "replace")).hexdigest()

    def _file(self) -> Path:
        return folder() / f"{self.key}.json"

    def _load(self) -> None:
        try:
            data = json.loads(self._file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if data.get("v") != CACHE_VERSION:
            return
        self.language = data.get("language")
        self.chunks = [list(c) for c in data.get("chunks", [])]
        self.segments = [list(s) for s in data.get("segments", [])]
        self.eof = data.get("eof")

    def save(self) -> None:
        data = {"v": CACHE_VERSION, "path": self.path, "language": self.language,
                "chunks": self.chunks, "segments": self.segments, "eof": self.eof}
        target = self._file()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            spare = target.with_suffix(".tmp")
            spare.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(spare, target)
        except OSError:
            pass

    # -- ce qui manque ------------------------------------------------------------------
    @staticmethod
    def _usable(chunk, french: bool) -> bool:
        return bool(chunk[3]) if french else chunk[2] == "transcribe"

    def next_job(self, position: float, french: bool, ahead: float):
        """Le prochain travail pour voir sans attente a partir de `position` :
        ("chunk", debut, duree), ("translate", morceau) ou None."""
        position = max(0.0, position)
        limit = position + ahead
        if self.eof is not None:
            limit = min(limit, self.eof)
        at = position
        cold = True
        for chunk in sorted(self.chunks, key=lambda c: c[0]):
            if chunk[1] <= at + 0.05:
                continue
            if chunk[0] > at + 0.05:
                break
            if french and not chunk[3] and chunk[2] == "transcribe":
                # Fait en langue d'origine : ne reste que la traduction.
                return ("translate", chunk)
            if not self._usable(chunk, french):
                break
            at = chunk[1]
            cold = False
        if at >= limit - 0.5:
            return None
        # Juste devant la lecture : de petits morceaux, qui arrivent avant
        # elle ; l'avance prise, des grands, plus economes.
        lead = at - position
        length = FIRST_S if cold or lead < 6 else (18.0 if lead < 20 else CHUNK_S)
        later = [c[0] for c in self.chunks if c[0] > at + 0.05 and self._usable(c, french)]
        if later and min(later) - at >= 3.0:
            length = min(length, min(later) - at)
        return ("chunk", at, length)

    def covered(self, position: float, french: bool) -> bool:
        return any(c[0] <= position < c[1] and self._usable(c, french) for c in self.chunks)

    def merge(self, result: dict, french: bool) -> None:
        start, end = float(result["start"]), float(result["end"])
        if result.get("eof"):
            self.eof = start if self.eof is None else min(self.eof, start)
            if end <= start:
                return
        if result.get("language"):
            self.language = result["language"]
        self.chunks = [c for c in self.chunks if c[1] <= start + 0.05 or c[0] >= end - 0.05]
        self.chunks.append([start, end, result.get("task", "transcribe"), bool(french)])
        self.segments = [s for s in self.segments if s[1] <= start + 0.05 or s[0] >= end - 0.05]
        self.segments.extend(list(s) for s in result.get("segments", []))
        self.segments.sort(key=lambda s: s[0])

    def text_at(self, position: float, french: bool) -> str:
        for seg in self.segments:
            if seg[0] - 0.15 <= position < seg[1] + 0.35:
                if french:
                    return seg[3] or ""
                return seg[2] or ""
        return ""


# -- le calcul, dans son processus -------------------------------------------------------

class _Engine(QObject):
    """Un seul pour tout Prisme : il choisit le travail le plus utile, le
    confie au processus des sous-titres, et range le resultat."""

    updated = Signal(str)             # un chemin dont les sous-titres ont avance
    failed = Signal(str)

    def __init__(self):
        super().__init__()
        self._lock = threading.Condition()
        self._wants: dict = {}        # proprietaire -> (chemin, position, francais, suivante)
        self._tracks: dict = {}
        self._busy = None             # le chemin en cours de calcul
        self._process = None
        self._requests = self._answers = None
        self._thread = None
        self._closed = False
        self._warm = None              # francais ? : charger les modeles d'avance
        self.ready = False
        self.error = ""

    # -- appele par l'interface ---------------------------------------------------------
    def track(self, path: str) -> Track:
        with self._lock:
            track = self._tracks.get(path)
            if track is None:
                track = self._tracks[path] = Track(path)
                if len(self._tracks) > 40:
                    for old in list(self._tracks)[:10]:
                        self._tracks.pop(old, None)
            return track

    def want(self, owner, path: str, position: float, french: bool, upcoming: str = "") -> None:
        with self._lock:
            wanted = (path, position, french, upcoming)
            if self._wants.get(owner) == wanted:
                return
            self._wants[owner] = wanted
            self._lock.notify_all()
        self._start()

    def warm(self, french: bool) -> None:
        """Charge les modeles en fond (une demi-minute a une minute sur le
        processeur) : la premiere video sous-titree n'attend plus."""
        with self._lock:
            self._warm = bool(french)
            self._lock.notify_all()
        self._start()

    def drop(self, owner) -> None:
        with self._lock:
            self._wants.pop(owner, None)

    def busy_with(self, path: str) -> bool:
        return self._busy == path

    def close(self) -> None:
        self._closed = True
        with self._lock:
            self._lock.notify_all()
        process, self._process = self._process, None
        if process is not None:
            try:
                self._requests.put(("quit", None))
            except Exception:                               # noqa: BLE001
                pass
            threading.Thread(target=lambda: process.join(3), daemon=True).start()

    # -- le fil du travail ------------------------------------------------------------
    def _start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, daemon=True,
                                            name="prisme-sous-titres")
            self._thread.start()

    def _pick(self):
        """(piste, travail, francais) le plus utile, ou None : la video qu'on
        regarde d'abord, puis le debut de la suivante."""
        wants = list(self._wants.values())
        for path, position, french, _next in wants:
            if path:
                track = self.track(path)
                job = track.next_job(position, french, AHEAD_S)
                if job is not None:
                    return track, job, french
        for _path, _position, french, upcoming in wants:
            if upcoming:
                track = self.track(upcoming)
                job = track.next_job(0.0, french, NEXT_S)
                if job is not None and job[0] == "chunk" and job[1] < 1.0:
                    return track, job, french
        return None

    def _ensure(self) -> None:
        if self._process is not None and self._process.is_alive():
            return
        self.ready = False
        self._process, self._requests, self._answers = iapython.spawn(
            subs_model.subs_child, (), "prisme-sous-titres")

    def _ask(self, kind: str, payload, timeout: float = 600.0):
        self._ensure()
        self._requests.put((kind, payload))
        left = timeout
        while True:
            try:
                state, value = self._answers.get(timeout=min(2.0, left))
                break
            except _queue.Empty:
                left -= 2.0
                if self._closed:
                    raise RuntimeError("fermé")
                if left <= 0 or not self._process.is_alive():
                    raise RuntimeError("le calcul des sous-titres ne répond pas")
        if state != "ok":
            raise RuntimeError(value)
        return value

    def _loop(self) -> None:
        from .media import Tools
        failures = 0
        while not self._closed:
            warm, self._warm = self._warm, None
            if warm is not None and not self.ready:
                try:
                    self._ask("load", warm)
                    self.ready = True
                except Exception as exc:                    # noqa: BLE001
                    self.error = str(exc)
            with self._lock:
                picked = self._pick()
                if picked is None:
                    self._lock.wait(1.0)
                    continue
            track, job, french = picked
            self._busy = track.path
            try:
                if not Tools.resolve():
                    raise RuntimeError("ffmpeg est introuvable")
                if job[0] == "translate":
                    chunk = job[1]
                    inside = [s for s in track.segments if chunk[0] - 0.05 <= s[0] < chunk[1]]
                    if track.language == "fr":
                        french_texts = [s[2] for s in inside]
                    else:
                        french_texts = self._ask("translate", [s[2] for s in inside])
                    with self._lock:
                        for seg, text in zip(inside, french_texts):
                            seg[3] = text
                        chunk[3] = True
                        track.save()
                else:
                    _kind, start, length = job
                    result = self._ask("chunk", {
                        "ffmpeg": Tools.ffmpeg, "path": track.path, "start": start,
                        "length": length, "language": track.language, "french": french})
                    with self._lock:
                        track.merge(result, french)
                        track.save()
                self.ready = True
                failures = 0
                self.updated.emit(track.path)
            except Exception as exc:                        # noqa: BLE001
                failures += 1
                self.error = str(exc)
                if self._closed:
                    return
                self.failed.emit(self.error)
                # Une video illisible ne doit pas tourner en boucle : on la
                # marque finie la ou elle bloque.
                if job[0] == "chunk" and failures >= 2:
                    with self._lock:
                        track.merge({"start": job[1], "end": job[1], "eof": True,
                                     "segments": []}, french)
                    failures = 0
                time.sleep(2.0)
            finally:
                self._busy = None


ENGINE = None


def engine() -> _Engine:
    global ENGINE
    if ENGINE is None:
        ENGINE = _Engine()
    return ENGINE


def close() -> None:
    if ENGINE is not None:
        ENGINE.close()


# -- ce qu'on voit ---------------------------------------------------------------------

class SubtitleChip(FloatGuard, QWidget):
    """Le texte, pose en bas de l'image : une petite fenetre a part, comme le
    bandeau -- le lecteur natif recouvre tout ce qu'on pose dans l'image."""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint
                         | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.text = ""
        self.dim = False
        self._font = QFont(self.font())
        self.hide()

    def show_text(self, text: str, area: QRect, lift: int, dim: bool = False) -> None:
        """`area` : l'image, en coordonnees de l'ecran ; `lift` : la place a
        laisser en bas (le bandeau, le trait)."""
        if not text or area.width() < 120 or area.height() < 80:
            return self.hide()
        size = max(13, min(30, area.height() // 22))
        self._font.setPixelSize(size)
        self._font.setWeight(QFont.DemiBold)
        metrics = QFontMetrics(self._font)
        width_max = int(area.width() * 0.84)
        box = metrics.boundingRect(QRect(0, 0, width_max - 24, 2000),
                                   Qt.AlignHCenter | Qt.TextWordWrap, text)
        width = min(width_max, box.width() + 28)
        height = box.height() + 14
        x = area.x() + (area.width() - width) // 2
        y = area.y() + area.height() - lift - height - max(10, area.height() // 30)
        wanted = QRect(x, max(area.y(), y), width, height)
        changed = (text, dim) != (self.text, self.dim)
        self.text, self.dim = text, dim
        if self.geometry() != wanted:
            self.setGeometry(wanted)
        if changed:
            self.update()
        if self.isHidden():
            self.show()
            self.raise_()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 120 if self.dim else 170))
        painter.drawRoundedRect(QRectF(self.rect()), 6, 6)
        painter.setFont(self._font)
        painter.setPen(QColor(255, 255, 255, 150 if self.dim else 245))
        painter.drawText(self.rect().adjusted(12, 6, -12, -6),
                         Qt.AlignCenter | Qt.TextWordWrap, self.text)


class Subtitles(QObject):
    """Les sous-titres d'un lecteur (`SinglePlayer`). `lift()` dit combien
    de place laisser en bas de l'image (le bandeau quand il est la)."""

    TICK_MS = 150

    def __init__(self, player, lift=None, mode=lambda: "off", active=lambda: True):
        super().__init__(player)
        self.player = player
        self.lift = lift or (lambda: 0)
        self.mode = mode
        self.active = active
        self._mode_seen = None
        self.chip = SubtitleChip(player)
        self.timer = QTimer(self)
        self.timer.setInterval(self.TICK_MS)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    def _off(self) -> None:
        self.chip.hide()
        if ENGINE is not None:
            ENGINE.drop(self)

    def tick(self) -> None:
        mode = self.mode()
        if mode != self._mode_seen:
            self._mode_seen = mode
            if mode in ("orig", "fr") and available():
                engine().warm(mode == "fr")
        player = self.player
        if (mode not in ("orig", "fr") or not player.isVisible() or player.still_path
                or not self.active() or not available()):
            return self._off()
        deck = player.decks[player._active]
        path = deck.path
        if not path:
            return self._off()
        french = mode == "fr"
        position = max(0, deck.player.position()) / 1000.0
        spare = player.spare.path if player.spare.path != path else ""
        work = engine()
        work.want(self, path, position, french, spare)
        track = work.track(path)
        text = track.text_at(position, french)
        dim = False
        if not text and not track.covered(position, french) and (
                track.eof is None or position < track.eof):
            text = ("Sous-titres en préparation…" if not work.error or work.ready
                    else f"Sous-titres impossibles : {work.error}")
            dim = True
        area = player.video_area
        corner = area.mapToGlobal(QPoint(0, 0))
        self.chip.show_text(text, QRect(corner, area.size()), int(self.lift() or 0), dim)

    def hide(self) -> None:
        self._off()
