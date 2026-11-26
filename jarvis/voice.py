"""Voix : micro, mot de réveil (openWakeWord), reconnaissance (faster-whisper), synthèse (edge-tts / SAPI)."""
from __future__ import annotations

import asyncio
import collections
import os
import queue
import re
import sys
import tempfile
import threading
import time

SR = 16000
FRAME = 1280            # 80 ms : taille attendue par openWakeWord


def beep(freq: int = 880, ms: int = 120) -> None:
    if sys.platform == "win32":
        import winsound
        try:
            winsound.Beep(freq, ms)
        except RuntimeError:
            pass


def clean_for_speech(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"https?://\S+", "le lien", text)
    text = re.sub(r"[*_#`>|]+", "", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.M)
    return " ".join(text.split())


# --------------------------------------------------------------------------- synthèse
class Speaker:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.speaking = threading.Event()
        self.stop = threading.Event()        # mis par le raccourci clavier pour couper la parole
        self._sapi = None
        self._mixer = None

    def _edge(self, text: str) -> None:
        import edge_tts
        import pygame
        if self._mixer is None:
            pygame.mixer.init()
            self._mixer = pygame.mixer
        fd, path = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        try:
            com = edge_tts.Communicate(text, self.cfg["tts_voix"], rate=self.cfg.get("tts_vitesse", "+0%"))
            asyncio.run(com.save(path))
            self._mixer.music.load(path)
            self._mixer.music.play()
            while self._mixer.music.get_busy():
                if self.stop.is_set():
                    self._mixer.music.stop()
                    break
                time.sleep(0.05)
            self._mixer.music.unload()
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def _sapi_say(self, text: str) -> None:
        import pyttsx3
        if self._sapi is None:
            self._sapi = pyttsx3.init()
            for v in self._sapi.getProperty("voices"):
                if "fr" in (v.id + v.name).lower():
                    self._sapi.setProperty("voice", v.id)
                    break
            self._sapi.setProperty("rate", 185)
        self._sapi.say(text)
        self._sapi.runAndWait()

    def say(self, text: str) -> None:
        text = clean_for_speech(text)
        if not text:
            return
        self.stop.clear()
        self.speaking.set()
        try:
            if self.cfg.get("tts", "edge") == "edge":
                try:
                    self._edge(text)
                    return
                except Exception as e:      # pas d'Internet, etc. : repli sur la voix Windows
                    print(f"[voix] edge-tts indisponible ({e}), repli sur la voix Windows.")
            self._sapi_say(text)
        except Exception as e:
            print(f"[voix] synthèse impossible : {e}")
        finally:
            time.sleep(0.25)                # laisse l'écho retomber avant de réécouter
            self.speaking.clear()


