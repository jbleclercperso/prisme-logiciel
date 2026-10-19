"""Les sous-titres automatiques, cote calcul, sans Qt : ce que fait tourner
le processus des sous-titres (`subs_child`) dans le Python du labo, et le
telechargement du modele de traduction (`fetch_translator`).

La reconnaissance est celle de la saisie vocale (Whisper « small », deja sur
le disque pour elle) ; la traduction anglais → francais, un modele Marian
d'Helsinki (« opus-mt-tc-big-en-fr », 440 Mo), choisi apres essai : le plus
juste des deux essayes, et le plus leger. Une autre langue que l'anglais ou
le francais passe d'abord en anglais (Whisper sait le faire), puis en
francais.

Tout tourne sur ce PC, rien n'est envoye nulle part. Sur le processeur d'un
Ryzen 7, trente secondes d'anglais se transcrivent en quinze : on garde
l'avance sur la lecture.
"""
from __future__ import annotations

import os
import re
import subprocess

from .voice_model import MODEL as WHISPER, RATE

TRANSLATOR = "Helsinki-NLP/opus-mt-tc-big-en-fr"
TRANSLATOR_SIZE = "440 Mo"
TRANSLATOR_BYTES = 455_000_000
TRANSLATOR_PATTERNS = ["*.json", "*.spm", "model.safetensors"]

# Ce que Whisper « entend » dans le silence ou la musique : on le jette.
HALLUCINATIONS = (
    "sous-titres réalisés", "sous-titrage", "amara.org", "merci d'avoir regardé",
    "thanks for watching", "thank you for watching", "subtitles by", "please subscribe",
    "like and subscribe", "transcription by", "www.", ".com",
)

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def fetch_translator() -> None:
    """Telecharge le modele de traduction (une fois)."""
    from .engine import ensure_streams
    ensure_streams()
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    from huggingface_hub import snapshot_download
    from huggingface_hub.utils import disable_progress_bars
    disable_progress_bars()
    snapshot_download(TRANSLATOR, allow_patterns=TRANSLATOR_PATTERNS)


def read_audio(ffmpeg: str, path: str, start: float, length: float):
    """Le son de `path`, de `start` a `start + length` secondes : 16 kHz mono,
    remonte et egalise (les videos basses ou confuses s'entendent mieux)."""
    import numpy as np
    argv = [ffmpeg, "-nostdin", "-v", "error", "-ss", f"{max(0.0, start):.3f}",
            "-t", f"{length:.3f}", "-i", path, "-vn", "-ac", "1", "-ar", str(RATE),
            "-af", "highpass=f=80,dynaudnorm=f=150:g=15", "-f", "f32le", "-"]
    done = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                          timeout=120, creationflags=_NO_WINDOW)
    return np.frombuffer(done.stdout or b"", dtype=np.float32)


def sentences(text: str) -> list:
    """Une ligne coupee en phrases : la traduction d'un bloc en oubliait la
    seconde (« What are you doing here? You scared me! »)."""
    parts = re.split(r"(?<=[.!?…])\s+", text.strip())
    return [part for part in parts if part.strip()]


def plausible(text: str) -> bool:
    low = text.lower().strip()
    if not low or not re.search(r"\w", low):
        return False
    return not any(word in low for word in HALLUCINATIONS)


