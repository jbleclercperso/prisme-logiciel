"""Fabrique Prisme.exe, à emporter sur un autre PC.

Un dossier plutôt qu'un fichier unique : un exécutable unique se décompresse
dans un dossier temporaire à **chaque** lancement — trois cents mégaoctets à
recopier avant de voir la fenêtre. Le dossier, lui, démarre tout de suite, et
se transporte aussi bien dans une archive.

    python construire.py              le programme seul
    python construire.py --complet    avec les vignettes deja fabriquees
    python construire.py --edition vendue
                                      la version vendue (edition.py) : sans
                                      les visiteurs, avec essai et licence,
                                      mises a jour par Internet

Le résultat est dans `dist/Prisme/`. Avec `--complet`, le cache de cette
machine y est copié dans `cache/` : le programme le trouve tout seul et
retrouve les vignettes sur n'importe quel ordinateur, sans rien refabriquer.

ffmpeg n'y est pas : les deux binaires pèsent plus de quatre cents mégaoctets
à eux seuls ; s'il manque, l'application propose de le télécharger
(ffmpeg_fetch).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "dist" / "Prisme"

# Deux programmes d'un seul code (videosorter/edition.py). La version perso
# se construit depuis ce dossier, telle quelle. La version vendue, depuis une
# copie ou EDITION dit « vendue » et ou les modules des visiteurs manquent :
# ils ne voyagent pas chez un client, pas meme en sources.
SOLD = "vendue"
STAGE = HERE / "build" / "sources-vendue"
# Ou la version vendue cherche ses mises a jour : les « releases » d'un depot
# GitHub public qui ne porte que les programmes publies, jamais le code.
# « latest/download » mene toujours aux fichiers de la derniere : version.json,
# puis l'archive qu'il nomme.
SOLD_REPO = "jbleclercperso/prisme-mises-a-jour"
SOLD_UPDATES = f"https://github.com/{SOLD_REPO}/releases/latest/download"
# Ou `--publier --edition vendue` prepare ce qu'il faudra envoyer.
SOLD_OUT = HERE / "dist" / "publication-vendue"


def stage(edition: str) -> Path:
    """Le dossier dont on construit : celui-ci (perso), ou la copie de la
    version vendue."""
    if edition != SOLD:
        return HERE
    sys.path.insert(0, str(HERE))
    from videosorter.edition import VISITORS_ONLY
    shutil.rmtree(STAGE, ignore_errors=True)
    (STAGE / "videosorter").mkdir(parents=True)
    for name in ("main.py", "prisme.ico"):
        shutil.copy2(HERE / name, STAGE / name)
    for path in (HERE / "videosorter").glob("*.py"):
        if path.stem not in VISITORS_ONLY:
            shutil.copy2(path, STAGE / "videosorter" / path.name)
    (STAGE / "nas").mkdir()
    shutil.copy2(HERE / "nas" / "serveur.py", STAGE / "nas" / "serveur.py")
    shutil.copytree(HERE / "moteur" / "reparateur", STAGE / "moteur" / "reparateur",
                    ignore=shutil.ignore_patterns("__pycache__"))
    place = STAGE / "videosorter" / "edition.py"
    text = place.read_text(encoding="utf-8")
    assert '\nEDITION = "perso"\n' in text, "edition.py : la ligne EDITION a change"
    place.write_text(text.replace('\nEDITION = "perso"\n', f'\nEDITION = "{SOLD}"\n'),
                     encoding="utf-8")
    print(f"Version vendue : {len(VISITORS_ONLY)} modules des visiteurs laissés de côté.")
    return STAGE

# Les modules Qt dont l'application ne se sert pas. Sans cette liste,
# PyInstaller emporte les outils de conception : des mégaoctets pour rien.
# Le moteur web, lui, reste : la recherche sur Internet s'en sert pour les
# sites qui ne cherchent qu'en JavaScript, et pour repérer la vidéo d'une
# page (webpage.py). Il tire avec lui QtQuick, QtQml et QtWebChannel.
EXCLUDES = [
    "PySide6.QtQuick3D", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtDesigner",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtSerialPort",
    "PySide6.QtTest", "PySide6.QtSql", "PySide6.QtHelp", "PySide6.QtPdf",
    "PySide6.QtWebSockets", "matplotlib", "scipy", "PIL", "tkinter",
    # numpy reste : le labo, les vidéos similaires, les doublons et la voix
    # s'en servent dans la fenêtre même (une vingtaine de Mo).
    # Le labo IA : ses bibliothèques vivent dans un Python à part, que le
    # programme se procure lui-même (iapython) ; s'il est installé sur la
    # machine qui construit, PyInstaller suivrait ses imports et emporterait
    # plusieurs gigaoctets de torch.
    "torch", "torchvision", "torchaudio", "transformers", "open_clip",
    # Le moteur de réparation PixMedic, pareil (moteur.py) : ses 150 Mo se
    # téléchargent au premier usage, jamais dans le programme.
    "reparateur", "cv2", "numba", "llvmlite", "av",
    "huggingface_hub", "safetensors", "tokenizers", "timm", "sympy",
    "faster_whisper", "ctranslate2", "onnxruntime", "sklearn", "pandas",
]


def gather_cache(into: Path) -> tuple:
    """Copie le cache de cette machine dans le paquet, pour qu'il voyage.

    Les vignettes sont le bien le plus cher : des heures de fabrication sur
    un partage reseau. Les emporter evite a l'autre ordinateur de tout
    refaire. L'index les accompagne — durees, resolutions, plans, empreintes.
    """
    from videosorter.config import APP_DIR

    source = APP_DIR
    if not (source / "thumbs").is_dir():
        older = source.parent / "VideoSorter"
        if (older / "thumbs").is_dir():
            source = older
    if not source.is_dir():
        return 0, 0

    into.mkdir(parents=True, exist_ok=True)
    count = weight = 0
    # Ni les journaux ni la liste de lecture : ils ne disent rien de la
    # collection et changent a chaque seance.
    # Ni les journaux, ni la liste de lecture, ni le registre des acces :
    # celui-ci ne regarde que la machine ou il a ete tenu.
    skip = {"gel.log", "preparation.log", "selection.m3u", "acces.db"}
    for entry in source.rglob("*"):
        if not entry.is_file() or entry.name in skip:
            continue
        target = into / entry.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(entry, target)
        count += 1
        weight += entry.stat().st_size
        if count % 5000 == 0:
            print(f"  {count} fichiers…", flush=True)
    return count, weight


# Le moteur web emporte avec lui ce dont Prisme n'a pas l'usage : ses fichiers
# de débogage, ses outils de développement, l'interface dans toutes les langues,
# et QtQuick avec ses thèmes et sa 3D (Prisme est en widgets). Retirés après
# coup : « Prisme-diagnostic.exe --verifier » fait tourner le moteur web, et dit
# si une pièce manquait.
PRUNE_GLOBS = (
    "resources/*.debug.*", "resources/qtwebengine_devtools_resources*",
    "qml",
    "Qt6Quick3D*", "Qt63D*", "Qt6Graphs*", "Qt6QuickControls2*", "Qt6QuickDialogs2*",
    "Qt6QuickTemplates2*", "Qt6QuickParticles*", "Qt6QuickShapes*", "Qt6QuickTimeline*",
    "Qt6QuickEffects*", "Qt6QuickLayouts*", "Qt6QuickVectorImage*", "Qt6WebEngineQuick*",
    "Qt6Charts*", "Qt6DataVisualization*", "Qt6Pdf*", "Qt6ShaderTools*",
)
KEEP_LANGUAGES = ("fr", "en")


def prune(qt: Path) -> int:
    """Retire de Qt ce que Prisme n'utilise pas ; rend les octets gagnés."""
    def weight(path: Path) -> int:
        if path.is_file():
            return path.stat().st_size
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())

    doomed = [path for pattern in PRUNE_GLOBS for path in qt.glob(pattern)]
    translations = qt / "translations"
    for path in translations.glob("*.qm"):
        # module_langue[_PAYS].qm (qt_fr, qtbase_pt_BR, qt_help_fr…)
        words = path.stem.split("_")
        language = words[-2] if len(words) > 2 and words[-1].isupper() else words[-1]
        if language not in KEEP_LANGUAGES:
            doomed.append(path)
    for path in (translations / "qtwebengine_locales").glob("*.pak"):
        if path.stem.split("-")[0] not in KEEP_LANGUAGES:
            doomed.append(path)
    saved = 0
    for path in doomed:
        if not path.exists():
            continue
        saved += weight(path)
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    return saved


