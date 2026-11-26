"""Le cerveau de Jarvis : boucle d'agent avec outils, sur Claude (API) ou sur une IA locale (Ollama)."""
from __future__ import annotations

import datetime as dt
import os

from . import tools
from .tools.extras import load_memory

MAX_STEPS = 25          # appels d'outils enchaînés au maximum pour une demande
MAX_MESSAGES = 80       # au-delà, la conversation repart d'un résumé
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def system_prompt(config: dict) -> str:
    a = config["assistant"]
    memory = load_memory()
    mem = "\n".join(f"- {m}" for m in memory) if memory else "(rien pour l'instant)"
    return f"""Tu es {a['nom']}, l'assistant personnel de l'utilisateur, installé sur son PC Windows. \
Tu t'adresses à lui en l'appelant « {a['appellation']} », en français, avec le ton d'un majordome \
compétent : courtois, direct, une pointe d'humour sec à l'occasion.

Tes réponses sont lues à voix haute par une synthèse vocale. Réponds donc en phrases parlées, courtes : \
une ou deux phrases suffisent pour confirmer une action. Pas de listes à puces, de markdown, d'emojis ni \
d'URL lues en entier. Si l'utilisateur demande un contenu long (un texte, un résumé détaillé), propose de \
l'écrire dans un fichier ou de le taper à l'écran plutôt que de tout réciter.

Tu agis réellement sur l'ordinateur grâce à tes outils : agis d'abord, puis rends compte de ce qui a été \
fait. N'affirme jamais qu'une action est faite sans avoir reçu le résultat de l'outil ; si un outil échoue, \
dis-le simplement et propose une alternative. Quand une demande nécessite plusieurs étapes, enchaîne-les \
sans demander la permission à chaque fois. Les actions sensibles (suppression, e-mail, extinction, \
PowerShell) déclenchent déjà une confirmation automatique auprès de l'utilisateur : inutile de la \
redemander toi-même. Ne pose une question que si la demande est réellement ambiguë.

La demande vient d'une reconnaissance vocale : elle peut contenir des mots mal transcrits. Interprète \
l'intention la plus probable (noms de logiciels, de fichiers ou de personnes écorchés).

Pour agir dans un logiciel sans outil dédié, pilote-le comme un humain : capture_ecran pour voir, puis \
souris, raccourci_clavier et taper_texte, et une nouvelle capture pour vérifier le résultat. Préfère toujours \
un outil direct ou un raccourci clavier quand il existe : c'est plus rapide et plus fiable que la souris.

Si l'utilisateur définit une routine (« quand je dis mode travail, ouvre ceci et cela »), enregistre-la avec \
memoriser, puis exécute-la quand il la demande.

Quand l'utilisateur te donne une information durable sur lui (préférence, adresse e-mail d'un contact, \
dossier de travail), enregistre-la avec l'outil memoriser.

Ce que tu sais déjà sur l'utilisateur :
{mem}"""


def stamp(text: str) -> str:
    now = dt.datetime.now()
    return f"[{JOURS[now.weekday()]} {now:%d/%m/%Y %H:%M}] {text}"


class Brain:
    def __init__(self, config: dict, on_tool=None):
        self.config = config
        self.on_tool = on_tool or (lambda name, args: None)
        self.system = system_prompt(config)

    def ask(self, text: str) -> str:
        raise NotImplementedError

    def reset(self) -> None:
        raise NotImplementedError


# --------------------------------------------------------------------------- Claude
FALLBACK_MODELS = ("claude-opus-5", "claude-sonnet-5-5", "claude-fable-5", "claude-mythos-5")


