"""Missions de fond : travaux longs confiés à un second Claude (Claude Code) pendant que Jarvis reste disponible."""
from __future__ import annotations

import asyncio
import os
import re
import threading
import time

from .files import resolve
from .registry import runtime, tool

_missions: dict[str, dict] = {}


def _workdir(nom: str):
    d = resolve("documents") / "Jarvis" / "missions" / nom
    d.mkdir(parents=True, exist_ok=True)
    return d


async def _run(m: dict) -> None:
    import claude_agent_sdk as sdk
    cfg = runtime.config.get("missions", {})
    allowed = ["Read", "Write", "Edit", "Glob", "Grep", "WebSearch", "WebFetch"]
    if cfg.get("terminal", False):
        allowed.append("Bash")
    options = sdk.ClaudeAgentOptions(
        system_prompt=("Tu es un agent de fond lancé par l'assistant Jarvis sur le PC Windows de l'utilisateur. "
                       "Mène la mission jusqu'au bout sans poser de questions : fais des choix raisonnables et "
                       "note-les. Travaille dans le dossier courant sauf indication contraire. Termine par un "
                       "compte rendu en français de quelques phrases : ce qui est fait, où sont les fichiers, "
                       "ce qui reste à faire."),
        model=cfg.get("modele", "sonnet"),
        tools=allowed, allowed_tools=allowed,
        permission_mode="acceptEdits",
        setting_sources=[],
        cwd=str(m["dossier"]),
        max_turns=int(cfg.get("etapes_max", 80)),
    )
    result = ""
    async for msg in sdk.query(prompt=m["description"], options=options):
        if isinstance(msg, sdk.AssistantMessage):
            for b in msg.content:
                if isinstance(b, sdk.ToolUseBlock):
                    m["etapes"] += 1
                    m["derniere"] = b.name
                elif isinstance(b, sdk.TextBlock) and b.text.strip():
                    result = b.text
        elif isinstance(msg, sdk.ResultMessage):
            if msg.is_error:
                raise RuntimeError(f"{msg.subtype} {msg.result or ''}")
            result = msg.result or result
    m["resultat"] = result


def _thread(m: dict) -> None:
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):      # forfait, jamais l'API
        os.environ.pop(var, None)
    loop = asyncio.new_event_loop()
    m["loop"] = loop
    try:
        m["task"] = loop.create_task(_run(m))
        loop.run_until_complete(m["task"])
        m["etat"] = "terminée"
    except asyncio.CancelledError:
        m["etat"] = "arrêtée"
    except Exception as e:
        m["etat"] = "échouée"
        m["resultat"] = f"{type(e).__name__}: {e}"
    finally:
        loop.close()
    m["fin"] = time.time()
    try:
        (m["dossier"] / "compte_rendu.md").write_text(
            f"# Mission {m['nom']} ({m['etat']})\n\n## Demande\n{m['description']}\n\n## Compte rendu\n{m['resultat']}\n",
            encoding="utf-8")
    except OSError:
        pass
    if m["etat"] != "arrêtée":
        runtime.notify(f"Mission {m['nom']} {m['etat']}. {m['resultat'][:400]}")


@tool("lancer_mission",
      "Confie un travail LONG à un agent de fond (plusieurs minutes : recherche approfondie avec rapport, "
      "écriture d'un programme ou d'un site, tri ou analyse d'un gros dossier, rédaction d'un document). "
      "L'agent travaille seul dans son dossier pendant que tu restes disponible ; l'utilisateur est prévenu "
      "à la fin. Rédige une description complète et autonome : objectif, contraintes, livrable attendu.",
      {"nom": {"type": "string", "description": "Nom court sans espaces (ex. 'comparatif_pc')."},
       "description": {"type": "string"},
       "dossier": {"type": "string", "description": "Dossier de travail (défaut : Documents/Jarvis/missions/<nom>)."}},
      ["nom", "description"],
      confirm=lambda a: f"Lancer la mission de fond « {a['nom']} » ? Elle consommera une partie du forfait.")
def lancer_mission(nom: str, description: str, dossier: str = "") -> str:
    nom = re.sub(r"[^\w-]", "_", nom.strip())[:40] or "mission"
    if nom in _missions and _missions[nom]["etat"] == "en cours":
        return f"ERREUR : une mission « {nom} » est déjà en cours."
    folder = resolve(dossier) if dossier else _workdir(nom)
    folder.mkdir(parents=True, exist_ok=True)
    m = {"nom": nom, "description": description, "dossier": folder, "etat": "en cours", "debut": time.time(),
         "etapes": 0, "derniere": "", "resultat": ""}
    _missions[nom] = m
    threading.Thread(target=_thread, args=(m,), daemon=True).start()
    return f"Mission « {nom} » lancée dans {folder}. Je préviendrai quand elle sera terminée."


@tool("missions", "État des missions de fond (en cours et terminées), ou arrêt d'une mission.",
      {"action": {"type": "string", "enum": ["etat", "arreter"]}, "nom": {"type": "string"}}, ["action"])
def missions(action: str, nom: str = "") -> str:
    if action == "arreter":
        m = _missions.get(nom)
        if not m or m["etat"] != "en cours":
            return f"ERREUR : aucune mission en cours nommée '{nom}'."
        m["loop"].call_soon_threadsafe(m["task"].cancel)
        return f"Arrêt de la mission « {nom} » demandé."
    if not _missions:
        return "Aucune mission lancée depuis le démarrage."
    lines = []
    for m in _missions.values():
        dur = int((m.get("fin") or time.time()) - m["debut"]) // 60
        line = f"- {m['nom']} : {m['etat']} ({dur} min, {m['etapes']} actions) — dossier {m['dossier']}"
        if m["etat"] == "en cours" and m["derniere"]:
            line += f" — en train d'utiliser {m['derniere']}"
        if m["resultat"]:
            line += f"\n  {m['resultat'][:600]}"
        lines.append(line)
    return "\n".join(lines)