def build(complete: bool = False, source: str = "", edition: str = "perso") -> int:
    for folder in (HERE / "build", HERE / "dist"):
        shutil.rmtree(folder, ignore_errors=True)
    src = stage(edition)

    spec = HERE / "Prisme.spec"
    spec.write_text(SPEC.format(
        here=str(src).replace("\\", "\\\\"),
        excludes=repr(EXCLUDES),
    ), encoding="utf-8")

    print("Construction…", flush=True)
    code = subprocess.call(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(spec)],
        cwd=str(HERE))
    if code != 0:
        return code
    saved = prune(OUT / "_internal" / "PySide6")
    print(f"Allégé de {saved / 2**20:.0f} Mo (Qt inutile).", flush=True)

    # Un exemple à côté du programme : il suffit de le renommer pour partager
    # les vignettes entre deux ordinateurs.
    (OUT / "prisme.cache.exemple").write_text(
        "X:\\_prisme\n", encoding="utf-8")
    (OUT / "LISEZ-MOI.txt").write_text(LISEZMOI, encoding="utf-8")
    # Ou ce programme cherchera ses mises a jour : sans rien regler, il sait.
    # La version vendue, sur Internet ; jamais sur le NAS de la maison.
    if edition == SOLD:
        source = SOLD_UPDATES
    if not source and PUBLICATION.is_file():
        lines = PUBLICATION.read_text(encoding="utf-8").strip().splitlines()
        source = ((lines[1].strip() if len(lines) > 1 else "")
                  or (lines[0].strip() if lines else ""))
    if source:
        (OUT / "mise-a-jour.txt").write_text(source + "\n", encoding="utf-8")
    if not sign(OUT):
        return 3

    cached = 0
    if complete:
        print("Copie du cache…", flush=True)
        cached, cache_weight = gather_cache(OUT / "cache")
        print(f"  {cached} fichier(s), {cache_weight / 1024 / 1024:.0f} Mo")

    name = "Prisme-complet.zip" if complete else "Prisme.zip"
    archive = HERE / "dist" / name
    print("Compression…", flush=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in OUT.rglob("*"):
            if not path.is_file():
                continue
            inside = Path("Prisme") / path.relative_to(OUT)
            # Une vignette est deja compressee : la recomprimer prendrait des
            # minutes pour gagner quelques pour cent.
            how = (zipfile.ZIP_STORED if path.suffix.lower() in (".jpg", ".jpeg")
                   else zipfile.ZIP_DEFLATED)
            zf.write(path, inside, compress_type=how)

    weight = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"\ndist/Prisme/      {weight / 1024 / 1024:.0f} Mo")
    print(f"dist/{name}   {archive.stat().st_size / 1024 / 1024:.0f} Mo")
    return 0


