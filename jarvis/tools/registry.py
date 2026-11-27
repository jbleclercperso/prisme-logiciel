"""Registre des outils que le cerveau (Claude ou IA locale) peut appeler."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Tool:
    name: str
    description: str
    params: dict
    required: list
    func: Callable[..., Any]
    confirm: Callable[[dict], str] | None = None  # renvoie la question à poser


@dataclass
class Runtime:
    """Ponts vers l'interface (voix ou texte), renseignés au démarrage."""
    config: dict = field(default_factory=dict)
    core: Any = None                # le coeur de Jarvis (jarvis.core.Core), pour les outils qui en ont besoin
    confirm: Callable[[str], bool] = lambda question: True
    notify: Callable[[str], None] = print


TOOLS: dict[str, Tool] = {}
runtime = Runtime()
dirty = False           # passe à True quand un outil est ajouté ou retiré en cours de route


def tool(name: str, description: str, params: dict | None = None,
         required: list | None = None, confirm: Callable[[dict], str] | None = None):
    props, req = dict(params or {}), list(required or [])
    if props.get("type") == "object" and isinstance(props.get("properties"), dict):
        req = req or list(props.get("required") or [])      # schéma JSON complet fourni : on le déplie
        props = props["properties"]
    if not re.fullmatch(r"[A-Za-z0-9_]{1,60}", name or ""):
        raise ValueError(f"nom d'outil invalide : {name!r} (lettres, chiffres et _ uniquement)")
    for key, spec in props.items():
        if not isinstance(spec, dict):
            raise ValueError(f"outil {name} : le paramètre '{key}' doit être décrit par un dictionnaire "
                             "JSON Schema, par exemple {\"type\": \"string\"}")
    missing = [r for r in req if r not in props]
    if missing:
        raise ValueError(f"outil {name} : paramètres obligatoires non déclarés : {missing}")

    def deco(func):
        TOOLS[name] = Tool(name, description, props, req, func, confirm)
        return func
    return deco


def schema(t: Tool) -> dict:
    return {"type": "object", "properties": t.params, "required": t.required}


def anthropic_tools() -> list[dict]:
    return [{"name": t.name, "description": t.description, "input_schema": schema(t)}
            for t in sorted(TOOLS.values(), key=lambda t: t.name)]


def ollama_tools() -> list[dict]:
    return [{"type": "function",
             "function": {"name": t.name, "description": t.description, "parameters": schema(t)}}
            for t in sorted(TOOLS.values(), key=lambda t: t.name)]


def _coerce(value: Any, spec: dict) -> Any:
    """Les modèles envoient parfois 'true' ou '3' en texte : on remet le type annoncé par le schéma."""
    kind = spec.get("type")
    if isinstance(value, str):
        v = value.strip()
        if kind == "boolean":
            return v.lower() in ("true", "1", "oui", "yes", "vrai")
        if kind == "integer" and re.fullmatch(r"-?\d+(\.0+)?", v):
            return int(float(v))
        if kind == "number" and re.fullmatch(r"-?\d+([.,]\d+)?", v):
            return float(v.replace(",", "."))
        if kind == "array":
            import json
            try:
                parsed = json.loads(v)
                return parsed if isinstance(parsed, list) else [parsed]
            except ValueError:
                return [x.strip() for x in v.split(",") if x.strip()]
    if kind == "integer" and isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def execute(name: str, args: dict | None) -> Any:
    """Exécute un outil. Renvoie du texte, ou {"text":..., "image_b64":..., "media_type":...}."""
    t = TOOLS.get(name)
    if t is None:
        return f"ERREUR : outil inconnu '{name}'."
    args = {k: _coerce(v, t.params.get(k) or {}) for k, v in (args or {}).items() if v is not None}
    unknown = [k for k in args if k not in t.params]
    if unknown:
        return f"ERREUR : paramètres inconnus pour {name} : {', '.join(unknown)}. Attendus : {', '.join(t.params) or 'aucun'}."
    missing = [r for r in t.required if r not in args]
    if missing:
        return f"ERREUR : paramètres manquants : {', '.join(missing)}."
    try:
        if t.confirm and runtime.config.get("securite", {}).get("confirmations", False):
            question = t.confirm(args)
            detail = ""
            if isinstance(question, tuple):         # (question courte dite à voix haute, détail seulement affiché)
                question, detail = question
            if question and not (runtime.confirm(question, detail) if detail else runtime.confirm(question)):
                return "Action annulée : l'utilisateur a refusé."
        result = t.func(**args)
        return result if result not in (None, "") else "OK"
    except Exception as exc:  # l'erreur est renvoyée au modèle pour qu'il s'adapte
        print(f"   [outil {name}] {type(exc).__name__}: {str(exc)[:200]}")
        return f"ERREUR : {type(exc).__name__} : {exc}"
