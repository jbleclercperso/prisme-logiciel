"""Garde-fous du nettoyage : en quelques secondes, rien n'a disparu.

    python tests/guard.py            vérifier
    python tests/guard.py --accept   prendre l'état actuel comme référence
                                     (après un retrait voulu, et dit)

1. Chaque module de Prisme s'importe — y compris ceux qu'on n'ouvre qu'au
   clic (Telegram, sondages, réparation…), où une faute ne se verrait
   qu'en ouvrant la fenêtre.
2. Aucun `self.x` lu dans une classe sans être défini nulle part (la faute
   typique d'une méthode déplacée à moitié).
3. Les noms des grandes classes et des modules : rien de retiré sans le
   vouloir.
4. Les menus : mêmes libellés, mêmes cases — plusieurs fonctions retrouvent
   une action par son texte exact.
5. Les fenêtres secondaires se construisent.

Bac à sable obligatoire : l'index réel a déjà été effacé une fois.
"""
import ast
import json
import os
import shutil
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
BASELINE = HERE / "guard_baseline.json"
ACCEPT = "--accept" in sys.argv

base = Path(os.environ["TEMP"]) / "vs-guard"
shutil.rmtree(base, ignore_errors=True)
sandbox = base / "_appdata"
sandbox.mkdir(parents=True)
(base / "root" / "Un dossier").mkdir(parents=True)
(base / "root" / "Un dossier" / "a.mp4").write_bytes(b"\0" * 2048)
os.environ["PRISME_SANDBOX"] = str(sandbox)      # avant tout import de Prisme

import importlib                                     # noqa: E402

FAILS: list[str] = []


def check(cond, label):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        FAILS.append(label)


# -- 1. tout s'importe ----------------------------------------------------------
print("[1] Import de chaque module")
modules = sorted(p.stem for p in (ROOT / "videosorter").glob("*.py") if p.stem != "__init__")
loaded = {}
for name in modules:
    try:
        loaded[name] = importlib.import_module(f"videosorter.{name}")
    except Exception as exc:  # noqa: BLE001
        check(False, f"videosorter.{name} : {type(exc).__name__} : {exc}")
check(len(loaded) == len(modules), f"{len(loaded)} / {len(modules)} modules importés")