# Deux executables pour un seul dossier : celui qu'on lance tous les jours,
# sans console, et son jumeau bavard. Une application sans console qui echoue
# au demarrage ne dit rien du tout — c'est le seul moyen de voir pourquoi.
SPEC = r"""# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

extra_datas, extra_binaries, extra_hidden = collect_all("PySide6.QtMultimedia")

# Le serveur du NAS se publie à partir des sources : nas_publish recopie ses
# fichiers (PROGRAM) tels quels sur le partage. Empaquetés en bytecode, ils
# manqueraient : toutes les sources, pour qu'aucun nouveau n'y échappe.
import glob
extra_datas += [(path, "videosorter") for path in glob.glob(r"{here}\videosorter\*.py")]
extra_datas += [(r"{here}\nas\serveur.py", "nas")]

# Le Python du labo IA importe les sources de Prisme telles quelles : elles
# voyagent aussi dans « prisme-src » (iapython.sources), seules -- le
# dossier du programme porte des paquets qui ne sont pas les siens.
extra_datas += [(path, "prisme-src/videosorter")
                for path in glob.glob(r"{here}\videosorter\*.py")]

# Le moteur de reparation PixMedic (sous-module « moteur ») : ses sources
# seules, 300 Ko. Ses bibliotheques (numba, OpenCV, Pillow, PyAV : 150 Mo) se
# telechargent au premier usage dans le Python du labo, ou il tourne
# (moteur.py) : jamais dans le programme.
extra_datas += [(path, "moteur-src/reparateur")
                for path in glob.glob(r"{here}\moteur\reparateur\*.py")]

a = Analysis(
    [r"{here}\main.py"],
    pathex=[r"{here}"],
    binaries=extra_binaries,
    datas=extra_datas,
    hiddenimports=extra_hidden + ["PySide6.QtMultimediaWidgets", "PySide6.QtSvg", "rapidfuzz",
                                  "segno", "yt_dlp", "truststore", "numpy", "telethon", "cryptg",
                                  "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets"],
    excludes={excludes},
    noarchive=False,
)
pyz = PYZ(a.pure)

fenetre = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="Prisme",
    console=False, icon=r"{here}\prisme.ico",
)
console = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="Prisme-diagnostic",
    console=True, icon=r"{here}\prisme.ico",
)
coll = COLLECT(
    fenetre, console, a.binaries, a.datas, strip=False, upx=False,
    name="Prisme",
)
"""


