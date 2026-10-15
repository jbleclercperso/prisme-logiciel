"""Le chien de garde : mesure les gels de l'interface, et dit ce qui les precede.

« C'est lent » ne se corrige pas ; « l'interface s'est figee 4,2 s juste
apres wall.play » se corrige. Un battement toutes les 50 ms : s'il arrive en
retard, c'est que le fil d'interface etait occupe -- on note combien de
temps, et la derniere action marquee avant.

Deux niveaux. Un « a-coup » (a partir de 0,15 s) se sent sans bloquer ; un
« gel » (a partir de 0,5 s) arrete tout. L'ancien battement, toutes les
250 ms avec un seuil de 0,8 s, ne voyait aucun des a-coups de 0,1 a 0,5 s
qu'on ressent pourtant en naviguant -- impossible de les classer.
"""
from __future__ import annotations

import sys
import threading
import time
import traceback
from datetime import datetime

from PySide6.QtCore import QObject, Qt, QTimer

from .config import PRIVATE_DIR

LOG = PRIVATE_DIR / "gel.log"
BEAT = 0.25       # secondes entre deux battements (4 reveils par seconde, pas 20)
HICCUP = 0.15     # retard a partir duquel on parle d'a-coup
STALL = 0.5       # retard a partir duquel on parle de gel
# Au-dela, les a-coups ne sont plus ecrits : une seance qui en accumule des
# milliers a deja dit ce qu'elle avait a dire, et le journal doit rester lisible.
MAX_HICCUP_LINES = 500
# Au-dela de ce retard, une sentinelle (un autre fil) releve ce que faisait le
# fil de l'interface : l'etiquette de la derniere action datait souvent de
# plusieurs minutes et n'accusait personne.
SAMPLE_AFTER = 0.3


def _where(frame) -> str:
    """Les dernieres fonctions de Prisme sur la pile : « window.py:4521
    on_patch < board.py:900 set_items »."""
    ours = [f for f in traceback.extract_stack(frame)
            if "videosorter" in f.filename or f.filename.endswith("main.py")]
    picked = ours[-4:] or traceback.extract_stack(frame)[-2:]
    return " < ".join(f"{f.filename.replace(chr(92), '/').rsplit('/', 1)[-1]}:{f.lineno} {f.name}"
                      for f in reversed(picked))


class Watchdog(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.last_action = "démarrage"
        self.last_at = time.monotonic()
        self._beat = time.monotonic()
        self.stalls = 0          # gels
        self.hiccups = 0         # a-coups
        self.worst = 0.0
        self.timer = QTimer(self)
        # Grossier suffit a 250 ms (5 % de derive : 12 ms, loin du seuil des
        # a-coups) et n'oblige pas Windows a affiner son horloge pour tout le
        # systeme, comme le faisait le battement precis de 50 ms.
        self.timer.setTimerType(Qt.CoarseTimer)
        self._sample: str | None = None
        self._main = threading.main_thread().ident
        self._sentinel: threading.Thread | None = None
        self.timer.setInterval(int(BEAT * 1000))
        self.timer.timeout.connect(self._tick)

    def start(self, root: str = "") -> None:
        """Ouvre le journal sur une ligne d'en-tete, puis se met a battre.

        Sans elle, on ne distinguait pas une seance de tri d'une autre — ni
        d'un passage du jeu de tests — et les lignes de plusieurs jours se
        melaient sans qu'on sache laquelle regarder.
        """
        self._beat = time.monotonic()
        self._say(f"--- {datetime.now():%d/%m %H:%M:%S}  ouverture"
                  + (f" — racine {root}" if root else "") + " ---")
        self.timer.start()
        if self._sentinel is None:
            self._sentinel = threading.Thread(target=self._watch, daemon=True,
                                              name="prisme-sentinelle")
            self._sentinel.start()

    def _watch(self) -> None:
        """Hors du fil de l'interface : s'il tarde, on note ou il en est."""
        while True:
            time.sleep(0.1)
            # Rien de Qt ici (on n'est pas dans son fil) : seulement l'heure
            # du dernier battement. Au-dela de 10 s de silence, la minuterie
            # est arretee (Prisme reduit) : rien a relever.
            if self._sample is not None:
                continue
            if SAMPLE_AFTER <= time.monotonic() - self._beat - BEAT < 10:
                frame = sys._current_frames().get(self._main)
                if frame is not None:
                    try:
                        self._sample = _where(frame)
                    except Exception:  # noqa: BLE001
                        self._sample = "?"
                    del frame

    @staticmethod
    def _say(line: str) -> None:
        try:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            fresh = not LOG.exists() or LOG.stat().st_size == 0
            # Un BOM a la creation : le Bloc-notes rend alors « figée », et
            # non « figÃ©e ».
            with open(LOG, "a", encoding="utf-8-sig" if fresh else "utf-8") as out:
                out.write(line + "\n")
        except OSError:
            pass

    def mark(self, action: str) -> None:
        """A appeler avant tout ce qui pourrait couter : on saura quoi accuser."""
        self.last_action = action
        self.last_at = time.monotonic()

    def _tick(self) -> None:
        now = time.monotonic()
        late = now - self._beat - BEAT
        self._beat = now
        sample, self._sample = self._sample, None
        if late < HICCUP:
            return
        since = now - self.last_at
        where = f" — dans : {sample}" if sample else ""
        if late < STALL:
            self.hiccups += 1
            if self.hiccups <= MAX_HICCUP_LINES:
                self._say(f"{datetime.now():%d/%m %H:%M:%S}  à-coup {late:.2f} s"
                          f"  — dernière action : {self.last_action}"
                          f" (il y a {since:.1f} s){where}")
            return
        self.stalls += 1
        self.worst = max(self.worst, late)
        self._say(f"{datetime.now():%d/%m %H:%M:%S}  interface figée {late:.1f} s"
                  f"  — dernière action : {self.last_action}"
                  f" (il y a {since:.1f} s){where}")


WATCH = Watchdog()


def mark(action: str) -> None:
    WATCH.mark(action)
