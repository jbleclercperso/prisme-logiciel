"""Pilotage de Jarvis depuis le téléphone, par un bot Telegram privé.

Dans .env : JARVIS_TELEGRAM_TOKEN (donné par @BotFather) et JARVIS_TELEGRAM_CHAT_ID (votre identifiant ;
laissez vide au début : le premier message reçu affiche l'identifiant à recopier).
"""
from __future__ import annotations

import os
import queue
import tempfile
import threading
import time

from .core import NON, OUI, Channel, plain
from .tools.files import resolve
from .tools.registry import tool

API = "https://api.telegram.org/bot{token}/{method}"
_bot: "TelegramBot | None" = None


class TelegramBot(Channel):
    name = "telegram"

    def __init__(self, core, token: str, chat_id: str):
        import requests
        self.requests, self.core, self.token, self.chat_id = requests, core, token, str(chat_id or "")
        self.answers: queue.Queue = queue.Queue()   # réponses aux questions oui/non
        self.waiting = threading.Event()
        self.offset = 0
        self.transcribe = None                      # fonction(chemin audio) -> texte, fournie par la voix

    # --- API Telegram -----------------------------------------------------
    def call(self, method: str, http_timeout: int = 40, files=None, **params):
        url = API.format(token=self.token, method=method)
        r = self.requests.post(url, data=params, files=files, timeout=http_timeout)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} : {data.get('description')}")
        return data["result"]

    def send(self, text: str) -> None:
        for i in range(0, len(text), 4000):
            self.call("sendMessage", chat_id=self.chat_id, text=text[i:i + 4000])

    def send_file(self, path, caption: str = "") -> None:
        image = str(path).lower().endswith((".png", ".jpg", ".jpeg"))
        with open(path, "rb") as f:
            self.call("sendPhoto" if image else "sendDocument", http_timeout=300, chat_id=self.chat_id,
                      caption=caption[:1000], files={"photo" if image else "document": f})

    # --- canal ------------------------------------------------------------
    def confirm(self, question: str) -> bool:
        while not self.answers.empty():
            self.answers.get_nowait()
        self.send(f"❓ {question}\nRépondez oui ou non.")
        self.waiting.set()
        try:
            answer = plain(self.answers.get(timeout=120))
        except queue.Empty:
            self.send("Pas de réponse : action annulée.")
            return False
        finally:
            self.waiting.clear()
        return bool(OUI.search(answer)) and not NON.search(answer)

    def notify(self, message: str) -> None:
        if self.chat_id:
            self.send(f"🔔 {message}")

    # --- réception --------------------------------------------------------
    def _text_of(self, msg: dict) -> str:
        if msg.get("text"):
            return msg["text"]
        media = msg.get("voice") or msg.get("audio")
        if media and self.transcribe:
            info = self.call("getFile", file_id=media["file_id"])
            url = f"https://api.telegram.org/file/bot{self.token}/{info['file_path']}"
            path = os.path.join(tempfile.mkdtemp(prefix="jarvis_tg_"), "vocal.ogg")
            with open(path, "wb") as f:
                f.write(self.requests.get(url, timeout=60).content)
            return self.transcribe(path)
        doc = msg.get("document")
        if doc:                                     # fichier envoyé depuis le téléphone -> Téléchargements
            info = self.call("getFile", file_id=doc["file_id"])
            url = f"https://api.telegram.org/file/bot{self.token}/{info['file_path']}"
            dest = resolve("telechargements") / (doc.get("file_name") or "fichier_telegram")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(self.requests.get(url, timeout=300).content)
            note = msg.get("caption") or "Dis-moi simplement que c'est bien reçu."
            return f"[Fichier reçu depuis le téléphone, enregistré dans {dest}] {note}"
        return ""

    def handle(self, msg: dict) -> None:
        chat = str(msg.get("chat", {}).get("id", ""))
        if not self.chat_id:
            self.call("sendMessage", chat_id=chat, text=(
                f"Votre identifiant est {chat}. Recopiez-le dans le fichier .env "
                f"(JARVIS_TELEGRAM_CHAT_ID={chat}) puis relancez Jarvis."))
            return
        if chat != self.chat_id:
            return                                  # quelqu'un d'autre : ignoré, silencieusement
        text = self._text_of(msg)
        if not text:
            return
        if self.waiting.is_set():
            self.answers.put(text)
            return
        threading.Thread(target=self._answer, args=(text,), daemon=True).start()

    def _answer(self, text: str) -> None:
        try:
            self.call("sendChatAction", chat_id=self.chat_id, action="typing")
            self.send(self.core.ask(text, channel=self))
        except Exception as e:
            print(f"[telegram] {type(e).__name__}: {e}")

    def poll_once(self, timeout: int = 30) -> None:
        updates = self.call("getUpdates", http_timeout=timeout + 10, offset=self.offset, timeout=timeout)
        for u in updates:
            self.offset = u["update_id"] + 1
            if "message" in u:
                self.handle(u["message"])

    def run(self) -> None:
        while True:
            try:
                self.poll_once()
            except Exception as e:
                print(f"[telegram] {type(e).__name__}: {e}")
                time.sleep(15)


def start(core) -> TelegramBot | None:
    global _bot
    token = os.environ.get("JARVIS_TELEGRAM_TOKEN", "")
    if not token:
        return None
    _bot = TelegramBot(core, token, os.environ.get("JARVIS_TELEGRAM_CHAT_ID", ""))
    core.extra_notifiers.append(_bot)
    threading.Thread(target=_bot.run, daemon=True).start()
    return _bot


@tool("envoyer_sur_telephone",
      "Envoie un message, un fichier ou une capture d'écran sur le téléphone de l'utilisateur (Telegram).",
      {"message": {"type": "string"},
       "fichier": {"type": "string", "description": "Chemin d'un fichier à joindre (optionnel)."},
       "capture_ecran": {"type": "boolean", "description": "Joindre une capture de l'écran actuel."}})
def envoyer_sur_telephone(message: str = "", fichier: str = "", capture_ecran: bool = False) -> str:
    if _bot is None or not _bot.chat_id:
        return ("ERREUR : Telegram n'est pas configuré. Il faut JARVIS_TELEGRAM_TOKEN et "
                "JARVIS_TELEGRAM_CHAT_ID dans le fichier .env (voir README).")
    sent = []
    if capture_ecran:
        from PIL import ImageGrab
        path = os.path.join(tempfile.mkdtemp(prefix="jarvis_cap_"), "ecran.png")
        ImageGrab.grab().save(path)
        _bot.send_file(path, message)
        sent.append("capture d'écran")
    if fichier:
        p = resolve(fichier)
        if not p.is_file():
            return f"ERREUR : fichier introuvable : {p}"
        if p.stat().st_size > 49 * 2**20:
            return "ERREUR : fichier trop gros pour Telegram (limite 50 Mo)."
        _bot.send_file(p, "" if capture_ecran else message)
        sent.append(p.name)
    if message and not sent:
        _bot.send(message)
        sent.append("message")
    return f"Envoyé sur le téléphone : {', '.join(sent) or 'rien'}."
