"""Outils fichiers : lister, lire, écrire, chercher, copier, déplacer, supprimer."""
from __future__ import annotations

import os
import shutil
import time
import unicodedata
from pathlib import Path

from .registry import tool

MAX_CHARS = 20000


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return s.lower().strip()


def _known_folders() -> dict[str, Path]:
    home = Path.home()

    def first(*names: str) -> Path:
        for base in (home, home / "OneDrive"):
            for n in names:
                if (base / n).exists():
                    return base / n
        return home / names[0]

    desktop = first("Desktop", "Bureau")
    docs = first("Documents")
    dl = first("Downloads", "Téléchargements")
    pics = first("Pictures", "Images")
    music = first("Music", "Musique")
    vids = first("Videos", "Vidéos")
    return {"bureau": desktop, "desktop": desktop, "documents": docs, "mes documents": docs,
            "telechargements": dl, "downloads": dl, "images": pics, "pictures": pics,
            "photos": pics, "musique": music, "music": music, "videos": vids,
            "maison": home, "home": home, "utilisateur": home}


def resolve(path: str) -> Path:
    """Accepte un chemin Windows complet ou un alias parlé ('bureau/notes.txt')."""
    raw = os.path.expandvars(os.path.expanduser(str(path).strip().strip('"')))
    parts = raw.replace("\\", "/").split("/")
    known = _known_folders()
    head = _norm(parts[0])
    if head in known:
        return known[head].joinpath(*[p for p in parts[1:] if p])
    p = Path(raw)
    return p if p.is_absolute() else Path.home() / p


def _size(n: float) -> str:
    for unit in ("o", "Ko", "Mo", "Go"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} To"


PATH_DOC = ("Chemin complet, ou alias : bureau, documents, telechargements, images, musique, "
            "videos, maison (ex: 'bureau/rapport.docx').")


@tool("lister_dossier", "Liste le contenu d'un dossier (nom, taille, date).",
      {"chemin": {"type": "string", "description": PATH_DOC}}, ["chemin"])
def lister_dossier(chemin: str) -> str:
    p = resolve(chemin)
    if not p.is_dir():
        return f"ERREUR : dossier introuvable : {p}"
    entries = sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
    lines = [f"Contenu de {p} ({len(entries)} éléments) :"]
    for e in entries[:200]:
        try:
            st = e.stat()
            info = "<dossier>" if e.is_dir() else _size(st.st_size)
            lines.append(f"- {e.name}  [{info}, {time.strftime('%d/%m/%Y %H:%M', time.localtime(st.st_mtime))}]")
        except OSError:
            lines.append(f"- {e.name}")
    if len(entries) > 200:
        lines.append(f"... et {len(entries) - 200} autres")
    return "\n".join(lines)


@tool("lire_fichier", "Lit le texte d'un fichier (txt, md, csv, json, code, pdf, docx).",
      {"chemin": {"type": "string", "description": PATH_DOC}}, ["chemin"])
def lire_fichier(chemin: str) -> str:
    p = resolve(chemin)
    if not p.is_file():
        return f"ERREUR : fichier introuvable : {p}"
    ext = p.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        text = "\n".join((page.extract_text() or "") for page in PdfReader(str(p)).pages)
    elif ext == ".docx":
        import re
        import zipfile
        xml = zipfile.ZipFile(p).read("word/document.xml").decode("utf-8", "ignore")
        text = re.sub(r"<[^>]+>", "", xml.replace("</w:p>", "\n"))
    else:
        data = p.read_bytes()
        if b"\x00" in data[:2000]:
            return f"ERREUR : {p.name} est un fichier binaire ({_size(len(data))}), illisible en texte."
        text = data.decode("utf-8", "replace")
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + f"\n[... tronqué : {len(text)} caractères au total]"
    return text or "(fichier vide)"


@tool("ecrire_fichier", "Crée ou remplace un fichier texte. 'ajouter' = true pour écrire à la suite.",
      {"chemin": {"type": "string", "description": PATH_DOC},
       "contenu": {"type": "string"},
       "ajouter": {"type": "boolean", "description": "Ajouter à la fin au lieu de remplacer."}},
      ["chemin", "contenu"])
def ecrire_fichier(chemin: str, contenu: str, ajouter: bool = False) -> str:
    p = resolve(chemin)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a" if ajouter else "w", encoding="utf-8") as f:
        f.write(contenu)
    return f"Fichier écrit : {p}"


@tool("creer_dossier", "Crée un dossier.", {"chemin": {"type": "string", "description": PATH_DOC}}, ["chemin"])
def creer_dossier(chemin: str) -> str:
    p = resolve(chemin)
    p.mkdir(parents=True, exist_ok=True)
    return f"Dossier créé : {p}"


@tool("chercher_fichiers",
      "Cherche des fichiers par nom (motif avec *, ex: '*facture*.pdf') dans un dossier et ses sous-dossiers.",
      {"motif": {"type": "string"},
       "dossier": {"type": "string", "description": "Où chercher (défaut : maison). " + PATH_DOC}},
      ["motif"])
