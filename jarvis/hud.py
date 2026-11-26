"""Interface de Jarvis : une page animée servie en local et affichée dans une fenêtre d'application.

Le serveur n'écoute que sur cet ordinateur (127.0.0.1) et exige un jeton secret tiré au démarrage :
aucun site web ni autre appareil ne peut lui envoyer de commandes.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import queue
import secrets
import shutil
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .core import QUIT, Channel, plain

PAGE = Path(__file__).with_name("hud.html")
SHOWN = ("vous", "jarvis", "outil", "notification", "etat", "niveau", "confirmer", "confirme")


class Hud(Channel):
    name = "interface"

    def __init__(self, core, port: int = 0):
        self.core = core
        self.token = secrets.token_urlsafe(18)
        self.clients: list[queue.Queue] = []
        self.backlog: list[tuple[str, str]] = []
        self.answers: queue.Queue = queue.Queue()
        self.on_mic = lambda: None              # branché sur le raccourci « parler » par la voix
        self.on_quit = lambda: None
        self.lock = threading.Lock()
        core.observers.append(self.publish)
        hud = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args) -> None:
                pass

            def _allowed(self) -> bool:
                url = urlparse(self.path)
                token = parse_qs(url.query).get("t", [""])[0]
                host = (self.headers.get("Host") or "").split(":")[0]
                return secrets.compare_digest(token, hud.token) and host in ("127.0.0.1", "localhost")

            def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype + "; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                if not self._allowed():
                    return self._send(403, b"{}")
                path = urlparse(self.path).path
                if path == "/":
                    return self._send(200, PAGE.read_bytes(), "text/html")
                if path == "/etat":
                    return self._send(200, json.dumps(hud.status(), ensure_ascii=False).encode())
                if path == "/events":
                    return self._events()
                self._send(404, b"{}")

            def _events(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                q: queue.Queue = queue.Queue()
                with hud.lock:
                    for item in hud.backlog:
                        q.put(item)
                    hud.clients.append(q)
                try:
                    while True:
                        try:
                            kind, text = q.get(timeout=15)
                            data = json.dumps({"k": kind, "t": text}, ensure_ascii=False)
                            self.wfile.write(f"data: {data}\n\n".encode())
                        except queue.Empty:
                            self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                except OSError:
                    pass
                finally:
                    with hud.lock:
                        if q in hud.clients:
                            hud.clients.remove(q)

            def do_POST(self) -> None:
                if not self._allowed():
                    return self._send(403, b"{}")
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    body = {}
                path = urlparse(self.path).path
                if path == "/ask" and QUIT.match(plain(str(body.get("text", "")))):
                    self._send(200, b'{"ok": true}')
                    return hud.on_quit()
                if path == "/ask" and str(body.get("text", "")).strip():
                    threading.Thread(target=hud.core.ask, args=(str(body["text"]).strip(), hud), daemon=True).start()
                elif path == "/confirm":
                    hud.answers.put(bool(body.get("ok")))
                elif path == "/micro":
                    hud.on_mic()
                else:
                    return self._send(404, b"{}")
                self._send(200, b'{"ok": true}')

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/?t={self.token}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    # --- événements vers la page ------------------------------------------
    def publish(self, kind: str, text: str) -> None:
        if kind not in SHOWN:
            return
        with self.lock:
            if kind in ("vous", "jarvis", "notification"):
                self.backlog = (self.backlog + [(kind, text)])[-30:]
            for q in self.clients:
                q.put((kind, text))

    # --- canal (demandes tapées dans la page) -----------------------------
    def confirm(self, question: str) -> bool:
        while not self.answers.empty():
            self.answers.get_nowait()
        self.publish("confirmer", question)
        try:
            return bool(self.answers.get(timeout=120))
        except queue.Empty:
            return False
        finally:
            self.publish("confirme", "")

    def notify(self, message: str) -> None:
        pass                                    # déjà affiché par l'observateur

    # --- données des panneaux ---------------------------------------------
    def status(self) -> dict:
        import psutil
        from .tools import pc, perso
        out = {"nom": self.core.config["assistant"]["nom"], "cpu": psutil.cpu_percent(interval=None),
               "ram": psutil.virtual_memory().percent, "batterie": None, "agenda": [], "taches": []}
        bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
        if bat:
            out["batterie"] = bat.percent
        today = dt.date.today()
        events = sorted((e for e in perso._read("agenda.json") if e["date"] >= today.isoformat()),
                        key=lambda e: (e["date"], e.get("heure") or ""))
        for e in events[:4]:
            d = dt.date.fromisoformat(e["date"])
            day = "auj." if d == today else "demain" if d == today + dt.timedelta(days=1) else f"{d:%d/%m}"
            out["agenda"].append({"quand": f"{day} {e.get('heure') or ''}".strip(), "texte": e["titre"]})
        out["taches"] = [{"texte": t["texte"]} for t in pc._load_tasks() if not t["fait"]][:4]
        return out

    # --- fenêtre -----------------------------------------------------------
    def open_window(self) -> bool:
        """Ouvre la page dans une fenêtre d'application (Edge ou Chrome, sans barres de navigateur)."""
        exe = find_browser()
        if exe:
            profile = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Jarvis" / "fenetre"
            try:
                subprocess.Popen([exe, f"--app={self.url}", "--window-size=470,820",
                                  f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check"])
                return True
            except OSError:
                pass
        try:
            return bool(webbrowser.open(self.url))
        except Exception:
            return False


def find_browser() -> str | None:
    if sys.platform != "win32":
        return shutil.which("msedge") or shutil.which("google-chrome") or shutil.which("chromium")
    roots = [os.environ.get(v, "") for v in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
    for rel in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
        for root in roots:
            path = Path(root) / rel
            if root and path.exists():
                return str(path)
    return shutil.which("msedge") or shutil.which("chrome")