# -- 2. self.x jamais défini ----------------------------------------------------
def undefined_attrs() -> dict[str, list[str]]:
    """Pour chaque classe : les `self.x` lus mais jamais écrits ni définis
    (ni dans la classe, ni dans ses bases Qt, ni par `self.x = …` dans le
    module)."""
    out = {}
    window_assigned = {sub.attr for node in ast.walk(ast.parse(
        (ROOT / "videosorter" / "window.py").read_text(encoding="utf-8")))
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        for sub in ast.walk(target) if isinstance(sub, ast.Attribute)}
    for name, module in loaded.items():
        source = (ROOT / "videosorter" / f"{name}.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        assigned = set()
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for sub in ast.walk(target):
                    if isinstance(sub, ast.Attribute):
                        assigned.add(sub.attr)
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "setattr" \
                    and len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                assigned.add(str(node.args[1].value))
        if name.startswith("win_"):
            # Les morceaux de la fenetre principale : leurs `self.x` sont ceux
            # de MainWindow (et de ce qu'elle ecrit dans window.py).
            assigned |= window_assigned
        for cls_node in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
            cls = getattr(module, cls_node.name, None)
            if not isinstance(cls, type):
                continue
            known = set(dir(cls)) | assigned
            if name.startswith("win_"):
                known |= set(dir(loaded["window"].MainWindow))
            missing = set()
            for sub in ast.walk(cls_node):
                if (isinstance(sub, ast.Attribute) and isinstance(sub.ctx, ast.Load)
                        and isinstance(sub.value, ast.Name) and sub.value.id == "self"
                        and sub.attr not in known):
                    missing.add(sub.attr)
            if missing:
                out[f"{name}.{cls_node.name}"] = sorted(missing)
    return out


def undefined_globals() -> dict[str, list[str]]:
    """Les noms lus dans une fonction sans etre definis nulle part (ni dans
    la fonction, ni dans le module, ni parmi ceux de Python) : un import
    oublie en deplacant du code."""
    import builtins
    import symtable
    out = {}
    for name in loaded:
        source = (ROOT / "videosorter" / f"{name}.py").read_text(encoding="utf-8")
        table = symtable.symtable(source, f"{name}.py", "exec")
        module_names = {s.get_name() for s in table.get_symbols()
                        if s.is_assigned() or s.is_imported() or s.is_namespace()}
        known = module_names | set(dir(builtins)) | {"__file__", "__name__", "__doc__"}
        missing = set()

        def walk(scope):
            for sym in scope.get_symbols():
                if (sym.is_global() and sym.is_referenced()
                        and sym.get_name() not in known):
                    missing.add(sym.get_name())
            for child in scope.get_children():
                walk(child)
        walk(table)
        if missing:
            out[name] = sorted(missing)
    return out


# -- 3. noms des classes et des modules ----------------------------------------
def api_surface() -> dict[str, list[str]]:
    surface = {}
    for name, module in loaded.items():
        surface[f"module {name}"] = sorted(n for n in vars(module) if not n.startswith("__"))
    from videosorter.board import BoardView
    from videosorter.index import Index
    from videosorter.widgets import SinglePlayer
    from videosorter.window import MainWindow
    from PySide6.QtWidgets import QMainWindow
    for cls in (BoardView, SinglePlayer, Index):
        surface[f"class {cls.__name__}"] = sorted(n for n in vars(cls) if not n.startswith("__"))
    # La fenetre principale : ce qu'elle porte, heritage des morceaux compris
    # (pas ce que Qt lui donne).
    qt = set(dir(QMainWindow))
    surface["class MainWindow"] = sorted(n for n in dir(MainWindow)
                                         if not n.startswith("__") and n not in qt)
    return surface


# -- 4. les menus ---------------------------------------------------------------
def build_window():
    from PySide6.QtWidgets import QApplication
    from videosorter import config as vs_config
    from videosorter import media as vs_media
    from videosorter.config import Config
    from videosorter.index import INDEX
    import videosorter.ratings as vs_ratings
    vs_media.THUMB_DIR = sandbox / "thumbs"
    vs_config.LOCAL_TRASH = sandbox / "_TRASH"
    vs_ratings.RATINGS_PATH = sandbox / "ratings.json"
    INDEX.reopen(sandbox / "index.db")
    app = QApplication.instance() or QApplication(sys.argv)
    from videosorter.window import MainWindow
    window = MainWindow(Config(path=sandbox / "config.json"))
    window.show()
    return app, window


def menu_dump(window) -> list[str]:
    from PySide6.QtWidgets import QMenu
    lines = []

    def walk(menu, depth):
        for action in menu.actions():
            if action.isSeparator():
                continue
            text = action.text().replace("&", "")
            flag = " [case]" if action.isCheckable() else ""
            lines.append("  " * depth + text + flag)
            if action.menu() is not None:
                walk(action.menu(), depth + 1)
    overflow = getattr(window, "overflow", None)
    if isinstance(overflow, QMenu):
        walk(overflow, 0)
    return lines


def pump(app, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


print("[2] Attributs lus jamais définis")
undefined = undefined_attrs()
print("[2b] Noms lus jamais définis (imports oubliés)")
for module_name, names in undefined_globals().items():
    check(False, f"{module_name} : {names}")
print("[3] Noms des modules et des grandes classes")
surface = api_surface()
print("[4] Menus")
app, window = build_window()
pump(app, 0.3)
menus = menu_dump(window)
check(len(menus) > 20, f"menu ⋯ : {len(menus)} entrées")

print("[5] Fenêtres secondaires")
for label, make in (
    ("Telegram", lambda: importlib.import_module("videosorter.telegram_hub").TelegramDialog(window)),
    ("Aide", lambda: importlib.import_module("videosorter.help").HelpDialog(window)),
):
    try:
        dialog = make()
        dialog.show()
        pump(app, 0.1)
        dialog.close()
        check(True, f"{label} se construit")
    except Exception as exc:  # noqa: BLE001
        check(False, f"{label} : {type(exc).__name__} : {exc}")

now = {"undefined": undefined, "surface": surface, "menus": menus}
if ACCEPT or not BASELINE.exists():
    BASELINE.write_text(json.dumps(now, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nRéférence enregistrée : {BASELINE.name}")
else:
    before = json.loads(BASELINE.read_text(encoding="utf-8"))
    for key, names in undefined.items():
        new = sorted(set(names) - set(before["undefined"].get(key, [])))
        check(not new, f"{key} : rien de nouveau lu sans être défini" + (f" — {new}" if new else ""))
    gone = {k: sorted(set(v) - set(surface.get(k, []))) for k, v in before["surface"].items()}
    gone = {k: v for k, v in gone.items() if v}
    check(not gone, "aucun nom retiré" + (f" — {gone}" if gone else ""))
    lost = [m for m in before["menus"] if m not in menus]
    check(not lost, "aucune entrée de menu perdue ni renommée" + (f" — {lost}" if lost else ""))

window.close()
pump(app, 0.2)
print("\ntout est vert" if not FAILS else f"\n{len(FAILS)} échec(s)")
sys.exit(1 if FAILS else 0)
