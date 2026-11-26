"""Importe tous les modules d'outils pour les enregistrer dans le registre."""
from . import extras, files, pc, system  # noqa: F401
from .registry import TOOLS, anthropic_tools, execute, ollama_tools, runtime  # noqa: F401
