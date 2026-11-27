"""Aperçus de l'interface : une animation et une planche d'images fabriquées par le moteur de rendu,
puis, si un écran est disponible, des captures de la vraie fenêtre et un essai de clic."""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from jarvis import config as c  # noqa: E402
from jarvis.blob import Blob, _font, bubble  # noqa: E402

out = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
out.mkdir(parents=True, exist_ok=True)
W, H, B = 480, 190, 180
SCENE_W, SCENE_H = 560, 300


def desktop() -> Image.Image:
    """Un coin d'écran neutre : fond d'écran en dégradé et barre des tâches."""
    y, x = np.mgrid[0:SCENE_H, 0:SCENE_W].astype(np.float32)
    t = (x / SCENE_W * 0.6 + y / SCENE_H * 0.4)[..., None]
    img = Image.fromarray((np.array([52, 84, 120]) * (1 - t) + np.array([96, 132, 150]) * t).astype(np.uint8), "RGB")
    d = ImageDraw.Draw(img)
    d.rectangle((0, SCENE_H - 44, SCENE_W, SCENE_H), fill=(28, 34, 44))
    d.text((SCENE_W - 70, SCENE_H - 30), "17:42", font=_font(13), fill=(220, 224, 230))
    return img


from jarvis.blob import PALETTES  # noqa: E402


def frame(blob: Blob, said: str, reply: str, asking: bool = False, status: str = "", state: str = "repos") -> Image.Image:
    scene = desktop().convert("RGBA")
    ox, oy = SCENE_W - W - 6, SCENE_H - H - 44
    color = tuple(int(v * 0.8) for v in PALETTES.get(state, PALETTES["repos"])[0])
    card, _ = bubble(said, reply, asking, status=status, status_color=color)
    if card is not None:
        near = int((1.0 - min(1.0, blob.scale)) * 70)
        scene.alpha_composite(card, (ox + max(0, W - B - card.width + 22 + near), oy + (H - card.height) // 2))
    scene.alpha_composite(Image.fromarray(blob.render(), "RGBA"), (ox + W - B, oy + (H - B) // 2))
    return scene.convert("RGB")


SAID, REPLY = "Ouvre Chrome et mets le volume à trente", "C'est fait, JB : Chrome est ouvert et le volume est à trente pour cent."
STATUS = {"écoute": "Je t'écoute…", "réfléchit": "J'ai compris, je réfléchis…", "agit": "J'ouvre chrome…"}
script = [  # (durée en secondes, état, ce que tu as dit, réponse)
    (1.4, "repos", "", ""), (2.0, "écoute", "", ""), (1.6, "réfléchit", SAID, ""), (1.8, "agit", SAID, ""),
    (3.2, "parle", SAID, REPLY), (1.4, "repos", SAID, REPLY), (1.0, "repos", "", ""),
]
blob, frames, stills, fps = Blob(B), [], {}, 20
for seconds, state, said, reply in script:
    for i in range(int(seconds * fps)):
        level = 0.25 + 0.6 * abs(np.sin(i / 3.1) * np.sin(i / 7.3)) if state == "écoute" else 0.0
        blob.step(1 / fps, state, level)
        frames.append(frame(blob, said, reply, status=STATUS.get(state, ""), state=state))
        if i == int(seconds * fps * 0.7) and (state, bool(reply)) not in stills:
            stills[(state, bool(reply))] = frames[-1]
frames[0].save(out / "interface_animation.gif", save_all=True, append_images=frames[1:], duration=1000 // fps,
               loop=0, optimize=True)

titles = {("repos", False): "Au repos", ("écoute", False): "Elle t'écoute", ("réfléchit", False): "Elle a compris et réfléchit",
          ("agit", False): "Elle agit", ("parle", True): "Elle répond"}
blob2 = Blob(B)
for _ in range(30):
    blob2.step(1 / fps, "repos")
stills[("question", True)] = frame(blob2, "Supprime le brouillon du bureau", "Envoyer brouillon.docx à la corbeille ?", True)
titles[("question", True)] = "Elle demande confirmation"
keep = [(titles[k], stills[k]) for k in titles if k in stills]
sheet = Image.new("RGB", ((SCENE_W + 10) * 3 + 10, (SCENE_H + 40) * 2 + 10), (245, 243, 238))
d = ImageDraw.Draw(sheet)
for i, (title, img) in enumerate(keep):
    x, y = 10 + (i % 3) * (SCENE_W + 10), 8 + (i // 3) * (SCENE_H + 40)
    d.text((x + 2, y), title, font=_font(18), fill=(40, 40, 48))
    sheet.paste(img, (x, y + 30))
sheet.save(out / "interface_etats.png")
print("rendu : animation de", len(frames), "images et planche de", len(keep), "états")

# ------------------------------------------------------------------ la vraie fenêtre, si possible
try:
    import tkinter  # noqa: F401
    from PIL import ImageGrab
    from jarvis.widget import Widget
except Exception as e:
    print("fenêtre réelle non testée ici :", type(e).__name__, e)
    sys.exit(0)

c.DATA = Path(tempfile.mkdtemp())


class Core:
    observers: list = []
    config = c.load_config()
    asked: list = []

    def ask(self, text, channel=None):
        self.asked.append(text)


w = Widget(Core())


def spin(seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        w.root.update()
        time.sleep(0.01)


def grab(name: str) -> None:
    img = ImageGrab.grab()
    img.crop((img.width - 620, img.height - 330, img.width, img.height)).save(out / f"fenetre_{name}.png")


spin(1.0)
grab("repos")
w.events.put(("etat", "écoute")); w.events.put(("niveau", "0.7")); spin(0.8)
grab("ecoute")
w.events.put(("vous", SAID)); w.events.put(("jarvis", REPLY)); w.events.put(("etat", "parle")); spin(1.2)
grab("parle")
mode = "transparence complète" if w.layer is not None else "mode simple"
clicked = "non essayé"
if sys.platform == "win32":
    import pyautogui
    pyautogui.FAILSAFE = False
    ox, oy = w.origin()
    before = w.clicks
    pyautogui.click(ox + 480 - 90, oy + 95)             # au centre de la forme
    spin(0.8)
    clicked = f"reçu, champ de saisie {'ouvert' if w.typing else 'FERMÉ'}" if w.clicks > before else "NON REÇU"
    grab("saisie")
print(f"fenêtre réelle : {w.frames} images affichées, {mode}, clic sur la forme : {clicked}")
w.root.destroy()
if sys.platform == "win32" and (w.layer is None or "reçu" not in clicked or not w.typing):
    sys.exit("la fenêtre réelle ne se comporte pas comme prévu")