class ClaudeBrain(Brain):
    def __init__(self, config: dict, on_tool=None, client=None):
        super().__init__(config, on_tool)
        import anthropic
        self.anthropic = anthropic
        self.client = client or anthropic.Anthropic()
        c = config["cerveau"]
        self.model = c["modele"]
        self.effort = c.get("effort", "low")
        self.tools = tools.anthropic_tools()
        if c.get("recherche_web", True):
            version = "web_search_20250305" if "haiku" in self.model else "web_search_20260209"
            self.tools.append({"type": version, "name": "web_search", "max_uses": 5})
        self.messages: list = []

    def reset(self) -> None:
        self.messages = []

    def _create(self):
        kwargs = dict(
            model=self.model,
            max_tokens=16000,
            system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
            tools=self.tools,
            messages=self.messages,
            output_config={"effort": self.effort},
        )
        # En cas de refus du modèle principal, l'API relance elle-même sur un modèle de secours.
        if self.model.startswith(FALLBACK_MODELS):
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
        return self.client.beta.messages.create(**kwargs)

    def _compact(self) -> None:
        """Repart d'une conversation neuve contenant un résumé texte des derniers échanges."""
        lines = []
        for m in self.messages[-30:]:
            content = m["content"]
            if isinstance(content, str):
                lines.append(f"Utilisateur : {content}")
                continue
            for b in content:
                btype = b.get("type") if isinstance(b, dict) else getattr(b, "type", None)
                if btype == "text" and m["role"] == "assistant":
                    lines.append(f"Jarvis : {b.text if not isinstance(b, dict) else b['text']}")
        recap = "\n".join(lines)[-6000:]
        self.messages = [
            {"role": "user", "content": "Résumé de nos échanges précédents, pour contexte :\n" + recap},
            {"role": "assistant", "content": "Bien noté."},
        ]

    @staticmethod
    def _result_block(use_id: str, result) -> dict:
        if isinstance(result, dict):
            content = [{"type": "text", "text": result.get("text", "")}]
            if result.get("image_b64"):
                content.append({"type": "image", "source": {
                    "type": "base64", "media_type": result.get("media_type", "image/jpeg"),
                    "data": result["image_b64"]}})
        else:
            content = str(result)
        block = {"type": "tool_result", "tool_use_id": use_id, "content": content}
        if isinstance(result, str) and result.startswith("ERREUR"):
            block["is_error"] = True
        return block

    def ask(self, text: str) -> str:
        if len(self.messages) > MAX_MESSAGES:
            self._compact()
        checkpoint = len(self.messages)
        self.messages.append({"role": "user", "content": stamp(text)})
        a = self.anthropic
        try:
            return self._loop()
        except a.AuthenticationError:
            msg = "Ma clé d'API Claude est absente ou invalide. Vérifiez le fichier point env."
        except a.PermissionDeniedError:
            msg = "Ma clé d'API n'a pas accès à ce modèle."
        except a.NotFoundError:
            msg = f"Le modèle {self.model} est introuvable. Vérifiez le fichier de configuration."
        except a.RateLimitError:
            msg = "L'API Claude me demande de ralentir. Réessayez dans un instant."
        except a.APIStatusError as e:
            if e.status_code >= 500:
                msg = "Les serveurs de Claude sont momentanément indisponibles."
            else:
                msg = f"L'API a refusé la requête : {e.message}"
        except a.APIConnectionError:
            msg = "Je n'arrive pas à joindre Claude. Vérifiez la connexion Internet."
        del self.messages[checkpoint:]      # l'historique reste valide après une erreur
        return msg

    def _loop(self) -> str:
        for _ in range(MAX_STEPS):
            resp = self._create()
            if resp.stop_reason == "refusal":
                while self.messages and not (self.messages[-1]["role"] == "user"
                                             and isinstance(self.messages[-1]["content"], str)):
                    self.messages.pop()
                if self.messages:
                    self.messages.pop()
                return "Désolé, je ne peux pas vous aider pour cette demande."
            self.messages.append({"role": "assistant", "content": resp.content})
            if resp.stop_reason == "pause_turn":      # recherche web en cours : on relance
                continue
            uses = [b for b in resp.content if b.type == "tool_use"]
            if not uses:
                return " ".join(b.text for b in resp.content if b.type == "text").strip() or "C'est fait."
            results = []
            for use in uses:
                if resp.stop_reason == "max_tokens":
                    results.append({"type": "tool_result", "tool_use_id": use.id, "is_error": True,
                                    "content": "ERREUR : appel tronqué, recommence avec une entrée plus courte."})
                    continue
                self.on_tool(use.name, use.input)
                results.append(self._result_block(use.id, tools.execute(use.name, use.input)))
            self.messages.append({"role": "user", "content": results})
        return "J'ai dû m'arrêter : la tâche demandait trop d'étapes."


