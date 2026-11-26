"""Autotest : vérifie chaque fonction de Jarvis sur ce PC et écrit data/autotest.txt.

    python -m jarvis --autotest              (complet : parle et écoute le micro)
    python -m jarvis --autotest --sans-audio (sans micro ni haut-parleur)
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

from . import __version__, tools
from .config import DATA

IS_WIN = sys.platform == "win32"
results: list[tuple[str, str, str]] = []


def step(name: str, fn, optional: bool = False) -> None:
    t0 = time.time()
    try:
        detail = fn()
        status = "OK"
    except Skip as e:
        status, detail = "IGNORÉ", str(e)
    except Exception as e:
        status = "AVERT." if optional else "ÉCHEC"
        detail = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"
    line = str(detail or "").strip()
    results.append((status, name, line))
    first = line.splitlines()[0][:110] if line else ""
    print(f"[{status:7}] {name} ({time.time() - t0:.1f}s) {first}", flush=True)


class Skip(Exception):
    pass


def call(name: str, **args):
    out = tools.execute(name, args)
    text = out.get("text", "") if isinstance(out, dict) else str(out)
    if text.startswith("ERREUR"):
        raise RuntimeError(text)
    return out


def need_win() -> None:
    if not IS_WIN:
        raise Skip("Windows uniquement")


# ----------------------------------------------------------------- étapes
def t_imports():
    import importlib
    missing = []
    for m in ["claude_agent_sdk", "psutil", "pyautogui", "pyperclip", "PIL", "pygetwindow", "send2trash",
              "pypdf", "numpy", "sounddevice", "faster_whisper", "openwakeword", "onnxruntime", "edge_tts",
              "pygame", "pyttsx3", "keyboard"]:
        try:
            importlib.import_module(m)
        except Exception as e:
            missing.append(f"{m} ({type(e).__name__}: {str(e)[:60]})")
    if missing:
        raise RuntimeError("modules manquants : " + "; ".join(missing))
    return "tous les modules sont importables"


def t_claude():
    from .__main__ import logged_in
    from .brain import claude_cli
    cli = claude_cli()
    if not cli:
        raise RuntimeError("programme Claude Code introuvable")
    if os.environ.get("JARVIS_CI"):
        import subprocess
        v = subprocess.run([cli, "--version"], capture_output=True, text=True, timeout=120).stdout.strip()
        return f"Claude Code {v} présent (connexion non testée en intégration continue)"
    if not logged_in():
        raise RuntimeError("non connecté : lancez Connexion-Claude.bat")
    return "connecté au compte Claude"


def t_claude_reponse():
    if os.environ.get("JARVIS_CI"):
        raise Skip("pas de compte Claude en intégration continue")
    from .brain import make_brain
    brain = make_brain(tools.runtime.config)
    seen = []
    brain.on_tool = lambda n, a: seen.append(n)
    answer = brain.ask("Test technique : appelle l'outil lire_memoire puis réponds uniquement « test réussi ».")
    brain.reset()
    if "lire_memoire" not in seen:
        raise RuntimeError(f"Claude n'a pas appelé l'outil. Réponse : {answer}")
    return f"réponse : {answer[:80]}"


def t_fichiers():
    d = Path(tempfile.mkdtemp(prefix="jarvis_test_"))
    f = d / "essai.txt"
    call("ecrire_fichier", chemin=str(f), contenu="héllo Jarvis")
    assert call("lire_fichier", chemin=str(f)) == "héllo Jarvis"
    call("copier", source=str(f), destination=str(d / "copie.txt"))
    call("archive", action="compresser", source=str(f))
    assert (d / "essai.zip").exists()
    saved = tools.runtime.confirm
    tools.runtime.confirm = lambda q: True
    try:
        call("supprimer", chemin=str(d / "copie.txt"))
    finally:
        tools.runtime.confirm = saved
    assert not (d / "copie.txt").exists()
    from .tools.files import _known_folders
    k = _known_folders()
    return f"bureau={k['bureau']} ; téléchargements={k['telechargements']}"


def t_systeme():
    return call("infos_systeme").replace("\n", " | ")[:300]


def t_powershell():
    need_win()
    from .tools.system import _powershell
    out = _powershell("Write-Output ('é' + (2+3))")
    assert "é5" in out, repr(out)
    return f"sortie : {out}"


def t_applis():
    need_win()
    from .tools.system import _start_menu_shortcuts, _store_apps
    s, a = _start_menu_shortcuts(), _store_apps()
    if not s and not a:
        raise RuntimeError("aucune application détectée")
    return f"{len(s)} raccourcis du menu Démarrer, {len(a)} applications Windows"


def t_presse_papiers():
    need_win()
    call("presse_papiers", action="ecrire", texte="jarvis-été-42")
    got = call("presse_papiers", action="lire")
    assert got == "jarvis-été-42", got
    return "écriture et lecture identiques"


def t_capture():
    need_win()
    out = call("capture_ecran")
    assert len(out["image_b64"]) > 1000
    from .tools import system
    return f"{out['text']} (échelle {system.SCREEN_SCALE:.2f})"


def t_bloc_notes():
    """Scénario complet : ouvrir le Bloc-notes, y taper du texte, le relire, le fermer."""
    need_win()
    import pyautogui
    saved = tools.runtime.confirm
    tools.runtime.confirm = lambda q: True
    try:
        call("ouvrir_application", nom="bloc-notes")
        title = ""
        for _ in range(40):
            time.sleep(0.5)
            wins = call("fenetres", action="lister")
            hit = [w for w in wins.splitlines() if "bloc-notes" in w.lower() or "notepad" in w.lower()]
            if hit:
                title = hit[0][2:]
                break
        if not title:
            raise RuntimeError(f"fenêtre du Bloc-notes introuvable. Fenêtres : {wins[:300]}")
        try:
            call("fenetres", action="activer", titre=title[:20])
        except Exception:
            pass
        time.sleep(1.0)
        call("taper_texte", texte="Jarvis écrit ça 123")
        time.sleep(0.5)
        call("raccourci_clavier", touches="ctrl+a")
        pyautogui.hotkey("ctrl", "c")
        time.sleep(0.5)
        got = call("presse_papiers", action="lire")
        typed = "Jarvis écrit ça 123" in got
        call("souris", action="deplacer", x=200, y=200)
        closed = call("fermer_application", nom="notepad")
        if not typed:
            raise RuntimeError(f"fenêtre ouverte ({title}) mais texte non retrouvé : {got[:80]!r}")
        return f"fenêtre « {title} » ; texte tapé et relu ; {closed}"
    finally:
        tools.runtime.confirm = saved


def t_volume():
    need_win()
    call("volume", action="niveau", niveau=30)
    return call("media", action="lecture_pause") and "touches multimédia envoyées (volume 30 %)"


def t_reseau():
    return call("reseau").replace("\n", " | ")[:250]


def t_meteo():
    return call("meteo", ville="Paris").splitlines()[0]


def t_rappels():
    got = []
    saved = tools.runtime.notify
    tools.runtime.notify = got.append
    try:
        call("minuteur", secondes=1, message="ding")
        time.sleep(1.6)
    finally:
        tools.runtime.notify = saved
    assert got == ["ding"], got
    return "minuteur déclenché"


# ----------------------------------------------------------------- voix
_audio: dict = {}


def t_tts_fichier():
    import asyncio
    import edge_tts
    cfg = tools.runtime.config["voix"]
    path = os.path.join(tempfile.mkdtemp(prefix="jarvis_tts_"), "phrase.mp3")
    asyncio.run(edge_tts.Communicate("Jarvis, ouvre le bloc-notes et mets le volume à trente.",
                                     cfg["tts_voix"], rate=cfg.get("tts_vitesse", "+0%")).save(path))
    size = os.path.getsize(path)
    assert size > 5000, size
    _audio["fr"] = path
    return f"voix {cfg['tts_voix']} : {size // 1024} Ko"


def t_whisper():
    from faster_whisper import WhisperModel
    from .voice import decode_file
    cfg = tools.runtime.config["voix"]
    name = os.environ.get("JARVIS_WHISPER", cfg["whisper_modele"])
    t0 = time.time()
    device = cfg.get("whisper_device", "cpu")
    model = WhisperModel(name, device=device, compute_type="float16" if device == "cuda" else "int8")
    load = time.time() - t0
    if "fr" not in _audio:
        raise Skip(f"modèle {name} chargé en {load:.0f}s, mais pas d'audio de test (synthèse en échec)")
    audio = decode_file(_audio["fr"])
    t0 = time.time()
    segments, _ = model.transcribe(audio, language="fr", beam_size=5, vad_filter=True)
    text = " ".join(s.text.strip() for s in segments)
    dur = time.time() - t0
    low = text.lower()
    if not ("bloc" in low and "volume" in low):
        raise RuntimeError(f"transcription inattendue : {text!r}")
    return f"modèle {name} chargé en {load:.0f}s ; {len(audio) / 16000:.1f}s d'audio transcrites en {dur:.1f}s : {text!r}"


def t_reveil():
    import asyncio
    import numpy as np
    import openwakeword
    from openwakeword.model import Model
    name = tools.runtime.config["voix"].get("mot_reveil", "hey_jarvis")
    openwakeword.utils.download_models([name])
    model = Model(wakeword_models=[name], inference_framework="onnx")
    silence = max(max(model.predict(np.zeros(1280, dtype=np.int16)).values()) for _ in range(20))
    assert silence < 0.3, f"faux déclenchement sur du silence ({silence:.2f})"
    best = -1.0
    try:
        import edge_tts
        from .voice import decode_file
        path = os.path.join(tempfile.mkdtemp(prefix="jarvis_wake_"), "hey.mp3")
        asyncio.run(edge_tts.Communicate("Hey Jarvis!", "en-US-GuyNeural").save(path))
        pcm = (decode_file(path) * 32767).astype(np.int16)
        pcm = np.concatenate([np.zeros(16000, np.int16), pcm, np.zeros(16000, np.int16)])
        model.reset()
        for i in range(0, len(pcm) - 1280, 1280):
            best = max(best, max(model.predict(pcm[i:i + 1280]).values()))
    except Exception as e:
        return f"modèle chargé, silence={silence:.2f} ; essai avec voix de synthèse impossible ({e})"
    seuil = float(tools.runtime.config["voix"].get("seuil_reveil", 0.5))
    verdict = "détecté" if best >= seuil else "NON détecté (voix de synthèse, à vérifier avec votre voix)"
    return f"silence={silence:.2f} ; « Hey Jarvis » synthétique : score {best:.2f} (seuil {seuil}) -> {verdict}"


def t_micro():
    import numpy as np
    import sounddevice as sd
    from .voice import list_microphones
    mics = list_microphones()
    if not mics:
        raise RuntimeError("aucun microphone détecté")
    rec = sd.rec(int(2 * 16000), samplerate=16000, channels=1, dtype="int16",
                 device=tools.runtime.config["voix"].get("micro"))
    sd.wait()
    level = float(np.sqrt(np.mean(rec.astype("float32") ** 2)))
    if level < 1:
        raise RuntimeError("le micro ne capte rien (niveau 0) : vérifiez le micro par défaut et les autorisations")
    return f"niveau ambiant {level:.0f} ; micros : " + " | ".join(mics.splitlines()[:6])


def t_parole():
    from .voice import Speaker
    Speaker(tools.runtime.config["voix"]).say("Autotest de Jarvis : si vous m'entendez, la voix fonctionne.")
    return "phrase prononcée (vérifiez que vous l'avez entendue)"


def t_extension():
    from .tools import extensions, registry
    saved = tools.runtime.confirm
    tools.runtime.confirm = lambda q: True
    code = ('from jarvis.tools.registry import tool\n\n'
            '@tool("autotest_double", "Double un nombre.", {"n": {"type": "integer"}}, ["n"])\n'
            'def autotest_double(n: int) -> str:\n    return str(n * 2)\n')
    try:
        call("creer_extension", nom="autotest_tmp", code=code)
        assert call("autotest_double", n=21) == "42"
        call("extensions", action="supprimer", nom="autotest_tmp")
        assert "autotest_double" not in tools.TOOLS
    finally:
        tools.runtime.confirm = saved
        registry.dirty = False
    return "extension créée, exécutée puis supprimée"


def t_fenetre():
    need_win()
    from .ui import Window

    class Fake:
        observers: list = []
        config = tools.runtime.config
    w = Window(Fake(), "Jarvis")
    w.events.put(("jarvis", "Bonjour Monsieur."))
    w.events.put(("etat", "écoute"))
    w.root.after(600, w.root.destroy)
    w.run()
    return "fenêtre affichée puis refermée"


def run(config: dict, audio: bool = True) -> int:
    tools.runtime.config = config
    print(f"Autotest Jarvis {__version__} — Python {sys.version.split()[0]} sur {sys.platform}\n")
    step("Modules installés", t_imports)
    step("Claude Code (forfait)", t_claude)
    step("Claude répond et appelle un outil", t_claude_reponse)
    step("Fichiers (écrire, lire, copier, zip, corbeille)", t_fichiers)
    step("État du système", t_systeme)
    step("PowerShell", t_powershell)
    step("Détection des applications", t_applis)
    step("Presse-papiers", t_presse_papiers)
    step("Capture d'écran", t_capture)
    step("Bloc-notes : ouvrir, taper, relire, fermer", t_bloc_notes)
    step("Volume et touches multimédia", t_volume)
    step("Réseau", t_reseau)
    step("Météo (Internet)", t_meteo, optional=True)
    step("Minuteur", t_rappels)
    step("Extensions (outil créé à chaud)", t_extension)
    step("Fenêtre flottante", t_fenetre)
    step("Synthèse vocale (fichier)", t_tts_fichier)
    step("Reconnaissance vocale Whisper", t_whisper)
    step("Mot de réveil « Hey Jarvis »", t_reveil)
    if audio:
        step("Microphone", t_micro)
        step("Haut-parleur", t_parole)
    fails = [r for r in results if r[0] == "ÉCHEC"]
    lines = [f"Autotest Jarvis {__version__} — {time.strftime('%d/%m/%Y %H:%M')} — Python "
             f"{sys.version.split()[0]} — {sys.platform}", ""]
    for status, name, detail in results:
        lines.append(f"[{status}] {name}")
        lines += [f"    {ln}" for ln in detail.splitlines()]
    lines += ["", f"BILAN : {len(results) - len(fails)} / {len(results)} sans échec."]
    DATA.mkdir(exist_ok=True)
    report = DATA / "autotest.txt"
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n{lines[-1]}\nRapport complet : {report}")
    return 1 if fails else 0
