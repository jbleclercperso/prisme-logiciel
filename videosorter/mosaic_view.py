"""La mosaique : de dix a cent videos a la fois, collees, sans bord.

Un essai, pour voir jusqu'ou la machine tient. Le mur s'arrete a dix
panneaux, chacun avec son cadre et ses gestes ; ici, rien que les images, en
plein ecran, chacune remplissant sa case (un leger recadrage plutot que des
bandes noires).

La grille couvre toujours tout l'ecran : on prend autant de colonnes et de
rangees qu'il faut pour que des cases a peu pres 16:9 le remplissent, et l'on
pose une video dans chacune -- le nombre demande, arrondi a la grille pleine.

Les lecteurs demarrent par petits paquets : cent d'un coup figeaient tout.

Gestes : clic sur une case, on l'entend (et elle seule) ; double-clic, sa
fiche dans Prisme ; molette sur une case, une autre video a sa place ; Espace,
un autre tirage ; Echap, on ferme. La barre du haut parait quand la souris y
monte.
"""
from __future__ import annotations

import math
import random

from PySide6.QtCore import QPoint, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QCursor
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget,
)

COUNT_MIN, COUNT_MAX = 10, 100
# Combien de lecteurs demarrent a chaque battement, et tous les combien.
BATCH, BATCH_MS = 3, 120

STYLE = """
QWidget#mosaic { background: #000000; }
QWidget#mosaicBar { background: rgba(12, 14, 18, 235); }
QWidget#mosaicBar QLabel { color: #e6e8ea; font-size: 13px; }
QWidget#mosaicBar QPushButton { background: #232a34; border: 1px solid #364050;
    border-radius: 6px; padding: 5px 12px; color: #eef1f4; }
QWidget#mosaicBar QPushButton:hover { background: #2c3541; }
"""


def grid_for(count: int, width: int, height: int) -> tuple:
    """(colonnes, rangees) dont les cases, a peu pres 16:9, remplissent
    l'ecran, pour au moins `count` videos."""
    count = max(1, count)
    width, height = max(1, width), max(1, height)
    best, best_score = (1, count), None
    for cols in range(1, count + 1):
        rows = math.ceil(count / cols)
        # Des cases proches du 16:9 (la forme la plus courante), et le moins
        # de cases en trop : chacune est une video de plus a decoder.
        shape = abs(math.log((width / cols) / (height / rows) / (16 / 9)))
        extra = (cols * rows - count) / count
        score = shape + 2 * extra
        if best_score is None or score < best_score:
            best, best_score = (cols, rows), score
    return best


class _Tile:
    """Une case : un lecteur muet qui tourne en boucle."""

    def __init__(self, owner, at: int):
        self.at = at
        self.video = QVideoWidget(owner)
        # Remplir la case, quitte a rogner : pas de bandes noires.
        self.video.setAspectRatioMode(Qt.KeepAspectRatioByExpanding)
        self.video.setStyleSheet("background: #000000;")
        self.audio = QAudioOutput(owner)
        self.audio.setMuted(True)
        self.player = QMediaPlayer(owner)
        from .media import track_player
        track_player(self.player)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.setLoops(QMediaPlayer.Infinite)
        self.player.mediaStatusChanged.connect(self._loaded)
        self.path = ""
        self._jump = True

    def load(self, path: str) -> None:
        self.path = path
        self._jump = True
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()

    def _loaded(self, status) -> None:
        # Un instant au hasard, pas toujours le generique.
        if self._jump and status in (QMediaPlayer.MediaStatus.LoadedMedia,
                                     QMediaPlayer.MediaStatus.BufferedMedia):
            duration = self.player.duration()
            if duration > 4000:
                self._jump = False
                self.player.setPosition(int(duration * random.uniform(0.1, 0.6)))

    def release(self) -> None:
        self.player.stop()
        self.player.setSource(QUrl())
        self.video.hide()
        self.video.deleteLater()
        self.player.deleteLater()
        self.audio.deleteLater()


