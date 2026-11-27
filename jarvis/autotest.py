"""Autotest : vérifie chaque fonction de Jarvis sur ce PC et écrit data/autotest.txt.

    python -m jarvis --autotest              (complet : parle et écoute le micro)
    python -m jarvis --autotest --sans-audio (sans micro ni haut-parleur)
"""
from __future__ import annotations

import json
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
              "pypdf", "playwright", "numpy", "sounddevice", "faster_whisper", "openwakeword", "onnxruntime", "edge_tts",
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


def t_souris():
    need_win()
    import pyautogui
    from .tools import system
    call("capture_ecran")
    call("souris", action="deplacer", x=300, y=200)
    x, y = pyautogui.position()
    want = (int(300 * system.SCREEN_SCALE), int(200 * system.SCREEN_SCALE))
    assert abs(x - want[0]) <= 2 and abs(y - want[1]) <= 2, f"souris en {x},{y}, attendu {want}"
    call("souris", action="defiler", quantite=-2)
    return f"pointeur placé en {x},{y} (échelle {system.SCREEN_SCALE:.2f})"


def t_reglages():
    need_win()
    from .tools.system import _powershell
    key = r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
    read = lambda: _powershell(f"(Get-ItemProperty -Path '{key}').AppsUseLightTheme").strip()  # noqa: E731
    before = read()
    call("reglage_windows", reglage="mode_sombre", valeur="oui")
    dark = read()
    call("reglage_windows", reglage="mode_sombre", valeur="oui" if before == "0" else "non")
    assert dark == "0", f"mode sombre non appliqué (valeur {dark!r})"
    call("notification", titre="Jarvis", message="Autotest")
    return f"mode sombre appliqué puis remis (valeur d'origine {before or 'absente'}) ; notification envoyée"


def t_perso():
    import datetime as dt
    day = (dt.date.today() + dt.timedelta(days=400)).isoformat()
    out = call("agenda", action="ajouter", titre="Autotest Jarvis", date=day, heure="09:30")
    assert "09:30" in out
    assert "Autotest Jarvis" in call("agenda", action="lister", date=day)
    call("agenda", action="supprimer", titre="Autotest Jarvis")
    return "rendez-vous ajouté, relu, supprimé"


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


def t_navigateur():
    d = Path(tempfile.mkdtemp(prefix="jarvis_web_"))
    (d / "page.html").write_text(
        "<html><head><meta charset='utf-8'><title>Essai</title></head><body><form onsubmit=\""
        "document.body.innerHTML='<h1>Bonjour '+document.getElementById('n').value+'</h1>';return false\">"
        "<input id='n' placeholder='Votre nom'><button type='submit'>Valider</button></form></body></html>",
        encoding="utf-8")
    cfg = tools.runtime.config.setdefault("navigateur", {})
    saved = cfg.get("invisible", False)
    cfg["invisible"] = True
    try:
        page = call("navigateur", action="ouvrir", url=(d / "page.html").as_uri())
        assert "[1] champ" in page and "Valider" in page, page
        call("navigateur", action="remplir", numero=1, valeur="Jarvis")
        call("navigateur", action="cliquer", texte="Valider")
        after = call("navigateur", action="lire")
        assert "Bonjour Jarvis" in after, after
        shot = call("navigateur", action="capture")
        assert len(shot["image_b64"]) > 500
    finally:
        cfg["invisible"] = saved
        tools.execute("navigateur", {"action": "fermer"})
    return f"formulaire rempli et validé dans {cfg.get('canal', 'msedge')}"


def t_widget():
    need_win()
    import gc
    from .widget import Widget

    class FakeCore:
        observers: list = []
        config = tools.runtime.config

    w = Widget(FakeCore())
    for item in (("etat", "écoute"), ("vous", "bonjour"), ("jarvis", "Bonjour JB."), ("etat", "parle")):
        w.events.put(item)
    seen = []
    w.root.after(900, lambda: seen.append((w.frames, w.layer is not None)))
    w.root.after(1000, w.root.destroy)
    w.run()
    del w
    gc.collect()
    assert seen and seen[0][0] >= 10, f"animation trop lente ou figée : {seen}"
    return f"{seen[0][0]} images en 0,9 s, {'transparence complète' if seen[0][1] else 'mode simple (bords nets)'}"


def t_interface():
    import urllib.request
    from .hud import Hud, find_browser

    class FakeCore:
        observers: list = []
        config = tools.runtime.config
        asked: list = []

        def ask(self, text, channel=None):
            self.asked.append(text)

    core = FakeCore()
    hud = Hud(core)
    try:
        page = urllib.request.urlopen(hud.url, timeout=10).read().decode("utf-8")
        assert "orb" in page and "Jarvis" in page
        status = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{hud.port}/etat?t={hud.token}", timeout=10).read())
        assert "cpu" in status and "agenda" in status
        req = urllib.request.Request(f"http://127.0.0.1:{hud.port}/ask?t={hud.token}",
                                     data=json.dumps({"text": "bonjour"}).encode(), method="POST")
        urllib.request.urlopen(req, timeout=10).read()
        time.sleep(0.3)
        assert core.asked == ["bonjour"], core.asked
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{hud.port}/etat?t=faux", timeout=10)
            raise AssertionError("une requête sans le bon jeton a été acceptée")
        except urllib.error.HTTPError as e:
            assert e.code == 403
    finally:
        hud.server.shutdown()
    browser = find_browser()
    if IS_WIN and not browser:
        raise RuntimeError("ni Edge ni Chrome trouvé pour afficher la fenêtre")
    return f"page servie, commande reçue, accès sans jeton refusé ; fenêtre via {browser}"


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
    step("Souris (coordonnées de capture)", t_souris)
    step("Réglages Windows et notification", t_reglages)
    step("Volume et touches multimédia", t_volume)
    step("Agenda", t_perso)
    step("Réseau", t_reseau)
    step("Météo (Internet)", t_meteo, optional=True)
    step("Minuteur", t_rappels)
    step("Extensions (outil créé à chaud)", t_extension)
    step("Interface (forme animée)", t_widget)
    step("Interface tableau", t_interface)
    step("Navigateur piloté (formulaire)", t_navigateur)
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
