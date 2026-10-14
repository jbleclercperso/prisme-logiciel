"""Telegram, version perso : ses groupes, leurs alertes, la recherche.

Le compte est celui de l'utilisateur (api_id / api_hash créés sur
my.telegram.org). La session et les identifiants restent dans le dossier
de configuration de Prisme, jamais dans le dépôt.

Quatre onglets :
- Mes groupes : les groupes et chaînes du compte (et les chaînes publiques
  ajoutées depuis la recherche). Pour chacun, deux cases : « Alerte » (compter
  les nouveaux médias) et « Auto » (les télécharger dès qu'ils arrivent).
- Recherche : groupes publics par mot-clé, messages publics qui contiennent
  le mot, ou liens t.me déjà publiés sur le web (à analyser ensuite).
- Journal : chaque passage qui a trouvé du nouveau — quand, quel groupe,
  combien, et le dossier où c'est arrivé.
- Compte : identifiants, dossier des téléchargements, fréquence.

La vérification tourne aussi sans la fenêtre (`start_watch`, lancée par la
fenêtre principale) et le dit par un bandeau. Un seul client Telegram à la
fois : la session est un fichier SQLite que deux clients se disputeraient.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QProgressBar, QPushButton, QSpinBox, QTabWidget, QTableWidget, QTableWidgetItem,
    QTextEdit, QVBoxLayout, QWidget,
)

from .cartes import ROLE_CHIP, ROLE_RES, ROLE_SUB, use_cards
from .config import APP_DIR

STATE_PATH = APP_DIR / "telegram.json"
SESSION_PATH = APP_DIR / "telegram"
THUMBS = APP_DIR / "telegram-thumbs"
DOWNLOADS = APP_DIR / "telegram-downloads"
URL_RE = re.compile(r"(?:https?://)?t\.me/([A-Za-z0-9_]{4,})", re.I)
JOURNAL_MAX = 300
BANK_PAGE = 48          # médias par page dans la banque
DURATION_CHOICES = ((0, "Toutes"), (10, "10 s"), (30, "30 s"), (60, "1 min"),
                    (180, "3 min"), (600, "10 min"))

STYLE = """
QDialog { background: #101318; color: #e8edf2; }
QLabel { color: #c9d1db; }
QLabel#tgHead { color: #ffffff; font-size: 20px; font-weight: 700; }
QLabel#tgLead { color: #8b97a6; font-size: 12px; }
QLabel#tgStatus { color: #d5e6ff; background: #1a2433; border-radius: 8px; padding: 7px 12px; }
QLabel#tgTotal { color: #9fd0ff; font-size: 14px; font-weight: 600; }
QLabel#tgWho { color: #7bd88f; font-weight: 600; }
QLineEdit, QTextEdit, QSpinBox {
    background: #0c0f14; border: 1px solid #2a3340; border-radius: 8px;
    color: #eef2f6; padding: 7px 8px; }
QLineEdit:focus, QTextEdit:focus { border-color: #3d7eff; }
QTableWidget, QListWidget {
    background: #0c0f14; border: 1px solid #2a3340; border-radius: 10px;
    color: #e8edf2; gridline-color: #1b222c; }
QHeaderView::section { background: #171d26; color: #9aa6b4; border: none; padding: 8px; }
QTableWidget::item { padding: 4px; }
QTableWidget::item:selected, QListWidget::item:selected { background: #243652; color: white; }
QPushButton { background: #1c2430; border: 1px solid #334052; border-radius: 8px;
              padding: 8px 14px; color: #eef2f6; }
QPushButton:hover { background: #273140; }
QPushButton:disabled { color: #66717e; background: #161b22; }
QPushButton:checked { background: #243652; border-color: #2f6fed; }
QPushButton#tgPrimary { background: #2f6fed; border-color: #2f6fed; font-weight: 650; }
QPushButton#tgPrimary:hover { background: #3d7cff; }
QCheckBox { color: #d5dde6; spacing: 6px; }
QTabWidget::pane { border: 1px solid #2a3340; border-radius: 10px; top: -1px; }
QTabBar::tab { background: transparent; color: #9aa6b4; padding: 9px 18px; margin-right: 4px;
               font-size: 13px; }
QTabBar::tab:selected { color: white; border-bottom: 2px solid #2f6fed; }
"""


# -- l'etat, sur le disque ------------------------------------------------------
_STATE_LOCK = threading.Lock()


def load_state() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    for key, empty in (("api_id", ""), ("api_hash", ""), ("phone", ""), ("channels", []),
                       ("last_id", {}), ("unread", {}), ("joined", []), ("journal", []),
                       ("checked", {}), ("download_dir", ""), ("interval_min", 10),
                       ("seen_from", {}), ("auto_from", {}), ("unseen", {}), ("top", {}),
                       ("medias", {}),
                       ("me", "")):
        data.setdefault(key, empty)
    # Avant : une liste d'alertes {username, title, follow}. Maintenant chaque
    # suivi a une cle (le nom public, ou c<id>/g<id> pour un groupe prive) et
    # deux cases : alerte, auto.
    for entry in data["channels"]:
        if "key" not in entry:
            entry["key"] = entry.get("username") or ""
            entry["alert"] = True
            entry["auto"] = bool(entry.pop("follow", False))
    data["channels"] = [c for c in data["channels"] if c.get("key")]
    return data


def save_state(data: dict) -> None:
    with _STATE_LOCK:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, STATE_PATH)


def update_state(change) -> dict:
    """Relit, modifie, réécrit : la fenêtre et la vérification de fond
    écrivent chacune leur part sans effacer celle de l'autre."""
    with _STATE_LOCK:
        data = load_state()
        change(data)
    save_state(data)
    return data


def download_root(state: dict) -> Path:
    chosen = (state.get("download_dir") or "").strip()
    return Path(chosen) if chosen else DOWNLOADS


def safe_name(text: str) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text or "").strip(" .")
    return text[:80] or "telegram"


def channel_folder(state: dict, title: str, key: str) -> Path:
    """L'ancien dossier unique du groupe : on n'y écrit plus, mais ce qui y
    est déjà n'est pas retéléchargé."""
    return download_root(state) / safe_name(title or key)


def media_folder(state: dict, title: str, key: str, video: bool) -> Path:
    """Les vidéos dans « Groupe - Vids », les photos dans « Groupe - Pix »,
    côte à côte dans le dossier des téléchargements."""
    return download_root(state) / f"{safe_name(title or key)} - {'Vids' if video else 'Pix'}"


def group_folders(state: dict, title: str, key: str) -> list[Path]:
    """Les dossiers du groupe qui existent déjà sur le disque."""
    return [p for p in (media_folder(state, title, key, True),
                        media_folder(state, title, key, False),
                        channel_folder(state, title, key)) if p.is_dir()]


def msg_is_video(msg) -> bool:
    mime = (msg.file.mime_type or "") if msg.file else ""
    return bool(msg.video) or mime.startswith("video")


def telegram_links(key: str, username: str = "", top: int = 0) -> list[str]:
    """De quoi ouvrir le groupe dans Telegram : l'appli d'abord (tg://), le
    navigateur ensuite. Un petit groupe privé (g<id>) n'a pas de lien."""
    username = username or ("" if key[:1] in "cg" and key[1:].isdigit() else key)
    if username:
        return [f"tg://resolve?domain={username}", f"https://t.me/{username}"]
    if key[:1] == "c" and key[1:].isdigit():
        post = max(1, int(top or 0))
        return [f"tg://privatepost?channel={key[1:]}&post={post}",
                f"https://t.me/c/{key[1:]}/{post}"]
    return []


VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".ts", ".wmv"}


def library_keys() -> set[tuple[str, int]]:
    """Nom + taille des vidéos déjà dans les racines Prisme."""
    keys: set[tuple[str, int]] = set()
    try:
        cfg = json.loads((APP_DIR / "config.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return keys
    roots = list(cfg.get("roots") or [])
    if cfg.get("root"):
        roots.append(cfg["root"])
    seen = set()
    for raw in roots:
        root = Path(str(raw))
        if not root.exists():
            continue
        key = str(root).lower()
        if key in seen:
            continue
        seen.add(key)
        for path in root.rglob("*"):
            if path.suffix.lower() not in VIDEO_EXT:
                continue
            try:
                keys.add((path.name.lower(), path.stat().st_size))
            except OSError:
                continue
    return keys


def already_in_library(name: str, size: int, keys: set[tuple[str, int]]) -> bool:
    if not name or not size:
        return False
    return (name.lower(), int(size)) in keys


INVITE_RE = re.compile(r"(?:https?://)?(?:t|telegram)\.me/(?:\+|joinchat/)([A-Za-z0-9_-]{10,})")
PUBLIC_RE = re.compile(r"(?:https?://)?(?:t|telegram)\.me/(?:s/)?([A-Za-z][A-Za-z0-9_]{4,31})\b")
RESERVED = {"joinchat", "share", "addstickers", "addemoji", "telegram", "iv", "proxy", "socks",
            "setlanguage", "addtheme", "login", "contact", "confirmphone", "addlist", "boost",
            "giftcode", "invoice", "telegramtips", "durov"}
SKIP_HOSTS = ("bing.", "microsoft.", "duckduckgo.", "t.me", "telegram.me", "telegram.org",
              "google.", "youtube.", "facebook.", "instagram.", "twitter.", "x.com", "wikipedia.",
              "msn.", "live.com", "apple.com", "play.google")
# Les fiches d'un annuaire de groupes : c'est la que le lien t.me se trouve.
DETAIL_RE = re.compile(r"/(?:[a-z]{2}/)?(?:listing|groupe-telegram|groupes?|group|canal|canaux|"
                       r"channels?|chat|join|go)s?/[^/\"#?]{3,}", re.I)
# Des annuaires de groupes, interroges directement par le mot-cle : ils
# repondent meme quand les moteurs bloquent, et donnent des invitations
# privees (telegram-board surtout). {q} : le mot, tel quel.
DIRECTORIES = ("https://telegram-board.com/fr/tags/{q}", "https://telegram-board.com/tags/{q}",
               "https://taligram.org/fr/groupes-telegram/{q}",
               "https://taligram.org/fr/meilleurs-groupes-telegram/{q}")
WEB_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                             "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
               "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"}


def _fetch(url: str, timeout: int = 12) -> str:
    import urllib.request
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=WEB_HEADERS),
                                    timeout=timeout) as resp:
            return resp.read(2_000_000).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 — un site muet n'arrete pas les autres
        return ""


def _harvest(html: str, found: dict, source: str) -> None:
    """Les liens t.me d'une page : invitations privees et noms publics."""
    import urllib.parse
    html = urllib.parse.unquote(html.replace("&amp;", "&"))
    for code in INVITE_RE.findall(html):
        found.setdefault(("invite", code), source)
    for name in PUBLIC_RE.findall(html):
        low = name.lower()
        if low in RESERVED or low.endswith("bot"):
            continue
        found.setdefault(("public", name), source)


def _engine_results(html: str, engine: str) -> list[str]:
    """Les adresses des resultats d'une page de moteur, en clair."""
    import base64
    import urllib.parse
    out = []
    if engine == "ddg":
        for href in re.findall(r'href="([^"]+)"[^>]*class=.result-link', html):
            target = urllib.parse.parse_qs(urllib.parse.urlparse(
                href.replace("&amp;", "&")).query).get("uddg", [""])[0]
            if target:
                out.append(target)
    else:
        for href in re.findall(r'<h2[^>]*><a[^>]+href="(https?://[^"]+)"', html):
            href = href.replace("&amp;", "&")
            coded = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("u", [""])[0]
            if coded.startswith("a1"):
                try:
                    raw = coded[2:] + "=" * (-len(coded[2:]) % 4)
                    out.append(base64.urlsafe_b64decode(raw).decode("utf-8", "replace"))
                except ValueError:
                    pass
            elif "bing.com" not in href:
                out.append(href)
    return out


def public_links(query: str, say=lambda _t: None) -> tuple[list[dict], str]:
    """Des liens de groupes Telegram sur un theme, cherches sur le web : les
    moteurs (DuckDuckGo, Bing), puis les pages qu'ils donnent (annuaires,
    forums) et les fiches de ces annuaires, ou le lien t.me se trouve. Les
    invitations privees (t.me/+…) sont rares sur le web : Telegram en
    trouve davantage (« Chercher des groupes »). Renvoie aussi pourquoi
    c'est vide."""
    import urllib.parse
    from concurrent.futures import ThreadPoolExecutor
    query = query.strip()
    if not query:
        return [], "Tape d'abord un mot-clé."
    import time as _time
    found: dict = {}
    notes: list[str] = []
    say("Recherche sur le web…")
    bing = [("bing", "https://www.bing.com/search?" + urllib.parse.urlencode({"q": q}))
            for q in (f"{query} telegram", f"{query} groupe telegram lien", f'"t.me/+" {query}')]
    with ThreadPoolExecutor(3) as pool:
        pages = list(pool.map(lambda e: (e[0], _fetch(e[1])), bing))
    # DuckDuckGo, une question apres l'autre : en rafale, il prend Prisme pour
    # un robot et demande une verification -- qu'on ne contourne pas.
    for q in (f"{query} telegram", f'{query} "t.me/+"', f"{query} groupe telegram"):
        html = _fetch("https://lite.duckduckgo.com/lite/?" + urllib.parse.urlencode({"q": q}))
        if "bots use DuckDuckGo" in html or "anomaly" in html.lower():
            notes.append("DuckDuckGo demande une vérification anti-robot (trop de recherches "
                         "rapprochées) : résultats partiels, réessaie dans quelques minutes")
            break
        pages.append(("ddg", html))
        _time.sleep(1.2)
    results: list[str] = []
    for engine, html in pages:
        _harvest(html, found, engine)
        for url in _engine_results(html, engine):
            _harvest(url, found, urllib.parse.urlparse(url).netloc or engine)
            host = urllib.parse.urlparse(url).netloc.lower()
            if host and not any(h in host for h in SKIP_HOSTS) and url not in results:
                results.append(url)
    results = results[:14]
    slug = urllib.parse.quote(query.lower().replace(" ", "-"))
    directories = [d.format(q=slug) for d in DIRECTORIES]
    results = directories + [u for u in results if u not in directories]
    say(f"Lecture de {len(results)} sites et annuaires…")
    with ThreadPoolExecutor(8) as pool:
        texts = list(pool.map(_fetch, results))
    details: list[tuple[str, str]] = []
    for url, html in zip(results, texts):
        host = urllib.parse.urlparse(url).netloc
        _harvest(html, found, host)
        taken = 0
        limit = 20 if url in directories else 8
        for href in re.findall(r'href="([^"#]+)"', html):
            full = urllib.parse.urljoin(url, href.replace("&amp;", "&"))
            parsed = urllib.parse.urlparse(full)
            if (parsed.netloc == host and DETAIL_RE.search(parsed.path) and full != url
                    and all(full != d for d, _h in details)):
                details.append((full, host))
                taken += 1
                if taken >= limit:
                    break
    if details:
        say(f"Lecture de {len(details)} fiches d'annuaires…")
        with ThreadPoolExecutor(8) as pool:
            for (url, host), html in zip(details, pool.map(lambda d: _fetch(d[0]), details)):
                _harvest(html, found, host)
    rows = []
    for (kind, code), source in found.items():
        if kind == "invite":
            rows.append({"mode": "web", "title": "invitation privée", "username": "",
                         "url": f"https://t.me/+{code}", "kind": "privé", "acces": "invitation",
                         "demande": True, "source": source})
        else:
            rows.append({"mode": "web", "title": code, "username": code,
                         "url": f"https://t.me/{code}", "kind": "public", "acces": "à analyser",
                         "demande": False, "source": source})
    rows.sort(key=lambda r: r["kind"] != "privé")
    if not rows:
        return [], ("Aucun lien trouvé sur le web pour ce mot. " + (notes[0] + ". " if notes else "")
                    + "Essaie « Chercher des groupes » : Telegram en trouve davantage.")
    return rows, (notes[0] + "." if notes else "")


class WebLinks(QThread):
    """La recherche de liens sur le web, hors du fil de l'interface (elle
    lit une vingtaine de pages : plusieurs secondes)."""
    done = Signal(list, str)
    status = Signal(str)

    def __init__(self, query: str):
        super().__init__()
        self.query = query

    def run(self) -> None:
        try:
            rows, why = public_links(self.query, self.status.emit)
        except Exception as exc:  # noqa: BLE001
            rows, why = [], f"Recherche sur le web impossible : {exc}"
        self.done.emit(rows, why)


def username_of(text: str) -> str:
    text = (text or "").strip()
    found = URL_RE.search(text)
    if found:
        return found.group(1)
    return text.lstrip("@").split("/")[0]


def _alive(days: int | None) -> str:
    if days is None:
        return "?"
    if days <= 2:
        return "vivant"
    if days <= 14:
        return "calme"
    return "endormi"


def _score(row: dict) -> float:
    if row.get("protege") or row.get("restreint") or not row.get("username"):
        return -1
    subs = int(row.get("abonnes") or 0)
    medias = int(row.get("medias") or 0)
    days = row.get("jours")
    freshness = 1.0 if days is None else 1.0 / (1 + days / 7)
    return subs * (1 + medias) * freshness


def is_media(msg) -> bool:
    """Une vidéo ou une photo (pas l'aperçu d'un lien, pas un autocollant)."""
    if getattr(msg, "web_preview", None):
        return False
    if msg.video or msg.photo:
        return True
    mime = (msg.file.mime_type or "") if msg.file else ""
    return bool(msg.document) and mime.startswith(("video", "image")) and not msg.sticker


def human_when(stamp: float) -> str:
    return time.strftime("%d/%m %H:%M", time.localtime(stamp)) if stamp else "jamais"


# -- des files, une seule connexion ---------------------------------------------
# Chaque file passe ses travaux l'un après l'autre ; les files tournent
# ensemble, sur la même connexion (`link`).
# « ui » : les clics courts (médias, rejoindre, connexion). « lecture » : le
# média double-cliqué. « recherche » : une recherche, qui peut durer.
# « fond » : recomptage des groupes et vérifications. « dl » : les gros
# téléchargements. Un clic n'attend plus derrière un recomptage de cent
# groupes, une recherche ou « Tout télécharger ».
JOB_LANE = {"dialogs": "fond", "check": "fond", "export": "dl", "download_many": "dl",
            "search": "recherche", "content": "recherche", "download": "lecture"}
LANE_NAMES = {"ui": "l'action précédente", "recherche": "la recherche précédente",
              "fond": "la vérification", "dl": "le téléchargement",
              "lecture": "le média précédent"}


class _Queue(QObject):
    """Les travaux d'une file passent l'un après l'autre (vit dans le fil de
    l'interface : la fin d'un travail y arrive en file, pas dans son fil)."""

    def __init__(self):
        super().__init__()
        self.waiting: list = []
        self.current = None

    def launch(self, worker) -> None:
        worker.finished.connect(self.next, Qt.QueuedConnection)
        self.waiting.append(worker)
        self.next()

    def next(self) -> None:
        if self.current is not None and self.current.isRunning():
            return
        self.current = None
        if self.waiting:
            self.current = self.waiting.pop(0)
            self.current.start()

    def busy(self) -> bool:
        return (self.current is not None and self.current.isRunning()) or bool(self.waiting)


class _Hub(QObject):
    """Ce qu'une vérification a trouvé, pour la fenêtre si elle est ouverte."""
    checked = Signal(dict)


_QUEUES: dict[str, _Queue] = {}
HUB: _Hub | None = None


def queue(lane: str = "ui") -> _Queue:
    global HUB
    if HUB is None:
        HUB = _Hub()
    if lane not in _QUEUES:
        _QUEUES[lane] = _Queue()
    return _QUEUES[lane]


def hub() -> _Hub:
    queue()
    return HUB


def _duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{round(seconds / 60)} min"
    return f"{seconds // 3600} h {seconds % 3600 // 60:02d}"


class _Link:
    """La seule connexion à Telegram : un client, ouvert une fois et gardé.
    Avant, chaque clic ouvrait la sienne (une seconde de poignée de main à
    chaque fois, et plusieurs à la fois sur le même compte : Telegram s'en
    méfie). Les travaux tournent ensemble sur ce client, dans son fil."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self._client = None
        self._key = None
        self._lock: asyncio.Lock | None = None
        self.authorized = False
        self.paused_until = 0.0
        threading.Thread(target=self.loop.run_forever, daemon=True,
                         name="prisme-telegram").start()

    async def client(self, api_id: int, api_hash: str):
        from telethon import TelegramClient
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._client is not None and self._key != (api_id, api_hash):
                await self._client.disconnect()
                self._client, self.authorized = None, False
            if self._client is None:
                # Les petites pauses demandées par Telegram (quelques
                # secondes) sont respectées sans rien dire ; les longues
                # remontent, et tout s'arrête le temps qu'il faut.
                self._client = TelegramClient(str(SESSION_PATH), api_id, api_hash,
                                              flood_sleep_threshold=15,
                                              connection_retries=3, request_retries=3,
                                              auto_reconnect=True)
                self._key = (api_id, api_hash)
            if not self._client.is_connected():
                await self._client.connect()
            return self._client

    def warm(self, api_id: int, api_hash: str) -> None:
        """Ouvre la connexion d'avance (à l'ouverture de la fenêtre) : le
        premier clic n'attend pas la poignée de main."""
        async def go():
            try:
                client = await self.client(api_id, api_hash)
                if not self.authorized and await client.is_user_authorized():
                    self.authorized = True
            except Exception:  # noqa: BLE001 — le premier travail le dira
                pass
        if api_id and api_hash and not self.paused_for():
            asyncio.run_coroutine_threadsafe(go(), self.loop)

    def pause(self, seconds: int) -> None:
        if seconds > 30:
            self.paused_until = max(self.paused_until, time.time() + seconds)

    def paused_for(self) -> float:
        return max(0.0, self.paused_until - time.time())

    def close(self) -> None:
        """À la fermeture de Prisme : la session se referme proprement."""
        try:
            if self._client is not None:
                asyncio.run_coroutine_threadsafe(self._client.disconnect(), self.loop).result(3)
        except Exception:  # noqa: BLE001
            pass
        self._client = None
        self.loop.call_soon_threadsafe(self.loop.stop)


_LINK: _Link | None = None


def link() -> _Link:
    global _LINK
    if _LINK is None:
        import atexit
        _LINK = _Link()
        atexit.register(_LINK.close)
    return _LINK


class TelegramWorker(QObject):
    """Un travail Telegram, sur la connexion partagée (`link`). Se lance et
    se suit comme un fil : start, isRunning, started, finished."""

    started = Signal()
    finished = Signal()
    status = Signal(str)
    need_code = Signal()
    need_password = Signal()
    search_done = Signal(list)
    joined_done = Signal(list)
    joined_progress = Signal(list)
    join_done = Signal(list)
    checked = Signal(dict)
    bank_done = Signal(list)
    bank_part = Signal(list)
    bank_totals = Signal(dict)
    file_ready = Signal(str)
    batch_done = Signal(int)
    progress = Signal(dict)
    failed = Signal(str)

    def __init__(self, state: dict, job: str = "login"):
        super().__init__()
        self.state = state
        self.job = job
        self.lane = JOB_LANE.get(job, "ui")
        self.stop = False           # « Arrêter » : lu entre deux fichiers
        self.got = 0                # banque : médias de la page
        self.error = ""
        self._running = False
        self.interactive = True
        self.query = ""
        self.pasted: list[str] = []
        self.channel = ""
        self.title = ""
        self.before_id = 0
        self.message_id = 0
        self.message_ids: list[int] = []
        self.only: list[str] = []
        self.targets: list[str] = []
        self.invites: dict[str, None] = {}
        self.kind = "all"           # banque, export : "all", "video", "photo"
        self.min_duration = 0       # banque, export : vidéos plus courtes laissées de côté
        self.short = 0
        self.skip_known = True
        self.known: set[tuple[str, int]] = set()
        self.skipped = 0
        self._dialogs_loaded = False
        self._code = ""
        self._password = ""
        self._wait = threading.Event()

    def give_code(self, code: str) -> None:
        self._code = code.strip()
        self._wait.set()

    def give_password(self, password: str) -> None:
        self._password = password
        self._wait.set()

    async def _ask(self, kind: str) -> str:
        self._wait.clear()
        (self.need_code if kind == "code" else self.need_password).emit()
        # Attendre la saisie sans bloquer la connexion (les autres travaux
        # continuent pendant ce temps).
        got = await asyncio.get_running_loop().run_in_executor(None, self._wait.wait, 180)
        if not got:
            raise TimeoutError("code Telegram non saisi")
        return self._code if kind == "code" else self._password

    # -- comme un fil : start, isRunning, started, finished ---------------------
    def start(self) -> None:
        self._running = True
        self.started.emit()
        asyncio.run_coroutine_threadsafe(self._main(), link().loop)

    def isRunning(self) -> bool:
        return self._running

    async def _main(self) -> None:
        try:
            await self._run()
        except Exception as exc:  # noqa: BLE001 — remonté à l'interface
            seconds = getattr(exc, "seconds", None)
            name = type(exc).__name__
            if name == "FloodWaitError" and seconds:
                link().pause(int(seconds))
                self.error = (f"Telegram demande une pause de {_duration(seconds)}. "
                              "Rien n'est cassé : Prisme reprendra tout seul ensuite.")
            elif name in ("AuthKeyUnregisteredError", "AuthKeyDuplicatedError",
                          "SessionRevokedError", "SessionExpiredError", "UnauthorizedError"):
                link().authorized = False
                self.error = ("Telegram a fermé la session de Prisme : reconnecte-toi "
                              "(onglet Compte, « Connecter »).")
            else:
                self.error = f"{type(exc).__name__} : {exc}"
            if self.interactive:
                self.failed.emit(self.error)
        finally:
            self._running = False
            self.finished.emit()

    async def _run(self) -> None:
        try:
            from telethon.errors import SessionPasswordNeededError
        except ImportError:
            self.failed.emit("Telethon n'est pas installé. Lance : pip install telethon")
            return
        api_id = int(self.state.get("api_id") or 0)
        api_hash = self.state.get("api_hash") or ""
        if not api_id or not api_hash:
            self.failed.emit("api_id et api_hash manquent (onglet Compte).")
            return
        shared = link()
        wait = shared.paused_for()
        if wait:
            # Garde-fou : Telegram a demandé une pause ; insister la rallonge.
            if self.interactive and self.lane != "fond":
                self.failed.emit(f"Telegram a demandé une pause : encore {_duration(wait)}. "
                                 "Rien n'est cassé, réessaie ensuite.")
            return
        client = await shared.client(api_id, api_hash)
        if not shared.authorized:
            if not await client.is_user_authorized():
                if not self.interactive or self.job != "login":
                    if self.interactive and self.lane != "fond":
                        self.failed.emit("Pas encore connecté : onglet Compte, « Connecter ».")
                    return
                phone = self.state.get("phone") or ""
                if not phone:
                    self.failed.emit("Numéro manquant, au format international (+33…).")
                    return
                await client.send_code_request(phone)
                self.status.emit("Code envoyé dans Telegram.")
                try:
                    await client.sign_in(phone, await self._ask("code"))
                except SessionPasswordNeededError:
                    await client.sign_in(password=await self._ask("password"))
            shared.authorized = True
        if self.job == "login" or not self.state.get("me"):
            # Une requête de moins à chaque clic : le nom est déjà connu.
            me = await client.get_me()
            who = me.first_name or me.username or str(me.id)
            update_state(lambda s: s.__setitem__("me", who))
            self.status.emit(f"Connecté : {who}")
        if self.job == "search":
            self.search_done.emit(await self._search(client))
        elif self.job == "content":
            self.search_done.emit(await self._content(client))
        elif self.job == "dialogs":
            self.joined_done.emit(await self._dialogs(client))
        elif self.job == "join":
            self.join_done.emit(await self._join(client))
        elif self.job == "check":
            self.checked.emit(await self._check(client))
        elif self.job == "bank":
            self.bank_done.emit(await self._bank(client))
        elif self.job == "download":
            self.file_ready.emit(await self._download(client) or "")
        elif self.job == "download_many":
            self.batch_done.emit(await self._download_many(client))
        elif self.job == "export":
            self.batch_done.emit(await self._export(client))

    # -- retrouver un groupe a partir de sa cle --------------------------------
    async def _resolve(self, client, key: str):
        """Nom public, c<id> (canal, supergroupe) ou g<id> (petit groupe).
        Un nombre seul, ancienne forme : un canal."""
        from telethon.tl.types import PeerChannel, PeerChat
        raw = str(key)
        if raw[:1] in "cg" and raw[1:].isdigit():
            peer = (PeerChannel if raw[0] == "c" else PeerChat)(int(raw[1:]))
        elif raw.lstrip("-").isdigit():
            peer = PeerChannel(int(raw))
        else:
            return raw
        try:
            return await client.get_entity(peer)
        except ValueError:
            # Pas encore dans le cache de la session : la liste des
            # conversations l'y met.
            if not self._dialogs_loaded:
                await client.get_dialogs()
                self._dialogs_loaded = True
            return await client.get_entity(peer)

    # -- recherche -------------------------------------------------------------
    async def _inspect(self, client, chat, flood) -> dict:
        """La fiche d'un groupe trouvé : combien de photos et vidéos en tout,
        la vignette des trois dernières, et la date du dernier message."""
        username = getattr(chat, "username", None)
        title = getattr(chat, "title", None) or username or "?"
        row = {
            "title": title,
            "username": username or "",
            "url": f"https://t.me/{username}" if username else "",
            "kind": "chaîne" if getattr(chat, "broadcast", False) else "groupe",
            "abonnes": int(getattr(chat, "participants_count", 0) or 0),
            "protege": bool(getattr(chat, "noforwards", False)),
            "restreint": bool(getattr(chat, "restricted", False)),
            "demande": bool(getattr(chat, "join_request", False)),
            "medias": 0,
            "jours": None,
            "dernier": "",
            "apercus": [],
        }
        if not username:
            return row
        try:
            row["medias"] = await self._count(client, username)
            last = await client.get_messages(username, limit=1)
            if last and last[0].date is not None:
                row["jours"] = int((time.time() - last[0].date.timestamp()) / 86400)
                row["dernier"] = last[0].date.astimezone().strftime("%d/%m/%y")
            if row["medias"]:
                THUMBS.mkdir(parents=True, exist_ok=True)
                async for msg in client.iter_messages(username, limit=3,
                                                      filter=self._photo_video()):
                    got = await self._thumb(msg, THUMBS / f"{safe_name(username)}_{msg.id}.jpg")
                    if got:
                        row["apercus"].append(got)
        except flood as exc:
            await asyncio.sleep(min(exc.seconds, 20))
        except Exception:  # noqa: BLE001 — illisible, on garde la fiche
            row["dernier"] = "illisible"
        row["vivant"] = _alive(row["jours"])
        row["score"] = round(_score(row))
        return row

    async def _content(self, client) -> list:
        """Messages publics qui contiennent le mot, avec le lien du message."""
        from telethon.tl.functions.messages import SearchGlobalRequest
        from telethon.tl.types import Channel, InputMessagesFilterEmpty, InputPeerEmpty
        query = self.query.strip()
        rows = []
        seen = set()
        offset_rate, offset_peer, offset_id = 0, InputPeerEmpty(), 0
        for _page in range(6):
            if self.stop:
                break
            try:
                page = await client(SearchGlobalRequest(
                    q=query, filter=InputMessagesFilterEmpty(),
                    min_date=None, max_date=None,
                    offset_rate=offset_rate, offset_peer=offset_peer,
                    offset_id=offset_id, limit=50,
                ))
            except Exception as exc:  # noqa: BLE001
                self.status.emit(str(exc)[:80])
                break
            chats = {c.id: c for c in page.chats if isinstance(c, Channel)}
            if not page.messages:
                break
            for msg in page.messages:
                chat = chats.get(getattr(msg.peer_id, "channel_id", 0))
                if chat is None or not getattr(chat, "username", None):
                    continue
                key = (chat.username, msg.id)
                if key in seen:
                    continue
                seen.add(key)
                media = "vidéo" if msg.video else "photo" if msg.photo else \
                    "fichier" if msg.file and not getattr(msg, "web_preview", None) else ""
                rows.append({
                    "mode": "message",
                    "title": chat.title or chat.username,
                    "username": chat.username,
                    "message_id": msg.id,
                    "url": f"https://t.me/{chat.username}/{msg.id}",
                    "abonnes": int(getattr(chat, "participants_count", 0) or 0),
                    "media": media,
                    "date": msg.date.astimezone().strftime("%d/%m/%y %H:%M") if msg.date else "",
                    "texte": (msg.message or "").replace("\n", " ")[:160],
                    "demande": bool(getattr(chat, "join_request", False)),
                })
            last = page.messages[-1]
            offset_id = last.id
            when = getattr(last, "date", None)
            offset_rate = int(when.timestamp()) if when else 0
            offset_peer = await client.get_input_entity(last.peer_id)
            self.status.emit(f"{len(rows)} messages trouvés…")
        return rows

    async def _collect(self, client, seen: dict) -> None:
        """Élargit au-delà d'une page : recherche contacts + recherche globale."""
        from telethon.errors import FloodWaitError
        from telethon.tl.functions.contacts import SearchRequest
        from telethon.tl.functions.messages import SearchGlobalRequest
        from telethon.tl.types import Channel, InputMessagesFilterEmpty, InputPeerEmpty
        query = self.query.strip()
        words = [query] + [w for w in query.split() if len(w) > 2 and w != query]
        for word in words[:4]:
            if self.stop:
                return
            self.status.emit(f"Recherche : {word}")
            try:
                found = await client(SearchRequest(q=word, limit=100))
                for chat in found.chats:
                    if isinstance(chat, Channel) and chat.username:
                        seen[str(chat.id)] = chat
            except FloodWaitError as exc:
                await asyncio.sleep(min(exc.seconds, 25))
            offset_rate, offset_peer, offset_id = 0, InputPeerEmpty(), 0
            for _page in range(4):
                if self.stop:
                    return
                try:
                    page = await client(SearchGlobalRequest(
                        q=word, filter=InputMessagesFilterEmpty(),
                        min_date=None, max_date=None,
                        offset_rate=offset_rate, offset_peer=offset_peer,
                        offset_id=offset_id, limit=50,
                    ))
                except FloodWaitError as exc:
                    await asyncio.sleep(min(exc.seconds, 25))
                    break
                except Exception:  # noqa: BLE001
                    break
                for chat in page.chats:
                    if isinstance(chat, Channel) and getattr(chat, "username", None):
                        seen[str(chat.id)] = chat
                # Les chaines publiques annoncent des groupes prives : leurs
                # liens d'invitation, dans le texte ou sous un mot.
                for msg in page.messages:
                    text = getattr(msg, "message", "") or ""
                    for entity in getattr(msg, "entities", None) or []:
                        text += " " + (getattr(entity, "url", "") or "")
                    for code in INVITE_RE.findall(text):
                        self.invites.setdefault(code, None)
                if not page.messages:
                    break
                last = page.messages[-1]
                when = getattr(last, "date", None)
                offset_rate = int(when.timestamp()) if when else 0
                offset_id = last.id
                offset_peer = await client.get_input_entity(last.peer_id)
                self.status.emit(f"{len(seen)} groupes publics trouvés…")

    async def _search(self, client) -> list:
        from telethon.errors import FloodWaitError
        from telethon.tl.types import Channel
        seen: dict[str, object] = {}
        self.invites = {}
        if self.query.strip():
            await self._collect(client, seen)
        for raw in self.pasted:
            code = INVITE_RE.search(raw)
            if code:
                self.invites.setdefault(code.group(1), None)
                continue
            name = username_of(raw)
            if not name:
                continue
            try:
                entity = await client.get_entity(name)
                if isinstance(entity, Channel):
                    seen[str(entity.id)] = entity
            except Exception as exc:  # noqa: BLE001
                seen[name] = {"title": name, "username": name, "error": str(exc)}
        rows = []
        for chat in seen.values():
            if self.stop:
                return rows
            if isinstance(chat, dict):
                rows.append({
                    "mode": "groupe", "title": chat["title"], "username": chat["username"],
                    "url": f"https://t.me/{chat['username']}", "kind": "?",
                    "abonnes": 0, "protege": False, "restreint": False,
                    "medias": 0, "jours": None, "dernier": chat["error"][:40],
                    "vivant": "?", "score": -1, "acces": "illisible",
                })
                continue
            row = await self._inspect(client, chat, FloodWaitError)
            if not row["username"]:
                continue
            row["mode"] = "groupe"
            row["acces"] = "à demander" if row["demande"] else "entrée libre"
            rows.append(row)
            self.status.emit(f"{len(rows)} groupes lus…")
        rows += await self._read_invites(client, FloodWaitError)
        rows.sort(key=lambda r: r.get("abonnes") or 0, reverse=True)
        return rows

    async def _read_invites(self, client, flood) -> list:
        """Chaque lien d'invitation lu sans y entrer : Telegram donne le nom,
        le nombre de membres, et s'il faut une approbation."""
        from telethon.tl.functions.messages import CheckChatInviteRequest
        from telethon.tl.types import ChatInvite
        rows = []
        codes = list(self.invites)[:40]
        for at, code in enumerate(codes, 1):
            if self.stop:
                break
            self.status.emit(f"Lecture des invitations privées : {at}/{len(codes)}")
            url = f"https://t.me/+{code}"
            try:
                info = await client(CheckChatInviteRequest(code))
            except flood as exc:
                self.status.emit(f"Telegram demande d'attendre {exc.seconds} s : "
                                 "invitations suivantes laissées de côté.")
                break
            except Exception:  # noqa: BLE001 — lien expire ou invalide
                continue
            if isinstance(info, ChatInvite):
                rows.append({
                    "mode": "groupe", "title": info.title, "username": "", "url": url,
                    "kind": "chaîne" if info.broadcast else "groupe",
                    "abonnes": int(info.participants_count or 0), "protege": False,
                    "restreint": False, "demande": bool(info.request_needed), "prive": True,
                    "medias": None, "jours": None, "dernier": "", "apercus": [],
                    "acces": "à demander" if info.request_needed else "invitation",
                })
            else:
                # Deja membre (ou apercu ouvert) : on peut lire le groupe.
                chat = getattr(info, "chat", None)
                if chat is not None:
                    row = await self._inspect(client, chat, flood)
                    row.update(mode="groupe", url=url, prive=not row.get("username"),
                               acces="déjà membre")
                    if not row.get("username"):
                        try:
                            row["medias"] = await self._count(client, chat)
                        except Exception:  # noqa: BLE001
                            pass
                    rows.append(row)
            await asyncio.sleep(0.6)
        return rows

    # -- compter les medias -----------------------------------------------------
    @staticmethod
    def _photo_video(kind: str = "all"):
        from telethon.tl.types import (InputMessagesFilterPhotos, InputMessagesFilterPhotoVideo,
                                       InputMessagesFilterVideo)
        return {"video": InputMessagesFilterVideo, "photo": InputMessagesFilterPhotos}.get(
            kind, InputMessagesFilterPhotoVideo)()

    async def _thumb(self, msg, path: Path) -> str:
        """La vignette d'un média : une taille moyenne pour une photo (pas
        la photo entière), la plus grande pour une vidéo (elle est petite)."""
        if path.exists():
            return str(path)
        for which in ((2, -1) if msg.photo else (-1,)):
            try:
                got = await msg.download_media(file=str(path), thumb=which)
                if got:
                    return str(path)
            except Exception:  # noqa: BLE001
                continue
        return ""

    async def _count(self, client, target, min_id: int = 0, kind: str = "all") -> int:
        """Photos et vidéos du groupe (après `min_id`), comptées par Telegram
        lui-même : une seule requête, quel que soit le nombre de messages."""
        kwargs = {"limit": 0, "filter": self._photo_video(kind)}
        if min_id:
            kwargs["min_id"] = int(min_id)
        found = await client.get_messages(target, **kwargs)
        return int(getattr(found, "total", 0) or 0)

    async def _top(self, client, target) -> int:
        """Le numéro du dernier message du groupe."""
        last = await client.get_messages(target, limit=1)
        return int(last[0].id) if last else 0

    # -- mes groupes -----------------------------------------------------------
    async def _dialogs(self, client) -> list:
        """Toutes les conversations de groupe du compte : chaînes,
        supergroupes ET petits groupes ; puis, pour chacun, combien de photos
        et vidéos il contient et combien n'ont pas encore été vues (après la
        dernière lecture dans Telegram, ou « Marquer comme vus »)."""
        from telethon.errors import FloodWaitError
        state = load_state()
        known = {r["key"]: r for r in state.get("joined") or []}
        rows, entities = [], {}
        async for dialog in client.iter_dialogs():
            if not (dialog.is_group or dialog.is_channel):
                continue
            ent = dialog.entity
            username = getattr(ent, "username", None) or ""
            small = not dialog.is_channel          # petit groupe (Chat)
            key = username or (f"g{ent.id}" if small else f"c{ent.id}")
            kind = "chaîne" if getattr(ent, "broadcast", False) else "groupe"
            before = known.get(key) or {}
            rows.append({
                "key": key,
                "title": dialog.name or username or str(ent.id),
                "username": username,
                "kind": kind,
                "membres": int(getattr(ent, "participants_count", 0) or 0),
                "non_lus": int(dialog.unread_count or 0),
                "activite": dialog.date.timestamp() if dialog.date else 0,
                "public": bool(username),
                "protege": bool(getattr(ent, "noforwards", False)),
                "read_max": int(getattr(dialog.dialog, "read_inbox_max_id", 0) or 0),
                "top": int(dialog.message.id) if dialog.message else 0,
                # les comptes d'avant restent affichés le temps du recomptage
                "medias": before.get("medias"),
            })
            entities[key] = ent
        rows.sort(key=lambda r: r["title"].lower())
        update_state(lambda s: (s.__setitem__("joined", rows),
                                s.__setitem__("joined_at", time.time())))
        self.joined_progress.emit(rows)
        unseen: dict[str, int] = {}
        for at, row in enumerate(rows, 1):
            key = row["key"]
            before = known.get(key) or {}
            # Rien de neuf dans le groupe depuis le dernier comptage (même
            # dernier message, même lecture) : ses comptes n'ont pas bougé.
            # Deux requêtes de moins par groupe, et Telegram moins sollicité.
            if (before.get("medias") is not None and before.get("top") == row["top"]
                    and before.get("read_max") == row["read_max"] and key in state["unseen"]):
                continue
            self.status.emit(f"Comptage des photos et vidéos : {at}/{len(rows)} — {row['title']}")
            # Vu : jusqu'à la dernière lecture dans Telegram, ou jusqu'à
            # « Marquer comme vus » si c'est plus loin.
            seen_from = max(int(state["seen_from"].get(key) or 0), row["read_max"])
            try:
                row["medias"] = await self._count(client, entities[key])
                unseen[key] = (await self._count(client, entities[key], seen_from)
                               if seen_from else row["medias"])
            except FloodWaitError as exc:
                if exc.seconds > 60:
                    link().pause(exc.seconds)
                    self.status.emit(f"Telegram demande d'attendre {exc.seconds} s : "
                                     "comptage arrêté, il reprendra au prochain « Actualiser ».")
                    break
                await asyncio.sleep(exc.seconds + 1)
            except Exception:  # noqa: BLE001 — un groupe illisible n'arrête pas les autres
                row["medias"] = row.get("medias")
            if at % 5 == 0 or at == len(rows):
                self.joined_progress.emit(rows)
            await asyncio.sleep(0.25)

        def keep(s: dict) -> None:
            s["joined"] = rows
            s["unseen"].update(unseen)
            for row in rows:
                s["top"][row["key"]] = row["top"]
        update_state(keep)
        return rows

    async def _join(self, client) -> list:
        """Rejoindre, ou demander à rejoindre : un nom public, ou un lien
        d'invitation t.me/+… (groupe privé)."""
        from telethon.errors import (FloodWaitError, InviteHashExpiredError,
                                     InviteRequestSentError, UserAlreadyParticipantError)
        from telethon.tl.functions.channels import JoinChannelRequest
        from telethon.tl.functions.messages import ImportChatInviteRequest
        out = []
        for raw in self.targets:
            invite = re.search(r"t\.me/(?:\+|joinchat/)([A-Za-z0-9_-]+)", raw)
            try:
                if invite:
                    await client(ImportChatInviteRequest(invite.group(1)))
                else:
                    await client(JoinChannelRequest(await client.get_entity(username_of(raw))))
                out.append((raw, "rejoint"))
            except InviteRequestSentError:
                out.append((raw, "demande envoyée"))
            except UserAlreadyParticipantError:
                out.append((raw, "déjà membre"))
            except InviteHashExpiredError:
                out.append((raw, "lien expiré"))
            except FloodWaitError as exc:
                out.append((raw, f"Telegram demande d'attendre {exc.seconds} s"))
                break
            except Exception as exc:  # noqa: BLE001
                out.append((raw, str(exc)[:80]))
            await asyncio.sleep(1.0)
        return out

    # -- alertes et telechargement automatique ---------------------------------
    async def _check(self, client) -> dict:
        """Pour chaque groupe suivi : combien de photos et vidéos non vues
        (Alerte), et le téléchargement de celles arrivées depuis qu'« Auto »
        est coché. Le journal note ce qui est arrivé depuis le passage
        précédent."""
        from telethon.errors import FloodWaitError
        state = load_state()
        joined = {r["key"]: r for r in state.get("joined") or []}
        wanted = [c for c in state["channels"] if c.get("alert") or c.get("auto")]
        if self.only:
            wanted = [c for c in wanted if c["key"] in self.only]
        news: dict[str, dict] = {}
        journal: list[dict] = []
        lines: list[dict] = []
        total = downloaded_total = 0
        for at, entry in enumerate(wanted, 1):
            key = entry["key"]
            title = entry.get("title") or key
            self.status.emit(f"Vérification {at}/{len(wanted)} : {title}")
            note = ""
            got = 0
            folder = ""
            try:
                target = await self._resolve(client, key)
                top = await self._top(client, target)
                # Un groupe du compte : vu jusqu'à la dernière lecture dans
                # Telegram (ou « Marquer comme vus », si c'est plus loin). Une
                # chaîne ajoutée à la main, sans lecture : depuis son ajout.
                marked = int(state["seen_from"].get(key) or 0)
                read_max = int(joined.get(key, {}).get("read_max") or 0)
                seen_from = max(marked, read_max) or top
                store_seen = not marked and not read_max
                unseen = await self._count(client, target, seen_from)
                medias = await self._count(client, target)
                before = state["unseen"].get(key)
                arrived = max(0, unseen - int(before)) if before is not None else 0
                auto_from = state["auto_from"].get(key)
                if entry.get("auto"):
                    if auto_from is None:
                        auto_from = top
                        note = "Auto en place : ce qui arrive désormais sera téléchargé"
                    else:
                        fresh = [m async for m in client.iter_messages(
                            target, min_id=int(auto_from), filter=self._photo_video(), limit=300)]
                        if fresh:
                            for n, msg in enumerate(reversed(fresh), 1):   # du plus ancien
                                self.status.emit(f"{title} : téléchargement {n}/{len(fresh)}")
                                try:
                                    saved = await self._save(msg, title, key)
                                    if saved:
                                        got += 1
                                        folder = str(Path(saved).parent)
                                except FloodWaitError as exc:
                                    await asyncio.sleep(min(exc.seconds, 30))
                                except Exception:  # noqa: BLE001
                                    continue
                            arrived = max(arrived, len(fresh))
                        auto_from = max([int(auto_from)] + [m.id for m in fresh])
                news[key] = {"unseen": unseen, "top": top, "auto_from": auto_from, "medias": medias,
                             "seen_from": seen_from if store_seen else None}
            except FloodWaitError as exc:
                await asyncio.sleep(min(exc.seconds, 20))
                lines.append({"key": key, "nouveaux": 0, "note": f"Telegram demande {exc.seconds} s"})
                continue
            except Exception as exc:  # noqa: BLE001
                lines.append({"key": key, "nouveaux": 0, "note": str(exc)[:60]})
                continue
            total += arrived
            downloaded_total += got
            if arrived or got:
                journal.append({"when": time.time(), "key": key, "title": title,
                                "nouveaux": arrived, "telecharges": got, "dossier": folder})
            lines.append({"key": key, "nouveaux": arrived, "telecharges": got, "note": note,
                          "non_vus": unseen})

        now = time.time()

        def merge(s: dict) -> None:
            for k, n in news.items():
                s["unseen"][k] = n["unseen"]
                s["top"][k] = n["top"]
                s["medias"][k] = n["medias"]
                if n["seen_from"]:
                    s["seen_from"].setdefault(k, n["seen_from"])
                if n["auto_from"] is not None:
                    s["auto_from"][k] = n["auto_from"]
                s["checked"][k] = now
            s["journal"] = (list(reversed(journal)) + s["journal"])[:JOURNAL_MAX]
        update_state(merge)
        return {"total": total, "telecharges": downloaded_total, "lines": lines,
                "journal": journal}

    def _target(self, msg, title: str, key: str) -> tuple[Path, Path | None]:
        """Où va le média (« Groupe - Vids » ou « Groupe - Pix »), et le
        fichier déjà sur le disque s'il y est : même nom et même taille, dans
        son dossier ou dans l'ancien dossier unique du groupe."""
        size = (msg.file.size if msg.file else 0) or 0
        name = (msg.file.name if msg.file else "") or ""
        ext = (msg.file.ext if msg.file else "") or (".jpg" if msg.photo else ".mp4")
        if not name:
            stamp = msg.date.astimezone().strftime("%Y-%m-%d_%H-%M") if msg.date else "media"
            name = f"{stamp}_{msg.id}{ext}"
        name = safe_name(name)
        folder = media_folder(self.state, title, key, msg_is_video(msg))
        for place in (folder, channel_folder(self.state, title, key)):
            old = place / name
            if old.exists() and (not size or old.stat().st_size == size):
                return folder / name, old
        path = folder / name
        if path.exists():
            path = folder / f"{path.stem}_{msg.id}{path.suffix}"
        return path, None

    async def _save(self, msg, title: str, key: str) -> str:
        """Le nouveau fichier, ou "" s'il était déjà là."""
        path, existing = self._target(msg, title, key)
        if existing is not None:
            return ""
        path.parent.mkdir(parents=True, exist_ok=True)
        got = await msg.download_media(file=str(path))
        return str(got or "")

    # -- banque de medias ------------------------------------------------------
    async def _bank(self, client) -> list:
        """Une page de médias du groupe, lue dans Telegram, vignettes en local.
        Les vignettes arrivent par paquets (lus ensemble) : la fenêtre se
        remplit tout de suite. À la première page, les comptes du groupe."""
        from telethon.errors import FloodWaitError
        THUMBS.mkdir(parents=True, exist_ok=True)
        self.got = 0
        items = []
        kwargs = {"limit": BANK_PAGE}
        if self.before_id:
            kwargs["offset_id"] = self.before_id
        try:
            target = await self._resolve(client, self.channel)
            # Les photos et vidéos seulement, filtrées par Telegram : une
            # seule requête pour toute la page.
            msgs = await client.get_messages(target, filter=self._photo_video(self.kind), **kwargs)
            self.got = len(msgs)
            # Un petit premier paquet (les premières vignettes en une
            # seconde), puis de plus gros.
            cuts = [0, 6] + list(range(18, len(msgs), 12)) + [len(msgs)]
            for start, end in zip(cuts, cuts[1:]):
                batch = msgs[start:end]
                if not batch:
                    continue
                thumbs = [THUMBS / f"{safe_name(self.channel)}_{m.id}.jpg" for m in batch]
                try:
                    await asyncio.gather(*(self._thumb(m, t) for m, t in zip(batch, thumbs)
                                           if not self._too_short(m)))
                except FloodWaitError as exc:
                    await asyncio.sleep(min(exc.seconds, 15))
                part = [self._bank_item(m, t) for m, t in zip(batch, thumbs)]
                items += part
                self.bank_part.emit(part)
                if start == 0 and not self.before_id:
                    totals = {self.kind: int(getattr(msgs, "total", 0) or 0)}
                    for kind in ("all", "video", "photo"):
                        if kind not in totals:
                            totals[kind] = await self._count(client, target, kind=kind)
                    self.bank_totals.emit(totals)
            if not msgs and not self.before_id:
                self.bank_totals.emit({"all": 0, "video": 0, "photo": 0})
        except FloodWaitError as exc:
            self.status.emit(f"Telegram demande d'attendre {exc.seconds} s.")
        return items

    @staticmethod
    def _bank_item(msg, thumb: Path) -> dict:
        file = msg.file
        mime = (file.mime_type or "") if file else ""
        return {
            "id": msg.id,
            "date": msg.date.astimezone().strftime("%d/%m/%y %H:%M") if msg.date else "",
            "title": (msg.text or (file.name if file else "") or "").replace("\n", " ")[:140],
            "size": (file.size if file else 0) or 0,
            "duration": float(getattr(file, "duration", 0) or 0) if file else 0.0,
            "height": int(getattr(file, "height", 0) or 0) if file else 0,
            "mime": mime,
            "video": bool(msg.video) or mime.startswith("video"),
            "thumb": str(thumb) if thumb.exists() else "",
        }

    def _too_short(self, msg) -> bool:
        """Une vidéo sous la durée choisie (les photos passent toujours)."""
        if not self.min_duration or not msg_is_video(msg) or not msg.file:
            return False
        return float(getattr(msg.file, "duration", 0) or 0) < self.min_duration

    async def _get(self, msg) -> tuple[str, bool]:
        """Télécharge le média : (le fichier, nouveau ?). Déjà là : le fichier
        existant, pas nouveau. Déjà dans la bibliothèque : rien."""
        name = (msg.file.name if msg.file else "") or ""
        size = (msg.file.size if msg.file else 0) or 0
        if self.skip_known and already_in_library(name, size, self.known):
            self.skipped += 1
            return "", False
        path, existing = self._target(msg, self.title, self.channel)
        if existing is not None:
            self.skipped += 1
            return str(existing), False
        path.parent.mkdir(parents=True, exist_ok=True)
        got = await msg.download_media(file=str(path))
        if got and name and size:
            self.known.add((name.lower(), int(size)))
        return str(got or ""), bool(got)

    def _tell(self, done: int, total: int, new: int, state: str = "run", folder: str = "") -> None:
        self.progress.emit({"title": self.title, "key": self.channel, "kind": self.kind,
                            "done": done, "total": total, "new": new,
                            "skipped": self.skipped, "short": self.short, "state": state,
                            "folder": folder})

    async def _download(self, client) -> str:
        target = await self._resolve(client, self.channel)
        msg = await client.get_messages(target, ids=self.message_id)
        if msg is None:
            return ""
        path, _new = await self._get(msg)
        return path

    async def _fetch_all(self, client, msgs, total: int) -> int:
        """Le cœur des gros téléchargements : un par un, avec l'avancement,
        jusqu'au bout ou jusqu'à « Arrêter »."""
        from telethon.errors import FloodWaitError
        n = done = 0
        last = ""
        self._tell(0, total, 0)
        async for msg in msgs:
            if self.stop:
                break
            if msg is None:
                continue
            if self._too_short(msg):
                self.short += 1
                done += 1
                self._tell(done, total, n)
                continue
            try:
                path, new = await self._get(msg)
            except FloodWaitError as exc:
                self.status.emit(f"Telegram demande d'attendre {exc.seconds} s…")
                await asyncio.sleep(exc.seconds + 1)
                path, new = await self._get(msg)
            except Exception as exc:  # noqa: BLE001 — un fichier illisible n'arrête pas le reste
                self.status.emit(f"Sauté (illisible) : {str(exc)[:60]}")
                path, new = "", False
            done += 1
            n += new
            if path:
                last = str(Path(path).parent)
            self._tell(done, total, n)
        self._tell(done, total, n, "stopped" if self.stop else "done", last)
        return n

    async def _download_many(self, client) -> int:
        if self.skip_known and not self.known:
            self._tell(0, len(self.message_ids), 0, "library")
            self.known = library_keys()
        self.skipped = 0
        target = await self._resolve(client, self.channel)
        msgs = await client.get_messages(target, ids=list(self.message_ids))

        async def each():
            for msg in msgs:
                yield msg
        return await self._fetch_all(client, each(), len(self.message_ids))

    async def _export(self, client) -> int:
        """Photos et vidéos du groupe, sans reprendre ce qui est déjà sur le disque."""
        if self.skip_known and not self.known:
            self._tell(0, 0, 0, "library")
            self.known = library_keys()
        self.skipped = 0
        target = await self._resolve(client, self.channel)
        total = await self._count(client, target, kind=self.kind)
        msgs = client.iter_messages(target, limit=None, filter=self._photo_video(self.kind))
        return await self._fetch_all(client, msgs, total)


def _cell(text, sort=None, tip: str = "") -> QTableWidgetItem:
    item = _SortItem(str(text))
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    if sort is not None:
        item.setData(Qt.UserRole + 1, sort)
    if tip:
        item.setToolTip(tip)
    return item


class _SortItem(QTableWidgetItem):
    """Trie sur la valeur cachée quand il y en a une (nombres, dates)."""

    def __lt__(self, other):
        a, b = self.data(Qt.UserRole + 1), other.data(Qt.UserRole + 1)
        if a is not None and b is not None:
            return a < b
        return self.text().lower() < other.text().lower()


def _strip(paths: list[str]):
    """Les vignettes des trois derniers médias, côte à côte, en une icône."""
    from PySide6.QtGui import QColor, QPainter
    pixes = [QPixmap(p) for p in paths[:3]]
    pixes = [p for p in pixes if not p.isNull()]
    if not pixes:
        return None
    w, h, gap = 92, 52, 4
    out = QPixmap(3 * w + 2 * gap, h)
    out.fill(QColor(0, 0, 0, 0))
    painter = QPainter(out)
    for i, pix in enumerate(pixes):
        scaled = pix.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        x0 = (scaled.width() - w) // 2
        y0 = (scaled.height() - h) // 2
        painter.drawPixmap(i * (w + gap), 0, scaled, x0, y0, w, h)
    painter.end()
    return QIcon(out)


def _table(parent, headers: list[str], stretch: int) -> QTableWidget:
    table = QTableWidget(0, len(headers), parent)
    table.setHorizontalHeaderLabels(headers)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    table.horizontalHeader().setSectionResizeMode(stretch, QHeaderView.Stretch)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.verticalHeader().hide()
    table.setAlternatingRowColors(False)
    table.setWordWrap(False)
    return table


class _DownloadBar(QWidget):
    """L'avancement des téléchargements : dans la fenêtre Telegram et dans
    celle des médias, la même barre. Cachée tant que rien ne tourne."""

    def __init__(self, owner: "TelegramDialog", parent=None):
        super().__init__(parent)
        self.owner = owner
        self.folder: Path | None = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        self.label = QLabel("", self)
        self.label.setObjectName("tgTotal")
        lay.addWidget(self.label, 1)
        self.bar = QProgressBar(self)
        self.bar.setFixedWidth(260)
        self.bar.setTextVisible(True)
        lay.addWidget(self.bar)
        self.stop = QPushButton("Arrêter", self)
        self.stop.clicked.connect(owner.stop_downloads)
        lay.addWidget(self.stop)
        self.open = QPushButton("Ouvrir le dossier", self)
        self.open.clicked.connect(lambda: self.folder and owner._open_folder(self.folder))
        lay.addWidget(self.open)
        self.hide()
        owner.download_info.connect(self.show_info)
        if owner.last_download:
            self.show_info(owner.last_download)

    def show_info(self, info: dict) -> None:
        state = info.get("state")
        done, total = int(info.get("done") or 0), int(info.get("total") or 0)
        what = {"video": "vidéos", "photo": "photos"}.get(info.get("kind"), "médias")
        title = info.get("title") or ""
        waiting = int(info.get("waiting") or 0)
        more = f"  (+{waiting} en attente)" if waiting else ""
        running = state in ("wait", "library", "run")
        self.stop.setVisible(running)
        self.bar.setVisible(running)
        self.folder = Path(info["folder"]) if info.get("folder") else None
        self.open.setVisible(not running and self.folder is not None)
        if state == "wait":
            self.label.setText(f"⏳ En attente : {what} de « {title} »{more}")
            self.bar.setRange(0, 0)
        elif state == "library":
            self.label.setText(f"⬇ « {title} » : lecture de la bibliothèque, pour sauter "
                               f"ce que tu as déjà…{more}")
            self.bar.setRange(0, 0)
        elif state == "run":
            short = f", {info['short']} trop courts" if info.get("short") else ""
            self.label.setText(f"⬇ Téléchargement des {what} de « {title} » : {done} / "
                               f"{total or '?'} — {info.get('new', 0)} nouveaux, "
                               f"{info.get('skipped', 0)} déjà là{short}{more}")
            if total:
                self.bar.setRange(0, total)
                self.bar.setValue(min(done, total))
            else:
                self.bar.setRange(0, 0)
        elif state in ("done", "stopped"):
            head = "✔ Fini" if state == "done" else "■ Arrêté"
            self.label.setText(f"{head} — « {title} » : {info.get('new', 0)} nouveaux, "
                               f"{info.get('skipped', 0)} déjà là{more}")
        elif state == "failed":
            self.label.setText(f"✖ Échec — « {title} » : {info.get('error', '')}"[:200])
        self.setVisible(True)


class TelegramDialog(QDialog):
    download_info = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Prisme — Telegram")
        self.setStyleSheet(STYLE)
        self.resize(1180, 760)
        self.state = load_state()
        self.found: list[dict] = []
        self.rows: list[dict] = []
        self.mode = "groupe"
        self.last_download: dict = {}
        self._bank_window = None
        box = QVBoxLayout(self)
        top = QHBoxLayout()
        head = QLabel("Telegram", self)
        head.setObjectName("tgHead")
        top.addWidget(head)
        self.who = QLabel("", self)
        self.who.setObjectName("tgWho")
        top.addWidget(self.who)
        top.addStretch(1)
        box.addLayout(top)
        self.status = QLabel("", self)
        self.status.setObjectName("tgStatus")
        self.status.setWordWrap(True)
        box.addWidget(self.status)
        box.addWidget(_DownloadBar(self, self))
        self.tabs = QTabWidget(self)
        self.tabs.addTab(self._groups_tab(), "Mes groupes")
        self.tabs.addTab(self._search_tab(), "Recherche")
        self.tabs.addTab(self._journal_tab(), "Journal")
        self.tabs.addTab(self._account_tab(), "Compte")
        box.addWidget(self.tabs, 1)
        self._show_who()
        self._fill_groups()
        self._fill_journal()
        hub().checked.connect(self._on_checked)
        if not (self.state.get("api_id") and self.state.get("api_hash")):
            self.tabs.setCurrentIndex(3)
            self.status.setText("Renseigne d'abord le compte (api_id, api_hash, téléphone), puis « Connecter ».")
        else:
            self.status.setText("Coche « Alerte » sur les groupes à surveiller, « Auto » pour "
                                "télécharger leurs nouveaux médias dès qu'ils arrivent.")
            # La liste du compte se met à jour seule à l'ouverture (elle est
            # gardée : on la voit tout de suite, puis elle se rafraîchit).
            stale = time.time() - float(self.state.get("joined_at") or 0) > 600
            if SESSION_PATH.with_suffix(".session").exists():
                api_id = str(self.state.get("api_id") or "")
                link().warm(int(api_id) if api_id.isdigit() else 0,
                            self.state.get("api_hash") or "")
                if stale or not self.state["joined"]:
                    QTimer.singleShot(300, self.load_groups)

    # == onglets =================================================================
    def _groups_tab(self) -> QWidget:
        page = QWidget(self)
        lay = QVBoxLayout(page)
        bar = QHBoxLayout()
        self.group_filter = QLineEdit(page)
        self.group_filter.setPlaceholderText("Chercher dans mes groupes…")
        self.group_filter.setClearButtonEnabled(True)
        self.group_filter.textChanged.connect(lambda _t: self._fill_groups())
        bar.addWidget(self.group_filter, 1)
        self.only_followed = QCheckBox("Seulement ceux que je suis", page)
        self.only_followed.toggled.connect(lambda _on: self._fill_groups())
        bar.addWidget(self.only_followed)
        self.only_media = QCheckBox("Seulement avec des médias", page)
        self.only_media.toggled.connect(lambda _on: self._fill_groups())
        bar.addWidget(self.only_media)
        reload_ = QPushButton("Actualiser la liste", page)
        reload_.setToolTip("Relire dans Telegram les groupes et chaînes du compte.")
        reload_.clicked.connect(self.load_groups)
        bar.addWidget(reload_)
        check = QPushButton("Vérifier maintenant", page)
        check.setObjectName("tgPrimary")
        check.setToolTip("Recompter les médias non vus des groupes en Alerte, "
                         "et télécharger ceux arrivés dans les groupes en Auto.")
        check.clicked.connect(self.check_now)
        bar.addWidget(check)
        lay.addLayout(bar)
        lead = QLabel(
            "<b>Médias</b> : les photos et vidéos du groupe. <b>Non vus</b> : celles arrivées "
            "depuis ta dernière lecture dans Telegram (ou « Marquer comme vus »). "
            "<b>Messages non lus</b> : tous les messages, texte compris, comme dans Telegram.<br>"
            "<b>Alerte</b> : Prisme te prévient quand il arrive des photos ou vidéos. "
            "<b>Auto</b> : il les télécharge aussi, dès qu'elles arrivent (les anciennes : "
            "« Tout télécharger »). Clic sur un titre de colonne : trier.", page)
        lead.setObjectName("tgLead")
        lead.setWordWrap(True)
        lay.addWidget(lead)
        self.total = QLabel("", page)
        self.total.setObjectName("tgTotal")
        lay.addWidget(self.total)
        # Ce qui tourne en fond (recomptage, vérifications) : à part, pour ne
        # pas couvrir la réponse aux clics.
        self.background = QLabel("", page)
        self.background.setObjectName("tgLead")
        lay.addWidget(self.background)
        self.groups = _table(page, ["Alerte", "Auto", "Nom", "Type", "Membres", "Médias",
                                    "Non vus", "Messages non lus", "Activité", "Telegram"], 2)
        for col, tip in ((5, "Photos et vidéos du groupe, en tout"),
                         (6, "Photos et vidéos arrivées depuis ta dernière lecture"),
                         (7, "Messages non lus dans Telegram, texte compris"),
                         (8, "Dernier message du groupe"),
                         (9, "Un clic : ouvrir le groupe dans Telegram")):
            self.groups.horizontalHeaderItem(col).setToolTip(tip)
        for col in (0, 1):
            self.groups.horizontalHeader().setSectionResizeMode(col, QHeaderView.Fixed)
            self.groups.setColumnWidth(col, 64)
        self.groups.setSortingEnabled(True)
        self.groups.itemChanged.connect(self._group_flag)
        self.groups.itemClicked.connect(self._group_clicked)
        self.groups.itemDoubleClicked.connect(
            lambda item: item.column() not in (0, 1, 9) and self.open_group_bank())
        lay.addWidget(self.groups, 1)
        row = QHBoxLayout()
        bank = QPushButton("Voir les médias", page)
        bank.setObjectName("tgPrimary")
        bank.clicked.connect(self.open_group_bank)
        row.addWidget(bank)
        tg = QPushButton("Ouvrir dans Telegram", page)
        tg.clicked.connect(self.open_group_telegram)
        row.addWidget(tg)
        folder = QPushButton("Ouvrir le dossier", page)
        folder.clicked.connect(self.open_group_folder)
        row.addWidget(folder)
        export = QPushButton("Tout télécharger", page)
        export.setToolTip("Toutes les photos et vidéos du groupe ; ce qui est déjà sur "
                          "le disque est sauté.")
        export.clicked.connect(self.export_group)
        row.addWidget(export)
        seen = QPushButton("Marquer comme vus", page)
        seen.setToolTip("Les groupes choisis (ou tous, si aucun) : leurs médias non vus "
                        "repartent de zéro. Rien ne change dans Telegram.")
        seen.clicked.connect(self.mark_seen)
        row.addWidget(seen)
        row.addStretch(1)
        self.add_url = QLineEdit(page)
        self.add_url.setPlaceholderText("Ajouter une chaîne publique : t.me/…")
        self.add_url.setMinimumWidth(300)
        self.add_url.returnPressed.connect(self.add_channel)
        row.addWidget(self.add_url)
        plus = QPushButton("Ajouter", page)
        plus.clicked.connect(self.add_channel)
        row.addWidget(plus)
        drop = QPushButton("Retirer", page)
        drop.setToolTip("Retirer une chaîne ajoutée à la main (pas un groupe du compte).")
        drop.clicked.connect(self.remove_channel)
        row.addWidget(drop)
        lay.addLayout(row)
        return page

    def _search_tab(self) -> QWidget:
        page = QWidget(self)
        lay = QVBoxLayout(page)
        row = QHBoxLayout()
        self.query = QLineEdit(page)
        self.query.setPlaceholderText("Mot-clé…")
        self.query.returnPressed.connect(self.search_groups)
        row.addWidget(self.query, 1)
        groups = QPushButton("Chercher des groupes", page)
        groups.setObjectName("tgPrimary")
        groups.setToolTip("Les groupes et chaînes dont le nom ou les messages contiennent "
                          "le mot : ouverts, ou 🔒 sur demande ; plus les groupes privés dont "
                          "les messages trouvés donnent l'invitation (t.me/+…). Avec le nombre "
                          "de photos et vidéos et l'aperçu des trois dernières.")
        groups.clicked.connect(self.search_groups)
        row.addWidget(groups)
        messages = QPushButton("Dans les messages", page)
        messages.setToolTip("Les messages publics qui contiennent le mot, avec leur "
                            "groupe et leur lien.")
        messages.clicked.connect(self.search_messages)
        row.addWidget(messages)
        web = QPushButton("Liens sur le web", page)
        web.setToolTip("Cherche sur le web (DuckDuckGo, Bing, annuaires, forums) des liens "
                       "de groupes sur ce thème, invitations privées t.me/+… comprises,\npuis "
                       "les lit dans Telegram : nom, membres, approbation. Sans y entrer.")
        web.clicked.connect(self.search_web)
        row.addWidget(web)
        lay.addLayout(row)
        self.paste_toggle = QPushButton("Coller des liens t.me…", page)
        self.paste_toggle.setCheckable(True)
        self.paste_toggle.setToolTip("Une liste de liens copiés d'un annuaire (tgstat.com…) : "
                                     "« Groupes publics » les lit et les note.")
        self.pasted = QTextEdit(page)
        self.pasted.setPlaceholderText("Un lien par ligne : https://t.me/nom  — ou une "
                                       "invitation discord.gg/…")
        self.pasted.setFixedHeight(90)
        self.pasted.hide()
        self.paste_toggle.toggled.connect(self.pasted.setVisible)
        lay.addWidget(self.paste_toggle, 0, Qt.AlignLeft)
        lay.addWidget(self.pasted)
        self.filters = QWidget(page)
        filt = QHBoxLayout(self.filters)
        filt.setContentsMargins(0, 0, 0, 0)
        self.f_free = QCheckBox("entrée libre", page)
        self.f_free.setChecked(True)
        self.f_ask = QCheckBox("sur demande", page)
        self.f_ask.setChecked(True)
        self.f_hide_protected = QCheckBox("masquer les protégés", page)
        self.f_hide_protected.setToolTip("Un groupe protégé interdit de télécharger ses médias.")
        self.f_hide_protected.setChecked(True)
        self.f_alive = QCheckBox("actifs seulement", page)
        self.min_subs = QSpinBox(page)
        self.min_subs.setRange(0, 1_000_000)
        self.min_subs.setSingleStep(100)
        self.min_subs.setPrefix("abonnés ≥ ")
        self.min_medias = QSpinBox(page)
        self.min_medias.setRange(0, 1_000_000)
        self.min_medias.setSingleStep(10)
        self.min_medias.setValue(0)
        self.min_medias.setPrefix("médias ≥ ")
        self.min_medias.setToolTip("Photos et vidéos du groupe, en tout.")
        for widget in (self.f_free, self.f_ask, self.f_hide_protected, self.f_alive):
            widget.toggled.connect(self._apply_filter)
            filt.addWidget(widget)
        for spin in (self.min_subs, self.min_medias):
            spin.valueChanged.connect(lambda _v: self._apply_filter())
            filt.addWidget(spin)
        filt.addStretch(1)
        lay.addWidget(self.filters)
        self.table = _table(page, [], 1)
        self.table.setSortingEnabled(True)
        self.table.setIconSize(QSize(3 * 92 + 8, 52))
        self.table.itemDoubleClicked.connect(lambda _i: self._result_open())
        lay.addWidget(self.table, 1)
        row2 = QHBoxLayout()
        self.r_join = QPushButton("Rejoindre / demander", page)
        self.r_join.setToolTip("Entrer dans les groupes choisis ; pour un groupe 🔒 sur "
                               "demande, la demande part, et il apparaîtra dans « Mes groupes » "
                               "une fois acceptée.")
        self.r_join.clicked.connect(self.join_selected)
        row2.addWidget(self.r_join)
        self.r_follow = QPushButton("Ajouter à mes alertes", page)
        self.r_follow.clicked.connect(self.save_selected)
        row2.addWidget(self.r_follow)
        self.r_bank = QPushButton("Voir les médias", page)
        self.r_bank.setObjectName("tgPrimary")
        self.r_bank.clicked.connect(self.open_selected_bank)
        row2.addWidget(self.r_bank)
        self.r_analyse = QPushButton("Analyser la sélection", page)
        self.r_analyse.setToolTip("Lire ces liens dans Telegram : abonnés, médias, activité.")
        self.r_analyse.clicked.connect(self.analyse_selected)
        row2.addWidget(self.r_analyse)
        self.r_export = QPushButton("Tout télécharger", page)
        self.r_export.clicked.connect(self.export_selected)
        row2.addWidget(self.r_export)
        link = QPushButton("Ouvrir dans Telegram", page)
        link.clicked.connect(self.open_selected_link)
        row2.addWidget(link)
        row2.addStretch(1)
        lay.addLayout(row2)
        self._set_mode("groupe")
        return page

    def _journal_tab(self) -> QWidget:
        page = QWidget(self)
        lay = QVBoxLayout(page)
        lead = QLabel("Chaque passage qui a trouvé du nouveau. Double-clic : ouvrir le dossier "
                      "où les médias sont arrivés.", page)
        lead.setObjectName("tgLead")
        lay.addWidget(lead)
        self.journal = _table(page, ["Quand", "Groupe", "Nouveaux médias", "Téléchargés",
                                     "Dossier"], 4)
        self.journal.itemDoubleClicked.connect(lambda _i: self.open_journal_folder())
        lay.addWidget(self.journal, 1)
        row = QHBoxLayout()
        folder = QPushButton("Ouvrir le dossier", page)
        folder.setObjectName("tgPrimary")
        folder.clicked.connect(self.open_journal_folder)
        row.addWidget(folder)
        bank = QPushButton("Voir les médias du groupe", page)
        bank.clicked.connect(self.open_journal_bank)
        row.addWidget(bank)
        row.addStretch(1)
        clear = QPushButton("Vider le journal", page)
        clear.clicked.connect(self.clear_journal)
        row.addWidget(clear)
        lay.addLayout(row)
        return page

    def _account_tab(self) -> QWidget:
        page = QWidget(self)
        outer = QVBoxLayout(page)
        lead = QLabel("Ton compte Telegram. api_id et api_hash se créent sur my.telegram.org "
                      "(« API development tools »). Ils restent sur ce PC.", page)
        lead.setObjectName("tgLead")
        lead.setWordWrap(True)
        outer.addWidget(lead)
        form = QFormLayout()
        self.api_id = QLineEdit(str(self.state.get("api_id") or ""), page)
        self.api_hash = QLineEdit(self.state.get("api_hash") or "", page)
        self.api_hash.setEchoMode(QLineEdit.Password)
        self.phone = QLineEdit(self.state.get("phone") or "", page)
        self.phone.setPlaceholderText("+33…")
        form.addRow("api_id", self.api_id)
        form.addRow("api_hash", self.api_hash)
        form.addRow("Téléphone", self.phone)
        where = QHBoxLayout()
        self.download_dir = QLineEdit(str(download_root(self.state)), page)
        self.download_dir.setToolTip("Chaque groupe y a deux dossiers, à son nom : "
                                     "« Groupe - Vids » et « Groupe - Pix ».")
        where.addWidget(self.download_dir, 1)
        pick = QPushButton("Choisir…", page)
        pick.clicked.connect(self._pick_dir)
        where.addWidget(pick)
        form.addRow("Téléchargements", where)
        self.interval = QSpinBox(page)
        self.interval.setRange(5, 720)
        self.interval.setSuffix(" min")
        self.interval.setValue(int(self.state.get("interval_min") or 10))
        self.interval.setToolTip("Prisme vérifie les groupes en alerte à ce rythme, "
                                 "même cette fenêtre fermée.")
        form.addRow("Vérifier toutes les", self.interval)
        outer.addLayout(form)
        row = QHBoxLayout()
        save = QPushButton("Enregistrer", page)
        save.clicked.connect(self._save_account)
        row.addWidget(save)
        login = QPushButton("Connecter", page)
        login.setObjectName("tgPrimary")
        login.clicked.connect(lambda: self._start("login"))
        row.addWidget(login)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)
        return page

    # == compte ==================================================================
    def _show_who(self) -> None:
        me = self.state.get("me") or ""
        self.who.setText(f"● connecté : {me}" if me else "")

    def _pick_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Dossier des téléchargements Telegram",
                                                  self.download_dir.text())
        if chosen:
            self.download_dir.setText(chosen)
            self._save_account()

    def _save_account(self) -> None:
        values = {
            "api_id": self.api_id.text().strip(),
            "api_hash": self.api_hash.text().strip(),
            "phone": self.phone.text().strip(),
            "download_dir": "" if Path(self.download_dir.text().strip() or ".") == DOWNLOADS
            else self.download_dir.text().strip(),
            "interval_min": self.interval.value(),
        }
        self.state = update_state(lambda s: s.update(values))
        watch = getattr(self.parent(), "_telegram_watch", None)
        if watch is not None:
            watch.setInterval(max(5, values["interval_min"]) * 60 * 1000)

    # == les travaux =============================================================
    def _worker(self, job: str) -> TelegramWorker:
        self._save_account()
        worker = TelegramWorker(self.state, job)
        worker.status.connect({"fond": self._on_background, "dl": self._on_dl_status}.get(
            worker.lane, self._on_status))
        worker.failed.connect(self._failed)
        worker.need_code.connect(self._ask_code)
        worker.need_password.connect(self._ask_password)
        self._current = worker
        return worker

    def _on_status(self, text: str) -> None:
        worker = self.sender()
        if isinstance(worker, TelegramWorker) and worker.stop:
            return          # une recherche remplacée ne parle plus
        self.status.setText(text)
        window = self._bank_window
        if window is not None and window.isVisible():
            window.note.setText(text)
        if text.startswith("Connecté"):
            self.state["me"] = load_state().get("me") or ""
            self._show_who()

    def _on_background(self, text: str) -> None:
        self.background.setText(f"En fond : {text}" if text else "")

    def _fond_finished(self) -> None:
        if not queue("fond").busy():
            self._on_background("")

    def _on_dl_status(self, text: str) -> None:
        window = self._bank_window
        if window is not None and window.isVisible():
            window.note.setText(text)

    def _start(self, job: str, **settings) -> TelegramWorker:
        before = self.status.text()
        worker = self._worker(job)
        for name, value in settings.items():
            setattr(worker, name, value)
        worker.search_done.connect(self._show_search)
        worker.joined_done.connect(self._show_joined)
        worker.joined_progress.connect(self._joined_progress)
        worker.join_done.connect(self._joined_result)
        worker.checked.connect(lambda report: hub().checked.emit(report))
        worker.bank_done.connect(self._open_bank)
        worker.bank_part.connect(self._bank_part)
        worker.bank_totals.connect(self._bank_totals)
        worker.file_ready.connect(self._play)
        worker.batch_done.connect(self._batch_done)
        worker.progress.connect(self._on_progress)
        lane = queue(worker.lane)
        if worker.lane == "fond":
            worker.finished.connect(self._fond_finished)
        elif worker.lane == "dl":
            worker.finished.connect(self._dl_finished)
            if lane.busy():
                self._on_progress({"title": worker.title, "kind": worker.kind, "state": "wait"},
                                  worker)
        elif worker.lane == "recherche" and lane.busy():
            # Une nouvelle recherche remplace l'ancienne : pas de file.
            for old in lane.waiting:
                old.stop = True
            lane.waiting.clear()
            if lane.current is not None:
                lane.current.stop = True
        if worker.lane in ("ui", "recherche", "lecture") and lane.busy():
            worker.start_text = before
            worker.started.connect(self._job_started)
            self.status.setText(f"⏳ {before.rstrip('…. ')} — en attente : "
                                f"{LANE_NAMES[worker.lane]} se termine, ça suit tout seul.")
        lane.launch(worker)
        return worker

    def _job_started(self) -> None:
        worker = self.sender()
        if isinstance(worker, TelegramWorker) and getattr(worker, "start_text", ""):
            self.status.setText(worker.start_text)

    def _ask_code(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        worker = self.sender()
        code, ok = QInputDialog.getText(self, "Code Telegram", "Code reçu dans Telegram :")
        if isinstance(worker, TelegramWorker):
            worker.give_code(code if ok else "")

    def _ask_password(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        worker = self.sender()
        password, ok = QInputDialog.getText(
            self, "Mot de passe Telegram", "Mot de passe 2FA :", QLineEdit.Password)
        if isinstance(worker, TelegramWorker):
            worker.give_password(password if ok else "")

    # == telechargements ==========================================================
    def _on_progress(self, info: dict, worker=None) -> None:
        worker = worker or self.sender()
        lane = queue("dl")
        info = dict(info, waiting=len(lane.waiting))
        if info.get("state") == "wait" and lane.current is not None and self.last_download:
            # La barre garde le travail en cours ; elle dit seulement qu'un
            # autre attend son tour.
            info = dict(self.last_download, waiting=len(lane.waiting) + 1)
        self.last_download = info
        self.download_info.emit(info)
        if info.get("state") in ("done", "stopped") and worker is not None:
            self._announce_download(info)

    def _dl_finished(self) -> None:
        worker = self.sender()
        state = self.last_download.get("state")
        if isinstance(worker, TelegramWorker) and state not in ("done", "stopped", "failed") \
                and self.last_download.get("key") in (worker.channel, None):
            # Le travail s'est arrêté sans dire « fini » : une erreur.
            self._on_progress({"title": worker.title, "key": worker.channel, "kind": worker.kind,
                               "state": "failed", "error": "voir le message d'erreur"}, worker)

    def _announce_download(self, info: dict) -> None:
        text = (f"Telegram : « {info.get('title')} » — {info.get('new', 0)} nouveau(x) "
                f"téléchargé(s), {info.get('skipped', 0)} déjà là")
        folder = info.get("folder")
        show = getattr(self.parent(), "show_banner", None)
        if show is not None:
            try:
                show(text + (". Clic : le dossier." if folder else "."), "done",
                     action=(lambda: self._open_folder(Path(folder))) if folder else None,
                     seconds=10)
            except Exception:  # noqa: BLE001
                pass

    def stop_downloads(self) -> None:
        lane = queue("dl")
        for worker in lane.waiting:
            worker.stop = True
        lane.waiting.clear()
        if lane.current is not None:
            lane.current.stop = True
            self.status.setText("Arrêt après le fichier en cours…")

    def _failed(self, text: str) -> None:
        self.status.setText(text)
        QMessageBox.warning(self, "Telegram", text)

    # == mes groupes =============================================================
    def load_groups(self) -> None:
        self._on_background("lecture de tes groupes dans Telegram…")
        self._start("dialogs")

    def _show_joined(self, rows: list) -> None:
        self.state = load_state()
        self._fill_groups()
        with_media = sum(1 for r in rows if r.get("medias"))
        self.status.setText(f"{len(rows)} groupes et chaînes sur ton compte, "
                            f"dont {with_media} avec des photos ou vidéos.")

    def _joined_progress(self, rows: list) -> None:
        """La liste arrive tout de suite, ses comptes de médias ensuite."""
        self.state["joined"] = rows
        self._fill_groups()

    def _group_rows(self) -> list[dict]:
        """Les groupes du compte, plus les chaînes publiques suivies sans y
        être entré ; chacun avec ses cases."""
        follow = {c["key"]: c for c in self.state["channels"]}
        rows = []
        known = set()
        for row in self.state.get("joined") or []:
            entry = follow.get(row["key"]) or follow.get(row.get("username") or "\0") or {}
            rows.append(dict(row, alert=bool(entry.get("alert")), auto=bool(entry.get("auto")),
                             mine=True))
            known.add(row["key"])
        for key, entry in follow.items():
            if key in known:
                continue
            rows.append({"key": key, "title": entry.get("title") or key,
                         "username": key if not key[1:].isdigit() else "",
                         "kind": entry.get("kind") or "chaîne publique", "membres": 0,
                         "non_lus": 0, "activite": 0, "alert": bool(entry.get("alert")),
                         "medias": (self.state.get("medias") or {}).get(key),
                         "auto": bool(entry.get("auto")), "mine": False})
        return rows

    def _fill_groups(self) -> None:
        needle = self.group_filter.text().strip().lower() if hasattr(self, "group_filter") else ""
        only = hasattr(self, "only_followed") and self.only_followed.isChecked()
        media_only = hasattr(self, "only_media") and self.only_media.isChecked()
        all_rows = self._group_rows()
        rows = [r for r in all_rows
                if (not needle or needle in r["title"].lower())
                and (not only or r["alert"] or r["auto"])
                and (not media_only or r.get("medias"))]
        rows.sort(key=lambda r: (not (r["alert"] or r["auto"]), r["title"].lower()))
        self.group_list = rows
        table = self.groups
        table.blockSignals(True)
        table.setSortingEnabled(False)
        table.setRowCount(len(rows))
        unseen = self.state.get("unseen") or {}
        for i, row in enumerate(rows):
            for col, flag in ((0, "alert"), (1, "auto")):
                box = QTableWidgetItem()
                box.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
                box.setCheckState(Qt.Checked if row[flag] else Qt.Unchecked)
                box.setData(Qt.UserRole, row["key"])
                box.setToolTip("Te prévenir quand il arrive des photos ou vidéos" if flag == "alert"
                               else "Les télécharger dès qu'elles arrivent")
                table.setItem(i, col, box)
            name = _cell(row["title"])
            name.setData(Qt.UserRole, row["key"])
            table.setItem(i, 2, name)
            kind = row.get("kind") or ""
            if not row.get("mine"):
                kind = "publique, pas membre"
            elif not row.get("public"):
                kind += " privée" if kind == "chaîne" else " privé"
            if row.get("protege"):
                kind += " · protégé"
            table.setItem(i, 3, _cell(kind, tip="Protégé : le groupe interdit de transférer ses "
                                      "médias ; Prisme les télécharge quand même."
                                      if row.get("protege") else ""))
            members = int(row.get("membres") or 0)
            table.setItem(i, 4, _cell(f"{members:,}".replace(",", " ") if members else "", members))
            medias = row.get("medias")
            table.setItem(i, 5, _cell("…" if medias is None else f"{medias:,}".replace(",", " "),
                                      -1 if medias is None else int(medias),
                                      tip="Pas encore compté" if medias is None else ""))
            fresh = unseen.get(row["key"])
            table.setItem(i, 6, _cell("" if fresh is None else f"{int(fresh):,}".replace(",", " "),
                                      int(fresh or 0)))
            table.setItem(i, 7, _cell(str(row.get("non_lus") or ""), int(row.get("non_lus") or 0)))
            act = float(row.get("activite") or 0)
            table.setItem(i, 8, _cell(human_when(act) if act else "", act))
            can = bool(telegram_links(row["key"], row.get("username") or ""))
            link = _cell("Ouvrir ↗" if can else "—",
                         tip="Ouvrir le groupe dans Telegram" if can else
                         "Petit groupe privé : Telegram ne donne pas de lien direct")
            link.setData(Qt.UserRole, row["key"])
            if can:
                link.setForeground(QColor("#6fb3ff"))
            table.setItem(i, 9, link)
        table.setSortingEnabled(True)
        table.blockSignals(False)
        followed = sum(1 for r in all_rows if r["alert"] or r["auto"])
        auto = sum(1 for r in all_rows if r["auto"])
        with_media = sum(1 for r in all_rows if r.get("medias"))
        counted = sum(1 for r in all_rows if r.get("medias") is not None)
        waiting = sum(int(unseen.get(r["key"]) or 0) for r in all_rows if r["alert"] or r["auto"])
        text = f"{len(all_rows)} groupes"
        if counted:
            text += f", dont {with_media} avec des photos ou vidéos"
        text += f" — {followed} suivi(s), {auto} en Auto"
        if followed:
            text += f" — {waiting} média(s) non vu(s) dans ceux que tu suis"
        self.total.setText(text + ".")

    def _group_flag(self, item: QTableWidgetItem) -> None:
        if item.column() not in (0, 1):
            return
        key = item.data(Qt.UserRole)
        on = item.checkState() == Qt.Checked
        row = next((r for r in self._group_rows() if r["key"] == key), None)
        if row is None:
            return
        alert = on if item.column() == 0 else (row["alert"] or on)
        auto = on if item.column() == 1 else (row["auto"] and on)

        def change(s: dict) -> None:
            entry = next((c for c in s["channels"] if c["key"] == key), None)
            if not auto:
                # Recocher Auto plus tard repartira de ce moment-là.
                s["auto_from"].pop(key, None)
            if alert or auto:
                if entry is None:
                    entry = {"key": key, "title": row["title"], "kind": row.get("kind") or ""}
                    s["channels"].append(entry)
                entry["alert"], entry["auto"] = alert, auto
            elif entry is not None:
                if row["mine"]:
                    # Un groupe du compte : il reste dans la liste, sans suivi.
                    s["channels"].remove(entry)
                else:
                    # Une chaine ajoutee a la main reste affichee (decochee)
                    # jusqu'a « Retirer ».
                    entry["alert"] = entry["auto"] = False
        self.state = update_state(change)
        QTimer.singleShot(0, self._fill_groups)
        name = row["title"]
        if auto and item.column() == 1:
            self.status.setText(f"✔ Auto activé sur « {name} » : chaque nouvelle photo ou vidéo "
                                f"sera téléchargée (« {safe_name(name)} - Vids » et "
                                f"« {safe_name(name)} - Pix »). Pour celles déjà là : "
                                "« Tout télécharger ».")
            self._start("check", only=[key])
        elif alert and not row["alert"]:
            self.status.setText(f"✔ Alerte activée sur « {name} » : Prisme te prévient quand il "
                                "y arrive des photos ou vidéos.")
            self._start("check", only=[key])
        elif item.column() == 1:
            self.status.setText(f"✔ Auto désactivé sur « {name} ».")
        else:
            self.status.setText(f"✔ Alerte désactivée sur « {name} ».")

    def _group_clicked(self, item: QTableWidgetItem) -> None:
        if item.column() == 9:
            self.open_in_telegram(item.data(Qt.UserRole))

    def open_group_telegram(self) -> None:
        row = self._current_group()
        if row:
            self.open_in_telegram(row["key"])

    def open_in_telegram(self, key: str) -> None:
        row = next((r for r in self._group_rows() if r["key"] == key), None) or {"key": key}
        top = row.get("top") or (self.state.get("top") or {}).get(key) or 0
        links = telegram_links(key, row.get("username") or "", top)
        title = row.get("title") or key
        if not links:
            self.status.setText(f"« {title} » est un petit groupe privé : Telegram ne donne pas "
                                "de lien pour l'ouvrir, cherche-le dans l'appli.")
            return
        for url in links:
            if QDesktopServices.openUrl(QUrl(url)):
                self.status.setText(f"Ouverture de « {title} » dans Telegram…")
                return
        self.status.setText(f"Impossible d'ouvrir « {title} » : {links[-1]}")

    def _current_group(self) -> dict | None:
        row = self.groups.currentRow()
        if row < 0:
            self.status.setText("Choisis un groupe dans la liste.")
            return None
        key = self.groups.item(row, 2).data(Qt.UserRole)
        return next((r for r in self._group_rows() if r["key"] == key), None)

    def open_group_bank(self) -> None:
        row = self._current_group()
        if row:
            self.open_bank(row["key"], row["title"])

    def open_group_folder(self) -> None:
        row = self._current_group()
        if row:
            self.open_group_folders(row["title"], row["key"])

    def export_group(self) -> None:
        row = self._current_group()
        if row:
            self._export(row["key"], row["title"])

    def check_now(self) -> None:
        if not any(c.get("alert") or c.get("auto") for c in self.state["channels"]):
            self.status.setText("Coche d'abord « Alerte » ou « Auto » sur au moins un groupe.")
            return
        self.status.setText("Vérification des groupes suivis…")
        self._start("check")

    def _on_checked(self, report: dict) -> None:
        self.state = load_state()
        self._fill_groups()
        self._fill_journal()
        notes = [line for line in report.get("lines") or [] if line.get("note")]
        text = (f"{report.get('total', 0)} nouveau(x) média(s), "
                f"{report.get('telecharges', 0)} téléchargé(s).")
        if notes:
            text += " " + " ; ".join(f"{self._title_of(n['key'])} : {n['note']}" for n in notes[:4])
        self.status.setText(text)

    def _title_of(self, key: str) -> str:
        row = next((r for r in self._group_rows() if r["key"] == key), None)
        return row["title"] if row else key

    def mark_seen(self) -> None:
        """Les groupes choisis (tous, si aucun) : vus jusqu'à leur dernier
        message. Rien ne change dans Telegram."""
        picked = {self.groups.item(r, 2).data(Qt.UserRole)
                  for r in {i.row() for i in self.groups.selectedIndexes()}}
        keys = picked or {r["key"] for r in self._group_rows()}
        tops = {r["key"]: int(r.get("top") or 0) for r in self.state.get("joined") or []}

        def change(s: dict) -> None:
            for k in keys:
                top = int(s["top"].get(k) or tops.get(k) or 0)
                if top:
                    s["seen_from"][k] = top
                    s["unseen"][k] = 0
        self.state = update_state(change)
        self._fill_groups()
        self.status.setText(f"{len(keys)} groupe(s) marqué(s) comme vus.")

    def add_channel(self) -> None:
        name = username_of(self.add_url.text())
        if not name:
            return

        def change(s: dict) -> None:
            if not any(c["key"] == name for c in s["channels"]):
                s["channels"].append({"key": name, "title": name, "kind": "chaîne publique",
                                      "alert": True, "auto": False})
        self.state = update_state(change)
        self.add_url.clear()
        self._fill_groups()
        self.status.setText(f"« {name} » ajoutée, en alerte.")

    def remove_channel(self) -> None:
        row = self._current_group()
        if row is None:
            return
        if row.get("mine"):
            self.status.setText("C'est un groupe de ton compte : décoche ses cases plutôt.")
            return
        key = row["key"]
        self.state = update_state(lambda s: (
            s.__setitem__("channels", [c for c in s["channels"] if c["key"] != key]),
            s["unseen"].pop(key, None)))
        self._fill_groups()

    # == recherche ===============================================================
    def _set_mode(self, mode: str) -> None:
        self.mode = mode
        heads = {
            "groupe": ["Aperçu", "Nom", "Accès", "Type", "Membres", "Médias",
                       "Dernier message", "Lien"],
            "message": ["Date", "Groupe", "Message", "Média", "Abonnés", "Lien"],
            "web": ["Lien", "Type", "Accès", "Trouvé sur"],
        }[mode]
        self.table.clear()
        self.table.setColumnCount(len(heads))
        self.table.setHorizontalHeaderLabels(heads)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode({"groupe": 1, "message": 2, "web": 0}[mode], QHeaderView.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(60 if mode == "groupe" else 30)
        self.filters.setVisible(mode == "groupe")
        self.r_analyse.setVisible(mode == "web")
        self.r_export.setVisible(mode != "web")
        self.r_bank.setVisible(mode != "web")

    def search_groups(self) -> None:
        pasted = [line for line in self.pasted.toPlainText().splitlines() if line.strip()]
        if not self.query.text().strip() and not pasted:
            self.status.setText("Tape un mot-clé, ou colle des liens t.me.")
            return
        if any("discord" in line for line in pasted):
            self._discord_invites(pasted)
        telegram = [line for line in pasted if "discord" not in line]
        if self.query.text().strip() or telegram:
            self.status.setText("Recherche des groupes publics…")
            self._start("search", query=self.query.text(), pasted=telegram)

    def search_messages(self) -> None:
        if not self.query.text().strip():
            self.status.setText("Tape d'abord un mot-clé.")
            return
        self.status.setText("Recherche dans les messages publics…")
        self._start("content", query=self.query.text())

    def search_web(self) -> None:
        if not self.query.text().strip():
            self.status.setText("Tape d'abord un mot-clé.")
            return
        if getattr(self, "_web", None) is not None and self._web.isRunning():
            self.status.setText("La recherche sur le web est déjà en cours.")
            return
        self._web = WebLinks(self.query.text())
        self._web.status.connect(self.status.setText)
        self._web.done.connect(self._web_done)
        self._web.start()

    def _web_done(self, rows: list, why: str) -> None:
        if not rows:
            self.status.setText(why)
            return
        self._show_search(rows)
        private = sum(1 for r in rows if r["kind"] == "privé")
        text = (f"{len(rows)} liens trouvés sur le web, dont {private} invitation(s) privée(s). "
                + (why + " " if why else ""))
        if SESSION_PATH.with_suffix(".session").exists():
            # Lus dans Telegram tout de suite : nom, membres, approbation.
            links = [r["url"] for r in rows if r["kind"] == "privé"][:40] + \
                    [r["url"] for r in rows if r["kind"] != "privé"][:30]
            self.status.setText(text + "Lecture dans Telegram…")
            self._start("search", query="", pasted=links)
        else:
            self.status.setText(text + "Connecte ton compte (onglet Compte) pour les lire.")

    def analyse_selected(self) -> None:
        links = [r["url"] for r in self._selected_rows() if "t.me/" in r.get("url", "")]
        if not links:
            self.status.setText("Sélectionne des liens t.me dans le tableau.")
            return
        self.status.setText(f"Analyse de {len(links)} lien(s)…")
        self._start("search", query="", pasted=links)

    def _discord_invites(self, lines: list[str]) -> None:
        import urllib.request
        rows = []
        for raw in lines:
            raw = raw.strip()
            if "discord.gg/" not in raw and "discord.com/invite/" not in raw:
                continue
            code = raw.rstrip("/").split("/")[-1].split("?")[0]
            if not code:
                continue
            try:
                req = urllib.request.Request(
                    f"https://discord.com/api/v10/invites/{code}?with_counts=true",
                    headers={"User-Agent": "Prisme"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode())
            except Exception as exc:  # noqa: BLE001
                rows.append({"mode": "web", "title": code, "url": raw, "kind": "discord",
                             "acces": str(exc)[:40], "demande": True})
                continue
            guild = data.get("guild") or {}
            rows.append({"mode": "web", "title": guild.get("name") or code,
                         "url": f"https://discord.gg/{code}", "kind": "discord",
                         "acces": f"{data.get('approximate_member_count') or 0} membres",
                         "demande": True})
        if rows:
            self._show_search(rows)

    def _show_search(self, rows: list) -> None:
        worker = self.sender()
        if isinstance(worker, TelegramWorker) and worker.stop:
            return          # remplacée par une recherche plus récente
        mode = (rows[0].get("mode") if rows else None) or self.mode
        self._set_mode(mode)
        self.found = rows
        self._apply_filter()

    def _apply_filter(self, *_args) -> None:
        rows = []
        for row in self.found:
            if self.mode == "groupe":
                ask = bool(row.get("demande"))
                if ask and not self.f_ask.isChecked():
                    continue
                if not ask and not self.f_free.isChecked():
                    continue
                if self.f_hide_protected.isChecked() and row.get("protege"):
                    continue
                if self.f_alive.isChecked() and row.get("vivant") == "endormi":
                    continue
                if (row.get("abonnes") or 0) < self.min_subs.value():
                    continue
                if row.get("medias") is not None and row["medias"] < self.min_medias.value():
                    continue
            rows.append(row)
        self.rows = rows
        mine = {(r.get("username") or "").lower() for r in self.state.get("joined") or []
                if r.get("username")}
        table = self.table
        table.setSortingEnabled(False)
        table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            subs = int(row.get("abonnes") or 0)
            if self.mode == "groupe":
                medias = int(row.get("medias") or 0)
                member = (row.get("username") or "").lower() in mine or row.get("acces") == "déjà membre"
                access = ("déjà membre" if member else
                          "🔒 privé, sur demande" if row.get("prive") and row.get("demande") else
                          "🔒 privé, par invitation" if row.get("prive") else
                          "🔒 sur demande" if row.get("demande") else "entrée libre")
                if row.get("protege"):
                    access += " · protégé"
                preview = _cell("", medias)
                strip = _strip(row.get("apercus") or [])
                if strip is not None:
                    preview.setIcon(strip)
                cells = [
                    preview,
                    _cell(("🔒 " if (row.get("demande") or row.get("prive")) and not member else "")
                          + (row.get("title") or "")),
                    _cell(access, tip="Protégé : Telegram interdit d'en télécharger les médias."
                          if row.get("protege") else ""),
                    _cell(row.get("kind") or ""),
                    _cell(f"{subs:,}".replace(",", " "), subs),
                    _cell("?" if row.get("medias") is None else f"{medias:,}".replace(",", " "),
                          medias, tip="Visible une fois entré dans le groupe"
                          if row.get("medias") is None else ""),
                    _cell(row.get("dernier") or "",
                          -(row.get("jours") if row.get("jours") is not None else 99999)),
                    _cell(row.get("url") or ""),
                ]
            elif self.mode == "message":
                cells = [
                    _cell(row.get("date") or ""),
                    _cell(row.get("title") or ""),
                    _cell(row.get("texte") or "", tip=row.get("texte") or ""),
                    _cell(row.get("media") or ""),
                    _cell(f"{subs:,}".replace(",", " "), subs),
                    _cell(row.get("url") or ""),
                ]
            else:
                cells = [_cell(row.get("url") or ""), _cell(row.get("kind") or ""),
                         _cell(row.get("acces") or ""), _cell(row.get("source") or "")]
            cells[0].setData(Qt.UserRole, i)
            for col, cell in enumerate(cells):
                table.setItem(i, col, cell)
        table.setSortingEnabled(True)
        if self.mode == "groupe":
            table.sortItems(4, Qt.DescendingOrder)     # les plus suivis d'abord
        what = {"groupe": "groupes", "message": "messages", "web": "liens"}[self.mode]
        hidden = len(self.found) - len(rows)
        self.status.setText(f"{len(rows)} {what} affichés" +
                            (f" ({hidden} masqués par les filtres)" if hidden else "") + ".")

    def _selected_rows(self) -> list[dict]:
        picked = sorted({i.row() for i in self.table.selectedIndexes()})
        out = []
        for r in picked:
            cell = self.table.item(r, 0)
            at = cell.data(Qt.UserRole) if cell is not None else None
            if at is not None and 0 <= at < len(self.rows):
                out.append(self.rows[at])
        return out

    def _selected_one(self) -> dict | None:
        rows = self._selected_rows()
        if not rows:
            self.status.setText("Choisis une ligne dans le tableau.")
            return None
        return rows[0]

    def join_selected(self) -> None:
        rows = self._selected_rows()
        targets = [r.get("url") or r.get("username") for r in rows
                   if (r.get("url") or "").startswith("https://t.me/") or r.get("username")]
        if not targets:
            self.status.setText("Choisis un ou plusieurs groupes Telegram dans le tableau.")
            return
        self.status.setText(f"Demande d'entrée dans {len(targets)} groupe(s)…")
        self._start("join", targets=targets)

    def _joined_result(self, results: list) -> None:
        done = [r for r in results if r[1] in ("rejoint", "demande envoyée", "déjà membre")]
        text = " ; ".join(f"{u.replace('https://t.me/', '')} : {what}" for u, what in results[:6])
        self.status.setText(text or "Rien n'a été fait.")
        if any(r[1] == "rejoint" for r in done):
            self.load_groups()      # ils arrivent dans « Mes groupes »

    def _result_open(self) -> None:
        row = self._selected_one()
        if row is None:
            return
        if self.mode == "web":
            self.analyse_selected()
        elif self.mode == "message" and row.get("media") in ("vidéo", "photo", "fichier"):
            self.download_and_play(row["username"], row["message_id"], row["title"])
        else:
            self.open_bank(row.get("username") or "", row.get("title") or "")

    def save_selected(self) -> None:
        rows = self._selected_rows()
        if not rows:
            self.status.setText("Sélectionne les groupes à suivre.")
            return
        new = [r for r in rows if r.get("username")]

        def change(s: dict) -> None:
            known = {c["key"] for c in s["channels"]}
            for r in new:
                if r["username"] not in known:
                    s["channels"].append({"key": r["username"], "title": r.get("title") or r["username"],
                                          "kind": r.get("kind") or "chaîne publique",
                                          "alert": True, "auto": False})
                    known.add(r["username"])
        self.state = update_state(change)
        self._fill_groups()
        self.status.setText(f"{len(new)} groupe(s) en alerte : ils sont dans « Mes groupes ».")

    def open_selected_bank(self) -> None:
        row = self._selected_one()
        if row is not None:
            self.open_bank(row.get("username") or "", row.get("title") or "")

    def export_selected(self) -> None:
        row = self._selected_one()
        if row is not None and row.get("username"):
            self._export(row["username"], row.get("title") or row["username"])

    def open_selected_link(self) -> None:
        row = self._selected_one()
        if row is not None and row.get("url"):
            QDesktopServices.openUrl(QUrl(row["url"]))

    # == journal =================================================================
    def _fill_journal(self) -> None:
        entries = self.state.get("journal") or []
        table = self.journal
        table.setRowCount(len(entries))
        for i, entry in enumerate(entries):
            when = _cell(time.strftime("%d/%m/%Y %H:%M", time.localtime(entry.get("when") or 0)))
            when.setData(Qt.UserRole, i)
            table.setItem(i, 0, when)
            table.setItem(i, 1, _cell(entry.get("title") or entry.get("key") or ""))
            table.setItem(i, 2, _cell(entry.get("nouveaux") or 0))
            table.setItem(i, 3, _cell(entry.get("telecharges") or "" if entry.get("dossier") else "—"))
            table.setItem(i, 4, _cell(entry.get("dossier") or "(alerte seule, rien téléchargé)"))

    def _journal_entry(self) -> dict | None:
        row = self.journal.currentRow()
        entries = self.state.get("journal") or []
        if not (0 <= row < len(entries)):
            self.status.setText("Choisis une ligne du journal.")
            return None
        return entries[row]

    def open_journal_folder(self) -> None:
        entry = self._journal_entry()
        if entry is None:
            return
        if not entry.get("dossier"):
            self.status.setText("Rien n'a été téléchargé ce jour-là (alerte seule) : "
                                "coche « Auto » pour que ça arrive sur le disque.")
            return
        self._open_folder(Path(entry["dossier"]))

    def open_journal_bank(self) -> None:
        entry = self._journal_entry()
        if entry is not None:
            self.open_bank(entry["key"], entry.get("title") or entry["key"])

    def clear_journal(self) -> None:
        self.state = update_state(lambda s: s.__setitem__("journal", []))
        self._fill_journal()

    def show_journal(self) -> None:
        self.tabs.setCurrentIndex(2)

    def _open_folder(self, folder: Path) -> None:
        if not folder.exists():
            self.status.setText(f"Pas encore de dossier : {folder}")
            return
        os.startfile(str(folder))

    def open_group_folders(self, title: str, key: str) -> None:
        """« Groupe - Vids » et « Groupe - Pix » : un seul, on l'ouvre ; les
        deux, l'explorateur s'ouvre dessus, côte à côte."""
        folders = group_folders(self.state, title, key)
        if not folders:
            self.status.setText(f"Rien n'a encore été téléchargé de « {title} ».")
            return
        if len(folders) == 1:
            os.startfile(str(folders[0]))
            return
        subprocess.Popen(f'explorer /select,"{folders[0]}"')

    # == medias ==================================================================
    def _export(self, key: str, title: str, kind: str = "all", min_duration: int = 0) -> None:
        what = {"video": "toutes les vidéos", "photo": "toutes les photos"}.get(
            kind, "toutes les photos et vidéos")
        self.status.setText(f"⬇ Téléchargement lancé : {what} de « {title} ». L'avancement "
                            "s'affiche dans la barre ; tu peux continuer à parcourir.")
        self._start("export", channel=key, title=title, skip_known=False, kind=kind,
                    min_duration=min_duration)

    def open_bank(self, key: str, title: str = "", before_id: int = 0, kind: str = "all") -> None:
        """La fenêtre des médias s'ouvre tout de suite ; les vignettes y
        arrivent par paquets."""
        if not key:
            self.status.setText("Ce groupe n'a pas de lien lisible.")
            return
        title = title or key
        window = self._bank_window
        if window is None or not window.isVisible() or window.key != key:
            window = MediaBank(self, key, title)
            self._bank_window = window
        window.show()
        window.raise_()
        window.activateWindow()
        window.loading_started()
        self.status.setText(f"Lecture des médias de « {title} »…")
        worker = self._start("bank", channel=key, title=title, before_id=before_id, kind=kind,
                             min_duration=window.min_duration)
        worker.finished.connect(self._bank_finished)

    def _bank_for(self, worker) -> "MediaBank | None":
        """La fenêtre des médias à qui revient ce que lit `worker` (pas une
        page d'avant, d'un autre groupe ou d'un autre choix Tout/Vidéos/Photos)."""
        window = self._bank_window
        if window is None or not window.isVisible() or not isinstance(worker, TelegramWorker):
            return None
        if window.key != worker.channel or window.kind != worker.kind:
            return None
        return window

    def _bank_part(self, items: list) -> None:
        window = self._bank_for(self.sender())
        if window is not None:
            window.add_items(items)

    def _bank_totals(self, totals: dict) -> None:
        window = self._bank_for(self.sender())
        if window is not None:
            window.set_totals(totals)

    def _open_bank(self, items: list) -> None:
        window = self._bank_for(self.sender())
        if window is not None:
            window.add_items(items)
            self.status.setText(f"{len(window.items)} médias de « {window.title} » affichés.")

    def _bank_finished(self) -> None:
        worker = self.sender()
        window = self._bank_for(worker)
        if window is not None:
            window.page_done(worker.got, worker.error)

    def download_and_play(self, key: str, message_id: int, title: str = "") -> None:
        self.status.setText("Téléchargement du média, il s'ouvre dès qu'il arrive…")
        self._on_dl_status("Téléchargement du média, il s'ouvre dès qu'il arrive…")
        self._start("download", channel=key, title=title or key, message_id=message_id,
                    skip_known=False)

    def download_many(self, key: str, title: str, ids: list[int], skip_known: bool) -> None:
        self.status.setText(f"⬇ Téléchargement lancé : {len(ids)} média(s) de « {title} ».")
        self._start("download_many", channel=key, title=title, message_ids=list(ids),
                    skip_known=skip_known)

    def _play(self, path: str) -> None:
        if not path:
            self.status.setText("Téléchargement impossible (déjà présent ailleurs, ou protégé).")
            self._on_dl_status(self.status.text())
            return
        self.status.setText(f"Arrivé : {path}")
        self._on_dl_status(f"Arrivé : {Path(path).name}")
        play = getattr(self.parent(), "_aside_play", None)
        if play is not None and Path(path).suffix.lower() in VIDEO_EXT:
            # Le lecteur de Prisme, celui de la fenêtre principale.
            play(path, Path(path).name, path)
            return
        os.startfile(path)

    def _batch_done(self, n: int) -> None:
        worker = self.sender() if isinstance(self.sender(), TelegramWorker) else self._current
        skipped = getattr(worker, "skipped", 0)
        self.status.setText(f"« {worker.title} » : {n} nouveau(x), {skipped} déjà là. Dossiers : "
                            f"« {safe_name(worker.title)} - Vids » et « - Pix », dans "
                            f"{download_root(self.state)}")


class MediaBank(QDialog):
    """Les médias d'un groupe, en vignettes comme dans la fenêtre principale.
    Défiler suffit : la page suivante se lit avant d'arriver en bas."""

    def __init__(self, owner: TelegramDialog, key: str, title: str):
        super().__init__(owner)
        self.owner = owner
        self.key = key
        self.title = title
        self.items: list[dict] = []
        self.totals: dict = {}
        self.loading = False
        self.exhausted = False
        self._ids: set[int] = set()
        self.hidden = 0             # vidéos lues mais masquées (trop courtes)
        self.idle_pages = 0         # pages d'affilée sans rien de visible
        self._visible_before = 0
        self.setWindowTitle(f"Médias — {title}")
        self.setStyleSheet(STYLE)
        self.resize(1180, 760)
        box = QVBoxLayout(self)
        head_row = QHBoxLayout()
        head = QLabel(title, self)
        head.setObjectName("tgHead")
        head_row.addWidget(head)
        self.counts = QLabel("", self)
        self.counts.setObjectName("tgTotal")
        head_row.addWidget(self.counts)
        head_row.addStretch(1)
        tg = QPushButton("Ouvrir dans Telegram", self)
        tg.clicked.connect(lambda: owner.open_in_telegram(self.key))
        head_row.addWidget(tg)
        box.addLayout(head_row)
        top = QHBoxLayout()
        lead = QLabel("Double-clic : télécharger et lire dans Prisme. La case, en haut à gauche "
                      "(au survol) : choisir pour « Télécharger les cochés ».", self)
        lead.setObjectName("tgLead")
        top.addWidget(lead, 1)
        self.kind = "all"
        self.kind_buttons = {}
        for kind, text in (("all", "Tout"), ("video", "Vidéos"), ("photo", "Photos")):
            button = QPushButton(text, self)
            button.setCheckable(True)
            button.setChecked(kind == "all")
            button.clicked.connect(lambda _c=False, k=kind: self.set_kind(k))
            top.addWidget(button)
            self.kind_buttons[kind] = button
        box.addLayout(top)
        # Les vidéos trop courtes (extraits de 3-4 s) : masquées, et sautées
        # par « Tout télécharger ». Le choix est gardé d'une fois sur l'autre.
        lengths = QHBoxLayout()
        lengths.addWidget(QLabel("Vidéos d'au moins :", self))
        self.min_duration = int(load_state().get("min_duration") or 0)
        self.length_buttons = {}
        for seconds, text in DURATION_CHOICES:
            button = QPushButton(text, self)
            button.setCheckable(True)
            button.setChecked(seconds == self.min_duration)
            button.clicked.connect(lambda _c=False, s=seconds: self.set_min_duration(s))
            lengths.addWidget(button)
            self.length_buttons[seconds] = button
        lengths.addStretch(1)
        box.addLayout(lengths)
        box.addWidget(_DownloadBar(owner, self))
        self.grid = QListWidget(self)
        use_cards(self.grid, 236)
        self.grid.itemDoubleClicked.connect(self._open)
        self.grid.verticalScrollBar().valueChanged.connect(self._near_end)
        self.grid.verticalScrollBar().rangeChanged.connect(lambda *_a: self._near_end())
        box.addWidget(self.grid, 1)
        info_row = QHBoxLayout()
        self.info = QLabel("", self)
        self.info.setObjectName("tgLead")
        info_row.addWidget(self.info, 1)
        self.note = QLabel("", self)
        self.note.setObjectName("tgLead")
        info_row.addWidget(self.note)
        self.further = QPushButton("Chercher plus loin", self)
        self.further.setToolTip("Le filtre de durée masque tout ce qui a été lu : lire les "
                                "pages suivantes du groupe.")
        self.further.clicked.connect(self._search_further)
        self.further.hide()
        info_row.addWidget(self.further)
        box.addLayout(info_row)
        row = QHBoxLayout()
        play = QPushButton("Lire", self)
        play.setObjectName("tgPrimary")
        play.clicked.connect(lambda: self._open(self.grid.currentItem()))
        row.addWidget(play)
        self.everything = QPushButton("Tout télécharger", self)
        self.everything.setToolTip("Toutes les photos et vidéos du groupe (ou seulement les "
                                   "vidéos, ou les photos, selon le choix en haut), pas seulement "
                                   "celles affichées. Ce qui est déjà dans le dossier est sauté.")
        self.everything.clicked.connect(
            lambda: owner._export(self.key, self.title, self.kind, self.min_duration))
        row.addWidget(self.everything)
        bulk = QPushButton("Télécharger les cochés", self)
        bulk.clicked.connect(self._download_checked)
        row.addWidget(bulk)
        all_ = QPushButton("Tout cocher", self)
        all_.clicked.connect(lambda: self._check_all(True))
        row.addWidget(all_)
        none = QPushButton("Tout décocher", self)
        none.clicked.connect(lambda: self._check_all(False))
        row.addWidget(none)
        self.skip_known = QCheckBox("Sauter ce qui est déjà dans la bibliothèque", self)
        self.skip_known.setToolTip("Même nom et même taille qu'une vidéo de Prisme : pas "
                                   "retéléchargée. (Lire la bibliothèque prend un moment.)")
        row.addWidget(self.skip_known)
        row.addStretch(1)
        folder = QPushButton("Ouvrir le dossier", self)
        folder.setToolTip("« Groupe - Vids » et « Groupe - Pix », dans le dossier des "
                          "téléchargements Telegram.")
        folder.clicked.connect(lambda: owner.open_group_folders(self.title, self.key))
        row.addWidget(folder)
        box.addLayout(row)
        self._show_info()

    # -- l'etat de la liste -------------------------------------------------------
    def _total(self) -> int | None:
        value = self.totals.get(self.kind)
        return None if value is None else int(value)

    def _too_short(self, item: dict) -> bool:
        return bool(self.min_duration and item.get("video")
                    and float(item.get("duration") or 0) < self.min_duration)

    def set_min_duration(self, seconds: int) -> None:
        """Masque les vidéos plus courtes, sans rien relire dans Telegram ;
        la suite se charge s'il ne reste pas de quoi remplir la fenêtre."""
        for value, button in self.length_buttons.items():
            button.setChecked(value == seconds)
        self.min_duration = seconds
        update_state(lambda s: s.__setitem__("min_duration", seconds))
        self.hidden = 0
        for i in range(self.grid.count()):
            cell = self.grid.item(i)
            short = self._too_short(cell.data(Qt.UserRole) or {})
            cell.setHidden(short)
            self.hidden += short
        self.idle_pages = 0
        self._show_info()
        QTimer.singleShot(0, self._near_end)

    def _show_info(self) -> None:
        loaded = len(self.items)
        hidden = self.hidden
        shown = loaded - hidden
        total = self._total()
        of = f" sur {total:,}".replace(",", " ") if total is not None else ""
        if self.loading:
            text = f"{shown}{of} affichés — chargement de la suite…" if shown else \
                "Chargement des médias…"
        elif self.exhausted or (total is not None and loaded >= total):
            text = (f"Tout est affiché : {shown}." if shown else
                    "Aucune photo ni vidéo ici (ou Telegram n'en montre pas à ton compte).")
        else:
            text = f"{shown}{of} affichés — fais défiler pour voir la suite."
        if hidden:
            text += f"  ({hidden} vidéo(s) trop courte(s) masquée(s))"
        self.info.setText(text)
        self.setWindowTitle(f"Médias — {self.title} ({shown}{of})")

    def set_totals(self, totals: dict) -> None:
        self.totals = dict(totals)

        def fmt(n) -> str:
            return f"{int(n):,}".replace(",", " ")
        videos, photos = totals.get("video"), totals.get("photo")
        if videos is not None and photos is not None:
            self.counts.setText(f"   {fmt(videos)} vidéos · {fmt(photos)} photos")
        for kind, text in (("all", "Tout"), ("video", "Vidéos"), ("photo", "Photos")):
            if totals.get(kind) is not None:
                self.kind_buttons[kind].setText(f"{text} ({fmt(totals[kind])})")
        total = self._total()
        if total is not None:
            self.everything.setText(f"Tout télécharger ({fmt(total)})")
        self._show_info()

    def loading_started(self) -> None:
        self.loading = True
        self._visible_before = len(self.items) - self.hidden
        self.further.hide()
        self._show_info()

    def page_done(self, got: int, error: str = "") -> None:
        self.loading = False
        if error:
            # Pas « tout est affiché » : la page a échoué, défiler réessaie.
            self._show_info()
            self.info.setText(self.info.text() + f"  (échec : {error[:80]} — fais défiler "
                                                 "pour réessayer)")
            return
        if got < BANK_PAGE:
            self.exhausted = True
        # Garde-fou : un filtre qui masque tout ferait lire le groupe entier
        # page après page. Trois pages sans rien de visible : on s'arrête et
        # l'on demande.
        visible = len(self.items) - self.hidden
        self.idle_pages = 0 if visible > self._visible_before else self.idle_pages + 1
        self._show_info()
        if self.idle_pages >= 3 and not self.exhausted:
            self.info.setText(self.info.text() + "  — rien d'assez long dans les dernières "
                                                 "pages lues.")
            self.further.show()
            return
        # La page ne remplit pas la fenêtre, ou l'on est déjà en bas : la
        # suite tout de suite.
        QTimer.singleShot(0, self._near_end)

    def _search_further(self) -> None:
        self.idle_pages = 0
        self.further.hide()
        self._more()

    def add_items(self, items: list) -> None:
        from .scan import human_duration, human_size
        for item in items:
            if item["id"] in self._ids:
                continue
            self._ids.add(item["id"])
            self.items.append(item)
            cell = QListWidgetItem(item.get("title") or ("vidéo" if item.get("video") else "photo"))
            cell.setFlags(cell.flags() | Qt.ItemIsUserCheckable)
            cell.setCheckState(Qt.Unchecked)
            if item.get("video") and item.get("duration"):
                cell.setData(ROLE_CHIP, human_duration(item["duration"]))
            elif not item.get("video"):
                cell.setData(ROLE_CHIP, "photo")
            cell.setData(ROLE_RES, int(item.get("height") or 0))
            size = item.get("size") or 0
            cell.setData(ROLE_SUB, item.get("date", "") + (f"  ·  {human_size(size)}" if size else ""))
            thumb = item.get("thumb") or ""
            if thumb:
                pix = QPixmap(thumb)
                if not pix.isNull():
                    cell.setIcon(QIcon(pix))
            cell.setToolTip(item.get("title") or "")
            cell.setData(Qt.UserRole, item)
            self.grid.addItem(cell)
            short = self._too_short(item)
            cell.setHidden(short)
            self.hidden += short
        self._show_info()

    def set_kind(self, kind: str) -> None:
        """Tout, les vidéos seules, ou les photos seules : Telegram les trie."""
        for k, button in self.kind_buttons.items():
            button.setChecked(k == kind)
        if kind == self.kind:
            return
        self.kind = kind
        self.items = []
        self._ids.clear()
        self.hidden = self.idle_pages = 0
        self.exhausted = False
        self.grid.clear()
        total = self._total()
        if total is not None:
            self.everything.setText(f"Tout télécharger ({total:,})".replace(",", " "))
        self.owner.open_bank(self.key, self.title, 0, kind)

    def _near_end(self, *_args) -> None:
        """Moins d'un écran avant le bas (ou pas de quoi défiler) : la suite."""
        bar = self.grid.verticalScrollBar()
        if bar.maximum() == 0 or bar.value() >= bar.maximum() - max(bar.pageStep(), 200):
            self._more()

    def _more(self) -> None:
        if not self.items or self.loading or self.exhausted or self.idle_pages >= 3:
            return
        total = self._total()
        if total is not None and len(self.items) >= total:
            self.exhausted = True
            self._show_info()
            return
        oldest = min(item["id"] for item in self.items)
        self.owner.open_bank(self.key, self.title, before_id=oldest, kind=self.kind)

    def _open(self, cell) -> None:
        if cell is None:
            return
        item = cell.data(Qt.UserRole)
        if item:
            self.note.setText("Téléchargement du média, il s'ouvre dès qu'il arrive…")
            self.owner.download_and_play(self.key, item["id"], self.title)

    def _download_checked(self) -> None:
        ids = []
        for i in range(self.grid.count()):
            cell = self.grid.item(i)
            if cell.checkState() == Qt.Checked and not cell.isHidden():
                ids.append((cell.data(Qt.UserRole) or {}).get("id"))
        ids = [i for i in ids if i]
        if not ids:
            self.note.setText("Coche au moins une vignette.")
            return
        self.owner.download_many(self.key, self.title, ids, self.skip_known.isChecked())

    def _check_all(self, on: bool) -> None:
        state = Qt.Checked if on else Qt.Unchecked
        for i in range(self.grid.count()):
            cell = self.grid.item(i)
            if not cell.isHidden():
                cell.setCheckState(state)


# -- en fond, depuis la fenetre principale --------------------------------------
def _watch_once(window) -> None:
    state = load_state()
    if not (state.get("api_id") and state.get("api_hash")):
        return
    if not any(c.get("alert") or c.get("auto") for c in state["channels"]):
        return
    if not SESSION_PATH.with_suffix(".session").exists() or queue("fond").busy():
        return
    worker = TelegramWorker(state, "check")
    worker.interactive = False
    worker.checked.connect(lambda report: hub().checked.emit(report))
    window._telegram_check_worker = worker
    queue(worker.lane).launch(worker)


def _announce(window, report: dict) -> None:
    """Un bandeau dans Prisme ; un clic ouvre le journal."""
    journal = report.get("journal") or []
    if not journal or window is None:
        return
    total = sum(e.get("nouveaux") or 0 for e in journal)
    got = sum(e.get("telecharges") or 0 for e in journal)
    names = ", ".join(e.get("title") or e.get("key") for e in journal[:3])
    if len(journal) > 3:
        names += f" et {len(journal) - 3} autre(s)"
    text = f"Telegram : {total} nouveau(x) média(s) dans {names}"
    if got:
        text += f" — {got} téléchargé(s)"
    try:
        window.show_banner(text + ". Clic : le journal.", "done",
                           action=lambda: window.open_telegram(journal=True), seconds=12)
    except Exception:  # noqa: BLE001
        pass


def start_watch(window) -> None:
    """Au lancement de Prisme : vérifie les groupes suivis, puis à intervalle
    régulier, fenêtre Telegram ouverte ou non."""
    state = load_state()
    timer = QTimer(window)
    timer.setInterval(max(5, int(state.get("interval_min") or 10)) * 60 * 1000)
    timer.timeout.connect(lambda: _watch_once(window))
    timer.start()
    window._telegram_watch = timer
    # File d'attente : le signal part du fil du travail, le bandeau se pose
    # dans celui de l'interface.
    hub().checked.connect(lambda report: _announce(window, report), Qt.QueuedConnection)
    _watch_once(window)


start_follow = start_watch   # ancien nom