class MosaicWindow(QWidget):
    """La fenetre de la mosaique, en plein ecran."""

    countChanged = Signal(int)       # pour le retenir dans les reglages
    sheetRequested = Signal(str)     # double-clic : la fiche de cette video

    def __init__(self, pool: list, count: int = 20, parent=None):
        super().__init__(parent, Qt.Window)
        self.setObjectName("mosaic")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setStyleSheet(STYLE)
        self.setWindowTitle("Prisme — Mosaïque")
        self.pool = list(dict.fromkeys(str(p) for p in pool))
        self.count = max(COUNT_MIN, min(COUNT_MAX, int(count)))
        self.tiles: list = []
        self._waiting: list = []
        self._loud = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.bar = QWidget(self)
        self.bar.setObjectName("mosaicBar")
        self.bar.setAttribute(Qt.WA_StyledBackground, True)
        row = QHBoxLayout(self.bar)
        row.setContentsMargins(12, 6, 12, 6)
        row.setSpacing(10)
        row.addWidget(QLabel("Mosaïque", self.bar))
        self.slider = QSlider(Qt.Horizontal, self.bar)
        self.slider.setRange(COUNT_MIN, COUNT_MAX)
        self.slider.setValue(self.count)
        self.slider.setFixedWidth(260)
        self.slider.setToolTip("Combien de vidéos à la fois (la grille est complétée "
                               "pour remplir l'écran)")
        row.addWidget(self.slider)
        self.said = QLabel("", self.bar)
        row.addWidget(self.said)
        row.addStretch(1)
        hint = QLabel("clic : le son · double-clic : sa fiche · molette : une autre · "
                      "Espace : un autre tirage · Échap : fermer", self.bar)
        hint.setStyleSheet("color: #8b94a1; font-size: 12px;")
        row.addWidget(hint)
        again = QPushButton("Un autre tirage", self.bar)
        again.clicked.connect(self.reshuffle)
        row.addWidget(again)
        close = QPushButton("Fermer", self.bar)
        close.clicked.connect(self.close)
        row.addWidget(close)
        outer.addWidget(self.bar)
        self.area = QWidget(self)
        self.grid = QGridLayout(self.area)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(0)
        outer.addWidget(self.area, 1)

        # Le nombre change au relache du curseur : cent lecteurs refaits a
        # chaque cran, c'etait la machine a genoux.
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(350)
        self._settle.timeout.connect(self._count_settled)
        self.slider.valueChanged.connect(self._count_moved)
        self.starter = QTimer(self)
        self.starter.setInterval(BATCH_MS)
        self.starter.timeout.connect(self._start_some)
        # La barre ne parait que quand la souris monte en haut.
        self.watch = QTimer(self)
        self.watch.setInterval(200)
        self.watch.timeout.connect(self._watch_bar)
        self.watch.start()

    # -- la grille ------------------------------------------------------------
    def showEvent(self, event):
        super().showEvent(event)
        if not self.tiles:
            QTimer.singleShot(0, self.rebuild)

    def _count_moved(self, value: int) -> None:
        self.said.setText(f"{value} vidéos")
        self._settle.start()

    def _count_settled(self) -> None:
        value = self.slider.value()
        if value != self.count:
            self.count = value
            self.countChanged.emit(value)
            self.rebuild()

    def _picks(self, wanted: int) -> list:
        """`wanted` videos du vivier, sans doublon tant qu'il y en a assez."""
        if not self.pool:
            return []
        if len(self.pool) >= wanted:
            return random.sample(self.pool, wanted)
        return [random.choice(self.pool) for _ in range(wanted)]

    def rebuild(self) -> None:
        """Refait la grille pour `count` videos, de quoi remplir l'ecran."""
        self.starter.stop()
        self._waiting = []
        self._loud = None
        for tile in self.tiles:
            self.grid.removeWidget(tile.video)
            tile.release()
        self.tiles = []
        room = self.area.size()
        cols, rows = grid_for(self.count, room.width(), room.height())
        for at in range(cols * rows):
            tile = _Tile(self.area, at)
            self._wire(tile)
            self.grid.addWidget(tile.video, at // cols, at % cols)
            self.tiles.append(tile)
        for r in range(rows):
            self.grid.setRowStretch(r, 1)
        for c in range(cols):
            self.grid.setColumnStretch(c, 1)
        self._waiting = list(zip(self.tiles, self._picks(len(self.tiles))))
        self.said.setText(f"{len(self.tiles)} vidéos ({cols} × {rows})")
        self.starter.start()

    def _start_some(self) -> None:
        for _ in range(BATCH):
            if not self._waiting:
                self.starter.stop()
                return
            tile, path = self._waiting.pop(0)
            tile.load(path)

    def reshuffle(self) -> None:
        self.starter.stop()
        self._waiting = list(zip(self.tiles, self._picks(len(self.tiles))))
        self.starter.start()

    # -- les gestes sur une case ------------------------------------------------
    def _wire(self, tile) -> None:
        # Le widget video natif avale les clics : on les lui prend a la source.
        tile.video.mousePressEvent = lambda event, t=tile: self._press(t, event)
        tile.video.mouseDoubleClickEvent = lambda event, t=tile: self._double(t, event)
        tile.video.wheelEvent = lambda event, t=tile: self._wheel(t, event)
        tile.video.setMouseTracking(True)

    def _press(self, tile, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        # On l'entend, et elle seule ; recliquer la fait taire.
        loud = None if self._loud is tile else tile
        for one in self.tiles:
            one.audio.setMuted(one is not loud)
        self._loud = loud

    def _double(self, tile, event) -> None:
        if tile.path:
            self.sheetRequested.emit(tile.path)
            self.close()

    def _wheel(self, tile, event) -> None:
        picks = self._picks(1)
        if picks:
            tile.load(picks[0])
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            return self.close()
        if event.key() == Qt.Key_Space:
            return self.reshuffle()
        super().keyPressEvent(event)

    # -- la barre --------------------------------------------------------------
    def _watch_bar(self) -> None:
        if not self.isVisible():
            return
        spot = self.mapFromGlobal(QCursor.pos())
        near = self.rect().contains(spot) and spot.y() < 60
        busy = self.slider.isSliderDown()
        if (near or busy) and self.bar.isHidden():
            self.bar.show()
        elif not (near or busy) and not self.bar.isHidden():
            self.bar.hide()

    def closeEvent(self, event):
        self.starter.stop()
        self.watch.stop()
        for tile in self.tiles:
            tile.release()
        self.tiles = []
        super().closeEvent(event)
