"""Fiches visuelles : quand la réponse est un tableau, des chiffres ou une comparaison, Jarvis l'affiche
dans une petite fenêtre flottante au lieu de la réciter."""
from __future__ import annotations

import html as _html
import os
import re
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from .. import config as _config
from .registry import tool

PAGE = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITRE__</title>
<style>
  :root { --fond:#fbf9f4; --carte:#ffffff; --encre:#26262e; --doux:#7d7d8c; --trait:#ebe7df;
          --a1:#7c83fd; --a2:#ff6e8a; --ok:#22a078; --alerte:#e0862b; }
  * { box-sizing: border-box; }
  body { margin:0; padding:22px 24px 28px; background:var(--fond); color:var(--encre);
         font:15px/1.5 "Segoe UI", system-ui, sans-serif; }
  h1 { font-size:19px; font-weight:600; margin:0 0 2px; }
  .sous-titre { color:var(--doux); font-size:13px; margin:0 0 16px; }
  h2 { font-size:14px; font-weight:600; margin:22px 0 8px; color:var(--doux); text-transform:uppercase; letter-spacing:.06em; }
  p { margin:8px 0; }
  table { width:100%; border-collapse:collapse; background:var(--carte); border-radius:14px; overflow:hidden;
          box-shadow:0 1px 3px rgba(30,30,60,.08); }
  th { text-align:left; font-size:12px; font-weight:600; color:var(--doux); text-transform:uppercase;
       letter-spacing:.05em; padding:11px 14px; border-bottom:1px solid var(--trait); white-space:nowrap; }
  td { padding:10px 14px; border-bottom:1px solid var(--trait); vertical-align:middle; }
  tr:last-child td { border-bottom:0; }
  td.num, th.num { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
  td.nom { font-weight:500; word-break:break-word; }
  .barre { height:8px; min-width:90px; background:var(--trait); border-radius:8px; overflow:hidden; }
  .barre > i { display:block; height:100%; border-radius:8px; background:linear-gradient(90deg,var(--a1),var(--a2)); }
  .cartes { display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:12px; margin:0 0 16px; }
  .carte { background:var(--carte); border-radius:14px; padding:14px 16px; box-shadow:0 1px 3px rgba(30,30,60,.08); }
  .carte b { display:block; font-size:24px; font-weight:600; line-height:1.2; }
  .carte span { color:var(--doux); font-size:12.5px; }
  .ok { color:var(--ok); } .alerte { color:var(--alerte); } .doux { color:var(--doux); }
  ul, ol { padding-left:20px; } li { margin:4px 0; }
  code { background:var(--trait); padding:1px 6px; border-radius:6px; font:13px Consolas, monospace; }
  footer { margin-top:18px; color:var(--doux); font-size:12px; }
</style></head>
<body>
<h1>__TITRE__</h1>
<div class="sous-titre">__SOUS_TITRE__</div>
__CORPS__
<footer>Jarvis · __DATE__</footer>
</body></html>
"""

DOC = ("Corps HTML de la fiche (sans <html> ni <body>, sans script). Mise en forme disponible : "
       "<table> avec <th>/<td>, class='num' pour les nombres alignés à droite, class='nom' pour un libellé ; "
       "barre de proportion : <div class='barre'><i style='width:42%'></i></div> ; chiffres clés : "
       "<div class='cartes'><div class='carte'><b>128 Go</b><span>utilisés</span></div>…</div> ; "
       "<h2> pour une section ; classes 'ok', 'alerte', 'doux' pour colorer un texte.")


def _clean(body: str) -> str:
    body = re.sub(r"(?is)<script.*?</script>", "", body)
    body = re.sub(r"(?is)</?(html|head|body|iframe|object|embed)[^>]*>", "", body)
    return re.sub(r"(?i)\son\w+\s*=", " data-x=", body)


def _open(path: Path, largeur: int, hauteur: int) -> str:
    if os.environ.get("JARVIS_SANS_FENETRE"):
        return "non ouverte (mode test)"
    from ..hud import find_browser
    exe = find_browser()
    if exe:
        profile = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Jarvis" / "fenetre"
        try:
            subprocess.Popen([exe, f"--app={path.as_uri()}", f"--window-size={largeur},{hauteur}",
                              f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check"])
            return "ouverte"
        except OSError:
            pass
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        webbrowser.open(path.as_uri())
    return "ouverte dans le navigateur"


@tool("afficher",
      "Montre une FICHE VISUELLE dans une petite fenêtre flottante. À utiliser dès que la réponse est un "
      "tableau, un classement, une comparaison, plusieurs chiffres ou une liste de plus de trois éléments : "
      "affiche la fiche, puis dis seulement une phrase très courte à voix haute (le fait principal). "
      "Ne récite jamais le contenu de la fiche.",
      {"titre": {"type": "string"},
       "sous_titre": {"type": "string", "description": "Une ligne de contexte (optionnel)."},
       "html": {"type": "string", "description": DOC},
       "taille": {"type": "string", "enum": ["petite", "moyenne", "grande"]}},
      ["titre", "html"])
def afficher(titre: str, html: str, sous_titre: str = "", taille: str = "moyenne") -> str:
    folder = _config.DATA / "fiches"
    folder.mkdir(parents=True, exist_ok=True)
    for old in sorted(folder.glob("*.html"))[:-30]:         # on garde les trente dernières
        old.unlink(missing_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", titre.lower())[:40].strip("-") or "fiche"
    path = folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}.html"
    page = (PAGE.replace("__TITRE__", _html.escape(titre)).replace("__SOUS_TITRE__", _html.escape(sous_titre))
            .replace("__DATE__", time.strftime("%d/%m/%Y %H:%M")).replace("__CORPS__", _clean(html)))
    path.write_text(page, encoding="utf-8")
    w, h = {"petite": (440, 420), "grande": (860, 760)}.get(taille, (600, 640))
    return f"Fiche « {titre} » {_open(path, w, h)} ({path})."
