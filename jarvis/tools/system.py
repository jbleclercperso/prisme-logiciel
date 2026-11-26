"""Outils système Windows : applications, volume, clavier, écran, PowerShell, alimentation."""
from __future__ import annotations

import base64
import difflib
import io
import os
import re
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from .files import _norm
from .registry import runtime, tool

IS_WIN = sys.platform == "win32"
SCREEN_SCALE = 1.0      # pixels écran par pixel de la dernière capture envoyée au modèle

if IS_WIN:              # coordonnées souris et captures en pixels réels, quel que soit le zoom Windows
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
NO_WINDOW = 0x08000000 if IS_WIN else 0

# --- touches multimédia (aucune dépendance) ---------------------------------
VK = {"volume_muet": 0xAD, "volume_bas": 0xAE, "volume_haut": 0xAF,
      "suivant": 0xB0, "precedent": 0xB1, "stop": 0xB2, "lecture_pause": 0xB3}


def _press(vk: int, times: int = 1) -> None:
    if not IS_WIN:
        raise RuntimeError("Disponible uniquement sous Windows.")
    import ctypes
    for _ in range(times):
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
        ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
        time.sleep(0.01)


def _powershell(cmd: str, timeout: int = 60) -> str:
    exe = "powershell" if IS_WIN else "pwsh"
    r = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", cmd],
                       capture_output=True, text=True, timeout=timeout,
                       encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    text = out + (f"\n[stderr] {err}" if err else "")
    return (text or f"(terminé, code {r.returncode})")[:8000]


# --- applications -----------------------------------------------------------
def _start_menu_shortcuts() -> dict[str, Path]:
    roots = [Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / r"Microsoft\Windows\Start Menu\Programs",
             Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs",
             Path.home() / "Desktop", Path.home() / "OneDrive" / "Desktop", Path.home() / "OneDrive" / "Bureau"]
    found: dict[str, Path] = {}
    for root in roots:
        if root.is_dir():
            for ext in ("*.lnk", "*.url"):
                for f in root.rglob(ext):
                    found.setdefault(_norm(f.stem), f)
    return found


def _store_apps() -> dict[str, str]:
    """Applications du Microsoft Store / menu Démarrer (nom -> AppID)."""
    try:
        out = _powershell("Get-StartApps | ForEach-Object { $_.Name + '|' + $_.AppID }", timeout=20)
    except Exception:
        return {}
    apps = {}
    for line in out.splitlines():
        if "|" in line:
            name, _, appid = line.partition("|")
            apps[_norm(name)] = appid.strip()
    return apps


def _best(name: str, candidates) -> str | None:
    cands = list(candidates)
    if name in cands:
        return name
    partial = [c for c in cands if name in c or c in name]
    if partial:
        return min(partial, key=len)
    close = difflib.get_close_matches(name, cands, n=1, cutoff=0.72)
    return close[0] if close else None


def _launch(target: str) -> None:
    if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) and not re.match(r"^[a-z]:\\", target, re.I):
        if target.lower().startswith(("http://", "https://")):
            webbrowser.open(target)
            return
    if IS_WIN:
        os.startfile(target)  # type: ignore[attr-defined]
    else:
        subprocess.Popen([target])


@tool("ouvrir_application",
      "Lance un logiciel, un jeu ou une application Windows par son nom (ex: 'chrome', 'spotify', 'word').",
      {"nom": {"type": "string"},
       "arguments": {"type": "string", "description": "Optionnel : fichier ou URL à ouvrir avec."}},
      ["nom"])
