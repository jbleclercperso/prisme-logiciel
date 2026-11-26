"""Le coeur de Jarvis : un seul cerveau partagé entre la voix, le clavier, l'interface, Telegram et le planning."""
from __future__ import annotations

import re
import threading
import time
import unicodedata

from . import tools
from .brain import make_brain
from .config import DATA
from .tools import registry

OUI = re.compile(r"\b(oui|ouais|yes|ok|okay|d'accord|vas-y|vas y|confirme|affirmatif|absolument|bien sur|fais-le|fais le)\b")
NON = re.compile(r"\b(non|no|annule|stop|surtout pas|negatif|laisse tomber)\b")


def plain(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return s.lower().strip()


def journal(kind: str, text: str) -> None:
    """Trace des échanges dans data/journal.log (utile pour comprendre un raté après coup)."""
    try:
        DATA.mkdir(exist_ok=True)
        with open(DATA / "journal.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{kind}] {text}\n")
    except OSError:
        pass


class Channel:
    """Un moyen de parler à l'utilisateur. Les sous-classes : console, voix, Telegram, interface."""
    name = "console"

    def confirm(self, question: str) -> bool:
        return bool(OUI.search(plain(input(f"   ❓ {question} (oui/non) > "))))

    def notify(self, message: str) -> None:
        print(f"\n🔔 {message}")


class Core:
    def __init__(self, config: dict):
        self.config = config
        self.lock = threading.RLock()           # une seule demande traitée à la fois
        self.default: Channel = Channel()       # canal principal (voix ou console)
        self.channel: Channel | None = None     # canal de la demande en cours
        self.observers: list = []               # fonctions(kind, text) : interface, journal...
        self.extra_notifiers: list = []         # ex. Telegram : reçoit aussi les notifications
        tools.runtime.config = config
        tools.runtime.core = self
        tools.runtime.confirm = self.confirm
        tools.runtime.notify = self.notify
        self.brain = make_brain(config, on_tool=self._on_tool)

    # --- événements -------------------------------------------------------
    def emit(self, kind: str, text: str) -> None:
        if kind != "etat":
            journal(kind, text)
        for obs in list(self.observers):
            try:
                obs(kind, text)
            except Exception:
                pass

    def _on_tool(self, name: str, args: dict) -> None:
        short = ", ".join(f"{k}={str(v)[:60]!r}" for k, v in (args or {}).items())
        print(f"   ⚙ {name}({short})")
        self.emit("outil", f"{name}({short})")

    # --- demandes ---------------------------------------------------------
    def ask(self, text: str, channel: Channel | None = None) -> str:
        with self.lock:
            self.channel = channel or self.default
            try:
                self.emit("vous", text)
                self.emit("etat", "réfléchit")
                if registry.dirty:
                    registry.dirty = False
                    self.brain.reload_tools()
                answer = self.brain.ask(text)
                self.emit("jarvis", answer)
                return answer
            finally:
                self.emit("etat", "repos")
                self.channel = None

    def reset(self) -> None:
        with self.lock:
            self.brain.reset()

    def confirm(self, question: str) -> bool:
        self.emit("question", question)
        return (self.channel or self.default).confirm(question)

    def notify(self, message: str) -> None:
        self.emit("notification", message)
        for target in [self.default] + self.extra_notifiers:
            try:
                target.notify(message)
            except Exception as e:
                print(f"[notification] {type(e).__name__}: {e}")
