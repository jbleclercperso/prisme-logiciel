"""Le visage de Jarvis : une forme souple et colorée qui respire au repos, se tend quand elle écoute,
tourne doucement quand elle réfléchit et ondule quand elle parle. Rendu logiciel (numpy), sans dépendance
graphique : la même fonction sert à l'affichage à l'écran et aux aperçus."""
from __future__ import annotations

import math

import numpy as np

# deux couleurs par état : la forme est un dégradé vivant entre les deux
PALETTES = {
    "repos": ((124, 131, 253), (196, 167, 255)),        # lavande calme
    "écoute": ((34, 197, 158), (163, 240, 205)),        # menthe
    "réfléchit": ((255, 176, 59), (255, 128, 92)),      # ambre
    "parle": ((255, 110, 138), (255, 184, 108)),        # corail, pêche
}
# par état : taille, vivacité, ampleur des ondulations, rotation
MOODS = {
    "repos": (0.56, 0.55, 0.16, 0.05),
    "écoute": (0.92, 1.5, 0.22, 0.10),
    "réfléchit": (0.86, 1.9, 0.42, 0.9),
    "parle": (1.0, 3.0, 0.34, 0.15),
}


class Blob:
    def __init__(self, size: int = 180):
        self.n = size
        c = (size - 1) / 2
        yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
        self.dx, self.dy = xx - c, yy - c
        self.dist = np.hypot(self.dx, self.dy)
        self.theta = np.arctan2(self.dy, self.dx)
        self.base = size * 0.5 * 0.60                   # rayon à pleine taille (marge pour vagues et halo)
        self.t = self.phase = self.spin = 0.0
        self.scale, self.speed, self.amp, self.turn = MOODS["repos"]
        self.level = 0.0
        self.colors = np.array(PALETTES["repos"], dtype=np.float32)

    def step(self, dt: float, state: str, level: float = 0.0) -> None:
        """Avance l'animation de dt secondes vers l'état demandé (transitions douces)."""
        scale, speed, amp, turn = MOODS.get(state, MOODS["repos"])
        k = min(1.0, dt * 5.0)
        self.t += dt
        if state == "parle":                            # pas de mesure du son émis : rythme de parole simulé
            level = 0.45 + 0.4 * abs(math.sin(self.t * 6.1) * math.sin(self.t * 2.3 + 0.7)) \
                + 0.15 * math.sin(self.t * 11.0)
        self.level += (max(0.0, min(1.0, level)) - self.level) * min(1.0, dt * 14.0)
        if state in ("écoute", "parle"):
            amp += self.level * (0.55 if state == "parle" else 0.75)
            scale *= 1.0 + self.level * 0.07
        if state == "repos":
            scale *= 1.0 + 0.035 * math.sin(self.t * 1.3)        # respiration
        self.scale += (scale - self.scale) * k
        self.speed += (speed - self.speed) * k
        self.amp += (amp - self.amp) * k
        self.turn += (turn - self.turn) * k
        self.phase += dt * self.speed
        self.spin += dt * self.turn
        target = np.array(PALETTES.get(state, PALETTES["repos"]), dtype=np.float32)
        self.colors += (target - self.colors) * min(1.0, dt * 4.0)

    def _radius(self, layer: int) -> np.ndarray:
        p, th = self.phase + layer * 1.7, self.theta + self.spin + layer * 0.9
        wave = (0.50 * np.sin(2 * th + p * 1.00) + 0.32 * np.sin(3 * th - p * 1.35 + 1.3)
                + 0.18 * np.sin(4 * th + p * 1.9 + 2.1))
        size = self.base * self.scale * (1.0 + 0.10 * layer)
        return size * (1.0 + self.amp * 0.36 * wave)

    def render(self) -> np.ndarray:
        """Image RGBA (hauteur, largeur, 4), alpha non prémultiplié."""
        n = self.n
        c1, c2 = self.colors
        rgb = np.zeros((n, n, 3), dtype=np.float32)
        alpha = np.zeros((n, n), dtype=np.float32)
        r0 = self._radius(0)
        # halo doux autour de la forme
        halo = 0.20 * np.exp(-np.clip(self.dist - r0, 0, None) / (self.base * 0.13))
        rgb[:] = c1
        alpha[:] = np.where(self.dist > r0, halo, 0.0)
        axis = self.phase * 0.35
        grad = np.clip(0.5 + 0.5 * (self.dx * math.cos(axis) + self.dy * math.sin(axis)) / (self.base * self.scale + 1e-6), 0, 1)
        for layer, opacity in ((2, 0.30), (1, 0.50), (0, 0.96)):       # du voile arrière au coeur
            r = r0 if layer == 0 else self._radius(layer)
            a = np.clip((r - self.dist) / 1.6 + 0.5, 0, 1) * opacity
            mix = np.clip(grad + (0.18 * layer), 0, 1)[..., None]
            color = c1 * (1 - mix) + c2 * mix
            rgb = rgb * (1 - a[..., None]) + color * a[..., None]
            alpha = alpha + a * (1 - alpha)
        # reflet clair en haut à gauche : donne du volume
        s = self.base * self.scale
        light = 0.30 * np.exp(-((self.dx + 0.28 * s) ** 2 + (self.dy + 0.34 * s) ** 2) / (0.42 * s) ** 2)
        light *= np.clip((r0 - self.dist) / 1.6 + 0.5, 0, 1)
        rgb = rgb + (255.0 - rgb) * light[..., None]
        out = np.empty((n, n, 4), dtype=np.uint8)
        out[..., :3] = np.clip(rgb, 0, 255)
        out[..., 3] = np.clip(alpha * 255.0, 0, 255)
        return out