def ouvrir_application(nom: str, arguments: str = "") -> str:
    key = _norm(nom)
    aliases = {_norm(k): str(v) for k, v in (runtime.config.get("applications") or {}).items()}
    hit = _best(key, aliases)
    if hit:
        target = aliases[hit]
        if arguments and IS_WIN and ":" not in target[:12]:
            subprocess.Popen(f'start "" "{target}" "{arguments}"', shell=True)
        else:
            _launch(target)
        return f"Lancé : {hit}"
    shortcuts = _start_menu_shortcuts()
    hit = _best(key, shortcuts)
    if hit:
        _launch(str(shortcuts[hit]))
        return f"Lancé : {shortcuts[hit].stem}"
    if IS_WIN:
        apps = _store_apps()
        hit = _best(key, apps)
        if hit:
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{apps[hit]}"])
            return f"Lancé : {hit}"
        try:
            os.startfile(nom)  # type: ignore[attr-defined]  # exécutable dans le PATH
            return f"Lancé : {nom}"
        except OSError:
            pass
    close = ", ".join(difflib.get_close_matches(key, list(shortcuts), n=5, cutoff=0.4)) or "aucune"
    return f"ERREUR : application '{nom}' introuvable. Applications connues proches : {close}"


@tool("fermer_application", "Ferme un logiciel par son nom de processus (ex: 'chrome', 'notepad').",
      {"nom": {"type": "string"}}, ["nom"],
      confirm=lambda a: f"Fermer {a['nom']} ? Le travail non enregistré sera perdu.")
def fermer_application(nom: str) -> str:
    import psutil
    key = _norm(nom).removesuffix(".exe")
    killed = 0
    for p in psutil.process_iter(["name"]):
        pname = _norm(p.info["name"] or "").removesuffix(".exe")
        if pname and (pname == key or key in pname):
            try:
                p.terminate()
                killed += 1
            except psutil.Error:
                pass
    return f"{killed} processus fermé(s) pour '{nom}'." if killed else f"Aucun processus trouvé pour '{nom}'."


@tool("ouvrir_url", "Ouvre une adresse web dans le navigateur par défaut.",
      {"url": {"type": "string"}}, ["url"])
def ouvrir_url(url: str) -> str:
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    webbrowser.open(url)
    return f"Ouvert : {url}"


@tool("recherche_navigateur",
      "Ouvre une recherche dans le navigateur de l'utilisateur (google, youtube, maps, amazon, wikipedia).",
      {"requete": {"type": "string"},
       "site": {"type": "string", "enum": ["google", "youtube", "maps", "amazon", "wikipedia"]}},
      ["requete"])
def recherche_navigateur(requete: str, site: str = "google") -> str:
    from urllib.parse import quote_plus
    q = quote_plus(requete)
    urls = {"google": f"https://www.google.com/search?q={q}",
            "youtube": f"https://www.youtube.com/results?search_query={q}",
            "maps": f"https://www.google.com/maps/search/{q}",
            "amazon": f"https://www.amazon.fr/s?k={q}",
            "wikipedia": f"https://fr.wikipedia.org/w/index.php?search={q}"}
    webbrowser.open(urls.get(site, urls["google"]))
    return f"Recherche '{requete}' ouverte sur {site}."


# --- son / média ------------------------------------------------------------
@tool("volume", "Règle le volume du PC : monter, baisser, couper/rétablir, ou fixer un niveau (0-100).",
      {"action": {"type": "string", "enum": ["monter", "baisser", "muet", "niveau"]},
       "niveau": {"type": "integer", "description": "0 à 100, pour l'action 'niveau'."}},
      ["action"])
