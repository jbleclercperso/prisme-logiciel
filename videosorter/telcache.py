"""Ce que le téléphone ne voit pas : des dossiers et des vidéos sortis du
catalogue de la page des visiteurs, sans rien changer sur le PC.

Ce n'est pas le voile de Prisme (BIN…), qui cache aussi sur l'écran du PC :
ici, la collection reste entière chez soi, et seule la page servie au dehors
les écarte -- celle du PC (`web.Library`) comme celle du NAS
(`nas/serveur.py`), qui relit la liste toute seule.

La liste vit dans `caches-telephone.json`, à côté des demandes : chez Prisme
et dans le partage du NAS. Chaque PC qui publie sur le NAS y réunit la sienne
(la retouche la plus récente gagne, un élément remontré reste noté comme tel)
: un PC n'efface pas ce qu'un autre a caché.

Sans Qt : le NAS s'en sert aussi.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

FILE_NAME = "caches-telephone.json"


def key_of(path) -> str:
    """La clé d'un chemin : son chemin réseau, sans casse ni barre finale.
    Un PC voit le NAS en « Z: », l'autre en « \\\\nas\\partage » : la même clé."""
    try:
        from .nas_publish import unc
        text = unc(path)
    except Exception:                                   # noqa: BLE001
        text = str(path)
    return os.path.normcase(os.path.normpath(text)).rstrip("\\/")


def rel_of(path, share) -> str:
    """Le chemin relatif au partage du NAS (« dossier/sous-dossier »), ou ""
    s'il n'y est pas : c'est ainsi que le NAS le reconnaît."""
    if share is None:
        return ""
    try:
        from .nas_publish import unc
        text = unc(path)
    except Exception:                                   # noqa: BLE001
        text = str(path)
    root = str(share).rstrip("\\/")
    if not text.lower().startswith(root.lower() + "\\") and \
            not text.lower().startswith(root.lower() + "/"):
        return ""
    return text[len(root):].lstrip("\\/").replace("\\", "/")


def entry(path, kind: str, share=None) -> dict:
    now = time.time()
    return {"path": str(path), "kind": kind, "rel": rel_of(path, share),
            "at": now, "updated": now, "removed": False}


def _read(path) -> dict | None:
    """{clé: élément} ; None quand le fichier existe sans pouvoir être lu."""
    if path is None:
        return {}
    try:
        told = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    found = {}
    for key, one in ((told or {}).get("entries") or {}).items() if isinstance(told, dict) \
            else []:
        if isinstance(one, dict) and one.get("path"):
            found[str(key)] = {
                "path": str(one["path"]), "kind": "video" if one.get("kind") == "video"
                else "dossier", "rel": str(one.get("rel") or ""),
                "at": float(one.get("at") or 0.0),
                "updated": float(one.get("updated") or 0.0),
                "removed": bool(one.get("removed"))}
    return found


def load(path) -> dict:
    found = _read(path)
    return found if found is not None else {}


def save(path, entries: dict) -> bool:
    if path is None:
        return False
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        spare = target.with_name(target.name + ".tmp")
        spare.write_text(json.dumps({"version": 1, "entries": entries}, ensure_ascii=False),
                         encoding="utf-8")
        os.replace(spare, target)
        return True
    except OSError:
        return False


def merge(*lists) -> dict:
    best: dict = {}
    for entries in lists:
        for key, one in (entries or {}).items():
            known = best.get(key)
            if known is None or one["updated"] > known["updated"]:
                best[key] = one
    return best


def sync(paths: list) -> dict:
    """Réunit les listes de ces fichiers et récrit celles qui diffèrent."""
    lists = [_read(path) for path in paths]
    merged = merge(*[found for found in lists if found])
    for path, found in zip(paths, lists):
        if path is not None and found is not None and found != merged:
            save(path, merged)
    return merged


def active(entries: dict) -> dict:
    return {key: one for key, one in entries.items() if not one["removed"]}


class Matcher:
    """Dit si un chemin (PC) ou un chemin relatif (NAS) est caché."""

    def __init__(self, entries: dict | None = None):
        live = active(entries or {})
        self.keys = tuple(sorted(live))
        self.rels = tuple(sorted({one["rel"].casefold().strip("/")
                                  for one in live.values() if one["rel"]}))

    def __bool__(self) -> bool:
        return bool(self.keys or self.rels)

    def path(self, path) -> bool:
        if not self.keys:
            return False
        text = key_of(path)
        return any(text == key or text.startswith(key + os.sep) for key in self.keys)

    def rel(self, rel: str) -> bool:
        if not self.rels:
            return False
        text = rel.casefold().strip("/")
        return any(text == one or text.startswith(one + "/") for one in self.rels)

