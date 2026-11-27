"""Point d'entrée : python -m jarvis [--texte] [--diagnostic] [--autotest] [--connexion] [--micros]"""
from __future__ import annotations

import argparse
import importlib
import os
import re
import sys
import threading

from . import __version__, tools
from .config import load_config
from .core import NON, OUI, QUIT, Channel, Core, plain

SLEEP = re.compile(r"^(merci|c'est tout|ce sera tout|rien|non merci|stop|silence|tais-toi)( jarvis)?[ .!]*$")
NEW = re.compile(r"^(nouvelle conversation|oublie la conversation|on recommence)[ .!]*$")


# --------------------------------------------------------------------------- compte Claude
def _cli_env() -> dict:
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)      # sinon Claude Code facturerait l'API au lieu du forfait
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    return env


def logged_in() -> bool:
    import json
    import subprocess
    from .brain import claude_cli
    cli = claude_cli()
    if not cli:
        return False
    try:
        out = subprocess.run([cli, "auth", "status"], capture_output=True, text=True, timeout=30, env=_cli_env()).stdout
        return bool(json.loads(out).get("loggedIn"))
    except Exception:
        return False


def login() -> None:
    import subprocess
    from .brain import claude_cli
    cli = claude_cli()
    if not cli:
        sys.exit("[!] Claude Code introuvable : relancez install.bat.")
    if logged_in():
        return print("Déjà connecté à votre compte Claude. Rien à faire.")
    print("Une page de connexion va s'ouvrir dans le navigateur : connectez-vous avec votre compte Claude.")
    subprocess.run([cli, "auth", "login", "--claudeai"], env=_cli_env())
    print("Connexion réussie." if logged_in() else "[!] Connexion non aboutie, relancez Connexion-Claude.bat.")


def diagnostic(config: dict) -> None:
    print(f"\nJarvis {__version__} — diagnostic")
    print(f"  Python {sys.version.split()[0]} sur {sys.platform}")
    kind = config["cerveau"]["fournisseur"]
    print(f"  Cerveau : {kind}")
    if kind == "forfait":
        print(f"  Compte Claude : {'connecté' if logged_in() else 'NON CONNECTÉ -> lancez Connexion-Claude.bat'}")
    elif kind == "api":
        print(f"  Clé API : {'présente' if os.environ.get('ANTHROPIC_API_KEY') else 'ABSENTE (remplir .env)'}")
    print(f"  E-mail : {'configuré' if os.environ.get('JARVIS_EMAIL_PASSWORD') else 'non configuré (brouillons mailto)'}")
    print(f"  Telegram : {'configuré' if os.environ.get('JARVIS_TELEGRAM_TOKEN') else 'non configuré'}")
    print(f"  Outils disponibles : {len(tools.TOOLS)}")
    mods = ["claude_agent_sdk", "psutil", "pyautogui", "pyperclip", "PIL", "pygetwindow", "send2trash", "pypdf",
            "playwright", "sounddevice", "faster_whisper", "openwakeword", "edge_tts", "pygame", "pyttsx3", "keyboard"]
    for m in mods:
        try:
            importlib.import_module(m)
            print(f"  [ok] {m}")
        except Exception as e:
            print(f"  [--] {m} : {type(e).__name__}: {str(e)[:80]}")


# --------------------------------------------------------------------------- démarrage commun
def boot(config: dict) -> Core:
    """Crée le coeur et lance les services de fond (extensions, rappels, planning, Telegram)."""
    from . import telegram                  # avant le coeur : déclare l'outil envoyer_sur_telephone
    from .tools import extensions, planning
    for err in extensions.load_all():
        print(f"[!] {err}")
    core = Core(config)
    planning.start(core)
    if telegram.start(core):
        print("Telegram : bot actif, Jarvis est joignable depuis le téléphone.")
    return core


def announce_missed(core: Core) -> None:
    from .tools.extras import restore_reminders
    for item in restore_reminders():
        core.notify(f"Rappel manqué pendant que j'étais éteint : {item}")


