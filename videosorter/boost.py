"""L'egalisation du volume : les videos trop basses sont remontees.

Beaucoup de videos sont enregistrees vingt a quarante decibels sous une
video ordinaire (micro loin, telephone pose) : meme au maximum, on
n'entendait rien, une video sur quinze seulement avait « du son ». Le
volume de Qt s'arrete a 100 % et ne peut pas amplifier.

Le lecteur livre donc son son decode a Prisme (`QAudioBufferOutput`), un peu
avant l'image (une trentaine de millisecondes) ; Prisme le remonte et le joue
lui-meme (`QAudioSink`). Le gain suit le niveau moyen de la video, lentement
pour ne pas pomper, et ne descend jamais sous 1 : une video deja forte passe
telle quelle. Un limiteur empeche la saturation.

Le travail se fait dans un fil a part : un gel de l'interface ne hache pas
le son. La sortie son du lecteur (`QAudioOutput`) reste l'objet que tout
Prisme regle -- coupure, volume, sortie choisie : on y lit ses reglages.
"""
from __future__ import annotations

import math

import numpy as np
import shiboken6
from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal, Slot
from PySide6.QtMultimedia import (QAudioBuffer, QAudioBufferOutput, QAudioFormat,
                                  QAudioSink, QMediaPlayer)

# Le niveau vise (moyenne quadratique des passages ou l'on entend quelque
# chose), celui d'une video ordinaire ; le gain le plus fort ; ce qui compte
# pour du silence et n'entre pas dans la moyenne.
TARGET_DB = -21.0
MAX_GAIN_DB = 30.0
SILENCE_DB = -62.0
CEILING = 0.92            # le limiteur : jamais plus haut
# La moyenne : simple pendant les premieres secondes (le gain se cale vite),
# glissante ensuite, sur quelques secondes.
SETTLE_S = 3.0
TAU_S = 5.0
# Le gain monte vite au debut d'une video (le niveau se cale en une demi-
# seconde : on n'entend pas la video faible puis forte), doucement ensuite ;
# il descend tout de suite (le limiteur).
RISE_START_DB_S = 80.0
RISE_DB_S = 12.0
# Un saut dans la video : ce qui attendait dans la sortie est jete.
JUMP_MS = 250

_enabled = True
_thread = None
_pipes: list = []


def _db_to_lin(db: float) -> float:
    return 10.0 ** (db / 20.0)


def enabled() -> bool:
    return _enabled


def set_enabled(on: bool) -> None:
    """L'egalisation pour tous les lecteurs de Prisme."""
    global _enabled
    _enabled = bool(on)
    for booster in list(_boosters):
        booster.apply()


def _worker() -> QThread:
    global _thread
    if _thread is None:
        _thread = QThread()
        _thread.setObjectName("prisme-son")
        _thread.start(QThread.HighPriority)
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(_stop_worker)
    return _thread


def _stop_worker() -> None:
    global _thread
    if _thread is not None:
        _thread.quit()
        _thread.wait(1000)
        _thread = None