def volume(action: str, niveau: int = 50) -> str:
    if action == "monter":
        _press(VK["volume_haut"], 5)
    elif action == "baisser":
        _press(VK["volume_bas"], 5)
    elif action == "muet":
        _press(VK["volume_muet"])
    else:
        niveau = max(0, min(100, int(niveau)))
        _press(VK["volume_bas"], 50)       # chaque pas = 2 %
        _press(VK["volume_haut"], niveau // 2)
        return f"Volume réglé à {niveau} %."
    return f"Volume : {action}."


@tool("media", "Contrôle la lecture en cours (Spotify, YouTube, VLC...).",
      {"action": {"type": "string", "enum": ["lecture_pause", "suivant", "precedent", "stop"]}}, ["action"])
def media(action: str) -> str:
    _press(VK[action])
    return f"Média : {action}."


# --- clavier / écran / fenêtres --------------------------------------------
@tool("taper_texte", "Tape du texte au clavier dans la fenêtre active (dictée).",
      {"texte": {"type": "string"},
       "entree": {"type": "boolean", "description": "Appuyer sur Entrée à la fin."}}, ["texte"])
def taper_texte(texte: str, entree: bool = False) -> str:
    import pyautogui
    import pyperclip
    old = ""
    try:
        old = pyperclip.paste()
    except Exception:
        pass
    pyperclip.copy(texte)           # collage : fiable avec les accents
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.15)
    if entree:
        pyautogui.press("enter")
    try:
        pyperclip.copy(old)
    except Exception:
        pass
    return "Texte tapé."


@tool("raccourci_clavier",
      "Envoie un raccourci ou une touche à la fenêtre active (ex: 'ctrl+s', 'alt+tab', 'win+d', 'enter', "
      "'tab', 'down', 'f5', 'esc'). 'repetitions' pour appuyer plusieurs fois.",
      {"touches": {"type": "string"}, "repetitions": {"type": "integer"}}, ["touches"])
def raccourci_clavier(touches: str, repetitions: int = 1) -> str:
    import pyautogui
    keys = [k.strip().lower() for k in touches.split("+") if k.strip()]
    keys = [{"windows": "win", "entree": "enter", "echap": "esc", "suppr": "delete", "maj": "shift"}.get(k, k)
            for k in keys]
    for _ in range(max(1, min(int(repetitions), 50))):
        pyautogui.hotkey(*keys)
    return f"Raccourci envoyé : {'+'.join(keys)}"


@tool("presse_papiers", "Lit ou remplace le contenu du presse-papiers.",
      {"action": {"type": "string", "enum": ["lire", "ecrire"]}, "texte": {"type": "string"}}, ["action"])
def presse_papiers(action: str, texte: str = "") -> str:
    import pyperclip
    if action == "ecrire":
        pyperclip.copy(texte)
        return "Copié dans le presse-papiers."
    return pyperclip.paste()[:8000] or "(presse-papiers vide)"


@tool("capture_ecran",
      "Prend une capture d'écran et te la montre, pour voir ce qui est affiché. "
      "'enregistrer' = true pour aussi la sauvegarder dans Images.",
      {"enregistrer": {"type": "boolean"}})
def capture_ecran(enregistrer: bool = False):
    from PIL import ImageGrab
    img = ImageGrab.grab()
    msg = f"Capture d'écran ({img.width}x{img.height})."
    if enregistrer:
        from .files import resolve
        path = resolve("images") / f"capture_{time.strftime('%Y%m%d_%H%M%S')}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(path)
        msg += f" Enregistrée : {path}"
    global SCREEN_SCALE
    full = img.width
    img.thumbnail((1568, 1568))
    SCREEN_SCALE = full / img.width
    msg += f" Image fournie en {img.width}x{img.height} : utilise ces coordonnées avec l'outil souris."
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=80)
    return {"text": msg, "image_b64": base64.standard_b64encode(buf.getvalue()).decode(),
            "media_type": "image/jpeg"}


@tool("fenetres", "Liste les fenêtres ouvertes, ou en active / réduit / agrandit / ferme une par son titre.",
      {"action": {"type": "string", "enum": ["lister", "activer", "reduire", "agrandir", "fermer"]},
       "titre": {"type": "string", "description": "Partie du titre de la fenêtre."}}, ["action"])
