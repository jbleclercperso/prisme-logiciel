"""Outils avancés : souris guidée par capture d'écran, réglages Windows, logiciels, réseau, archives, tâches."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from ..config import DATA
from . import system
from .files import resolve
from .registry import tool
from .system import IS_WIN, NO_WINDOW, _powershell


# --- souris (coordonnées = pixels de la dernière capture d'écran) -----------
@tool("souris",
      "Agit avec la souris. Les coordonnées x,y sont celles de la DERNIÈRE image renvoyée par capture_ecran "
      "(fais toujours une capture avant, et une après pour vérifier). Sert à cliquer sur un bouton, un lien, "
      "un champ, dans n'importe quel logiciel.",
      {"action": {"type": "string", "enum": ["clic", "double_clic", "clic_droit", "deplacer", "defiler", "glisser"]},
       "x": {"type": "integer"}, "y": {"type": "integer"},
       "x2": {"type": "integer", "description": "Arrivée, pour 'glisser'."},
       "y2": {"type": "integer"},
       "quantite": {"type": "integer", "description": "Pour 'defiler' : crans, positif = vers le haut."}},
      ["action"])
def souris(action: str, x: int | None = None, y: int | None = None, x2: int | None = None,
           y2: int | None = None, quantite: int = -5) -> str:
    import pyautogui
    pyautogui.FAILSAFE = False
    s = system.SCREEN_SCALE
    pos = (int(x * s), int(y * s)) if x is not None and y is not None else None
    if action == "defiler":
        if pos:
            pyautogui.moveTo(*pos)
        pyautogui.scroll(int(quantite) * 120 if IS_WIN else int(quantite))
        return f"Défilement de {quantite} crans."
    if pos is None:
        return "ERREUR : x et y sont nécessaires."
    if action == "clic":
        pyautogui.click(*pos)
    elif action == "double_clic":
        pyautogui.doubleClick(*pos)
    elif action == "clic_droit":
        pyautogui.rightClick(*pos)
    elif action == "deplacer":
        pyautogui.moveTo(*pos)
    elif action == "glisser":
        if x2 is None or y2 is None:
            return "ERREUR : x2 et y2 sont nécessaires pour glisser."
        pyautogui.moveTo(*pos)
        pyautogui.dragTo(int(x2 * s), int(y2 * s), duration=0.4, button="left")
    time.sleep(0.3)
    return f"{action} en ({x}, {y})."


# --- réglages Windows -------------------------------------------------------
PAGES = {"wifi": "network-wifi", "bluetooth": "bluetooth", "affichage": "display", "son": "sound",
         "mises_a_jour": "windowsupdate", "applications": "appsfeatures", "batterie": "batterysaver",
         "stockage": "storagesense", "notifications": "notifications", "imprimantes": "printers",
         "souris": "mousetouchpad", "clavier": "keyboard", "confidentialite": "privacy",
         "personnalisation": "personalization", "comptes": "yourinfo", "vpn": "network-vpn",
         "date_heure": "dateandtime", "demarrage": "startupapps", "accueil": ""}


@tool("reglage_windows",
      "Modifie un réglage Windows : luminosite (0-100), mode_sombre (oui/non), fond_ecran (chemin d'image), "
      "wifi (oui/non), vider_corbeille, ou ouvre une page des Paramètres (valeur = "
      + ", ".join(PAGES) + ").",
      {"reglage": {"type": "string", "enum": ["luminosite", "mode_sombre", "fond_ecran", "wifi",
                                               "vider_corbeille", "page_parametres"]},
       "valeur": {"type": "string"}}, ["reglage"],
      confirm=lambda a: "Vider définitivement la corbeille ?" if a["reglage"] == "vider_corbeille" else "")
def reglage_windows(reglage: str, valeur: str = "") -> str:
    if not IS_WIN:
        return "ERREUR : disponible uniquement sous Windows."
    oui = valeur.strip().lower() in ("oui", "on", "true", "1", "active", "activer")
    if reglage == "luminosite":
        n = max(0, min(100, int(float(valeur))))
        out = _powershell("(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods)"
                          f".WmiSetBrightness(1,{n})")
        return ("ERREUR : cet écran ne permet pas le réglage logiciel de la luminosité (écran externe ?)."
                if "stderr" in out else f"Luminosité réglée à {n} %.")
    if reglage == "mode_sombre":
        v = 0 if oui else 1
        key = r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        _powershell(f"Set-ItemProperty -Path '{key}' -Name AppsUseLightTheme -Value {v}; "
                    f"Set-ItemProperty -Path '{key}' -Name SystemUsesLightTheme -Value {v}")
        return f"Mode {'sombre' if oui else 'clair'} activé."
    if reglage == "fond_ecran":
        import ctypes
        p = resolve(valeur)
        if not p.is_file():
            return f"ERREUR : image introuvable : {p}"
        ctypes.windll.user32.SystemParametersInfoW(20, 0, str(p), 3)
        return f"Fond d'écran changé : {p.name}"
    if reglage == "wifi":
        out = _powershell(f"netsh interface set interface name='Wi-Fi' admin={'enabled' if oui else 'disabled'}")
        if "stderr" in out or "admin" in out.lower() or "élévation" in out.lower() or "elevation" in out.lower():
            os.startfile("ms-settings:network-wifi")  # type: ignore[attr-defined]
            return "Droits administrateur requis : j'ai ouvert la page Wi-Fi des Paramètres à la place."
        return f"Wi-Fi {'activé' if oui else 'désactivé'}."
    if reglage == "vider_corbeille":
        _powershell("Clear-RecycleBin -Force -ErrorAction SilentlyContinue")
        return "Corbeille vidée."
    page = PAGES.get(valeur.strip().lower().replace(" ", "_"), valeur.strip())
    os.startfile(f"ms-settings:{page}")  # type: ignore[attr-defined]
    return f"Page des paramètres ouverte : {valeur or 'accueil'}."


# --- logiciels --------------------------------------------------------------
@tool("logiciels",
      "Gère les logiciels avec winget : chercher, installer, desinstaller, mettre_a_jour (nom vide = tout), "
      "ou lister ceux installés.",
      {"action": {"type": "string", "enum": ["chercher", "installer", "desinstaller", "mettre_a_jour", "lister"]},
       "nom": {"type": "string"}}, ["action"],
      confirm=lambda a: (f"{a['action']} {a.get('nom') or 'tous les logiciels'} ?"
                         if a["action"] in ("installer", "desinstaller", "mettre_a_jour") else ""))
def logiciels(action: str, nom: str = "") -> str:
    if not shutil.which("winget"):
        return "ERREUR : winget est introuvable sur ce PC (installer « App Installer » depuis le Microsoft Store)."
    accept = ["--accept-source-agreements", "--disable-interactivity"]
    cmds = {"chercher": ["search", nom] + accept,
            "lister": ["list"] + ([nom] if nom else []) + accept,
            "installer": ["install", nom, "--silent", "--accept-package-agreements"] + accept,
            "desinstaller": ["uninstall", nom, "--silent"] + accept,
            "mettre_a_jour": ["upgrade"] + ([nom] if nom else ["--all"]) + ["--silent", "--accept-package-agreements"] + accept}
    r = subprocess.run(["winget"] + cmds[action], capture_output=True, text=True, timeout=900,
                       encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip() and not set(ln.strip()) <= set("-\\|/ █▒")]
    return "\n".join(lines)[-5000:] or f"(terminé, code {r.returncode})"


# --- réseau / téléchargement / archives / impression ------------------------
@tool("reseau", "État du réseau : adresses IP, Wi-Fi connecté, accès Internet.")
def reseau() -> str:
    import socket
    import psutil
    lines = []
    for name, addrs in psutil.net_if_addrs().items():
        ips = [a.address for a in addrs if a.family == socket.AF_INET and not a.address.startswith("127.")]
        if ips:
            lines.append(f"{name} : {', '.join(ips)}")
    if IS_WIN:
        out = _powershell("netsh wlan show interfaces")
        for ln in out.splitlines():
            if ln.strip().lower().startswith(("ssid", "signal")):
                lines.append(ln.strip())
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=4).close()
        lines.append("Internet : connecté")
    except OSError:
        lines.append("Internet : INJOIGNABLE")
    return "\n".join(lines)


@tool("telecharger", "Télécharge un fichier depuis une URL dans le dossier Téléchargements (ou 'dossier').",
      {"url": {"type": "string"}, "dossier": {"type": "string"}}, ["url"])
def telecharger(url: str, dossier: str = "telechargements") -> str:
    import re
    import requests
    from urllib.parse import unquote, urlparse
    dest = resolve(dossier)
    dest.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=60, headers={"User-Agent": "Mozilla/5.0"}) as r:
        r.raise_for_status()
        cd = re.search(r'filename="?([^";]+)', r.headers.get("content-disposition", ""))
        name = cd.group(1) if cd else unquote(Path(urlparse(r.url).path).name) or "telechargement"
        name = re.sub(r'[<>:"/\\|?*]', "_", name)
        path = dest / name
        size = 0
        with open(path, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
                size += len(chunk)
    return f"Téléchargé : {path} ({size / 2**20:.1f} Mo)"


@tool("archive", "Compresse un fichier/dossier en .zip, ou décompresse une archive (zip, tar...).",
      {"action": {"type": "string", "enum": ["compresser", "decompresser"]},
       "source": {"type": "string"}, "destination": {"type": "string", "description": "Optionnel."}},
      ["action", "source"])
def archive(action: str, source: str, destination: str = "") -> str:
    src = resolve(source)
    if not src.exists():
        return f"ERREUR : introuvable : {src}"
    if action == "compresser":
        base = resolve(destination) if destination else src.parent / src.stem
        base = base.with_suffix("") if base.suffix == ".zip" else base
        out = shutil.make_archive(str(base), "zip", root_dir=src.parent, base_dir=src.name)
        return f"Archive créée : {out}"
    dest = resolve(destination) if destination else src.parent / src.stem
    shutil.unpack_archive(str(src), str(dest))
    return f"Décompressé dans : {dest}"


@tool("imprimer", "Envoie un document à l'imprimante par défaut.",
      {"chemin": {"type": "string"}}, ["chemin"],
      confirm=lambda a: f"Imprimer {resolve(a['chemin']).name} ?")
def imprimer(chemin: str) -> str:
    p = resolve(chemin)
    if not p.is_file():
        return f"ERREUR : introuvable : {p}"
    os.startfile(str(p), "print")  # type: ignore[attr-defined]
    return f"Impression lancée : {p.name}"


@tool("notification", "Affiche une notification Windows (bulle) avec un titre et un message.",
      {"titre": {"type": "string"}, "message": {"type": "string"}}, ["titre", "message"])
def notification(titre: str, message: str) -> str:
    if not IS_WIN:
        return "ERREUR : disponible uniquement sous Windows."
    esc = lambda s: s.replace("'", "''")  # noqa: E731
    script = ("Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; "
              "$n = New-Object System.Windows.Forms.NotifyIcon; $n.Icon = [System.Drawing.SystemIcons]::Information; "
              f"$n.Visible = $true; $n.ShowBalloonTip(8000, '{esc(titre)}', '{esc(message)}', 'Info'); "
              "Start-Sleep -Seconds 9; $n.Dispose()")
    subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
                     creationflags=NO_WINDOW)
    return "Notification affichée."


# --- liste de tâches --------------------------------------------------------
def _tasks_file() -> Path:
    return DATA / "taches.json"


def _load_tasks() -> list[dict]:
    try:
        return json.loads(_tasks_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


@tool("taches", "Liste de choses à faire de l'utilisateur : ajouter, lister, terminer (cocher) ou supprimer.",
      {"action": {"type": "string", "enum": ["ajouter", "lister", "terminer", "supprimer"]},
       "texte": {"type": "string", "description": "La tâche, ou un extrait pour la retrouver."}}, ["action"])
def taches(action: str, texte: str = "") -> str:
    items = _load_tasks()
    if action == "ajouter":
        items.append({"texte": texte.strip(), "fait": False, "date": time.strftime("%d/%m/%Y")})
    elif action in ("terminer", "supprimer"):
        hits = [t for t in items if texte.lower() in t["texte"].lower()]
        if not hits:
            return f"ERREUR : aucune tâche contenant '{texte}'."
        for t in hits:
            if action == "terminer":
                t["fait"] = True
            else:
                items.remove(t)
    if action != "lister":
        _tasks_file().parent.mkdir(exist_ok=True)
        _tasks_file().write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    todo = [t for t in items if not t["fait"]]
    return (f"{len(todo)} tâche(s) à faire :\n" + "\n".join(f"- {t['texte']} (ajoutée le {t['date']})" for t in todo)
            if todo else "Aucune tâche en attente.")
