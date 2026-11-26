"""Fabrique une planche d'images de l'interface discrète dans chacun de ses états (pour vérification)."""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jarvis import config as c  # noqa: E402

c.DATA = Path(tempfile.mkdtemp())
cfg = c.load_config()
import tkinter as tk  # noqa: E402

from PIL import Image, ImageDraw, ImageFont, ImageGrab  # noqa: E402

from jarvis.widget import Widget  # noqa: E402


class Core:
    observers: list = []
    config = cfg

    def ask(self, text, channel=None):
        pass


out = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
out.mkdir(parents=True, exist_ok=True)
w = Widget(Core())
sw, sh = w.root.winfo_screenwidth(), w.root.winfo_screenheight()
desk = tk.Toplevel(w.root)                      # faux bureau, pour juger de la place prise
desk.overrideredirect(True)
desk.geometry(f"{sw}x{sh}+0+0")
desk.configure(bg="#3a5f8a")
tk.Frame(desk, bg="#1c2733", height=48).pack(side="bottom", fill="x")
desk.lower()


def spin(n=12):
    for _ in range(n):
        w.root.update()
        time.sleep(0.03)


shots = []


def shot(name):
    w.root.lift()
    spin(8)
    img = ImageGrab.grab()
    shots.append((name, img.crop((img.width - 470, img.height - 250, img.width, img.height))))
    if name == "Il répond":
        img.save(out / "interface_ecran_entier.png")


ev = w.events.put
spin()
shot("En veille")
ev(("etat", "écoute")); ev(("niveau", "0.8")); spin(3)
shot("Il écoute")
ev(("vous", "Ouvre Chrome et mets le volume à trente")); ev(("etat", "réfléchit")); spin()
shot("Il réfléchit")
ev(("jarvis", "C'est fait, Monsieur : Chrome est ouvert et le volume est à trente pour cent.")); ev(("etat", "parle")); spin()
shot("Il répond")
ev(("etat", "repos")); ev(("vous", "Supprime le fichier brouillon du bureau"))
ev(("question", "Envoyer brouillon.docx à la corbeille ?")); ev(("boutons", "")); spin()
shot("Il demande confirmation")
w._answer(False)
w.said = w.reply = ""
w.expand_typing()
w.entry.insert(0, "rappelle-moi d'appeler le garage à 17 h")
spin()
shot("On lui écrit")

W, H = 470, 250
sheet = Image.new("RGB", (W * 3 + 40, (H + 36) * 2 + 10), "#101418")
d = ImageDraw.Draw(sheet)
try:
    font = ImageFont.truetype("segoeui.ttf", 18)
except OSError:
    font = ImageFont.load_default(size=18)
for i, (name, img) in enumerate(shots):
    x, y = 10 + (i % 3) * (W + 10), 6 + (i // 3) * (H + 36)
    d.text((x + 4, y + 2), name, fill="#cfd6df", font=font)
    sheet.paste(img, (x, y + 30))
sheet.save(out / "interface_etats.png")
w.root.destroy()
print("ok", len(shots), "états ; écran", sw, "x", sh)
