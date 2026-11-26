"""Interface discrète : un point dans le coin de l'écran, qui se déplie en une petite barre quand Jarvis
est appelé (voix, raccourci, clic) puis se replie tout seul. Tourne dans le fil principal."""
from __future__ import annotations

import json
import queue
import sys
import threading
import time

from . import config as _config
from .core import QUIT, Channel, plain

KEY = "#010203"                 # couleur rendue transparente sous Windows (coins arrondis)
BG, EDGE, INK, MUTED, FIELD = "#16191f", "#2c323c", "#eef0f3", "#8b93a1", "#20252e"
COLORS = {"repos": "#7a8291", "écoute": "#22c55e", "réfléchit": "#f59e0b", "parle": "#3b82f6"}
LABELS = {"écoute": "Je vous écoute…", "réfléchit": "Un instant…", "parle": ""}
DOT, WIDTH, PAD = 30, 380, 14


class Widget:
    def __init__(self, core, on_quit=None):
        import tkinter as tk
        import tkinter.font as tkfont
        self.tk, self.core, self.on_quit = tk, core, on_quit
        self.on_mic = lambda: None
        cfg = core.config.get("interface_options", {})
        self.idle_hidden = cfg.get("veille", "point") == "cache"
        self.linger = float(cfg.get("delai_repli", 7))
        self.events: queue.Queue = queue.Queue()
        events = self.events
        self._observer = lambda kind, text: events.put((kind, text))
        core.observers.append(self._observer)

        self.state, self.level = "repos", 0.0
        self.said, self.reply = "", ""
        self.open, self.typing, self.asking = False, False, False
        self.answers: queue.Queue = queue.Queue()
        self.last_activity = time.time()
        self.margin = self._load_margin()

        root = self.root = tk.Tk()
        root.title(core.config["assistant"]["nom"])
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=KEY)
        if sys.platform == "win32":
            root.attributes("-transparentcolor", KEY)
        family = "Segoe UI" if sys.platform == "win32" else "DejaVu Sans"
        self.font = tkfont.Font(family=family, size=10)
        self.small = tkfont.Font(family=family, size=9)
        self.cv = tk.Canvas(root, bg=KEY, highlightthickness=0, bd=0)
        self.cv.pack(fill="both", expand=True)
        self.entry = tk.Entry(self.cv, bg=FIELD, fg=INK, insertbackground=INK, relief="flat", font=self.font,
                              highlightthickness=1, highlightbackground=EDGE, highlightcolor=COLORS["parle"])
        self.entry.bind("<Return>", self._send)
        self.entry.bind("<Escape>", lambda e: self.collapse())
        self.yes = self._button("Oui", lambda: self._answer(True))
        self.no = self._button("Non", lambda: self._answer(False))

        self.menu = tk.Menu(root, tearoff=0)
        self.menu.add_command(label="Écrire à Jarvis", command=self.expand_typing)
        self.menu.add_command(label="Parler", command=lambda: self.on_mic())
        self.menu.add_command(label="Replier", command=self.collapse)
        self.menu.add_separator()
        self.menu.add_command(label="Quitter Jarvis", command=self.quit)
        self.cv.bind("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))
        self.cv.bind("<ButtonPress-1>", self._press)
        self.cv.bind("<B1-Motion>", self._drag)
        self.cv.bind("<ButtonRelease-1>", self._release)
        root.bind("<Escape>", lambda e: self.collapse())
        self._drag_from, self._moved = None, False
        self.render()
        if self.idle_hidden:
            root.withdraw()
        root.after(80, self._pump)

    # --- petits éléments ---------------------------------------------------
    def _button(self, text: str, command):
        b = self.tk.Label(self.cv, text=text, bg=FIELD, fg=INK, font=self.small, padx=14, pady=3, cursor="hand2")
        b.bind("<Button-1>", lambda e: command())
        return b

    def _load_margin(self) -> tuple[int, int]:
        try:
            d = json.loads((_config.DATA / "widget.json").read_text(encoding="utf-8"))
            return int(d["droite"]), int(d["bas"])
        except (OSError, ValueError, KeyError):
            return 18, 64                       # au-dessus de la barre des tâches, à droite

    def _save_margin(self) -> None:
        try:
            _config.DATA.mkdir(exist_ok=True)
            (_config.DATA / "widget.json").write_text(json.dumps({"droite": self.margin[0], "bas": self.margin[1]}))
        except OSError:
            pass

    @staticmethod
    def _round(cv, x1, y1, x2, y2, r, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
               x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return cv.create_polygon(pts, smooth=True, **kw)

    # --- dessin ------------------------------------------------------------
    def render(self) -> None:
        cv, color = self.cv, COLORS.get(self.state, COLORS["repos"])
        cv.delete("all")
        for w in (self.entry, self.yes, self.no):
            w.place_forget()
        if not self.open:
            w = h = DOT
            cv.create_oval(2, 2, DOT - 2, DOT - 2, fill=BG, outline=EDGE)
            r = 5 + self.level * 4
            cv.create_oval(DOT / 2 - r, DOT / 2 - r, DOT / 2 + r, DOT / 2 + r, fill=color, outline="")
        else:
            w, x, y = WIDTH, 44, PAD
            items = []
            if self.said:
                items.append(cv.create_text(x, y, anchor="nw", width=w - x - PAD, font=self.small, fill=MUTED,
                                            text=self._clip("« " + self.said + " »", 120)))
                y = cv.bbox(items[-1])[3] + 3
            main = self.reply or LABELS.get(self.state, "") or ("Dites, ou écrivez ci-dessous." if self.typing else "")
            if main:
                items.append(cv.create_text(x, y, anchor="nw", width=w - x - PAD, font=self.font, fill=INK,
                                            text=self._clip(main, 420)))
                y = cv.bbox(items[-1])[3] + 4
            if self.asking:
                y += 4
                self.yes.place(x=x, y=y)
                self.no.place(x=x + 62, y=y)
                y += 30
            if self.typing:
                y += 2
                self.entry.place(x=x, y=y, width=w - x - PAD, height=26)
                y += 30
            h = max(46, y + PAD - 4)
            bg = self._round(cv, 1, 1, w - 1, h - 1, 16, fill=BG, outline=EDGE)
            cv.tag_lower(bg)
            r = 6 + self.level * 5
            cv.create_oval(22 - r, 23 - r, 22 + r, 23 + r, fill=color, outline="")
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.size = (int(w), int(h))
        self.root.geometry(f"{int(w)}x{int(h)}+{sw - int(w) - self.margin[0]}+{sh - int(h) - self.margin[1]}")

    @staticmethod
    def _clip(text: str, n: int) -> str:
        text = " ".join(text.split())
        return text if len(text) <= n else text[: n - 1].rstrip() + "…"

    # --- comportement ------------------------------------------------------
    def expand(self) -> None:
        self.last_activity = time.time()
        if self.idle_hidden:
            self.root.deiconify()
        if not self.open:
            self.open = True
        self.render()

    def expand_typing(self) -> None:
        self.typing = True
        self.expand()
        self.root.focus_force()
        self.entry.focus_set()

    def collapse(self) -> None:
        if self.asking:
            return
        self.open = self.typing = False
        self.said = self.reply = ""
        self.entry.delete(0, "end")
        self.render()
        if self.idle_hidden:
            self.root.withdraw()

    def _press(self, e) -> None:
        self._drag_from, self._moved = (e.x_root, e.y_root, *self.margin), False

    def _drag(self, e) -> None:
        if not self._drag_from:
            return
        x0, y0, right, bottom = self._drag_from
        if abs(e.x_root - x0) + abs(e.y_root - y0) > 4:
            self._moved = True
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            self.margin = (max(0, min(sw - self.size[0], right - (e.x_root - x0))),
                           max(0, min(sh - self.size[1], bottom - (e.y_root - y0))))
            self.root.geometry(f"+{sw - self.size[0] - self.margin[0]}+{sh - self.size[1] - self.margin[1]}")

    def _release(self, e) -> None:
        if self._moved:
            self._save_margin()
        elif not self.typing:
            self.expand_typing()                # un clic : on peut écrire
        self._drag_from = None

    def _send(self, event=None) -> None:
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        if QUIT.match(plain(text)):
            return self.quit()
        threading.Thread(target=self.core.ask, args=(text, self.channel()), daemon=True).start()

    def _answer(self, value: bool) -> None:
        self.asking = False
        self.answers.put(value)
        self.render()

    def _pump(self) -> None:
        changed = False
        try:
            while True:
                kind, text = self.events.get_nowait()
                changed = True
                if kind == "etat":
                    self.state = text if text in COLORS else "repos"
                    if self.state != "repos":
                        self.open = True
                        if self.idle_hidden:
                            self.root.deiconify()
                        if self.state == "écoute":
                            self.reply = ""
                elif kind == "niveau":
                    self.level = min(1.0, float(text or 0))
                elif kind == "vous":
                    self.said, self.reply, self.open = text, "", True
                elif kind in ("jarvis", "notification", "question"):
                    self.reply, self.open = text, True
                    if kind == "notification":
                        self.said = ""
                    if self.idle_hidden:
                        self.root.deiconify()
                elif kind == "boutons":
                    self.asking = self.open = True
                elif kind == "fermer":
                    self.root.destroy()
                    return
                if kind != "niveau":
                    self.last_activity = time.time()
        except queue.Empty:
            pass
        if self.level > 0.01:
            self.level *= 0.8
            changed = True
        busy = self.state != "repos" or self.asking or (self.typing and self.entry.get().strip())
        if self.open and not busy and time.time() - self.last_activity > self.linger:
            self.collapse()
        elif changed:
            self.render()
        self.root.after(80, self._pump)

    def channel(self):
        """Canal des demandes tapées ici : confirmations par les boutons Oui / Non."""
        widget = self

        class WidgetChannel(Channel):
            name = "widget"

            def confirm(self, question: str) -> bool:
                while not widget.answers.empty():
                    widget.answers.get_nowait()
                widget.events.put(("boutons", ""))
                try:
                    return bool(widget.answers.get(timeout=120))
                except queue.Empty:
                    widget.asking = False
                    return False

            def notify(self, message: str) -> None:
                pass                            # déjà affiché par l'observateur

        return WidgetChannel()

    def quit(self) -> None:
        if self.on_quit:
            self.on_quit()
        root = self.__dict__.get("root")
        if root is not None:
            root.destroy()

    def run(self) -> None:
        """Boucle d'affichage. À la sortie, libère les objets Tk ici même, dans le fil principal :
        détruits plus tard par un autre fil, ils feraient planter tout le programme."""
        import gc
        try:
            self.root.mainloop()
        finally:
            if self._observer in self.core.observers:
                self.core.observers.remove(self._observer)
            for name in ("cv", "entry", "yes", "no", "menu", "font", "small", "root", "tk"):
                self.__dict__.pop(name, None)
            gc.collect()
