"""Interface de Jarvis : une forme vivante posée dans un coin de l'écran, sur fond transparent.
Elle respire au repos, grossit et se colore quand on l'appelle, ondule quand elle parle ; une bulle de
texte apparaît à côté le temps de l'échange. Tourne dans le fil principal."""
from __future__ import annotations

import ctypes
import json
import queue
import sys
import threading
import time

import numpy as np

from . import config as _config
from .blob import PALETTES, Blob, bubble
from .core import QUIT, Channel, plain

W, H, B = 480, 190, 180                 # fenêtre et côté de la forme (la forme est à droite)
KEY = (1, 2, 3)                         # couleur rendue transparente quand la vraie transparence manque


class Layered:
    """Fenêtre Windows à transparence par pixel (bords doux, ombres, clics qui traversent le vide)."""

    def __init__(self, tk_id: int):
        from ctypes import wintypes as wt
        self.u, self.g = ctypes.windll.user32, ctypes.windll.gdi32
        u, g = self.u, self.g
        u.GetAncestor.restype = wt.HWND
        u.GetAncestor.argtypes = [wt.HWND, wt.UINT]
        u.GetDC.restype = wt.HDC
        u.GetDC.argtypes = [wt.HWND]
        u.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
        u.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
        u.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, wt.LONG]
        u.UpdateLayeredWindow.argtypes = [wt.HWND, wt.HDC, ctypes.c_void_p, ctypes.c_void_p, wt.HDC,
                                          ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, wt.DWORD]
        u.UpdateLayeredWindow.restype = wt.BOOL
        g.CreateCompatibleDC.restype = wt.HDC
        g.CreateCompatibleDC.argtypes = [wt.HDC]
        g.CreateDIBSection.restype = wt.HBITMAP
        g.CreateDIBSection.argtypes = [wt.HDC, ctypes.c_void_p, wt.UINT, ctypes.POINTER(ctypes.c_void_p),
                                       wt.HANDLE, wt.DWORD]
        g.SelectObject.restype = wt.HGDIOBJ
        g.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
        g.DeleteObject.argtypes = [wt.HGDIOBJ]
        g.DeleteDC.argtypes = [wt.HDC]
        self.hwnd = u.GetAncestor(tk_id, 2) or tk_id
        style = u.GetWindowLongW(self.hwnd, -20)
        u.SetWindowLongW(self.hwnd, -20, style | 0x80000 | 0x80)       # WS_EX_LAYERED | WS_EX_TOOLWINDOW
        self.wt = wt

    def show(self, rgba: np.ndarray, x: int, y: int) -> None:
        wt, u, g = self.wt, self.u, self.g
        h, w = rgba.shape[:2]
        a = rgba[..., 3:4].astype(np.uint16)
        bgra = np.empty((h, w, 4), dtype=np.uint8)
        bgra[..., 0] = (rgba[..., 2] * a[..., 0]) // 255                # couleurs prémultipliées, ordre BGRA
        bgra[..., 1] = (rgba[..., 1] * a[..., 0]) // 255
        bgra[..., 2] = (rgba[..., 0] * a[..., 0]) // 255
        bgra[..., 3] = rgba[..., 3]

        class BMI(ctypes.Structure):
            _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                        ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                        ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                        ("biClrImportant", wt.DWORD)]

        bmi = BMI(ctypes.sizeof(BMI), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        screen = u.GetDC(None)
        mem = g.CreateCompatibleDC(screen)
        bits = ctypes.c_void_p()
        bmp = g.CreateDIBSection(mem, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        if not bmp:
            g.DeleteDC(mem)
            u.ReleaseDC(None, screen)
            raise OSError("CreateDIBSection a échoué")
        ctypes.memmove(bits, bgra.ctypes.data, w * h * 4)
        old = g.SelectObject(mem, bmp)
        pos, size, src = wt.POINT(x, y), wt.SIZE(w, h), wt.POINT(0, 0)
        blend = (ctypes.c_ubyte * 4)(0, 0, 255, 1)                      # AC_SRC_OVER, alpha global 255, AC_SRC_ALPHA
        ok = u.UpdateLayeredWindow(self.hwnd, screen, ctypes.byref(pos), ctypes.byref(size), mem,
                                   ctypes.byref(src), 0, ctypes.byref(blend), 2)
        g.SelectObject(mem, old)
        g.DeleteObject(bmp)
        g.DeleteDC(mem)
        u.ReleaseDC(None, screen)
        if not ok:
            raise OSError("UpdateLayeredWindow a échoué")


class Widget:
    def __init__(self, core, on_quit=None):
        import tkinter as tk
        self.tk, self.core, self.on_quit = tk, core, on_quit
        self.on_mic = lambda: None
        cfg = core.config.get("interface_options", {})
        self.linger = float(cfg.get("delai_repli", 7))
        self.events: queue.Queue = queue.Queue()
        events = self.events
        self._observer = lambda kind, text: events.put((kind, text))
        core.observers.append(self._observer)

        self.blob = Blob(B)
        self.state, self.level = "repos", 0.0
        self.said = self.reply = self.action = ""
        self.asking = False
        self.busy_since = 0.0                           # début du travail en cours (pour afficher la durée)
        self.answers: queue.Queue = queue.Queue()
        self.last_activity = time.time()
        self.bubble_key, self.bubble_img, self.buttons, self.bubble_at = None, None, {}, (0, 0)
        self.margin = self._load_margin()
        self.frames = 0
        self.clicks = 0                                 # compteur de clics reçus (sert aux tests)

        root = self.root = tk.Tk()
        root.title(core.config["assistant"]["nom"])
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg="#%02x%02x%02x" % KEY)
        self.place()
        root.update_idletasks()
        self.layer, self.label, self.photo = None, None, None
        if sys.platform == "win32":
            try:
                self.layer = Layered(root.winfo_id())
            except Exception as e:
                print(f"[interface] transparence complète indisponible ({e}), mode simple.")
        if self.layer is None:
            if sys.platform == "win32":
                root.attributes("-transparentcolor", "#%02x%02x%02x" % KEY)
            self.label = tk.Label(root, bd=0, bg="#%02x%02x%02x" % KEY)
            self.label.pack()

        self.entry_win = tk.Toplevel(root)
        self.entry_win.overrideredirect(True)
        self.entry_win.attributes("-topmost", True)
        self.entry_win.configure(bg="#d9d6cf")
        self.entry = tk.Entry(self.entry_win, bg="#fffdf9", fg="#26262e", insertbackground="#26262e",
                              relief="flat", font=("Segoe UI" if sys.platform == "win32" else "DejaVu Sans", 11))
        self.entry.pack(fill="both", expand=True, padx=1, pady=1, ipady=6, ipadx=8)
        self.entry.bind("<Return>", self._send)
        self.entry.bind("<Escape>", lambda e: self.hide_entry())
        self.entry_win.withdraw()
        self.typing = False

        self.menu = tk.Menu(root, tearoff=0)
        self.menu.add_command(label="Écrire à Jarvis", command=self.show_entry)
        self.menu.add_command(label="Parler", command=lambda: self.on_mic())
        self.menu.add_command(label="Annuler la demande en cours", command=self.cancel)
        self.menu.add_separator()
        self.menu.add_command(label="Quitter Jarvis", command=self.quit)
        for target in (root, self.label):
            if target is not None:
                target.bind("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))
                target.bind("<ButtonPress-1>", self._press)
                target.bind("<B1-Motion>", self._drag)
                target.bind("<ButtonRelease-1>", self._release)
        self._drag_from, self._moved = None, False
        self._last = time.time()
        root.after(30, self._tick)

    # --- position ----------------------------------------------------------
    def _load_margin(self) -> tuple[int, int]:
        try:
            d = json.loads((_config.DATA / "widget.json").read_text(encoding="utf-8"))
            return int(d["droite"]), int(d["bas"])
        except (OSError, ValueError, KeyError):
            return 6, 44                                # coin bas droit, au-dessus de la barre des tâches

    def _save_margin(self) -> None:
        try:
            _config.DATA.mkdir(exist_ok=True)
            (_config.DATA / "widget.json").write_text(json.dumps({"droite": self.margin[0], "bas": self.margin[1]}))
        except OSError:
            pass

    def origin(self) -> tuple[int, int]:
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        return sw - W - self.margin[0], sh - H - self.margin[1]

    def place(self) -> None:
        x, y = self.origin()
        self.root.geometry(f"{W}x{H}+{x}+{y}")

    # --- image -------------------------------------------------------------
    def compose(self) -> np.ndarray:
        frame = np.zeros((H, W, 4), dtype=np.uint8)
        status = self.status()
        key = (self.said, self.reply, self.asking, status, self.state)
        if key != self.bubble_key:
            self.bubble_key = key
            color = tuple(int(v * 0.8) for v in PALETTES.get(self.state, PALETTES["repos"])[0])
            img, self.buttons = (None, {}) if self.typing else bubble(self.said, self.reply, self.asking,
                                                                     status=status, status_color=color)
            self.bubble_img = None if img is None else np.asarray(img)
        if self.bubble_img is not None and not self.typing:
            bh, bw = self.bubble_img.shape[:2]
            near = int((1.0 - min(1.0, self.blob.scale)) * 70)          # forme petite : la bulle se rapproche
            bx, by = max(0, W - B - bw + 22 + near), max(0, min(H - bh, (H - bh) // 2))
            self.bubble_at = (bx, by)
            frame[by:by + bh, bx:bx + bw] = self.bubble_img[:H - by, :W - bx]
        blob = self.blob.render()
        x0, y0 = W - B, (H - B) // 2
        region = frame[y0:y0 + B, x0:x0 + B].astype(np.float32)
        a = blob[..., 3:4].astype(np.float32) / 255.0
        region[..., :3] = blob[..., :3] * a + region[..., :3] * (1 - a)
        region[..., 3:4] = (a + region[..., 3:4] / 255.0 * (1 - a)) * 255.0
        frame[y0:y0 + B, x0:x0 + B] = region.astype(np.uint8)
        return frame

    def status(self) -> str:
        """Ce que Jarvis est en train de faire, en clair, avec la durée quand ça dure."""
        if self.state == "écoute":
            return "Je t'écoute…"
        if self.state not in ("réfléchit", "agit"):
            return ""
        text = (self.action or "Je m'en occupe") if self.state == "agit" else "J'ai compris, je réfléchis"
        seconds = int(time.time() - self.busy_since) if self.busy_since else 0
        if seconds >= 60:
            return f"{text}… {seconds} s. C'est long : clic droit pour annuler."
        return f"{text}… {seconds} s" if seconds >= 6 else f"{text}…"

    def draw(self) -> None:
        frame = self.compose()
        x, y = self.origin()
        if self.layer is not None:
            self.layer.show(frame, x, y)
        else:
            from PIL import Image, ImageTk
            a = frame[..., 3:4].astype(np.float32) / 255.0
            a = (a > 0.5).astype(np.float32)            # pas de demi-transparence dans ce mode : bord net
            rgb = (frame[..., :3] * a + np.array(KEY, dtype=np.float32) * (1 - a)).astype(np.uint8)
            self.photo = ImageTk.PhotoImage(Image.fromarray(rgb, "RGB"))
            self.label.configure(image=self.photo)
        self.frames += 1

    # --- boucle ------------------------------------------------------------
    def _tick(self) -> None:
        try:
            while True:
                kind, text = self.events.get_nowait()
                if kind == "etat":
                    previous = self.state
                    self.state = text if text in ("écoute", "réfléchit", "agit", "parle") else "repos"
                    if self.state == "écoute":
                        self.said = self.reply = ""
                    working = ("réfléchit", "agit")
                    if self.state in working and previous not in working:
                        self.busy_since = time.time()
                    if self.state == "réfléchit":
                        self.action = ""
                elif kind == "action":
                    self.action = text
                elif kind == "niveau":
                    self.level = min(1.0, float(text or 0))
                elif kind == "vous":
                    self.said, self.reply = text, ""
                elif kind in ("jarvis", "notification", "question"):
                    self.reply = text
                    if kind == "notification":
                        self.said = ""
                elif kind == "boutons":
                    self.asking = True
                elif kind == "fermer":
                    self.root.destroy()
                    return
                if kind != "niveau":
                    self.last_activity = time.time()
        except queue.Empty:
            pass
        now = time.time()
        if (self.said or self.reply) and self.state == "repos" and not self.asking \
                and now - self.last_activity > self.linger:
            self.said = self.reply = ""                 # l'échange est fini : la bulle s'efface
        dt, self._last = min(0.1, now - self._last), now
        self.blob.step(dt, self.state, self.level)
        self.level *= 0.85
        try:
            self.draw()
        except Exception as e:
            if self.layer is not None:                  # repli définitif sur le mode simple
                print(f"[interface] {e} : passage en mode simple.")
                self.layer = None
                self.label = self.tk.Label(self.root, bd=0, bg="#%02x%02x%02x" % KEY)
                self.label.pack()
        calm = self.state == "repos" and not (self.said or self.reply)
        self.root.after(80 if calm else 33, self._tick)

    # --- souris ------------------------------------------------------------
    def _press(self, e) -> None:
        self._drag_from, self._moved = (e.x_root, e.y_root, *self.margin), False

    def _drag(self, e) -> None:
        if not self._drag_from:
            return
        x0, y0, right, bottom = self._drag_from
        if abs(e.x_root - x0) + abs(e.y_root - y0) > 4:
            self._moved = True
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            self.margin = (max(-W + B, min(sw - W, right - (e.x_root - x0))),
                           max(0, min(sh - H, bottom - (e.y_root - y0))))
            if self.layer is None:
                self.place()

    def _release(self, e) -> None:
        self._drag_from = None
        if self._moved:
            self.place()
            return self._save_margin()
        self.clicks += 1
        ox, oy = self.origin()
        x, y = e.x_root - ox - self.bubble_at[0], e.y_root - oy - self.bubble_at[1]
        for name, (x1, y1, x2, y2) in self.buttons.items():
            if self.asking and x1 <= x <= x2 and y1 <= y <= y2:
                return self._answer(name == "oui")
        if e.x_root - ox >= W - B:                      # clic sur la forme : on peut lui écrire
            self.hide_entry() if self.typing else self.show_entry()

    # --- saisie ------------------------------------------------------------
    def show_entry(self) -> None:
        self.typing = True
        self.bubble_key = None
        ox, oy = self.origin()
        self.entry_win.geometry(f"290x38+{ox + W - B - 290 + 18}+{oy + H // 2 - 19}")
        self.entry_win.deiconify()
        self.entry_win.lift()
        self.entry_win.focus_force()
        self.entry.focus_set()

    def hide_entry(self) -> None:
        self.typing = False
        self.bubble_key = None
        self.entry.delete(0, "end")
        self.entry_win.withdraw()

    def _send(self, event=None) -> None:
        text = self.entry.get().strip()
        self.hide_entry()
        if not text:
            return
        if QUIT.match(plain(text)):
            return self.quit()
        threading.Thread(target=self.core.ask, args=(text, self.channel()), daemon=True).start()

    def cancel(self) -> None:
        threading.Thread(target=self.core.cancel, daemon=True).start()

    def _answer(self, value: bool) -> None:
        self.asking = False
        self.reply = ""
        self.answers.put(value)

    def channel(self):
        """Canal des demandes tapées ici : confirmations par les boutons Oui / Non de la bulle."""
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
                pass                                    # déjà affiché par l'observateur

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
            for name in ("label", "photo", "entry", "entry_win", "menu", "root", "tk", "layer"):
                self.__dict__.pop(name, None)
            gc.collect()