LISEZMOI = """Prisme — tri rapide d'une collection vidéo
=========================================

POUR L'INSTALLER
Décompressez ce dossier où vous voulez, puis lancez Prisme.exe.
Rien d'autre à installer : Python et Qt sont dedans.

IL FAUT FFMPEG
Prisme s'en sert pour fabriquer les vignettes. S'il ne le trouve pas, il
propose au démarrage de le télécharger lui-même (environ 115 Mo, une seule
fois). Ou bien, dans une invite de commandes :

    winget install Gyan.FFmpeg

LE LABO IA
Au premier usage (⋯ › Collection › Labo IA), Prisme installe ce dont l'IA a
besoin : un Python à lui, puis ses bibliothèques (plusieurs Go, une fois).
Tout tourne sur ce PC.

PARTAGER LES VIGNETTES ENTRE DEUX ORDINATEURS
Fabriquer les vignettes est le travail le plus long. Deux machines qui
regardent le même partage fabriquent exactement les mêmes : leur nom ne
dépend que du chemin, de la taille et de la date de chaque vidéo.

Pour qu'elles se les partagent :
  1. choisissez un dossier sur le partage, par exemple X:\\_prisme ;
  2. renommez « prisme.cache.exemple » en « prisme.cache » et mettez-y
     ce seul chemin ;
  3. faites de même sur l'autre ordinateur.

Condition : le partage doit porter la même lettre de lecteur des deux
côtés. Si c'est X: ici, ce doit être X: là-bas.

L'index (durées, résolutions, empreintes) reste propre à chaque machine :
c'est une base de données, et deux écritures simultanées par le réseau la
fragiliseraient. Seules les vignettes se partagent — ce sont elles qui
coûtent des heures.

SI CE PAQUET CONTIENT UN DOSSIER « cache »
Alors tout est déjà là : vignettes, durées, plans, empreintes, notes et
réglages. Prisme s'en sert tout seul, sans rien refabriquer.

Les vignettes sont retrouvées par le chemin de chaque vidéo : le partage
doit donc porter la même lettre de lecteur qu'à la fabrication. Si c'était
X:, ce doit être X: ici aussi.

OÙ TROUVER TOUT ÇA
Le menu ⋯ → « Où sont les vignettes » le dit, avec le compte des fichiers.

SI RIEN NE SE LANCE
Lancez « Prisme-diagnostic.exe » : c'est le même programme, mais avec une
fenêtre noire qui affiche ce qui ne va pas. Le détail est aussi écrit
dans « prisme-erreur.log », à côté du programme.

Trois causes habituelles :
  1. Windows a bloqué les fichiers venus du réseau. Clic droit sur
     l'archive AVANT de la décompresser → Propriétés → cocher
     « Débloquer » → Appliquer.
  2. L'antivirus a mis Prisme.exe en quarantaine. Regardez son journal.
  3. Un composant Windows manque. La fenêtre de diagnostic le nomme.

LES RACCOURCIS
Le menu ⋯ → « Raccourcis et recherche ».
"""