class _Pipe(QObject):
    """Dans le fil du son : recoit les tampons, les remonte, les joue."""

    flushWanted = Signal(bool)          # vrai : nouvelle video, moyenne oubliee
    deviceWanted = Signal(object)

    def __init__(self, device):
        super().__init__()
        self.device = device
        self.muted = False
        self.volume = 1.0
        self.sink = None
        self.io = None
        self.fmt_key = None
        self.expected_us = None
        self._reset_level()
        # Emis depuis l'interface, executes ici (connexion en file).
        self.flushWanted.connect(self._flush)
        self.deviceWanted.connect(self._set_device)

    def _reset_level(self) -> None:
        self.energy = 0.0        # moyenne des carres
        self.heard_s = 0.0       # secondes de son prises dans la moyenne
        self.gain = 1.0

    # -- la sortie ------------------------------------------------------------
    def _close(self) -> None:
        if self.sink is not None:
            try:
                self.sink.stop()
            except RuntimeError:
                pass
            self.sink.deleteLater()
        self.sink = self.io = None
        self.fmt_key = None

    def _open(self, fmt: QAudioFormat) -> None:
        self._close()
        if self.device is None or self.device.isNull():
            sink = QAudioSink(fmt, self)
        else:
            sink = QAudioSink(self.device, fmt, self)
        sink.setBufferSize(int(fmt.bytesForDuration(250_000)))
        self.sink = sink
        self.io = sink.start()
        self.fmt_key = (fmt.sampleRate(), fmt.channelCount(), fmt.sampleFormat())

    @Slot(bool)
    def _flush(self, forget: bool) -> None:
        """Jette ce qui attend encore dans la sortie (arret, autre video)."""
        if self.sink is not None:
            try:
                self.sink.reset()
                self.io = self.sink.start()
            except RuntimeError:
                self._close()
        self.expected_us = None
        if forget:
            self._reset_level()

    @Slot(object)
    def _set_device(self, device) -> None:
        self.device = device
        self._close()

    # -- le son ---------------------------------------------------------------
    @Slot(QAudioBuffer)
    def take(self, buffer) -> None:
        try:
            if not buffer.isValid() or buffer.byteCount() <= 0:
                return
            fmt = buffer.format()
            kind = fmt.sampleFormat()
            if kind == QAudioFormat.SampleFormat.Float:
                samples = np.frombuffer(bytes(buffer.constData()), dtype=np.float32)
                scale = 1.0
            elif kind == QAudioFormat.SampleFormat.Int16:
                samples = np.frombuffer(bytes(buffer.constData()), dtype=np.int16)
                scale = 32768.0
            elif kind == QAudioFormat.SampleFormat.Int32:
                samples = np.frombuffer(bytes(buffer.constData()), dtype=np.int32)
                scale = 2147483648.0
            else:
                return
            start = buffer.startTime()
            seconds = buffer.duration() / 1e6
            jumped = (self.expected_us is not None
                      and abs(start - self.expected_us) > JUMP_MS * 1000)
            self.expected_us = start + buffer.duration()
            wave = samples.astype(np.float32) / scale if scale != 1.0 else samples
            self._measure(wave, seconds)
            if self.muted:
                return
            key = (fmt.sampleRate(), fmt.channelCount(), kind)
            if self.sink is None or key != self.fmt_key:
                self._open(fmt)
            elif jumped:
                self._flush(False)
            out = self._amplify(wave, seconds) * float(self.volume)
            if scale != 1.0:
                out = np.clip(out * scale, -scale, scale - 1).astype(samples.dtype)
            else:
                out = out.astype(np.float32, copy=False)
            if self.io is not None:
                self.io.write(out.tobytes())
        except RuntimeError:
            # La sortie a disparu (casque debranche) : elle se rouvrira.
            self._close()

    def _measure(self, wave, seconds: float) -> None:
        """La moyenne du niveau, sans les silences."""
        if wave.size == 0 or seconds <= 0:
            return
        power = float(np.mean(np.square(wave, dtype=np.float32)))
        if power <= 0 or 10 * math.log10(power) < SILENCE_DB:
            return
        if self.heard_s < SETTLE_S:
            weight = seconds / (self.heard_s + seconds)
        else:
            weight = seconds / (TAU_S + seconds)
        self.energy += (power - self.energy) * weight
        self.heard_s += seconds

    def _amplify(self, wave, seconds: float):
        if self.energy <= 0:
            wanted = self.gain
        else:
            level_db = 10 * math.log10(self.energy)
            wanted = _db_to_lin(max(0.0, min(MAX_GAIN_DB, TARGET_DB - level_db)))
        before = self.gain
        if wanted > before:
            rate = RISE_START_DB_S if self.heard_s < SETTLE_S else RISE_DB_S
            wanted = min(wanted, before * _db_to_lin(rate * max(seconds, 0.001)))
        # Le limiteur : le gain cede tout de suite devant un pic (jamais sous
        # 1 : ce qui etait deja fort ne baisse pas).
        peak = float(np.max(np.abs(wave))) if wave.size else 0.0
        if peak > 0 and peak * wanted > CEILING:
            wanted = max(1.0, CEILING / peak)
        self.gain = wanted
        if wanted <= before or abs(wanted - before) < 1e-4:
            # Une baisse s'applique a tout le tampon, pic compris.
            return wave * wanted
        ramp = np.linspace(before, wanted, num=wave.size, dtype=np.float32)
        return wave * ramp


_boosters: list = []


class Booster:
    """Branche l'egalisation sur un lecteur. `output` reste la sortie que
    Prisme regle (coupure, volume, appareil) ; on en suit les reglages."""

    def __init__(self, player: QMediaPlayer, output):
        self.player = player
        self.output = output
        self.pipe = _Pipe(output.device())
        self.pipe.muted = output.isMuted()
        self.pipe.volume = output.volume()
        self.pipe.moveToThread(_worker())
        self.tap = QAudioBufferOutput(player)
        self.tap.audioBufferReceived.connect(self.pipe.take)
        output.mutedChanged.connect(self._muted)
        output.volumeChanged.connect(self._volume)
        output.deviceChanged.connect(
            lambda: self.pipe.deviceWanted.emit(self.output.device()))
        player.sourceChanged.connect(lambda *_a: self.pipe.flushWanted.emit(True))
        player.playbackStateChanged.connect(self._state)
        self.on = None
        _boosters.append(self)
        player.destroyed.connect(lambda *_a: self._gone())
        self.apply()

    def _gone(self) -> None:
        if self in _boosters:
            _boosters.remove(self)
        if shiboken6.isValid(self.pipe):
            self.pipe.deleteLater()

    def _muted(self, muted: bool) -> None:
        self.pipe.muted = bool(muted)
        if muted:
            self.pipe.flushWanted.emit(False)

    def _volume(self, volume: float) -> None:
        self.pipe.volume = float(volume)

    def _state(self, state) -> None:
        if state == QMediaPlayer.PlaybackState.StoppedState:
            self.pipe.flushWanted.emit(False)

    def apply(self) -> None:
        """Le son passe par Prisme (egalise), ou directement par Qt."""
        on = _enabled
        if on == self.on or not shiboken6.isValid(self.player):
            return
        self.on = on
        if on:
            self.player.setAudioOutput(None)
            self.player.setAudioBufferOutput(self.tap)
        else:
            self.player.setAudioBufferOutput(None)
            self.player.setAudioOutput(self.output)
            self.pipe.flushWanted.emit(False)