class _Models:
    """Charges a la premiere demande, gardes ensuite."""

    def __init__(self):
        self.whisper = self.processor = None
        self.marian = self.marian_tok = None
        self.device, self.dtype = "cpu", None

    def load_whisper(self) -> None:
        if self.whisper is not None:
            return
        import torch
        from transformers import WhisperForConditionalGeneration, WhisperProcessor
        # Deux coeurs laisses a la video qui joue.
        torch.set_num_threads(max(1, (os.cpu_count() or 4) - 2))
        self.dtype = torch.float32
        self.processor = WhisperProcessor.from_pretrained(WHISPER)
        model = WhisperForConditionalGeneration.from_pretrained(WHISPER).eval()
        try:
            if torch.cuda.is_available() and torch.cuda.mem_get_info()[0] > 1500 * 2**20:
                model = model.to("cuda", dtype=torch.float16)
                self.device, self.dtype = "cuda", torch.float16
        except Exception:                                   # noqa: BLE001
            model = model.to("cpu", dtype=torch.float32)
            self.device, self.dtype = "cpu", torch.float32
        if self.device == "cpu":
            # Sur le processeur, les calculs en entiers 8 bits : une fois et
            # demie plus vite (trente secondes de son en dix au lieu de
            # quinze, sur un Ryzen 7), meme texte a l'essai. Le modele de
            # traduction, lui, ne le supporte pas.
            try:
                model = torch.ao.quantization.quantize_dynamic(
                    model, {torch.nn.Linear}, dtype=torch.qint8)
            except Exception:                               # noqa: BLE001
                pass
        self.whisper = model

    def load_marian(self) -> None:
        if self.marian is not None:
            return
        from transformers import MarianMTModel, MarianTokenizer
        self.marian_tok = MarianTokenizer.from_pretrained(TRANSLATOR)
        model = MarianMTModel.from_pretrained(TRANSLATOR).eval()
        if self.device == "cuda":
            model = model.to("cuda")
        self.marian = model

    # -- la reconnaissance ------------------------------------------------------
    def features(self, audio):
        return self.processor(audio, sampling_rate=RATE, return_tensors="pt") \
            .input_features.to(self.device, dtype=self.dtype)

    def language(self, features) -> str:
        """« en », « fr »… : la langue parlee dans ce morceau."""
        import torch
        with torch.inference_mode():
            ids = self.whisper.detect_language(features)
        token = self.processor.tokenizer.decode([int(ids.flatten()[0])])
        return token.strip("<|>") or "en"

    def transcribe(self, audio, language: str | None, task: str) -> tuple:
        """(langue, [(debut, fin, texte)]) en secondes depuis le debut du morceau."""
        import torch
        features = self.features(audio)
        if not language:
            language = self.language(features)
        options = dict(task=task, language=language, return_timestamps=True,
                       return_segments=True)
        # Sans les seuils de silence de Whisper (no_speech, logprob) : ils
        # doublaient le calcul -- douze secondes de son en treize, au lieu de
        # cinq et demie --, et le sous-titre arrivait apres la phrase. Les
        # phrases fantomes du silence sont ecartees par `plausible`.
        with torch.inference_mode():
            out = self.whisper.generate(features, **options)
        found = []
        segments = out.get("segments") if isinstance(out, dict) else None
        if segments:
            for seg in segments[0]:
                text = self.processor.decode(seg["tokens"], skip_special_tokens=True).strip()
                if plausible(text):
                    found.append((float(seg["start"]), float(seg["end"]), text))
        else:
            ids = out["sequences"] if isinstance(out, dict) else out
            text = self.processor.batch_decode(ids, skip_special_tokens=True)[0].strip()
            if plausible(text):
                found.append((0.0, len(audio) / RATE, text))
        return language, found

    # -- la traduction --------------------------------------------------------
    def translate(self, texts: list) -> list:
        """Chaque texte anglais en francais, phrase par phrase."""
        import torch
        if not texts:
            return []
        self.load_marian()
        pieces, owners = [], []
        for index, text in enumerate(texts):
            for sentence in sentences(text) or [text]:
                pieces.append(sentence)
                owners.append(index)
        out = [[] for _ in texts]
        for at in range(0, len(pieces), 16):
            batch = self.marian_tok(pieces[at:at + 16], return_tensors="pt", padding=True,
                                    truncation=True, max_length=256)
            if self.device == "cuda":
                batch = {k: v.to("cuda") for k, v in batch.items()}
            with torch.inference_mode():
                ids = self.marian.generate(**batch, num_beams=2, max_new_tokens=160)
            for owner, french in zip(owners[at:at + 16],
                                     self.marian_tok.batch_decode(ids, skip_special_tokens=True)):
                out[owner].append(french.strip())
        return [" ".join(parts) for parts in out]


def run_chunk(models: _Models, job: dict) -> dict:
    """Un morceau de video : son, langue, texte, et sa traduction s'il le faut."""
    start, length = float(job["start"]), float(job["length"])
    audio = read_audio(job["ffmpeg"], job["path"], start, length)
    result = {"start": start, "end": start + len(audio) / RATE, "language": job.get("language"),
              "task": "transcribe", "segments": []}
    if len(audio) < RATE // 4:
        # La fin de la video (ou un fichier sans son) : rien a dire.
        result["end"] = start + length if len(audio) == 0 else result["end"]
        result["eof"] = True
        return result
    models.load_whisper()
    language = job.get("language")
    french = bool(job.get("french"))
    task = "transcribe"
    if language is None:
        language = models.language(models.features(audio))
    if french and language not in ("en", "fr"):
        task = "translate"            # vers l'anglais, puis le francais
    language, found = models.transcribe(audio, language, task)
    segments = [[start + s, start + max(e, s + 0.4), text, None] for s, e, text in found]
    heard_until = start + len(audio) / RATE
    if (len(segments) >= 2 and len(audio) >= int(length * RATE) - RATE // 2
            and segments[-1][1] >= heard_until - 0.8):
        # La derniere phrase touche la fin du morceau : elle est sans doute
        # coupee (« je vais juste... » / « appelez un taxi »). On la laisse au
        # morceau suivant, qui commencera a son debut.
        result["end"] = segments.pop()[0]
    if french:
        if language == "fr" and task == "transcribe":
            for seg in segments:
                seg[3] = seg[2]
        else:
            for seg, fr in zip(segments, models.translate([s[2] for s in segments])):
                seg[3] = fr
    result.update(language=language, task=task, segments=segments)
    return result


def subs_child(requests, answers) -> None:
    import multiprocessing
    import queue as _queue
    os.environ["HF_HUB_OFFLINE"] = "1"
    from .engine import _lower_priority, ensure_streams
    from .ia import _die_with
    ensure_streams()
    _lower_priority()
    parent = multiprocessing.parent_process()
    _die_with(parent)
    models = _Models()
    while True:
        try:
            kind, payload = requests.get(timeout=2)
        except _queue.Empty:
            if parent is not None and not parent.is_alive():
                return
            continue
        except (EOFError, OSError):
            return
        if kind == "quit":
            return
        try:
            if kind == "load":
                models.load_whisper()
                if payload:
                    models.load_marian()
                answers.put(("ok", models.device))
            elif kind == "chunk":
                answers.put(("ok", run_chunk(models, payload)))
            elif kind == "translate":
                answers.put(("ok", models.translate(list(payload))))
            else:
                answers.put(("error", f"demande inconnue : {kind}"))
        except Exception as exc:                            # noqa: BLE001
            answers.put(("error", f"{type(exc).__name__} : {exc}"))
