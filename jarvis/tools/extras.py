"""Outils divers : e-mail, météo, lecture de page web, mémoire, minuteurs et rappels."""
from __future__ import annotations

import datetime as dt
import json
import mimetypes
import os
import re
import threading
import webbrowser
from email.message import EmailMessage
from pathlib import Path

from ..config import DATA
from .files import resolve
from .registry import runtime, tool

# --- e-mail -----------------------------------------------------------------


def _mail_creds():
    return os.environ.get("JARVIS_EMAIL_ADDRESS", ""), os.environ.get("JARVIS_EMAIL_PASSWORD", "")


@tool("envoyer_email",
      "Envoie un e-mail (avec pièces jointes éventuelles). Si la messagerie n'est pas configurée, "
      "ouvre un brouillon pré-rempli dans le logiciel de messagerie.",
      {"destinataire": {"type": "string", "description": "Adresse e-mail (plusieurs séparées par des virgules)."},
       "sujet": {"type": "string"}, "corps": {"type": "string"},
       "pieces_jointes": {"type": "array", "items": {"type": "string"}, "description": "Chemins de fichiers."}},
      ["destinataire", "sujet", "corps"],
      confirm=lambda a: f"Envoyer l'e-mail « {a['sujet']} » à {a['destinataire']} ?")
