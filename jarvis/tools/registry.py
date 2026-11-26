"""Registre des outils que le cerveau (Claude ou IA locale) peut appeler."""
from __future__ import annotations

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
    confirm: Callable[[str], bool] = lambda question: True
    notify: Callable[[str], None] = print


TOOLS: dict[str, Tool] = {}
runtime = Runtime()


def tool(name: str, description: str, params: dict | None = None,
         required: list | None = None, confirm: Callable[[dict], str] | None = None):
    def deco(func):
        TOOLS[name] = Tool(name, description, params or {}, required or [], func, confirm)
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


def execute(name: str, args: dict | None) -> Any:
    """Exécute un outil. Renvoie du texte, ou {"text":..., "image_b64":..., "media_type":...}."""
    t = TOOLS.get(name)
    if t is None:
        return f"ERREUR : outil inconnu '{name}'."
    args = args or {}
    missing = [r for r in t.required if r not in args]
    if missing:
        return f"ERREUR : paramètres manquants : {', '.join(missing)}."
    try:
        if t.confirm and runtime.config.get("securite", {}).get("confirmations", True):
            question = t.confirm(args)
            if question and not runtime.confirm(question):
                return "Action annulée : l'utilisateur a refusé."
        result = t.func(**args)
        return result if result not in (None, "") else "OK"
    except Exception as exc:  # l'erreur est renvoyée au modèle pour qu'il s'adapte
        print(f"   [outil {name}] {type(exc).__name__}: {str(exc)[:200]}")
        return f"ERREUR : {type(exc).__name__} : {exc}"
