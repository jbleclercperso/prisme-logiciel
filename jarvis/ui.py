"""Interface : petite fenêtre flottante (état, échanges, saisie clavier). Tourne dans le fil principal."""
from __future__ import annotations

import queue
import threading

COLORS = {"repos": "#3b82f6", "écoute": "#22c55e", "réfléchit": "#f59e0b", "parle": "#a855f7", "erreur": "#ef4444"}
BG, FG, MUTED = "#0f172a", "#e2e8f0", "#94a3b8"


class Window:
    def __init__(self, core, title: str = "Jarvis", on_quit=None):
        import tkinter as tk
        self.tk, self.core, self.on_quit = tk, core, on_quit
        self.events: queue.Queue = queue.Queue()
        self.pending_confirm: queue.Queue | None = None
        events = self.events                    # l'observateur ne retient pas la fenêtre elle-même
        self._observer = lambda kind, text: events.put((kind, text))
        core.observers.append(self._observer)

        root = self.root = tk.Tk()
        root.title(title)
        root.configure(bg=BG)
        root.attributes("-topmost", True)
        w, h = 380, 460
        root.geometry(f"{w}x{h}+{root.winfo_screenwidth() - w - 24}+{root.winfo_screenheight() - h - 90}")
        root.protocol("WM_DELETE_WINDOW", self.quit)

        top = tk.Frame(root, bg=BG)
        top.pack(fill="x", padx=12, pady=(10, 4))
        self.dot = tk.Canvas(top, width=18, height=18, bg=BG, highlightthickness=0)
        self.dot_id = self.dot.create_oval(2, 2, 16, 16, fill=COLORS["repos"], outline="")
        self.dot.pack(side="left")
        tk.Label(top, text=title.upper(), bg=BG, fg=FG, font=("Segoe UI", 12, "bold")).pack(side="left", padx=8)
        self.state = tk.Label(top, text="en veille", bg=BG, fg=MUTED, font=("Segoe UI", 9))
        self.state.pack(side="right")

        self.log = tk.Text(root, bg="#1e293b", fg=FG, wrap="word", relief="flat", font=("Segoe UI", 10),
                           padx=8, pady=8, state="disabled", height=10)
        self.log.pack(fill="both", expand=True, padx=12, pady=4)
        for tag, color in (("vous", "#93c5fd"), ("jarvis", FG), ("outil", MUTED), ("notification", "#fcd34d"),
                           ("question", "#fca5a5")):
            self.log.tag_configure(tag, foreground=color, spacing3=6)

        self.confirm_bar = tk.Frame(root, bg=BG)
        tk.Button(self.confirm_bar, text="Oui", width=10, command=lambda: self._answer(True)).pack(side="left", padx=4)
        tk.Button(self.confirm_bar, text="Non", width=10, command=lambda: self._answer(False)).pack(side="left", padx=4)

        bottom = tk.Frame(root, bg=BG)
        bottom.pack(fill="x", padx=12, pady=(4, 12))
        self.entry = tk.Entry(bottom, bg="#1e293b", fg=FG, insertbackground=FG, relief="flat", font=("Segoe UI", 10))
        self.entry.pack(side="left", fill="x", expand=True, ipady=5)
        self.entry.bind("<Return>", self._send)
        tk.Button(bottom, text="Envoyer", command=self._send).pack(side="left", padx=(6, 0))
        root.after(100, self._pump)

    # --- affichage --------------------------------------------------------
    def _append(self, tag: str, text: str) -> None:
        prefix = {"vous": "Vous : ", "jarvis": "Jarvis : ", "outil": "⚙ ", "notification": "🔔 ", "question": "❓ "}
        self.log.configure(state="normal")
        self.log.insert("end", prefix.get(tag, "") + text + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def set_state(self, state: str) -> None:
        self.events.put(("etat", state))

    def _pump(self) -> None:
        try:
            while True:
                kind, text = self.events.get_nowait()
                if kind == "etat":
                    self.dot.itemconfigure(self.dot_id, fill=COLORS.get(text, COLORS["repos"]))
                    self.state.configure(text={"repos": "en veille"}.get(text, text))
                elif kind == "fermer":
                    self.root.destroy()
                    return
                elif kind == "boutons":
                    self.confirm_bar.pack(fill="x", padx=12, pady=2, before=self.entry.master)
                elif kind in ("vous", "jarvis", "outil", "notification", "question"):
                    self._append(kind, text)
        except queue.Empty:
            pass
        self.root.after(100, self._pump)

    # --- saisie -----------------------------------------------------------
    def _send(self, event=None) -> None:
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        threading.Thread(target=self.core.ask, args=(text, self.channel()), daemon=True).start()

    def _answer(self, value: bool) -> None:
        self.confirm_bar.pack_forget()
        if self.pending_confirm is not None:
            self.pending_confirm.put(value)

    def channel(self):
        """Canal pour les demandes tapées dans la fenêtre : confirmations par boutons Oui / Non."""
        from .core import Channel
        window = self

        class WindowChannel(Channel):
            name = "fenetre"

            def confirm(self, question: str) -> bool:
                window.pending_confirm = queue.Queue()
                window.events.put(("boutons", ""))
                try:
                    return bool(window.pending_confirm.get(timeout=120))
                except queue.Empty:
                    return False
                finally:
                    window.pending_confirm = None

            def notify(self, message: str) -> None:
                pass                        # déjà affiché par l'observateur

        return WindowChannel()

    def quit(self) -> None:
        if self.on_quit:
            self.on_quit()
        root = self.__dict__.get("root")
        if root is not None:
            root.destroy()

    def run(self) -> None:
        """Boucle d'affichage. À la sortie, libère les objets Tk ici même, dans le fil principal :
        s'ils étaient détruits plus tard par un autre fil, Tcl ferait planter tout le programme."""
        import gc
        try:
            self.root.mainloop()
        finally:
            if self._observer in self.core.observers:
                self.core.observers.remove(self._observer)
            for name in ("dot", "state", "log", "confirm_bar", "entry", "root", "tk"):
                self.__dict__.pop(name, None)
            gc.collect()