def chercher_fichiers(motif: str, dossier: str = "maison") -> str:
    import fnmatch
    base = resolve(dossier)
    if "*" not in motif and "?" not in motif:
        motif = f"*{motif}*"
    motif = motif.lower()
    skip = {"appdata", "node_modules", ".git", "$recycle.bin", ".venv", "__pycache__"}
    found, deadline = [], time.time() + 20
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d.lower() not in skip and not d.startswith(".")]
        for name in files + dirs:
            if fnmatch.fnmatch(name.lower(), motif):
                found.append(os.path.join(root, name))
        if len(found) >= 50 or time.time() > deadline:
            break
    if not found:
        return f"Aucun résultat pour '{motif}' dans {base}."
    return f"{len(found)} résultat(s) :\n" + "\n".join(found[:50])


@tool("taille_dossiers",
      "Mesure la place prise sur le disque : classe les sous-dossiers (et gros fichiers) d'un dossier du plus "
      "volumineux au moins volumineux, avec leur taille et leur part du disque. Pour « qu'est-ce qui prend "
      "de la place », « mes plus gros dossiers ».",
      {"chemin": {"type": "string", "description": "Dossier à analyser (défaut : maison). " + PATH_DOC},
       "nombre": {"type": "integer", "description": "Combien d'éléments renvoyer (défaut 10)."}})
def taille_dossiers(chemin: str = "maison", nombre: int = 10) -> str:
    base = resolve(chemin)
    if not base.is_dir():
        return f"ERREUR : dossier introuvable : {base}"
    deadline = time.time() + 45
    partial = False

    def size_of(path: str) -> int:
        nonlocal partial
        total, stack = 0, [path]
        while stack:
            if time.time() > deadline:
                partial = True
                break
            try:
                with os.scandir(stack.pop()) as it:
                    for e in it:
                        try:
                            if e.is_symlink():
                                continue
                            if e.is_dir(follow_symlinks=False):
                                stack.append(e.path)
                            else:
                                total += e.stat(follow_symlinks=False).st_size
                        except OSError:
                            pass
            except OSError:
                pass
        return total

    items = []
    try:
        entries = list(os.scandir(base))
    except OSError as e:
        return f"ERREUR : accès refusé à {base} : {e}"
    for e in entries:
        try:
            if e.is_symlink():
                continue
            if e.is_dir(follow_symlinks=False):
                items.append((size_of(e.path), e.name, "dossier"))
            else:
                items.append((e.stat().st_size, e.name, "fichier"))
        except OSError:
            pass
    items.sort(reverse=True)
    disk = shutil.disk_usage(base)
    total = sum(s for s, _, _ in items)
    lines = [f"Analyse de {base} : {_size(total)} au total, soit {total / disk.total * 100:.1f} % du disque "
             f"(disque : {_size(disk.total)}, dont {_size(disk.free)} libres)."]
    for s, name, kind in items[:max(1, int(nombre))]:
        lines.append(f"- {name} ({kind}) : {_size(s)} — {s / disk.total * 100:.1f} % du disque, "
                     f"{s / total * 100 if total else 0:.0f} % de ce dossier")
    if partial:
        lines.append("(Analyse interrompue au bout de 45 secondes : les tailles sont des minimums.)")
    return "\n".join(lines)


@tool("copier", "Copie un fichier ou un dossier (transfert vers clé USB, autre dossier, etc.).",
      {"source": {"type": "string", "description": PATH_DOC},
       "destination": {"type": "string", "description": "Dossier ou chemin cible."}},
      ["source", "destination"])
def copier(source: str, destination: str) -> str:
    src, dst = resolve(source), resolve(destination)
    if not src.exists():
        return f"ERREUR : source introuvable : {src}"
    if dst.is_dir():
        dst = dst / src.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)
    return f"Copié : {src} -> {dst}"


@tool("deplacer", "Déplace ou renomme un fichier ou un dossier.",
      {"source": {"type": "string", "description": PATH_DOC},
       "destination": {"type": "string", "description": "Dossier ou nouveau chemin."}},
      ["source", "destination"])
def deplacer(source: str, destination: str) -> str:
    src, dst = resolve(source), resolve(destination)
    if not src.exists():
        return f"ERREUR : source introuvable : {src}"
    if dst.is_dir():
        dst = dst / src.name
    if dst.exists():
        return f"ERREUR : la destination existe déjà : {dst}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return f"Déplacé : {src} -> {dst}"


@tool("supprimer", "Envoie un fichier ou un dossier à la corbeille (récupérable).",
      {"chemin": {"type": "string", "description": PATH_DOC}}, ["chemin"],
      confirm=lambda a: f"Envoyer {resolve(a['chemin']).name} à la corbeille ?")
def supprimer(chemin: str) -> str:
    p = resolve(chemin)
    if not p.exists():
        return f"ERREUR : introuvable : {p}"
    if p == Path.home() or len(p.parts) <= 2:
        return "ERREUR : refus de supprimer un dossier racine."
    from send2trash import send2trash
    send2trash(str(p))
    return f"Envoyé à la corbeille : {p}"


@tool("ouvrir_fichier", "Ouvre un fichier ou un dossier avec le programme par défaut de Windows.",
      {"chemin": {"type": "string", "description": PATH_DOC}}, ["chemin"])
def ouvrir_fichier(chemin: str) -> str:
    p = resolve(chemin)
    if not p.exists():
        return f"ERREUR : introuvable : {p}"
    if hasattr(os, "startfile"):
        os.startfile(str(p))  # type: ignore[attr-defined]
    else:
        import subprocess
        subprocess.Popen(["xdg-open", str(p)])
    return f"Ouvert : {p}"