# --------------------------------------------------------------------------- la bulle de texte
INK, SOFT, CARD = (38, 38, 46), (128, 128, 140), (255, 253, 249)
YES, NO = (34, 160, 120), (120, 120, 132)


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    names = (["segoeuib.ttf", "DejaVuSans-Bold.ttf"] if bold else ["segoeui.ttf", "DejaVuSans.ttf"])
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _wrap(draw, text: str, font, width: int, max_lines: int) -> list[str]:
    lines, line = [], ""
    for word in " ".join(text.split()).split(" "):
        trial = (line + " " + word).strip()
        if draw.textlength(trial, font=font) <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines[-1] and draw.textlength(lines[-1] + "…", font=font) > width:
            lines[-1] = lines[-1][:-1]
        lines[-1] = lines[-1].rstrip() + "…"
    return [ln for ln in lines if ln]


def bubble(said: str = "", reply: str = "", asking: bool = False, width: int = 290):
    """Dessine la bulle (ce que tu as dit, ce que Jarvis répond, boutons Oui / Non).
    Renvoie (image RGBA ou None, {nom du bouton: (x1, y1, x2, y2)})."""
    from PIL import Image, ImageDraw, ImageFilter
    if not (said or reply):
        return None, {}
    pad, margin = 14, 12                            # marge = place pour l'ombre
    small, normal, bold = _font(12), _font(14), _font(13, True)
    probe = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
    inner = width - 2 * pad - 2 * margin
    said_lines = _wrap(probe, "« " + said + " »", small, inner, 2) if said else []
    reply_lines = _wrap(probe, reply, normal, inner, 6) if reply else []
    text_w = max([probe.textlength(ln, font=small) for ln in said_lines]
                 + [probe.textlength(ln, font=normal) for ln in reply_lines] + [150 if asking else 40])
    h = pad * 2 + len(said_lines) * 17 + len(reply_lines) * 20 + (4 if said_lines and reply_lines else 0) \
        + (40 if asking else 0)
    w = int(text_w) + 2 * pad
    img = Image.new("RGBA", (w + 2 * margin, h + 2 * margin), (0, 0, 0, 0))
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((margin, margin + 3, margin + w, margin + h + 3), 16, fill=(20, 20, 40, 70))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(6)))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((margin, margin, margin + w, margin + h), 16, fill=CARD + (250,))
    x, y = margin + pad, margin + pad - 2
    for ln in said_lines:
        d.text((x, y), ln, font=small, fill=SOFT)
        y += 17
    if said_lines and reply_lines:
        y += 4
    for ln in reply_lines:
        d.text((x, y), ln, font=normal, fill=INK)
        y += 20
    buttons = {}
    if asking:
        y += 8
        for name, label, color in (("oui", "Oui", YES), ("non", "Non", NO)):
            bw = int(d.textlength(label, font=bold)) + 34
            d.rounded_rectangle((x, y, x + bw, y + 28), 14, fill=color)
            d.text((x + 17, y + 5), label, font=bold, fill=(255, 255, 255))
            buttons[name] = (x, y, x + bw, y + 28)
            x += bw + 10
    return img, buttons
