"""Vie personnelle : agenda local, carnet de contacts, recherche dans l'historique des échanges."""
from __future__ import annotations

import datetime as dt
import json
import re
import threading

from .. import config as _config
from .registry import tool

_lock = threading.Lock()


def _read(name: str) -> list[dict]:
    try:
        return json.loads((_config.DATA / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _write(name: str, items: list[dict]) -> None:
    _config.DATA.mkdir(exist_ok=True)
    (_config.DATA / name).write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def _date(text: str) -> dt.date:
    text = text.strip().lower()
    today = dt.date.today()
    if text in ("", "aujourd'hui", "aujourdhui", "aujourd_hui"):
        return today
    if text == "demain":
        return today + dt.timedelta(days=1)
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        return dt.date(int(m[1]), int(m[2]), int(m[3]))
    m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?", text)
    if m:
        year = int(m[3]) if m[3] else today.year
        return dt.date(year + 2000 if year < 100 else year, int(m[2]), int(m[1]))
    raise ValueError(f"date illisible : {text!r} (attendu AAAA-MM-JJ)")


def _fmt(e: dict) -> str:
    d = dt.date.fromisoformat(e["date"])
    jours = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    line = f"- {jours[d.weekday()]} {d:%d/%m/%Y}" + (f" à {e['heure']}" if e.get("heure") else " (journée)")
    line += f" : {e['titre']}"
    if e.get("lieu"):
        line += f" — {e['lieu']}"
    return line


@tool("agenda",
      "Agenda de l'utilisateur : ajouter un rendez-vous, lister (aujourd'hui, demain, semaine, tout, ou une "
      "date), supprimer. Jarvis prévient à voix haute avant chaque rendez-vous. Calcule toi-même la date "
      "exacte à partir de la date du jour indiquée en tête de chaque demande.",
      {"action": {"type": "string", "enum": ["ajouter", "lister", "supprimer"]},
       "titre": {"type": "string"},
       "date": {"type": "string", "description": "AAAA-MM-JJ. Pour 'lister' : aujourd'hui, demain, semaine, tout ou une date."},
       "heure": {"type": "string", "description": "HH:MM, vide pour toute la journée."},
       "lieu": {"type": "string"},
       "rappel_minutes": {"type": "integer", "description": "Prévenir combien de minutes avant (défaut 15)."}},
      ["action"])
def agenda(action: str, titre: str = "", date: str = "", heure: str = "", lieu: str = "",
           rappel_minutes: int = 15) -> str:
    with _lock:
        items = _read("agenda.json")
        if action == "ajouter":
            if not titre:
                return "ERREUR : il faut un titre."
            d = _date(date)
            if heure:
                h, m = (int(x) for x in re.split(r"[:h]", heure.strip().rstrip("h") + ":0")[:2])
                heure = f"{h:02d}:{m:02d}"
            e = {"titre": titre, "date": d.isoformat(), "heure": heure, "lieu": lieu,
                 "rappel": int(rappel_minutes), "prevenu": False}
            items.append(e)
            _write("agenda.json", items)
            return "Ajouté à l'agenda :\n" + _fmt(e)
        if action == "supprimer":
            kept = [e for e in items if not (titre.lower() in e["titre"].lower()
                                             and (not date or e["date"] == _date(date).isoformat()))]
            _write("agenda.json", kept)
            return f"{len(items) - len(kept)} rendez-vous supprimé(s)."
    today = dt.date.today()
    key = date.strip().lower()
    if key in ("tout", "tous"):
        start, end = today, dt.date.max
    elif key == "semaine":
        start, end = today, today + dt.timedelta(days=7)
    else:
        start = end = _date(date)
    hits = sorted((e for e in items if start.isoformat() <= e["date"] <= end.isoformat()),
                  key=lambda e: (e["date"], e.get("heure") or ""))
    return "\n".join(_fmt(e) for e in hits) or "Aucun rendez-vous sur cette période."


def agenda_due(now: dt.datetime | None = None) -> list[str]:
    """Rendez-vous dont l'heure de rappel est arrivée (chacun n'est annoncé qu'une fois)."""
    now = now or dt.datetime.now()
    out = []
    with _lock:
        items = _read("agenda.json")
        for e in items:
            if e.get("prevenu") or not e.get("heure"):
                continue
            when = dt.datetime.fromisoformat(f"{e['date']}T{e['heure']}")
            if when - dt.timedelta(minutes=e.get("rappel", 15)) <= now < when + dt.timedelta(minutes=5):
                e["prevenu"] = True
                minutes = max(0, int((when - now).total_seconds() // 60))
                delay = f"dans {minutes} minutes" if minutes else "maintenant"
                out.append(f"Rendez-vous {delay} : {e['titre']}" + (f", {e['lieu']}" if e.get("lieu") else "") + ".")
        if out:
            _write("agenda.json", items)
    return out


@tool("contacts",
      "Carnet de contacts : ajouter (ou compléter), chercher, lister, supprimer. À consulter avant d'envoyer "
      "un e-mail à quelqu'un dont l'utilisateur ne donne que le nom.",
      {"action": {"type": "string", "enum": ["ajouter", "chercher", "lister", "supprimer"]},
       "nom": {"type": "string"}, "email": {"type": "string"}, "telephone": {"type": "string"},
       "note": {"type": "string"}}, ["action"])
def contacts(action: str, nom: str = "", email: str = "", telephone: str = "", note: str = "") -> str:
    def fmt(c: dict) -> str:
        return " — ".join(x for x in (c["nom"], c.get("email"), c.get("telephone"), c.get("note")) if x)

    with _lock:
        items = _read("contacts.json")
        if action == "ajouter":
            if not nom:
                return "ERREUR : il faut un nom."
            c = next((c for c in items if c["nom"].lower() == nom.lower()), None)
            if c is None:
                c = {"nom": nom}
                items.append(c)
            for k, v in (("email", email), ("telephone", telephone), ("note", note)):
                if v:
                    c[k] = v
            _write("contacts.json", items)
            return "Contact enregistré : " + fmt(c)
        if action == "supprimer":
            kept = [c for c in items if nom.lower() not in c["nom"].lower()]
            _write("contacts.json", kept)
            return f"{len(items) - len(kept)} contact(s) supprimé(s)."
    hits = items if action == "lister" else [c for c in items if nom.lower() in json.dumps(c, ensure_ascii=False).lower()]
    return "\n".join("- " + fmt(c) for c in sorted(hits, key=lambda c: c["nom"].lower())) or "Aucun contact trouvé."


@tool("historique",
      "Recherche dans l'historique de tout ce qui a été dit et fait avec Jarvis (y compris les jours "
      "précédents). Pour « qu'est-ce que je t'avais demandé hier », « où as-tu rangé le fichier ».",
      {"recherche": {"type": "string", "description": "Mot ou expression ; vide = les derniers échanges."},
       "jours": {"type": "integer", "description": "Remonter sur combien de jours (défaut 7)."}})
def historique(recherche: str = "", jours: int = 7) -> str:
    try:
        lines = (_config.DATA / "journal.log").read_text(encoding="utf-8").splitlines()
    except OSError:
        return "Historique vide."
    since = (dt.datetime.now() - dt.timedelta(days=max(1, int(jours)))).strftime("%Y-%m-%d")
    lines = [ln for ln in lines if ln[:10] >= since and "[etat]" not in ln]
    if recherche:
        words = [w for w in recherche.lower().split() if len(w) > 2] or [recherche.lower()]
        lines = [ln for ln in lines if any(w in ln.lower() for w in words)]
    return "\n".join(ln[:400] for ln in lines[-40:]) or "Rien trouvé dans l'historique."
