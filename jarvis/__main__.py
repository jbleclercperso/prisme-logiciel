"""Point d'entrée : python -m jarvis [--texte] [--diagnostic] [--micros]"""
from __future__ import annotations

import argparse
import importlib
import os
import re
import sys
import unicodedata

from . import __version__, tools
from .brain import make_brain
from .config import load_config

OUI = re.compile(r"\b(oui|ouais|yes|ok|okay|d'accord|vas-y|vas y|confirme|affirmatif|absolument|bien sur|fais-le|fais le)\b")
NON = re.compile(r"\b(non|no|annule|stop|surtout pas|negatif|laisse tomber)\b")
QUIT = re.compile(r"^(au revoir|arrete-toi|arrete toi|eteins-toi|eteins toi|quitte|quitter|exit|quit)( jarvis)?[ .!]*$")
SLEEP = re.compile(r"^(merci|c'est tout|ce sera tout|rien|non merci|stop|silence|tais-toi)( jarvis)?[ .!]*$")
NEW = re.compile(r"^(nouvelle conversation|oublie la conversation|on recommence)[ .!]*$")


def plain(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return s.lower().strip()


def journal(kind: str, text: str) -> None:
    """Trace des échanges dans data/journal.log (utile pour comprendre un raté après coup)."""
    import time
    from .config import DATA
    try:
        DATA.mkdir(exist_ok=True)
        with open(DATA / "journal.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{kind}] {text}\n")
    except OSError:
        pass


def show_tool(name: str, args: dict) -> None:
    short = ", ".join(f"{k}={str(v)[:60]!r}" for k, v in (args or {}).items())
    print(f"   ⚙ {name}({short})")
    journal("outil", f"{name}({short})")


def ask(brain, text: str) -> str:
    journal("vous", text)
    answer = brain.ask(text)
    journal("jarvis", answer)
    return answer


def announce_missed() -> None:
    from .tools.extras import restore_reminders
    for item in restore_reminders():
        tools.runtime.notify(f"Rappel manqué pendant que j'étais éteint : {item}")


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
    print(f"  Outils disponibles : {len(tools.TOOLS)}")
    mods = ["claude_agent_sdk", "psutil", "pyautogui", "pyperclip", "PIL", "pygetwindow", "send2trash", "pypdf",
            "sounddevice", "faster_whisper", "openwakeword", "edge_tts", "pygame", "pyttsx3", "keyboard"]
    for m in mods:
        try:
            importlib.import_module(m)
            print(f"  [ok] {m}")
        except Exception as e:
            print(f"  [--] {m} : {type(e).__name__}: {str(e)[:80]}")


def run_text(config: dict, brain) -> None:
    name = config["assistant"]["nom"]
    tools.runtime.confirm = lambda q: bool(OUI.search(plain(input(f"   ❓ {q} (oui/non) > "))))
    tools.runtime.notify = lambda msg: print(f"\n🔔 {name} : {msg}")
    print(f"{name} est prêt (mode texte). Tapez votre demande, ou 'quitter'.")
    announce_missed()
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
            brain.reset()
            print("Conversation réinitialisée.")
            continue
        print(f"{name} > {ask(brain, text)}")


def run_voice(config: dict, brain) -> None:
    from .voice import Listener, Speaker, beep
    name, vcfg = config["assistant"]["nom"], config["voix"]
    speaker = Speaker(vcfg)
    listener = Listener(vcfg, speaker)

    def say(text: str) -> None:
        print(f"{name} > {text}")
        speaker.say(text)

    def confirm(question: str) -> bool:
        say(question + " Oui ou non ?")
        for _ in range(2):
            beep(660)
            answer = plain(listener.listen(start_timeout=8))
            print(f"   (réponse : {answer or '...'})")
            if NON.search(answer):
                return False
            if OUI.search(answer):
                return True
            say("Je n'ai pas compris. Oui ou non ?")
        return False

    tools.runtime.confirm = confirm
    tools.runtime.notify = say
    follow = float(vcfg.get("suivi_secondes", 6))
    announce_missed()
    say(f"{name} en ligne. Dites « Hey Jarvis », ou appuyez sur {vcfg.get('raccourci', 'ctrl+alt+j')}.")
    try:
        while True:
            listener.wait_for_wake()
            beep(880)
            timeout = 6.0
            while True:                     # conversation : on enchaîne sans mot de réveil
                text = listener.listen(start_timeout=timeout)
                if not text:
                    break
                print(f"\nVous > {text}")
                p = plain(text)
                if QUIT.match(p):
                    say(f"Au revoir, {config['assistant']['appellation']}.")
                    return
                if SLEEP.match(p):
                    break
                if NEW.match(p):
                    brain.reset()
                    say("Très bien, on repart de zéro.")
                    continue
                say(ask(brain, text))
                if follow <= 0:
                    break
                timeout = follow
                beep(520, 60)
            beep(440, 80)
    except KeyboardInterrupt:
        pass
    finally:
        listener.close()


def main() -> None:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    ap = argparse.ArgumentParser(prog="jarvis", description="Jarvis, assistant vocal pour Windows")
    ap.add_argument("--texte", action="store_true", help="mode clavier (sans micro)")
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
    if config["cerveau"]["fournisseur"] == "forfait" and not logged_in():
        login()
    try:
        brain = make_brain(config, on_tool=show_tool)
    except RuntimeError as e:
        sys.exit(f"\n[!] {e}")
    if args.commande:
        tools.runtime.confirm = lambda q: bool(OUI.search(plain(input(f"   ❓ {q} (oui/non) > "))))
        return print(brain.ask(args.commande))
    if args.texte:
        return run_text(config, brain)
    try:
        run_voice(config, brain)
    except ImportError as e:
        print(f"\n[!] Module voix manquant ({e}). Relancez install.bat, ou utilisez Jarvis-texte.bat.")
        print("    Passage en mode texte.\n")
        run_text(config, brain)


if __name__ == "__main__":
    main()