def envoyer_email(destinataire: str, sujet: str, corps: str, pieces_jointes: list | None = None) -> str:
    address, password = _mail_creds()
    if not address or not password:
        from urllib.parse import quote
        webbrowser.open(f"mailto:{destinataire}?subject={quote(sujet)}&body={quote(corps)}")
        return ("Messagerie non configurée (.env) : un brouillon a été ouvert dans le logiciel de messagerie, "
                "l'utilisateur doit cliquer sur Envoyer. Les pièces jointes ne sont pas ajoutées dans ce mode.")
    import smtplib
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = address, destinataire, sujet
    msg.set_content(corps)
    for item in pieces_jointes or []:
        p = resolve(item)
        if not p.is_file():
            return f"ERREUR : pièce jointe introuvable : {p}"
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        main, _, sub = ctype.partition("/")
        msg.add_attachment(p.read_bytes(), maintype=main, subtype=sub, filename=p.name)
    host = os.environ.get("JARVIS_SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("JARVIS_SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls()
        s.login(address, password)
        s.send_message(msg)
    return f"E-mail envoyé à {destinataire}."


@tool("lire_emails", "Lit les derniers e-mails reçus (expéditeur, sujet, date, début du message).",
      {"nombre": {"type": "integer", "description": "Nombre d'e-mails (défaut 5)."},
       "non_lus_seulement": {"type": "boolean"}})
def lire_emails(nombre: int = 5, non_lus_seulement: bool = False) -> str:
    address, password = _mail_creds()
    if not address or not password:
        return "ERREUR : messagerie non configurée. Renseigner JARVIS_EMAIL_ADDRESS et JARVIS_EMAIL_PASSWORD dans .env."
    import email
    import imaplib
    from email.header import decode_header, make_header
    box = imaplib.IMAP4_SSL(os.environ.get("JARVIS_IMAP_HOST", "imap.gmail.com"))
    try:
        box.login(address, password)
        box.select("INBOX", readonly=True)
        _, data = box.search(None, "UNSEEN" if non_lus_seulement else "ALL")
        ids = data[0].split()[-max(1, min(int(nombre), 20)):]
        out = []
        for i in reversed(ids):
            _, raw = box.fetch(i, "(BODY.PEEK[])")
            m = email.message_from_bytes(raw[0][1])
            body = ""
            for part in m.walk():
                if part.get_content_type() == "text/plain":
                    body = (part.get_payload(decode=True) or b"").decode(part.get_content_charset() or "utf-8", "replace")
                    break
            hdr = lambda k: str(make_header(decode_header(m.get(k, ""))))  # noqa: E731
            out.append(f"De : {hdr('From')}\nSujet : {hdr('Subject')}\nDate : {m.get('Date', '')}\n"
                       f"{' '.join(body.split())[:400]}")
        return "\n\n---\n".join(out) or "Aucun e-mail."
    finally:
        try:
            box.logout()
        except Exception:
            pass


# --- web --------------------------------------------------------------------
WMO = {0: "ciel dégagé", 1: "peu nuageux", 2: "partiellement nuageux", 3: "couvert", 45: "brouillard",
       48: "brouillard givrant", 51: "bruine légère", 53: "bruine", 55: "bruine forte", 61: "pluie faible",
       63: "pluie", 65: "pluie forte", 71: "neige faible", 73: "neige", 75: "neige forte", 80: "averses",
       81: "averses", 82: "fortes averses", 95: "orage", 96: "orage avec grêle", 99: "orage violent"}


@tool("meteo", "Météo actuelle et prévisions sur 3 jours pour une ville.",
      {"ville": {"type": "string"}}, ["ville"])
def meteo(ville: str) -> str:
    import requests
    geo = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                       params={"name": ville, "count": 1, "language": "fr"}, timeout=15).json()
    if not geo.get("results"):
        return f"ERREUR : ville introuvable : {ville}"
    g = geo["results"][0]
    w = requests.get("https://api.open-meteo.com/v1/forecast", timeout=15, params={
        "latitude": g["latitude"], "longitude": g["longitude"], "timezone": "auto", "forecast_days": 3,
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"}).json()
    c, d = w["current"], w["daily"]
    lines = [f"{g['name']} ({g.get('country', '')}) maintenant : {c['temperature_2m']}°C "
             f"(ressenti {c['apparent_temperature']}°C), {WMO.get(c['weather_code'], '?')}, "
             f"vent {c['wind_speed_10m']} km/h."]
    for i, day in enumerate(d["time"]):
        lines.append(f"{day} : {WMO.get(d['weather_code'][i], '?')}, {d['temperature_2m_min'][i]}°C à "
                     f"{d['temperature_2m_max'][i]}°C, pluie {d['precipitation_probability_max'][i]} %.")
    return "\n".join(lines)


@tool("lire_page_web", "Télécharge une page web et renvoie son texte (pour la lire ou la résumer).",
      {"url": {"type": "string"}}, ["url"])
def lire_page_web(url: str) -> str:
    import html
    import requests
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (assistant personnel)"})
    r.raise_for_status()
    text = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", r.text)
    text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", text))
    return " ".join(text.split())[:15000] or "(page vide)"


# --- mémoire ----------------------------------------------------------------
MEMORY_FILE = DATA / "memoire.json"


def load_memory() -> list[str]:
    try:
        return json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save_memory(items: list[str]) -> None:
    MEMORY_FILE.parent.mkdir(exist_ok=True)
    MEMORY_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


@tool("memoriser",
      "Retient durablement une information sur l'utilisateur (préférence, contact, adresse e-mail d'un proche, "
      "habitude...). Elle sera connue aux prochains démarrages.",
      {"information": {"type": "string"}}, ["information"])
def memoriser(information: str) -> str:
    items = load_memory()
    items.append(information.strip())
    _save_memory(items)
    return "Mémorisé."


@tool("oublier", "Supprime de la mémoire les informations contenant un texte donné.",
      {"texte": {"type": "string"}}, ["texte"])
def oublier(texte: str) -> str:
    items = load_memory()
    kept = [i for i in items if texte.lower() not in i.lower()]
    _save_memory(kept)
    return f"{len(items) - len(kept)} information(s) oubliée(s)."


@tool("lire_memoire", "Relit tout ce que Jarvis a mémorisé.")
def lire_memoire() -> str:
    return "\n".join(f"- {i}" for i in load_memory()) or "Mémoire vide."


# --- minuteurs et rappels ---------------------------------------------------
_timers: list[tuple[dt.datetime, str, threading.Timer]] = []
REMINDERS_FILE = DATA / "rappels.json"


def _save_reminders() -> None:
    now = dt.datetime.now()
    data = [[w.isoformat(), msg] for w, msg, _ in _timers if w > now]
    try:
        REMINDERS_FILE.parent.mkdir(exist_ok=True)
        REMINDERS_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _fire(message: str) -> None:
    runtime.notify(message)
    _save_reminders()


def _schedule(seconds: float, message: str, save: bool = True) -> dt.datetime:
    when = dt.datetime.now() + dt.timedelta(seconds=seconds)
    t = threading.Timer(seconds, _fire, args=[message])
    t.daemon = True
    t.start()
    _timers.append((when, message, t))
    if save:
        _save_reminders()
    return when


def restore_reminders() -> list[str]:
    """Au démarrage : reprogramme les rappels enregistrés, renvoie ceux qui ont été manqués."""
    try:
        data = json.loads(REMINDERS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    now, missed = dt.datetime.now(), []
    for iso, msg in data:
        when = dt.datetime.fromisoformat(iso)
        if when > now:
            _schedule((when - now).total_seconds(), msg, save=False)
        else:
            missed.append(f"{msg} (prévu le {when:%d/%m à %H:%M})")
    _save_reminders()
    return missed


@tool("minuteur", "Lance un minuteur : Jarvis prévient à voix haute quand le temps est écoulé.",
      {"secondes": {"type": "integer"}, "message": {"type": "string", "description": "Ce qu'il faut annoncer."}},
      ["secondes"])
def minuteur(secondes: int, message: str = "Le minuteur est terminé.") -> str:
    when = _schedule(max(1, int(secondes)), message)
    return f"Minuteur lancé, fin à {when:%H:%M:%S}."


@tool("rappel", "Programme un rappel vocal à une heure donnée aujourd'hui (ou demain si l'heure est passée). "
      "Le rappel est conservé même si Jarvis est relancé entre-temps.",
      {"heure": {"type": "string", "description": "Format HH:MM (24 h)."}, "message": {"type": "string"}},
      ["heure", "message"])
def rappel(heure: str, message: str) -> str:
    h, m = (int(x) for x in re.split(r"[:h]", heure.strip())[:2])
    now = dt.datetime.now()
    when = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if when <= now:
        when += dt.timedelta(days=1)
    _schedule((when - now).total_seconds(), f"Rappel : {message}")
    return f"Rappel programmé pour {when:%d/%m à %H:%M}."


@tool("lister_rappels", "Liste les minuteurs et rappels en attente.")
def lister_rappels() -> str:
    now = dt.datetime.now()
    pending = [(w, msg) for w, msg, t in _timers if w > now and t.is_alive()]
    return "\n".join(f"- {w:%d/%m %H:%M:%S} : {msg}" for w, msg in sorted(pending)) or "Aucun rappel en attente."