# --------------------------------------------------------------------------- mode texte
def run_text(core: Core) -> None:
    name = core.config["assistant"]["nom"]
    print(f"{name} est prêt (mode texte). Tapez votre demande, ou 'quitter'.")
    announce_missed(core)
    while True:
        try:
            text = input("\nVous > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if QUIT.match(plain(text)):
            break
        if NEW.match(plain(text)):
            core.reset()
            print("Conversation réinitialisée.")
            continue
        print(f"{name} > {core.ask(text)}")


# --------------------------------------------------------------------------- mode voix
class VoiceChannel(Channel):
    name = "voix"

    def __init__(self, core: Core, speaker, listener, beep):
        self.core, self.speaker, self.listener, self.beep = core, speaker, listener, beep

    def say(self, text: str) -> None:
        print(f"{self.core.config['assistant']['nom']} > {text}")
        self.core.emit("etat", "parle")
        self.speaker.say(text)
        self.core.emit("etat", "repos")

    def confirm(self, question: str) -> bool:
        self.say(question)
        for _ in range(2):
            self.beep(660)
            self.core.emit("etat", "écoute")
            answer = plain(self.listener.listen(start_timeout=8))
            print(f"   (réponse : {answer or '...'})")
            if NON.search(answer):
                return False
            if OUI.search(answer):
                return True
            self.say("Oui ou non ?")
        return False

    def notify(self, message: str) -> None:
        self.say(message)


def run_voice(core: Core, stop: threading.Event, ui=None) -> None:
    core.emit("notification", "Je démarre… (au tout premier lancement, quelques minutes de téléchargement)")
    from .voice import Listener, Speaker, beep
    config = core.config
    name, vcfg = config["assistant"]["nom"], config["voix"]
    speaker = Speaker(vcfg)
    listener = Listener(vcfg, speaker)
    core.emit("notification", "Je suis prête. Dis « Hey Jarvis ».")
    voice = VoiceChannel(core, speaker, listener, beep)
    core.default = voice
    listener.on_level = lambda v: core.emit("niveau", f"{v:.2f}")       # anime l'interface
    if ui is not None:
        ui.on_mic = listener._on_hotkey
    from . import telegram
    if telegram._bot is not None:           # les messages vocaux Telegram passent par le même Whisper
        from .voice import decode_file
        telegram._bot.transcribe = lambda path: listener.transcribe(decode_file(path))
    follow = float(vcfg.get("suivi_secondes", 6))
    voice.say(f"{name} en ligne.")
    announce_missed(core)
    try:
        while not stop.is_set():
            listener.wait_for_wake()
            beep(880)
            timeout = 6.0
            while not stop.is_set():        # conversation : on enchaîne sans mot de réveil
                core.emit("etat", "écoute")
                text = listener.listen(start_timeout=timeout, strict=timeout != 6.0)
                core.emit("etat", "repos")
                if not text:
                    break
                print(f"\nVous > {text}")
                p = plain(text)
                if QUIT.match(p):
                    voice.say(f"Au revoir, {config['assistant']['appellation']}.")
                    stop.set()
                    return
                if SLEEP.match(p):
                    break
                if NEW.match(p):
                    core.reset()
                    voice.say("Très bien, on repart de zéro.")
                    continue
                voice.say(core.ask(text, channel=voice))
                if follow <= 0:
                    break
                timeout = follow
                beep(520, 60)
            beep(440, 80)
    except KeyboardInterrupt:
        pass
    finally:
        listener.close()


def run_app(core: Core) -> None:
    """Voix + interface. Si la voix est indisponible, l'interface reste utilisable au clavier."""
    stop = threading.Event()
    mode = core.config.get("interface", "discret")
    mode = {True: "forme", False: "aucune", None: "aucune", "discret": "forme"}.get(mode, mode)
    widget = hud = None
    try:
        if mode == "forme":
            from .widget import Widget
            widget = Widget(core, on_quit=stop.set)
        elif mode == "tableau":
            from .hud import Hud
            hud = Hud(core)
            hud.on_quit = lambda: os._exit(0)
            hud.open_window()
    except Exception as e:
        print(f"[interface] indisponible ({type(e).__name__}: {e}), Jarvis continue sans.")

    if widget is None:
        try:
            return run_voice(core, stop, hud)
        except ImportError as e:
            if hud is None:
                raise
            core.emit("notification", f"Voix indisponible ({e}). Écrivez vos demandes dans cette fenêtre.")
            return stop.wait()

    def voice_thread() -> None:                 # la fenêtre garde le fil principal, la voix tourne à côté
        try:
            run_voice(core, stop, widget)
        except ImportError as e:
            return core.emit("notification", f"Voix indisponible ({e}). Cliquez sur le point pour écrire.")
        except Exception as e:
            return core.emit("notification", f"La voix s'est arrêtée : {type(e).__name__}: {e}")
        widget.events.put(("fermer", ""))       # « au revoir Jarvis » ferme aussi l'interface

    threading.Thread(target=voice_thread, daemon=True).start()
    widget.run()
    stop.set()


def popup(text: str) -> None:
    """Message à l'écran quand il n'y a pas de terminal pour l'afficher."""
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, "Jarvis", 0x40)
    else:
        print(text)


