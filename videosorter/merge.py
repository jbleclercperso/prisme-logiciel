"""Un dossier qui revient sous le même nom rejoint le premier.

Le premier dossier d'un nom que Prisme a connu est la référence : on peut le
déplacer où l'on veut, Prisme le retrouve à son nom. Quand un dossier du même
nom apparaît plus tard ailleurs (nouveaux médias d'une même série, trois mois
après), son contenu est coupé-collé dans la référence, et le dossier vide
disparaît. Rien n'est écrasé : un fichier déjà là (même nom, même taille)
reste où il est ; un homonyme différent prend un nom « (2) ».

Seuls les dossiers *nouveaux* d'une relecture sont concernés — jamais le
premier inventaire d'une racine —, et pas les noms trop génériques
(« Photos », « Nouveau dossier », des chiffres…).
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

GENERIC = {
    "photo", "photos", "pix", "image", "images", "video", "videos", "vidéo", "vidéos", "vids",
    "new folder", "nouveau dossier", "download", "downloads", "téléchargement",
    "téléchargements", "telegram", "divers", "misc", "autre", "autres", "tmp", "temp",
    "dcim", "camera", "caméra", "screenshots", "captures", "whatsapp", "whatsapp video",
    "sans titre", "untitled", "a trier", "à trier", "(sans dossier)",
}


def key_of(name: str) -> str:
    return " ".join(name.casefold().split())


def mergeable(name: str) -> bool:
    key = key_of(name)
    return (len(key) >= 3 and key not in GENERIC and not key.startswith("+")
            and not re.fullmatch(r"[\d\s._-]+", key))


def _inside(a: Path, b: Path) -> bool:
    """a est b, ou dedans."""
    a, b = os.path.normcase(str(a)), os.path.normcase(str(b))
    return a == b or a.startswith(b.rstrip("\\/") + os.sep)


def find_merges(new_paths: list, known_paths: list, refs: dict) -> list[tuple[Path, Path]]:
    """(dossier nouveau, sa référence) pour chaque nouveau dossier dont le nom
    existe déjà. Plusieurs anciens du même nom : la référence notée (`refs`,
    nom -> chemin) tranche ; sans elle, on ne devine pas."""
    by_name: dict[str, list[Path]] = {}
    fresh = {os.path.normcase(str(p)) for p in new_paths}
    for path in known_paths:
        path = Path(path)
        if os.path.normcase(str(path)) in fresh or not mergeable(path.name):
            continue
        by_name.setdefault(key_of(path.name), []).append(path)
    out = []
    for path in new_paths:
        path = Path(path)
        if not mergeable(path.name):
            continue
        olds = by_name.get(key_of(path.name)) or []
        noted = refs.get(key_of(path.name))
        if len(olds) == 1:
            ref = olds[0]
        else:
            ref = next((o for o in olds if noted and
                        os.path.normcase(str(o)) == os.path.normcase(noted)), None)
        if ref is None or _inside(path, ref) or _inside(ref, path):
            continue
        out.append((path, ref))
    return out


def _free_name(folder: Path, name: str) -> Path:
    stem, ext = os.path.splitext(name)
    n = 2
    while (folder / f"{stem} ({n}){ext}").exists():
        n += 1
    return folder / f"{stem} ({n}){ext}"


def merge_into(src: Path, ref: Path, report: dict | None = None) -> dict:
    """Coupe-colle le contenu de `src` dans `ref`. Rend {moved, kept, renamed}."""
    report = report if report is not None else {"moved": 0, "kept": 0, "renamed": 0}
    src, ref = Path(src), Path(ref)
    if not src.is_dir() or not ref.is_dir() or _inside(ref, src) or _inside(src, ref):
        return report
    for entry in list(os.scandir(src)):
        source = Path(entry.path)
        target = ref / entry.name
        if entry.is_dir(follow_symlinks=False):
            if target.exists():
                merge_into(source, target, report)
            else:
                shutil.move(str(source), str(target))
                report["moved"] += sum(len(files) for _r, _d, files in os.walk(target))
            continue
        if target.exists():
            try:
                same = target.stat().st_size == entry.stat().st_size
            except OSError:
                same = False
            if same:
                report["kept"] += 1          # déjà là : on ne double rien
                continue
            target = _free_name(ref, entry.name)
            report["renamed"] += 1
        shutil.move(str(source), str(target))
        report["moved"] += 1
    try:
        src.rmdir()                          # vide : il disparaît ; sinon il reste
    except OSError:
        pass
    return report