def fenetres(action: str, titre: str = "") -> str:
    import pygetwindow as gw
    wins = [w for w in gw.getAllWindows() if w.title.strip()]
    if action == "lister":
        return "\n".join(f"- {w.title}" for w in wins) or "Aucune fenêtre."
    match = [w for w in wins if _norm(titre) in _norm(w.title)]
    if not match:
        return f"ERREUR : aucune fenêtre contenant '{titre}'."
    w = match[0]
    if action == "activer":
        if w.isMinimized:
            w.restore()
        w.activate()
    elif action == "reduire":
        w.minimize()
    elif action == "agrandir":
        w.maximize()
    elif action == "fermer":
        w.close()
    return f"{action} : {w.title}"


# --- informations / commandes ----------------------------------------------
@tool("infos_systeme", "État du PC : processeur, mémoire, disques, batterie, processus les plus gourmands.")
def infos_systeme() -> str:
    import platform
    import psutil
    vm = psutil.virtual_memory()
    lines = [f"Système : {platform.system()} {platform.release()} ({platform.node()})",
             f"Processeur : {psutil.cpu_percent(interval=0.5)} % ({psutil.cpu_count()} cœurs)",
             f"Mémoire : {vm.percent} % utilisée ({vm.used / 2**30:.1f} / {vm.total / 2**30:.1f} Go)"]
    for part in psutil.disk_partitions():
        try:
            u = psutil.disk_usage(part.mountpoint)
            lines.append(f"Disque {part.device} : {u.percent} % ({u.free / 2**30:.0f} Go libres)")
        except OSError:
            pass
    bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    if bat:
        lines.append(f"Batterie : {bat.percent:.0f} % ({'en charge' if bat.power_plugged else 'sur batterie'})")
    procs = []
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            procs.append((p.info["memory_info"].rss, p.info["name"]))
        except Exception:
            pass
    top = sorted(procs, reverse=True)[:5]
    lines.append("Plus gourmands en mémoire : " + ", ".join(f"{n} ({m / 2**20:.0f} Mo)" for m, n in top))
    return "\n".join(lines)


BLOCKED = re.compile(
    r"format-volume|\bformat\s+[a-z]:|remove-item\b.*\s[a-z]:\\\\?\s*(-|$)|rd\s+/s\s+/q\s+[a-z]:\\\\?\s*$|"
    r"del\s+/[sq].*[a-z]:\\\\\*|clear-disk|diskpart|bcdedit|reg\s+delete\s+hk(lm|cr)|"
    r"set-executionpolicy|invoke-expression.*downloadstring|\biex\b.*\bnet\.webclient", re.I)


@tool("executer_powershell",
      "Exécute une commande PowerShell sur le PC et renvoie sa sortie. À utiliser quand aucun autre outil "
      "ne convient (réglages Windows, réseau, installation via winget, etc.).",
      {"commande": {"type": "string"}}, ["commande"],
      confirm=lambda a: f"Exécuter la commande PowerShell : {a['commande'][:200]} ?")
def executer_powershell(commande: str) -> str:
    if BLOCKED.search(commande):
        return "ERREUR : commande refusée par la sécurité de Jarvis (opération destructrice)."
    return _powershell(commande)


@tool("alimentation", "Verrouille la session, met en veille, redémarre ou éteint le PC.",
      {"action": {"type": "string", "enum": ["verrouiller", "veille", "redemarrer", "eteindre", "annuler"]}},
      ["action"],
      confirm=lambda a: f"Confirmer : {a['action']} l'ordinateur ?" if a["action"] in ("redemarrer", "eteindre", "veille") else "")
def alimentation(action: str) -> str:
    if not IS_WIN:
        return "ERREUR : disponible uniquement sous Windows."
    cmds = {"verrouiller": "rundll32.exe user32.dll,LockWorkStation",
            "veille": "rundll32.exe powrprof.dll,SetSuspendState 0,1,0",
            "redemarrer": "shutdown /r /t 30", "eteindre": "shutdown /s /t 30", "annuler": "shutdown /a"}
    subprocess.Popen(cmds[action], shell=True)
    if action in ("redemarrer", "eteindre"):
        return f"{action} dans 30 secondes (dites 'annule l'extinction' pour annuler)."
    return f"Fait : {action}."
