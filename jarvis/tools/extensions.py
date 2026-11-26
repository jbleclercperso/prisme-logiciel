"""Extensions : des outils supplémentaires dans le dossier extensions/, que Jarvis peut écrire lui-même."""
from __future__ import annotations

import importlib.util
import re
import sys

from ..config import ROOT
from . import registry
from .registry import TOOLS, tool

EXT_DIR = ROOT / "extensions"
_owned: dict[str, list[str]] = {}       # extension -> noms des outils qu'elle a déclarés

MODELE = '''"""Exemple d'extension Jarvis. Chaque fonction décorée par @tool devient un outil."""
from jarvis.tools.registry import tool


@tool("lancer_de", "Lance un dé et renvoie le résultat.",
      {"faces": {"type": "integer", "description": "Nombre de faces (défaut 6)."}})
def lancer_de(faces: int = 6) -> str:
    import random
    return f"Le dé à {faces} faces donne {random.randint(1, faces)}."
'''


def _load(path) -> list[str]:
    name = path.stem
    for old in _owned.pop(name, []):
        TOOLS.pop(old, None)
    before = set(TOOLS)
    spec = importlib.util.spec_from_file_location(f"jarvis_ext_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _owned[name] = sorted(set(TOOLS) - before)
    return _owned[name]


def load_all() -> list[str]:
    """Charge toutes les extensions au démarrage. Renvoie les messages d'erreur éventuels."""
    EXT_DIR.mkdir(exist_ok=True)
    example = EXT_DIR / "_exemple.py.txt"
    if not example.exists():
        example.write_text(MODELE, encoding="utf-8")
    errors = []
    for path in sorted(EXT_DIR.glob("*.py")):
        try:
            _load(path)
        except Exception as e:
            errors.append(f"extension {path.name} ignorée : {type(e).__name__}: {e}")
    return errors


@tool("creer_extension",
      "Ajoute à Jarvis un NOUVEL OUTIL permanent en écrivant un module Python dans le dossier extensions/. "
      "À utiliser quand l'utilisateur demande une capacité que tu n'as pas et qui reviendra (ex. piloter un "
      "logiciel précis, appeler un service web, automatiser une manipulation). Le code doit importer "
      "`from jarvis.tools.registry import tool`, décorer chaque fonction avec "
      "@tool(nom, description, {paramètres JSON Schema}, [obligatoires]) et renvoyer du texte "
      "(commençant par 'ERREUR' en cas d'échec). L'outil est utilisable dès la demande suivante.",
      {"nom": {"type": "string", "description": "Nom du module, en minuscules sans espaces (ex. 'spotify')."},
       "code": {"type": "string", "description": "Code Python complet du module."}},
      ["nom", "code"],
      confirm=lambda a: f"Installer l'extension « {a['nom']} » que je viens d'écrire ?")
def creer_extension(nom: str, code: str) -> str:
    nom = re.sub(r"[^a-z0-9_]", "_", nom.lower()).strip("_")
    if not nom:
        return "ERREUR : nom d'extension invalide."
    try:
        compile(code, f"{nom}.py", "exec")
    except SyntaxError as e:
        return f"ERREUR : le code ne compile pas : {e}"
    EXT_DIR.mkdir(exist_ok=True)
    path = EXT_DIR / f"{nom}.py"
    backup = path.read_text(encoding="utf-8") if path.exists() else None
    path.write_text(code, encoding="utf-8")
    try:
        added = _load(path)
    except Exception as e:
        if backup is None:
            path.unlink()
        else:
            path.write_text(backup, encoding="utf-8")
            _load(path)
        return f"ERREUR : l'extension plante au chargement, elle n'a pas été installée : {type(e).__name__}: {e}"
    if not added:
        path.unlink()
        return "ERREUR : le module ne déclare aucun outil avec @tool ; il n'a pas été installé."
    registry.dirty = True
    return f"Extension « {nom} » installée. Nouveaux outils disponibles à la prochaine demande : {', '.join(added)}."


@tool("extensions", "Liste les extensions installées, ou en supprime une.",
      {"action": {"type": "string", "enum": ["lister", "supprimer", "lire"]}, "nom": {"type": "string"}},
      ["action"],
      confirm=lambda a: f"Supprimer l'extension « {a.get('nom')} » ?" if a["action"] == "supprimer" else "")
def extensions(action: str, nom: str = "") -> str:
    if action == "lister":
        return "\n".join(f"- {n} : {', '.join(t) or '(aucun outil)'}" for n, t in sorted(_owned.items())) \
            or "Aucune extension installée."
    path = EXT_DIR / f"{nom}.py"
    if not path.exists():
        return f"ERREUR : extension '{nom}' introuvable."
    if action == "lire":
        return path.read_text(encoding="utf-8")
    for old in _owned.pop(nom, []):
        TOOLS.pop(old, None)
    path.unlink()
    registry.dirty = True
    return f"Extension « {nom} » supprimée."