# --------------------------------------------------------------------------- IA locale
class OllamaBrain(Brain):
    """IA locale via Ollama (http://localhost:11434). Nécessite un modèle sachant appeler des outils."""

    def __init__(self, config: dict, on_tool=None):
        super().__init__(config, on_tool)
        c = config["cerveau"]
        self.url = c["ollama_url"].rstrip("/") + "/api/chat"
        self.model = c["ollama_modele"]
        self.tools = tools.ollama_tools()
        self.reset()

    def reset(self) -> None:
        self.messages = [{"role": "system", "content": self.system}]

    def ask(self, text: str) -> str:
        import requests
        if len(self.messages) > MAX_MESSAGES:
            self.messages = self.messages[:1] + self.messages[-20:]
            while len(self.messages) > 1 and self.messages[1]["role"] != "user":
                del self.messages[1]
        checkpoint = len(self.messages)
        self.messages.append({"role": "user", "content": stamp(text)})
        try:
            for _ in range(MAX_STEPS):
                r = requests.post(self.url, timeout=300, json={
                    "model": self.model, "messages": self.messages, "tools": self.tools, "stream": False})
                r.raise_for_status()
                msg = r.json()["message"]
                self.messages.append(msg)
                calls = msg.get("tool_calls") or []
                if not calls:
                    return (msg.get("content") or "").strip() or "C'est fait."
                for call in calls:
                    fn = call["function"]
                    args = fn.get("arguments") or {}
                    self.on_tool(fn["name"], args)
                    result = tools.execute(fn["name"], args)
                    if isinstance(result, dict):     # les images ne sont pas transmises au modèle local
                        result = result.get("text", "")
                    self.messages.append({"role": "tool", "tool_name": fn["name"], "content": str(result)})
            return "J'ai dû m'arrêter : la tâche demandait trop d'étapes."
        except requests.RequestException as e:
            del self.messages[checkpoint:]
            return f"Je n'arrive pas à joindre l'IA locale Ollama : {e}"

# --------------------------------------------------------------------------- Forfait Claude
def claude_cli() -> str | None:
    """Chemin du programme Claude Code : celui livré avec le SDK, sinon celui installé sur le PC."""
    import shutil
    import sys
    from pathlib import Path
    try:
        import claude_agent_sdk
        exe = Path(claude_agent_sdk.__file__).parent / "_bundled" / ("claude.exe" if sys.platform == "win32" else "claude")
        if exe.exists():
            return str(exe)
    except ImportError:
        pass
    return shutil.which("claude")