# -- publier une version ---------------------------------------------------
# Ou deposer les versions, et ou les PC les lisent : `publication.txt`, a cote
# de ce script. Ligne 1, le dossier ou deposer ; ligne 2, facultative,
# l'adresse que les PC lisent si elle differe (un stockage en ligne, plus
# tard). Sans fichier : `--vers <dossier>`.
PUBLICATION = HERE / "publication.txt"
# La signature Windows, des qu'il y aura un certificat : `signature.json`,
# a cote de ce script, {"signtool": "C:/.../signtool.exe", "arguments": [...]}
# -- les arguments de `signtool sign` que le fournisseur indique. Sans fichier,
# rien n'est signe ; avec, chaque .exe l'est avant la mise en archive, et un
# Prisme signe n'accepte plus que des mises a jour signees du meme editeur.
SIGNATURE = HERE / "signature.json"


def sign(folder: Path) -> bool:
    if not SIGNATURE.is_file():
        print("Pas de signature.json : programme non signé.")
        return True
    import json
    told = json.loads(SIGNATURE.read_text(encoding="utf-8"))
    for exe in sorted(folder.glob("*.exe")):
        code = subprocess.call([told["signtool"], "sign", *told.get("arguments", []),
                                str(exe)])
        if code != 0:
            print(f"Signature impossible : {exe.name}")
            return False
    print("Programme signé.")
    return True


# L'installateur Windows (Inno Setup 6, gratuit) : « installer sur ce PC » ou
# « version portable », au choix de l'utilisateur. Sans Inno Setup, seule
# l'archive portable est faite.
ISS = HERE / "installateur" / "prisme.iss"


def _iscc() -> str:
    import os
    found = shutil.which("iscc") or shutil.which("ISCC")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", ""),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        candidate = Path(base) / "Inno Setup 6" / "ISCC.exe"
        if base and candidate.is_file():
            return str(candidate)
    return ""


def installer(version: str) -> Path | None:
    """Prisme-<version>-installation.exe, a partir de dist/Prisme ; None sans
    Inno Setup (ou s'il echoue)."""
    iscc = _iscc()
    if not iscc:
        print("Inno Setup absent : pas d'installateur, l'archive portable seule.")
        return None
    if not (OUT / "Prisme.exe").is_file():
        print("dist/Prisme est vide : construisez d'abord.")
        return None
    print("Installateur…", flush=True)
    code = subprocess.call([iscc, "/Q", f"/DAppVersion={version}", str(ISS)])
    made = HERE / "dist" / f"Prisme-{version}-installation.exe"
    if code != 0 or not made.is_file():
        print("Installateur impossible (Inno Setup a échoué).")
        return None
    print(f"dist/{made.name}   {made.stat().st_size / 1024 / 1024:.0f} Mo")
    return made


def set_version(version: str) -> None:
    import re
    place = HERE / "videosorter" / "__init__.py"
    text = place.read_text(encoding="utf-8")
    text = re.sub(r'__version__ = "[^"]*"', f'__version__ = "{version}"', text)
    place.write_text(text, encoding="utf-8")