# --------------------------------------------------------------------------- écoute
class Listener:
    def __init__(self, cfg: dict, speaker: Speaker):
        import numpy as np
        import sounddevice as sd
        self.np, self.cfg, self.speaker = np, cfg, speaker
        self.q: queue.Queue = queue.Queue()
        self.noise = 150.0                  # niveau de bruit ambiant (RMS int16), ajusté en continu
        self.hotkey = threading.Event()
        self.pending = ""                   # demande déjà entendue avec le mot de réveil
        self.stream = sd.InputStream(samplerate=SR, channels=1, dtype="int16", blocksize=FRAME,
                                     device=cfg.get("micro"), callback=self._callback)
        self.stream.start()

        from faster_whisper import WhisperModel
        device = cfg.get("whisper_device", "cpu")
        print(f"[voix] chargement de Whisper '{cfg['whisper_modele']}' (1er lancement : téléchargement)...")
        self.whisper = WhisperModel(cfg["whisper_modele"], device=device,
                                    compute_type="float16" if device == "cuda" else "int8")
        self.wake = None
        self.wake_name = cfg.get("mot_reveil", "hey_jarvis")
        try:
            import openwakeword
            from openwakeword.model import Model
            try:
                openwakeword.utils.download_models([self.wake_name])
            except Exception:
                pass
            self.wake = Model(wakeword_models=[self.wake_name], inference_framework="onnx")
        except Exception as e:
            print(f"[voix] modèle « Hey Jarvis » indisponible ({e}) : repli sur la détection du mot "
                  "« Jarvis » par Whisper (dites « Jarvis, ... »).")
        try:
            import keyboard
            keyboard.add_hotkey(cfg.get("raccourci", "ctrl+alt+j"), self._on_hotkey)
        except Exception as e:
            print(f"[voix] raccourci clavier indisponible ({e}).")

    def _on_hotkey(self) -> None:
        if self.speaker.speaking.is_set():
            self.speaker.stop.set()         # pendant qu'il parle : le raccourci le fait taire
        self.hotkey.set()

    def _callback(self, indata, frames, t, status) -> None:
        if not self.speaker.speaking.is_set():      # Jarvis ne s'écoute pas parler
            self.q.put(indata[:, 0].copy())

    def _flush(self) -> None:
        while not self.q.empty():
            try:
                self.q.get_nowait()
            except queue.Empty:
                break

    def _rms(self, frame) -> float:
        return float(self.np.sqrt(self.np.mean(frame.astype("float32") ** 2)))

    def wait_for_wake(self) -> str:
        """Bloque jusqu'au mot de réveil ou au raccourci. Renvoie 'mot' ou 'raccourci'."""
        self._flush()
        self.hotkey.clear()
        if self.wake:
            self.wake.reset()
        threshold = float(self.cfg.get("seuil_reveil", 0.5))
        while self.wake is None:            # repli : on guette le mot « Jarvis » avec Whisper
            if self.hotkey.is_set():
                self.hotkey.clear()
                return "raccourci"
            audio = self.record(start_timeout=2.0, max_seconds=12.0)
            if audio is None:
                continue
            text = self.transcribe(audio)
            m = re.search(r"\b(d?jarvis+e?|jarvi|charvis|jervis)\b[\s,.!?:;-]*", text, flags=re.I)
            if m:
                rest = text[m.end():].strip()
                self.pending = rest if len(rest.split()) >= 2 else ""
                return "mot"
        while True:
            if self.hotkey.is_set():
                self.hotkey.clear()
                return "raccourci"
            try:
                frame = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            self.noise = 0.97 * self.noise + 0.03 * self._rms(frame)
            if self.wake:
                scores = self.wake.predict(frame)
                if max(scores.values()) >= threshold:
                    self.wake.reset()
                    return "mot"

    def record(self, start_timeout: float = 6.0, max_seconds: float = 30.0):
        """Enregistre une phrase. Renvoie l'audio (float32) ou None si personne n'a parlé."""
        np = self.np
        self._flush()
        threshold = max(350.0, self.noise * 3.0)
        silence_frames = int(float(self.cfg.get("silence_fin", 1.0)) * SR / FRAME)
        pre = collections.deque(maxlen=5)
        chunks, started, quiet, loud = [], False, 0, 0
        t0 = time.time()
        while True:
            try:
                frame = self.q.get(timeout=0.5)
            except queue.Empty:
                if time.time() - t0 > start_timeout + max_seconds:
                    break
                continue
            level = self._rms(frame)
            if not started:
                pre.append(frame)
                loud = loud + 1 if level > threshold else 0
                if loud >= 2:
                    started = True
                    chunks.extend(pre)
                elif time.time() - t0 > start_timeout:
                    return None
            else:
                chunks.append(frame)
                quiet = quiet + 1 if level < threshold else 0
                if quiet >= silence_frames or len(chunks) * FRAME / SR > max_seconds:
                    break
        if not chunks:
            return None
        return np.concatenate(chunks).astype("float32") / 32768.0

    def transcribe(self, audio) -> str:
        segments, _ = self.whisper.transcribe(
            audio, language="fr", beam_size=5, vad_filter=True,
            initial_prompt="Jarvis, ouvre Chrome, envoie un e-mail, mets le fichier sur le bureau.")
        text = " ".join(s.text.strip() for s in segments if s.no_speech_prob < 0.6).strip()
        # hallucinations classiques de Whisper sur du silence
        if re.fullmatch(r"(sous-titres?.*|merci d'avoir regardé.*|\W*)", text, flags=re.I):
            return ""
        return text

    def listen(self, start_timeout: float = 6.0) -> str:
        if self.pending:
            text, self.pending = self.pending, ""
            return text
        audio = self.record(start_timeout=start_timeout)
        return self.transcribe(audio) if audio is not None else ""

    def close(self) -> None:
        self.stream.stop()
        self.stream.close()


def decode_file(path: str):
    """Décode un fichier audio (mp3, ogg, wav...) en float32 mono 16 kHz."""
    import av
    import numpy as np
    chunks = []
    with av.open(path) as container:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=SR)
        for frame in container.decode(audio=0):
            chunks += [f.to_ndarray().reshape(-1) for f in resampler.resample(frame)]
        try:
            chunks += [f.to_ndarray().reshape(-1) for f in resampler.resample(None)]
        except Exception:
            pass
    if not chunks:
        return np.zeros(0, dtype="float32")
    return np.concatenate(chunks).astype("float32") / 32768.0


def list_microphones() -> str:
    import sounddevice as sd
    lines = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            lines.append(f"{i} : {d['name']}")
    return "\n".join(lines)