def no_console() -> bool:
    """Lancé sans terminal (pythonw) : tout ce qui s'écrirait à l'écran part dans data/console.log."""
    if sys.stdout is not None and sys.stderr is not None:
        return False
    from .config import DATA
    DATA.mkdir(exist_ok=True)
    log = open(DATA / "console.log", "a", encoding="utf-8", buffering=1, errors="replace")
    sys.stdout = sys.stderr = log
    import time
    print(f"\n===== Jarvis {__version__} lancé le {time.strftime('%d/%m/%Y %H:%M:%S')} =====")
    return True


_instance = None


def already_running() -> bool:
    """Une seule instance à la fois : deux Jarvis s'écouteraient et se répondraient en double."""
    global _instance
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 47653))
        s.listen(1)
    except OSError:
        return True
    _instance = s
    return False


def main() -> None:
    silent = no_console()
    try:
        _main(silent)
    except SystemExit:
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        if silent:
            popup("Jarvis s'est arrêté sur une erreur.\nLe détail est dans le fichier data\\console.log.")
        raise


def _main(silent: bool) -> None:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    ap = argparse.ArgumentParser(prog="jarvis", description="Jarvis, assistant vocal pour Windows")
    ap.add_argument("--texte", action="store_true", help="mode clavier dans la console (sans micro)")
    ap.add_argument("--diagnostic", action="store_true", help="vérifie l'installation")
    ap.add_argument("--connexion", action="store_true", help="connecte Jarvis à votre compte Claude (forfait)")
    ap.add_argument("--autotest", action="store_true", help="teste chaque fonction sur ce PC et écrit un rapport")
    ap.add_argument("--sans-audio", action="store_true", help="autotest sans micro ni haut-parleur")
    ap.add_argument("--micros", action="store_true", help="liste les microphones")
    ap.add_argument("-c", "--commande", help="exécute une seule demande puis quitte")
    args = ap.parse_args()

    config = load_config()
    tools.runtime.config = config
    if args.connexion:
        return login()
    if args.diagnostic:
        return diagnostic(config)
    if args.autotest:
        from .autotest import run
        sys.exit(run(config, audio=not args.sans_audio))
    if args.micros:
        from .voice import list_microphones
        return print(list_microphones())
    interactive = not (args.commande or args.texte)
    if interactive and already_running():
        return popup("Jarvis est déjà lancé (la forme est dans le coin de l'écran).")
    if config["cerveau"]["fournisseur"] == "forfait" and not logged_in():
        if silent:
            return popup("Jarvis n'est pas encore connecté à ton compte Claude.\n"
                         "Double-clique sur Connexion-Claude.bat, puis relance Jarvis.")
        login()
    try:
        core = boot(config)
    except RuntimeError as e:
        sys.exit(f"\n[!] {e}")
    if args.commande:
        return print(core.ask(args.commande))
    if args.texte:
        return run_text(core)
    try:
        run_app(core)
    except ImportError as e:
        print(f"\n[!] Module voix manquant ({e}). Relancez install.bat, ou utilisez Jarvis-texte.bat.")
        print("    Passage en mode texte.\n")
        run_text(core)


if __name__ == "__main__":
    main()