def publish(version: str, notes: list, into: str = "", edition: str = "perso") -> int:
    """Construit la version, la depose, puis l'annonce (version.json en
    dernier : un PC qui regarde pendant la copie ne voit rien a moitie).

    La version vendue se prepare dans dist/publication-vendue : ses propres
    notes (rien des visiteurs), puis `--envoyer` la met en ligne (send)."""
    import hashlib
    import json
    import re
    from datetime import date

    if not re.fullmatch(r"\d+(\.\d+){1,3}", version):
        print(f"Numéro de version invalide : {version} (attendu : 1.2.0)")
        return 2
    lines = (PUBLICATION.read_text(encoding="utf-8").strip().splitlines()
             if PUBLICATION.is_file() and edition != SOLD else [])
    target = Path(into or (lines[0].strip() if lines else "")
                  or (str(SOLD_OUT) if edition == SOLD else ""))
    readers = (lines[1].strip() if len(lines) > 1 else "") or str(target)
    if edition == SOLD:
        readers = SOLD_UPDATES
    if not str(target) or str(target) == ".":
        print("Où publier ? Mettez le dossier dans publication.txt, ou --vers <dossier>.")
        return 2
    target.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(HERE))
    from videosorter.update import MANIFEST, newer
    try:
        before = json.loads((target / MANIFEST).read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError):
        before = ""
    if edition == SOLD:
        # Ce que les clients ont deja : la derniere version en ligne.
        from videosorter.update import UpdateError, fetch_manifest
        try:
            before = fetch_manifest(SOLD_UPDATES)["version"]
        except UpdateError:
            before = ""
    if before and not newer(version, before):
        print(f"La version publiée est déjà la {before} : choisissez un numéro plus grand.")
        return 2

    set_version(version)
    if edition == SOLD:
        # Rien d'une publication precedente : seulement celle-ci.
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True)
    code = build(source=readers, edition=edition)
    if code != 0:
        return code
    archive = HERE / "dist" / "Prisme.zip"
    name = f"Prisme-{version}.zip"
    print(f"Dépôt dans {target}…", flush=True)
    shutil.copyfile(archive, target / (name + ".partiel"))
    (target / (name + ".partiel")).replace(target / name)
    digest = hashlib.sha256()
    with open(target / name, "rb") as src:
        for piece in iter(lambda: src.read(1 << 20), b""):
            digest.update(piece)
    told = {"version": version, "date": date.today().isoformat(),
            "notes": [n for n in notes if n.strip()], "fichier": name,
            "taille": (target / name).stat().st_size, "sha256": digest.hexdigest()}
    (target / (MANIFEST + ".partiel")).write_text(
        json.dumps(told, ensure_ascii=False, indent=2), encoding="utf-8")
    (target / (MANIFEST + ".partiel")).replace(target / MANIFEST)
    # Les deux dernieres archives suffisent : un PC en retard passe
    # directement a la derniere.
    olds = sorted((p for p in target.glob("Prisme-*.zip") if p.name != name),
                  key=lambda p: p.stat().st_mtime)
    for old in olds[:-1]:
        old.unlink(missing_ok=True)
    # L'installateur, a cote : pour un nouveau PC (la mise a jour, elle, passe
    # par l'archive).
    setup = installer(version)
    if setup is not None:
        shutil.copyfile(setup, target / (setup.name + ".partiel"))
        (target / (setup.name + ".partiel")).replace(target / setup.name)
        for old in target.glob("Prisme-*-installation.exe"):
            if old.name != setup.name:
                old.unlink(missing_ok=True)
        if edition == SOLD:
            # Un nom qui ne change pas : le lien « Télécharger » du site.
            shutil.copyfile(setup, target / SOLD_SETUP)
    if edition == SOLD:
        print(f"\nPrisme {version} (version vendue) prêt dans {target}.\n"
              "Pour le mettre en ligne : python construire.py --envoyer")
        return 0
    print(f"\nPrisme {version} publié : les PC le verront dans les six heures, "
          "ou tout de suite par le menu : Aide, Rechercher une mise à jour.")
    return 0


