"""Planning : instructions exécutées automatiquement à heure fixe, et surveillance du PC (batterie, disque)."""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time

from .. import config as _config
from .registry import runtime, tool

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_lock = threading.Lock()
_alerts: dict[str, float] = {}


def _file():
    return _config.DATA / "planning.json"


def _load() -> list[dict]:
    try:
        return json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save(items: list[dict]) -> None:
    _file().parent.mkdir(exist_ok=True)
    _file().write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_days(jours: str) -> list[int]:
    j = jours.lower()
    if not j or "tous" in j or "chaque jour" in j or "quotidien" in j:
        return list(range(7))
    if "semaine" in j or "ouvr" in j:
        return [0, 1, 2, 3, 4]
    if "week" in j:
        return [5, 6]
    days = [i for i, name in enumerate(JOURS) if name in j]
    return days or list(range(7))


@tool("planifier",
      "Programme une instruction que Jarvis exécutera automatiquement à heure fixe, comme si l'utilisateur la "
      "lui disait (ex. briefing du matin : météo, e-mails non lus, tâches du jour ; « à 18 h en semaine, "
      "ferme Outlook et lance Spotify »).",
      {"nom": {"type": "string"},
       "heure": {"type": "string", "description": "HH:MM (24 h)."},
       "jours": {"type": "string", "description": "'tous les jours', 'en semaine', 'week-end', ou 'lundi, jeudi'."},
       "instruction": {"type": "string", "description": "Ce que Jarvis doit faire, rédigé comme une demande."}},
      ["nom", "heure", "instruction"])
def planifier(nom: str, heure: str, instruction: str, jours: str = "tous les jours") -> str:
    h, m = (int(x) for x in re.split(r"[:h]", heure.strip())[:2])
    if not (0 <= h < 24 and 0 <= m < 60):
        return "ERREUR : heure invalide."
    with _lock:
        items = [i for i in _load() if i["nom"].lower() != nom.lower()]
        days = _parse_days(jours)
        items.append({"nom": nom, "heure": f"{h:02d}:{m:02d}", "jours": days, "instruction": instruction,
                      "dernier": ""})
        _save(items)
    return f"Planifié : « {nom} » à {h:02d}:{m:02d}, {', '.join(JOURS[d] for d in days)}."


@tool("planning", "Liste les instructions planifiées, ou en supprime une.",
      {"action": {"type": "string", "enum": ["lister", "supprimer"]}, "nom": {"type": "string"}}, ["action"])
def planning(action: str, nom: str = "") -> str:
    with _lock:
        items = _load()
        if action == "supprimer":
            kept = [i for i in items if nom.lower() not in i["nom"].lower()]
            _save(kept)
            return f"{len(items) - len(kept)} planification(s) supprimée(s)."
    return "\n".join(f"- {i['nom']} : {i['heure']} ({', '.join(JOURS[d] for d in i['jours'])}) — {i['instruction']}"
                     for i in items) or "Rien de planifié."


def due(now: dt.datetime | None = None) -> list[dict]:
    """Renvoie les planifications à exécuter maintenant et les marque comme faites pour aujourd'hui."""
    now = now or dt.datetime.now()
    today, hhmm = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
    out = []
    with _lock:
        items = _load()
        for i in items:
            # tolérance de 10 minutes si le PC était occupé ou en veille à l'heure dite
            late = (now - now.replace(hour=int(i["heure"][:2]), minute=int(i["heure"][3:]), second=0)).total_seconds()
            if now.weekday() in i["jours"] and i.get("dernier") != today and hhmm >= i["heure"] and late < 600:
                i["dernier"] = today
                out.append(i)
        if out:
            _save(items)
    return out


def _watch() -> list[str]:
    """Alertes de surveillance (au plus une par sujet toutes les 6 heures)."""
    import psutil
    alerts = []

    def once(key: str, message: str) -> None:
        if time.time() - _alerts.get(key, 0) > 6 * 3600:
            _alerts[key] = time.time()
            alerts.append(message)

    bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    if bat and not bat.power_plugged and bat.percent <= 15:
        once("batterie", f"La batterie est à {bat.percent:.0f} pour cent, pensez à brancher le chargeur.")
    for part in psutil.disk_partitions():
        try:
            u = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        if u.total > 10 * 2**30 and u.free < 5 * 2**30:
            once(f"disque{part.device}", f"Le disque {part.device[:2]} est presque plein : "
                                          f"il reste {u.free / 2**30:.1f} giga-octets.")
    return alerts


def start(core) -> None:
    """Lance le fil qui exécute le planning et la surveillance."""
    def loop() -> None:
        while True:
            time.sleep(20)
            try:
                for item in due():
                    answer = core.ask(f"[Instruction planifiée « {item['nom']} »] {item['instruction']}")
                    core.notify(answer)
                if runtime.config.get("surveillance", True):
                    for alert in _watch():
                        core.notify(alert)
            except Exception as e:
                print(f"[planning] {type(e).__name__}: {e}")

    threading.Thread(target=loop, daemon=True).start()
