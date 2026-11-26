"""Chargement de config.yaml et du fichier .env."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

DEFAULTS = {
    "assistant": {"nom": "Jarvis", "appellation": "Monsieur", "langue": "fr"},
    "cerveau": {
        "fournisseur": "forfait",
        "modele_forfait": "sonnet",
        "modele": "claude-opus-5-5",
        "effort": "low",
        "recherche_web": True,
        "ollama_url": "http://localhost:11434",
        "ollama_modele": "qwen3:14b",
    },
    "voix": {
        "mot_reveil": "hey_jarvis",
        "seuil_reveil": 0.5,
        "raccourci": "ctrl+alt+j",
        "whisper_modele": "small",
        "whisper_device": "cpu",
        "tts": "edge",
        "tts_voix": "fr-FR-HenriNeural",
        "tts_vitesse": "+8%",
        "suivi_secondes": 6,
        "silence_fin": 1.0,
        "micro": None,
    },
    "securite": {"confirmations": True},
    "interface": "discret",
    "interface_options": {"veille": "point", "delai_repli": 7},
    "navigateur": {"canal": "msedge", "invisible": False},
    "surveillance": True,
    "missions": {"modele": "sonnet", "terminal": False, "etapes_max": 80},
    "applications": {},
}


def _merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_env(path: Path | None = None) -> None:
    """Lit .env (CLE=valeur) sans écraser les variables déjà définies."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        if value and not os.environ.get(key.strip()):
            os.environ[key.strip()] = value


def load_config(path: Path | None = None) -> dict:
    load_env()
    path = path or ROOT / "config.yaml"
    user = {}
    if path.exists():
        user = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    DATA.mkdir(exist_ok=True)
    return _merge(DEFAULTS, user)