SOLD_SETUP = "Prisme-installation.exe"


def send(folder: Path = SOLD_OUT) -> int:
    """Met en ligne la version vendue preparee : une « release » du depot
    SOLD_REPO, avec ses fichiers. Les clients la voient aussitot (version.json
    est envoye en dernier).

    Le jeton GitHub : la variable GITHUB_TOKEN, sinon celui que git connait
    deja pour github.com (git credential fill), sans rien afficher."""
    import json
    import requests
    try:
        told = json.loads((folder / "version.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print(f"Rien a envoyer dans {folder} : publiez d'abord (--publier … --edition vendue).")
        return 2
    token = _github_token()
    if not token:
        print("Pas de jeton GitHub : posez GITHUB_TOKEN, ou connectez git a github.com.")
        return 2
    api = f"https://api.github.com/repos/{SOLD_REPO}"
    head = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    tag = "v" + told["version"]
    made = requests.post(api + "/releases", headers=head, timeout=60, json={
        "tag_name": tag, "name": f"Prisme {told['version']}",
        "body": "\n".join("- " + n for n in told.get("notes", [])),
        "draft": True})
    if made.status_code >= 300:
        print(f"GitHub refuse la release {tag} : {made.status_code} {made.text[:200]}")
        return 1
    release = made.json()
    upload = release["upload_url"].split("{")[0]
    # Les gros fichiers d'abord, version.json en dernier.
    names = [told["fichier"]] + [p.name for p in sorted(folder.glob("*.exe"))] + ["version.json"]
    for name in names:
        path = folder / name
        print(f"Envoi de {name} ({path.stat().st_size / 2**20:.0f} Mo)…", flush=True)
        with open(path, "rb") as data:
            sent = requests.post(upload, params={"name": name}, data=data, timeout=3600,
                                 headers={**head, "Content-Type": "application/octet-stream"})
        if sent.status_code >= 300:
            print(f"Envoi impossible : {name} : {sent.status_code} {sent.text[:200]}")
            return 1
    # Brouillon jusqu'ici : les clients ne voyaient rien a moitie envoye.
    done = requests.patch(f"{api}/releases/{release['id']}", headers=head, timeout=60,
                          json={"draft": False, "make_latest": "true"})
    if done.status_code >= 300:
        print(f"La release reste en brouillon : {done.status_code} {done.text[:200]}")
        return 1
    print(f"Prisme {told['version']} en ligne : {release['html_url'].replace('/untagged-', '/')}")
    return 0


def _github_token() -> str:
    import os
    if os.environ.get("GITHUB_TOKEN"):
        return os.environ["GITHUB_TOKEN"].strip()
    try:
        answer = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                                capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in answer.stdout.splitlines():
        if line.startswith("password="):
            return line[len("password="):]
    return ""


def _option(name: str) -> list:
    found, args = [], sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == name and i + 1 < len(args):
            found.append(args[i + 1])
    return found


if __name__ == "__main__":
    if "--installateur" in sys.argv:
        # L'installateur seul, depuis le dist/Prisme deja construit.
        chosen = _option("--installateur")
        from videosorter import __version__
        sys.exit(0 if installer(chosen[0] if chosen else __version__) else 1)
    edition = (_option("--edition") or ["perso"])[0]
    if edition not in ("perso", SOLD):
        print(f"Édition inconnue : {edition} (perso ou {SOLD})")
        sys.exit(2)
    if "--envoyer" in sys.argv:
        sys.exit(send())
    if "--publier" in sys.argv:
        chosen = _option("--publier")
        sys.exit(publish(chosen[0] if chosen else "", _option("--notes"),
                         (_option("--vers") or [""])[0], edition))
    sys.exit(build("--complet" in sys.argv, edition=edition))
