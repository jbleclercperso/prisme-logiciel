"""Importe tous les modules d'outils pour les enregistrer dans le registre."""
from . import browser, extensions, extras, files, missions, pc, perso, planning, system  # noqa: F401
from .registry import TOOLS, anthropic_tools, execute, ollama_tools, runtime  # noqa: F401