class ForfaitBrain(Brain):
    """Claude via l'abonnement (Pro / Max) : passe par Claude Code et sa connexion, sans clé API."""

    BUILTINS = ["WebSearch", "WebFetch"]

    def __init__(self, config: dict, on_tool=None):
        super().__init__(config, on_tool)
        import asyncio
        import threading
        import claude_agent_sdk as sdk
        self.sdk, self.asyncio = sdk, asyncio
        # Une clé API présente dans l'environnement ferait facturer l'API au lieu du forfait.
        for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            os.environ.pop(var, None)
        c = config["cerveau"]
        web = self.BUILTINS if c.get("recherche_web", True) else []
        self.options = sdk.ClaudeAgentOptions(
            system_prompt=self.system,
            model=c.get("modele_forfait", "sonnet"),
            effort=c.get("effort", "low"),
            mcp_servers={"jarvis": sdk.create_sdk_mcp_server("jarvis", tools=self._sdk_tools())},
            tools=web,                                  # aucun autre outil intégré de Claude Code
            allowed_tools=[f"mcp__jarvis__{n}" for n in tools.TOOLS] + web,
            setting_sources=[],                         # ignore les réglages Claude Code du PC
            max_turns=MAX_STEPS,
            cwd=os.path.expanduser("~"),
        )
        self.client = None
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.asyncio.run_coroutine_threadsafe(self._connect(), self.loop)   # démarre Claude en avance

    async def _connect(self) -> None:
        if self.client is None:
            client = self.sdk.ClaudeSDKClient(self.options)
            try:
                await client.connect()
                self.client = self.client or client
            except Exception:
                pass                                    # l'erreur sera signalée à la première demande

    def _sdk_tools(self) -> list:
        sdk, asyncio = self.sdk, self.asyncio

        def wrap(t):
            async def handler(args):
                self.on_tool(t.name, args)
                result = await asyncio.to_thread(tools.execute, t.name, args)
                if isinstance(result, dict):
                    content = [{"type": "text", "text": result.get("text", "")}]
                    if result.get("image_b64"):
                        content.append({"type": "image", "data": result["image_b64"],
                                        "mimeType": result.get("media_type", "image/jpeg")})
                    return {"content": content}
                text = str(result)
                return {"content": [{"type": "text", "text": text}], "is_error": text.startswith("ERREUR")}
            return sdk.tool(t.name, t.description, tools.registry.schema(t))(handler)

        return [wrap(t) for t in sorted(tools.TOOLS.values(), key=lambda t: t.name)]

    def _run(self, coro, timeout=None):
        return self.asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    async def _ask(self, text: str) -> str:
        sdk = self.sdk
        if self.client is None:
            self.client = sdk.ClaudeSDKClient(self.options)
            await self.client.connect()
        await self.client.query(stamp(text))
        parts, final = [], None
        async for msg in self.client.receive_response():
            if isinstance(msg, sdk.AssistantMessage):
                if msg.error:
                    final = self._error(str(msg.error))
                texts = [b.text for b in msg.content if isinstance(b, sdk.TextBlock)]
                if texts:
                    parts = texts               # on ne garde que le dernier message parlé
            elif isinstance(msg, sdk.ResultMessage):
                if msg.is_error:
                    final = final or self._error(f"{msg.subtype} {msg.result or ''} {msg.errors or ''}")
                elif msg.result:
                    final = final or msg.result
        return (final or " ".join(parts)).strip() or "C'est fait."

    @staticmethod
    def _error(detail: str) -> str:
        low = detail.lower()
        if any(k in low for k in ("authentication", "login", "unauthorized", "401", "oauth", "api key")):
            return "Je ne suis pas connecté à votre compte Claude. Lancez Connexion-Claude point bat, puis relancez-moi."
        if any(k in low for k in ("rate_limit", "rate limit", "usage limit", "429", "billing")):
            return "La limite d'utilisation de votre forfait Claude est atteinte pour le moment."
        if "max_turns" in low:
            return "J'ai dû m'arrêter : la tâche demandait trop d'étapes."
        return f"Claude a renvoyé une erreur : {detail[:200]}"

    def ask(self, text: str) -> str:
        try:
            return self._run(self._ask(text), timeout=600)
        except self.sdk.CLINotFoundError:
            return "Le programme Claude Code est introuvable. Relancez l'installation."
        except Exception as e:
            self._drop()
            return self._error(f"{type(e).__name__}: {e}")

    def _drop(self) -> None:
        client, self.client = self.client, None
        if client is not None:
            try:
                self._run(client.disconnect(), timeout=15)
            except Exception:
                pass

    def reset(self) -> None:
        self._drop()


def make_brain(config: dict, on_tool=None) -> Brain:
    kind = config["cerveau"]["fournisseur"]
    if kind == "ollama":
        return OllamaBrain(config, on_tool)
    if kind == "api":
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            raise RuntimeError("Clé API manquante : renseignez ANTHROPIC_API_KEY dans .env, "
                               "ou remettez 'fournisseur: forfait' dans config.yaml.")
        return ClaudeBrain(config, on_tool)
    try:
        return ForfaitBrain(config, on_tool)
    except ImportError:
        raise RuntimeError("Module claude-agent-sdk manquant : relancez install.bat.")
