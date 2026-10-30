"""La télé trouve Prisme toute seule, et s'y relie sans rien taper.

L'application Prisme TV (Android TV, Fire TV) ne sait pas où est le PC, et une
télécommande est une piètre machine à écrire. Elle crie donc sur le Wi-Fi de la
maison (UDP, port 8714) ; Prisme répond, et la télé l'affiche dans sa liste.
Choisie, elle demande l'accès : Prisme pose la question sur le PC (« Autoriser
la télé du salon ? »), et seulement si l'on dit oui, lui répond avec le chemin
de son lien d'invitation. La télé le garde, et s'ouvre ensuite directement.

Messages, en JSON, un par datagramme :

    télé → tous    {"prisme": "hello", "v": 1}
    PC   → télé    {"prisme": "here", "name": "BUREAU", "port": 8713}
    télé → PC      {"prisme": "pair", "device": "Fire TV Stick", "nonce": "…"}
    PC   → télé    {"prisme": "wait"} tant que personne n'a répondu,
                   {"prisme": "paired", "path": "/entrer/…", "port": 8713},
                   ou {"prisme": "refused"}

La télé répète sa demande toutes les deux ou trois secondes (l'UDP se perd) :
une même demande (même « nonce ») ne pose la question qu'une fois.
Rien ne répond hors du réseau local : le port n'est ouvert ni sur la box ni
par le tunnel.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import threading
import time

from PySide6.QtCore import QObject, Signal

PORT = 8714
# Une demande sans réponse depuis ce temps est oubliée ; la télé, elle,
# renonce au bout d'une minute et demie.
FORGET = 180


def _local(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


class TvBeacon(QObject):
    """Écoute les télés du réseau local ; `asked` pose la question à l'écran."""

    # appareil, nonce, adresse de la télé
    asked = Signal(str, str, str)

    def __init__(self, port_of, name: str = "", parent=None):
        super().__init__(parent)
        self.port_of = port_of          # le port du partage, ou 0 s'il est fermé
        self.name = name or socket.gethostname()
        self.sock = None
        self.thread = None
        self.lock = threading.Lock()
        self.pending: dict = {}         # nonce -> (appareil, depuis)
        self.decided: dict = {}         # nonce -> réponse

    def start(self) -> bool:
        if self.sock is not None:
            return True
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(("0.0.0.0", PORT))
            sock.settimeout(1.0)
        except OSError:
            return False
        self.sock = sock
        self.thread = threading.Thread(target=self._run, name="prisme-tv", daemon=True)
        self.thread.start()
        return True

    def stop(self) -> None:
        sock, self.sock = self.sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        with self.lock:
            self.pending.clear()
            self.decided.clear()

    def answer(self, nonce: str, accepted: bool, path: str = "") -> None:
        """La réponse de l'utilisateur ; elle part à la prochaine relance."""
        with self.lock:
            self.pending.pop(nonce, None)
            self.decided[nonce] = ({"prisme": "paired", "path": path,
                                    "port": int(self.port_of() or 0)}
                                   if accepted and path else {"prisme": "refused"})

    # -- le fil d'écoute --------------------------------------------------------
    def _run(self) -> None:
        sock = self.sock
        while self.sock is sock and sock is not None:
            try:
                data, (ip, port) = sock.recvfrom(2048)
            except socket.timeout:
                self._forget()
                continue
            except OSError:
                break
            if not _local(ip):
                continue
            try:
                message = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                continue
            if not isinstance(message, dict):
                continue
            reply = self._reply(message, ip)
            if reply is not None:
                try:
                    sock.sendto(json.dumps(reply).encode("utf-8"), (ip, port))
                except OSError:
                    pass

    def _reply(self, message: dict, ip: str):
        shared = int(self.port_of() or 0)
        if not shared:
            return None                 # partage fermé : on ne se montre pas
        kind = message.get("prisme")
        if kind == "hello":
            return {"prisme": "here", "name": self.name, "port": shared}
        if kind != "pair":
            return None
        nonce = str(message.get("nonce") or "")[:64]
        device = str(message.get("device") or "Une télé")[:60]
        if not nonce:
            return None
        with self.lock:
            if nonce in self.decided:
                return self.decided[nonce]
            if nonce in self.pending:
                return {"prisme": "wait"}
            self.pending[nonce] = (device, time.time())
        self.asked.emit(device, nonce, ip)
        return {"prisme": "wait"}

    def _forget(self) -> None:
        now = time.time()
        with self.lock:
            for nonce, (_device, since) in list(self.pending.items()):
                if now - since > FORGET:
                    del self.pending[nonce]
            if len(self.decided) > 50:
                self.decided.clear()
