"""La fenêtre « Sondages » : préparer, envoyer, et lire les réponses.

À gauche, les sondages, du plus récent au plus ancien ; à droite, celui qu'on
a choisi, en deux onglets :

* **Contenu** -- son titre, un mot pour les visiteurs, ce qu'il rapporte, à
  qui il s'adresse, et ses vidéos. On les ajoute d'un clic, depuis une
  recherche dans toute la collection, les vidéos cochées dans Prisme, celle
  qu'on regarde, ou au hasard. Pour un duel, le premier clic donne la vidéo
  A, le second la B : la paire est faite. « Envoyer » le rend visible ; il ne
  se modifie plus ensuite (on le duplique).
* **Résultats** -- le rapport de chaque personne (sa note pour chaque vidéo,
  ou son choix pour chaque duel), et la synthèse de tous. Double-clic : la
  vidéo dans le lecteur flottant ; « Montrer dans Prisme » en fait une
  planche, chaque carte portant sa note.

Les sondages vivent dans `sondages.json` (`sondages.py`), recopiés sur le NAS
à chaque changement ; les réponses arrivent des deux serveurs.
"""
from __future__ import annotations

import os
import random
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import shiboken6
from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QStackedWidget,
    QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from . import sondages
from .access import spell, when

STYLE = """
QWidget#surveys { background: #0e1116; }
QWidget#surveys QLabel { color: #b9c2cd; font-size: 13px; }
QLabel#sTitle { color: #ffffff; font-size: 17px; font-weight: 700; }
QLabel#sHead { color: #e6ebf1; font-size: 13px; font-weight: 600; }
QLabel#sDim { color: #8b94a1; font-size: 12px; }
QLabel#sWarn { color: #d8c05a; font-size: 12px; }
QLabel#sPill { border-radius: 10px; padding: 3px 10px; font-size: 11px; font-weight: 700; }
QLabel#sPill[state="brouillon"] { background: #232a35; color: #aeb8c5; }
QLabel#sPill[state="ouvert"] { background: #173524; color: #7bd88f; }
QLabel#sPill[state="clos"] { background: #2a2330; color: #c3a6e0; }
QLabel#sEmpty { color: #8b94a1; font-size: 14px; }
QLabel#sBig { color: #ffffff; font-size: 20px; font-weight: 700; }
QLineEdit { background: #151a21; border: 1px solid #262e39; border-radius: 6px;
            padding: 6px 9px; color: #e9eef4; font-size: 13px; }
QLineEdit#sName { font-size: 16px; font-weight: 600; padding: 7px 10px; }
QLineEdit:read-only { color: #aeb8c5; background: #11151b; }
QSpinBox { background: #151a21; border: 1px solid #262e39; border-radius: 6px;
           padding: 4px 6px; color: #e9eef4; }
QPushButton { background: #1b222c; border: 1px solid #2b3542; border-radius: 6px;
              padding: 6px 12px; color: #cdd5df; font-size: 12px; }
QPushButton:hover { background: #232c38; }
QPushButton:disabled { color: #5d6672; border-color: #222a34; }
QPushButton#sGo { background: #2f6fed; border-color: #2f6fed; color: #ffffff;
                  font-weight: 600; padding: 7px 16px; }
QPushButton#sGo:hover { background: #3d7cf5; }
QPushButton#sGo:disabled { background: #22324f; border-color: #22324f; color: #8da0bf; }
QPushButton#sNew { text-align: left; padding: 8px 10px; font-size: 12px; font-weight: 600;
                   color: #e6ebf1; }
QPlainTextEdit { background: #151a21; border: 1px solid #262e39; border-radius: 6px;
                 padding: 5px; color: #e9eef4; font-size: 13px; }
QComboBox { background: #151a21; border: 1px solid #262e39; border-radius: 6px;
            padding: 5px 9px; color: #cdd5df; }
QPushButton#sDanger { color: #ff9ca3; }
QPushButton#sToggle:checked { background: #1d2a40; border-color: #4f8bf0; color: #ffffff; }
QListWidget { background: #11151b; border: 1px solid #1c222b; border-radius: 8px;
              color: #cdd5df; font-size: 12px; outline: 0; }
QListWidget::item { border-radius: 6px; }
QListWidget::item:selected { background: #1d2a40; color: #ffffff; }
QListWidget::item:hover:!selected { background: #161c25; }
QListWidget#sList::item { padding: 0; margin: 2px 3px; }
QTreeWidget { background: #11151b; border: 1px solid #1c222b; border-radius: 8px;
              color: #cdd5df; font-size: 12px; outline: 0; }
QTreeWidget::item { padding: 3px 2px; }
QTreeWidget::item:selected { background: #1d2a40; color: #ffffff; }
QHeaderView::section { background: #151a21; color: #8b94a1; border: 0; padding: 5px; }
QTabWidget::pane { background: #0e1116; border: 1px solid #1c222b; top: -1px;
                   border-radius: 0 6px 6px 6px; }
QTabBar::tab { background: #151a21; color: #8b94a1; padding: 6px 16px;
               border: 1px solid #1c222b; border-bottom: 0; margin-right: 2px; }
QTabBar::tab:selected { background: #0e1116; color: #ffffff; }
QFrame#sCard { background: transparent; }
QSplitter::handle { background: #0e1116; }
"""

THUMB = QSize(160, 90)
SMALL = QSize(112, 63)
STAR, HOLLOW = "★", "☆"
NOTE_WORDS = {1: "pas pour moi", 2: "bof", 3: "pas mal", 4: "j'aime", 5: "j'adore"}
ICONS = {"notes": "★ ", "duel": "◧◨ ", "choix": "☰ ", "echelle": "▁▃▅ "}
KIND_LABELS = {"notes": "Notes de 1 à 5", "duel": "Duels A / B",
               "choix": "Questions à choix", "echelle": "Échelle de 0 à 5"}
FIRST_TITLES = {"notes": "Notez ces vidéos", "duel": "Laquelle préférez-vous ?",
                "choix": "Ça vous tente ?", "echelle": "Qu'est-ce qui vous intéresse ?"}
# Des reponses toutes faites, pour les questions a choix.
PRESETS = [
    ["J'ai déjà essayé", "J'aimerais bien essayer", "Ça ne m'intéresse pas"],
    ["Déjà fait", "Envie de le faire", "Pas pour moi"],
    ["Oui", "Non"],
    ["J'adore", "Ça me tente", "Bof", "Pas du tout"],
    ["Souvent", "Parfois", "Jamais"],
]
SCALE_COLORS = {0: "#8b94a1", 1: "#e06c7c", 2: "#e0915f", 3: "#d8c05a", 4: "#8fd16f",
                5: "#5fd3a0"}


class QuestionDialog(QDialog):
    """Retoucher une question : son texte, et sa precision."""

    def __init__(self, entry: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Modifier la question")
        self.setStyleSheet(STYLE + "QDialog { background: #0e1116; } QLabel { color: #b9c2cd; }")
        self.resize(520, 140)
        form = QFormLayout(self)
        self.text = QLineEdit(entry.get("text", ""), self)
        self.text.setMaxLength(sondages.QUESTION_MAX)
        self.detail = QLineEdit(entry.get("detail", ""), self)
        self.detail.setMaxLength(sondages.DETAIL_MAX)
        form.addRow("Question", self.text)
        form.addRow("Précision", self.detail)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        form.addRow(box)


def stars(value) -> str:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return ""
    return STAR * value + HOLLOW * (5 - value)


def _label(text: str, name: str, parent=None) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName(name)
    return label


def _cover(file: str, size: QSize) -> QPixmap:
    """L'image, remplissant la case (rognee au centre, sans bandes)."""
    source = QPixmap(file) if file else QPixmap()
    out = QPixmap(size)
    out.fill(QColor("#05070a"))
    if source.isNull():
        return out
    scaled = source.scaled(size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    painter = QPainter(out)
    painter.drawPixmap(0, 0, scaled, (scaled.width() - size.width()) // 2,
                       (scaled.height() - size.height()) // 2, size.width(), size.height())
    painter.end()
    return out


def _blank(size: QSize, text: str = "") -> QPixmap:
    out = QPixmap(size)
    out.fill(QColor("#151a21"))
    if text:
        painter = QPainter(out)
        painter.setPen(QColor("#5d6672"))
        painter.drawText(out.rect(), Qt.AlignCenter, text)
        painter.end()
    return out


class Thumbs(QObject):
    """Les vignettes des vidéos, deja faites ou fabriquees en fond (trois a la
    fois). Celles qu'on fabrique ici servent aussi la page des visiteurs."""

    ready = Signal(str, str)            # chemin de la video, fichier image

    def __init__(self, width: int, parent=None):
        super().__init__(parent)
        self.width = width
        self.pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="prisme-sondages")
        self.done: dict = {}
        self.asked: set = set()

    def ask(self, path) -> str:
        """Le fichier s'il est deja connu ; sinon "" et `ready` suivra."""
        from . import media
        key = str(path)
        if key in self.done:
            return self.done[key]
        if key in self.asked:
            return ""
        self.asked.add(key)

        def make() -> None:
            try:
                found = media.cached_thumb(Path(key), media.BLIND_START, self.width)
                if found is None:
                    found = media.extract_thumb(Path(key), media.BLIND_START, self.width)
                if found is None:
                    # Une video plus courte que l'instant habituel : son debut.
                    found = (media.cached_thumb(Path(key), 1.0, self.width)
                             or media.extract_thumb(Path(key), 1.0, self.width))
            except Exception:                                # noqa: BLE001
                found = None
            file = str(found) if found else ""
            self.done[key] = file
            self.ready.emit(key, file)
        self.pool.submit(make)
        return ""

    def close(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)


class Grid(QListWidget):
    """Une liste qu'on remet dans l'ordre en glissant ses elements, meme en
    vignettes : en mode icones, Qt deplace les cases sans changer l'ordre."""

    reordered = Signal()

    def dropEvent(self, event):                     # noqa: N802
        if event.source() is not self:
            event.ignore()
            return
        source = self.currentRow()
        target = self.indexAt(event.position().toPoint())
        dest = target.row() if target.isValid() else self.count() - 1
        moving = self.item(source) if source >= 0 else None
        if moving is None or dest == source or not (moving.flags() & Qt.ItemIsDragEnabled):
            event.ignore()
            return
        item = self.takeItem(source)
        self.insertItem(dest, item)
        self.setCurrentItem(item)
        # Rien a retirer ensuite : le deplacement est deja fait.
        event.setDropAction(Qt.IgnoreAction)
        event.accept()
        self.reordered.emit()


class SurveysWindow(QWidget):
    """La fenetre des sondages, a cote de Prisme (pas modale : une video
    s'ouvre dans le lecteur flottant pendant qu'on lit un rapport)."""

    def __init__(self, window):
        super().__init__(window, Qt.Window)
        self.window = window
        self.setObjectName("surveys")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setWindowTitle("Sondages aux visiteurs")
        self.setStyleSheet(STYLE)
        self.resize(1180, 780)
        from .config import PRIVATE_DIR
        self.path = PRIVATE_DIR / sondages.FILE_NAME
        self.answers_pc = PRIVATE_DIR / sondages.ANSWERS_NAME
        self.surveys: list = sondages.load(self.path)
        self.rows: dict = {}             # sondage -> fiches des participants
        self.people: list = []           # [{where, key, name, email}]
        self.current_id = ""
        self.half = None                 # la video A d'une paire en cours
        self._paths = None               # (cle, {empreinte: chemin}, [(chemin, nom replie)])
        self._items_for: dict = {}       # chemin -> [elements a illustrer]
        self._reading = False
        self._syncing = False
        self._sync_again = False
        self._pending_pairs: dict = {}   # chemin -> [(paire a redessiner)]
        self.thumbs = Thumbs(int(window.cfg["thumb_width"] or 480), self)
        self.thumbs.ready.connect(self._thumb_ready)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(12)
        outer.addWidget(self._column(), 0)
        self.stack = QStackedWidget(self)
        self.stack.addWidget(self._welcome())
        self.stack.addWidget(self._editor())
        outer.addWidget(self.stack, 1)

        self._fill_list()
        self.beat = QTimer(self)
        self.beat.setInterval(5000)
        self.beat.timeout.connect(self._read_answers)
        self.beat.start()
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self._save_now)
        self._sync()
        self._read_answers()
        if self.surveys:
            self.select(self.surveys[0]["id"])

    # -- la colonne : nouveaux sondages, et la liste ---------------------------
    def _column(self) -> QWidget:
        box = QWidget(self)
        box.setFixedWidth(320)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(_label("Sondages", "sTitle", box))
        lay.addWidget(_label("Créer un sondage :", "sDim", box))
        grid = QGridLayout()
        grid.setSpacing(6)
        for place, (kind, text, tip) in enumerate((
                ("notes", "★  Noter des vidéos",
                 "Une liste de vidéos ; chaque visiteur donne une note de 1 à 5 à chacune."),
                ("duel", "◧◨  Duels A / B",
                 "Des paires de vidéos lues ensemble ; le visiteur garde l'une des deux."),
                ("choix", "☰  Questions à choix",
                 "Des propositions en texte (un plat, une pratique…) et des réponses que vous "
                 "choisissez : « J'ai déjà essayé », « J'aimerais bien essayer »…"),
                ("echelle", "▁▃▅  Échelle 0 à 5",
                 "Des propositions en texte ; le visiteur dit son intérêt de 0 (pas du tout) "
                 "à 5 (carrément)."))):
            button = QPushButton(text, box)
            button.setObjectName("sNew")
            button.setToolTip(tip)
            button.clicked.connect(lambda _c=False, k=kind: self.create(k))
            grid.addWidget(button, place // 2, place % 2)
        lay.addLayout(grid)
        self.list = QListWidget(box)
        self.list.setObjectName("sList")
        self.list.setSpacing(1)
        self.list.currentItemChanged.connect(self._picked_survey)
        lay.addWidget(self.list, 1)
        self.where = _label("", "sDim", box)
        self.where.setWordWrap(True)
        lay.addWidget(self.where)
        return box

    def _welcome(self) -> QWidget:
        page = QWidget(self)
        lay = QVBoxLayout(page)
        lay.addStretch(1)
        big = _label("Demandez leur avis à vos visiteurs", "sBig", page)
        big.setAlignment(Qt.AlignCenter)
        lay.addWidget(big)
        text = _label(
            "Deux sortes de sondages, à créer à gauche :\n\n"
            "•  Noter des vidéos — une liste que vous choisissez ; chacun donne une note "
            "de 1 (pas pour moi) à 5 (j'adore).\n"
            "•  Duels — des paires de vidéos lues ensemble ; chacun garde l'une des deux.\n\n"
            "Une fois envoyé, le sondage s'annonce sur la page des visiteurs (une "
            "notification en haut, à chaque ouverture, tant qu'il n'est pas fait). Un "
            "sondage fini compte pour quelques vidéos dans leur bon d'achat. Leurs réponses "
            "arrivent ici, personne par personne.", "sEmpty", page)
        text.setWordWrap(True)
        text.setMaximumWidth(560)
        lay.addWidget(text, 0, Qt.AlignHCenter)
        lay.addStretch(2)
        return page

    # -- le sondage choisi -----------------------------------------------------
    def _editor(self) -> QWidget:
        page = QWidget(self)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        top = QHBoxLayout()
        self.kind_label = _label("", "sDim", page)
        top.addWidget(self.kind_label)
        self.name = QLineEdit(page)
        self.name.setObjectName("sName")
        self.name.setPlaceholderText("Le titre que verront les visiteurs")
        self.name.setMaxLength(sondages.TITLE_MAX)
        self.name.textEdited.connect(lambda text: self._edit(title=text))
        top.addWidget(self.name, 1)
        self.pill = _label("", "sPill", page)
        top.addWidget(self.pill)
        lay.addLayout(top)
        self.tabs = QTabWidget(page)
        self.tabs.addTab(self._content(), "Contenu")
        self.tabs.addTab(self._results(), "Résultats")
        lay.addWidget(self.tabs, 1)
        # Les gestes du sondage, en bas.
        foot = QHBoxLayout()
        self.saved = _label("", "sDim", page)
        foot.addWidget(self.saved, 1)
        self.delete_button = QPushButton("Supprimer", page)
        self.delete_button.setObjectName("sDanger")
        self.delete_button.clicked.connect(self.delete_current)
        foot.addWidget(self.delete_button)
        self.copy_button = QPushButton("Dupliquer en brouillon", page)
        self.copy_button.setToolTip("Un nouveau sondage, avec les mêmes vidéos, à retoucher.")
        self.copy_button.clicked.connect(self.duplicate_current)
        foot.addWidget(self.copy_button)
        self.close_button = QPushButton("Clore le sondage", page)
        self.close_button.clicked.connect(self.toggle_closed)
        foot.addWidget(self.close_button)
        self.send_button = QPushButton("Envoyer aux visiteurs", page)
        self.send_button.setObjectName("sGo")
        self.send_button.clicked.connect(self.send_current)
        foot.addWidget(self.send_button)
        lay.addLayout(foot)
        return page

    def _content(self) -> QWidget:
        page = QWidget(self)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)
        form = QHBoxLayout()
        form.addWidget(_label("Message :", "sDim", page))
        self.message = QLineEdit(page)
        self.message.setPlaceholderText("Facultatif : un mot pour les visiteurs, en tête du sondage")
        self.message.setMaxLength(sondages.MESSAGE_MAX)
        self.message.textEdited.connect(lambda text: self._edit(message=text))
        form.addWidget(self.message, 1)
        form.addSpacing(10)
        form.addWidget(_label("Compte pour", "sDim", page))
        self.reward = QSpinBox(page)
        self.reward.setRange(0, sondages.REWARD_MAX)
        self.reward.setSuffix(" vidéos")
        self.reward.setToolTip("Ce que vaut le sondage fini dans le compteur du bon d'achat "
                               f"({self._goal()} vidéos regardées).")
        self.reward.valueChanged.connect(lambda value: self._edit(reward=value))
        form.addWidget(self.reward)
        form.addSpacing(10)
        form.addWidget(_label("Pour :", "sDim", page))
        self.to_button = QPushButton("Tout le monde", page)
        self.to_button.setToolTip("Tout le monde, ou seulement certaines personnes.")
        self.to_button.clicked.connect(self._to_menu)
        form.addWidget(self.to_button)
        lay.addLayout(form)

        split = QSplitter(Qt.Horizontal, page)
        split.setChildrenCollapsible(False)
        # Les videos du sondage.
        left = QWidget(split)
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 6, 0)
        head = QHBoxLayout()
        self.items_head = _label("", "sHead", left)
        head.addWidget(self.items_head, 1)
        self.clear_button = QPushButton("Tout retirer", left)
        self.clear_button.clicked.connect(self._clear_items)
        head.addWidget(self.clear_button)
        left_lay.addLayout(head)
        self.items_hint = _label("", "sDim", left)
        self.items_hint.setWordWrap(True)
        left_lay.addWidget(self.items_hint)
        self.items = Grid(left)
        self.items.setContextMenuPolicy(Qt.CustomContextMenu)
        self.items.customContextMenuRequested.connect(self._item_menu)
        self.items.itemDoubleClicked.connect(self._play_item)
        self.items.reordered.connect(self._items_moved)
        left_lay.addWidget(self.items, 1)
        self.warn = _label("", "sWarn", left)
        self.warn.setWordWrap(True)
        self.warn.hide()
        left_lay.addWidget(self.warn)
        split.addWidget(left)
        # Ajouter des videos (ou, pour un sondage en texte, des questions).
        self.adder = QStackedWidget(split)
        right = QWidget(self.adder)
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(6, 0, 0, 0)
        right_lay.addWidget(_label("Ajouter des vidéos", "sHead", right))
        self.query = QLineEdit(right)
        self.query.setPlaceholderText("Chercher dans toute la collection (un mot, « or », -mot…)")
        self.query.setClearButtonEnabled(True)
        self.query.textChanged.connect(lambda _t: self._search_soon.start())
        right_lay.addWidget(self.query)
        quick = QHBoxLayout()
        self.picked_button = QPushButton("Les vidéos cochées", right)
        self.picked_button.setToolTip("Les vidéos cochées sur la planche de Prisme "
                                      "(dossiers cochés : toutes leurs vidéos).")
        self.picked_button.clicked.connect(self._add_picked)
        quick.addWidget(self.picked_button)
        self.shown_button = QPushButton("La vidéo affichée", right)
        self.shown_button.setToolTip("Celle de la fiche, dans la fenêtre de Prisme.")
        self.shown_button.clicked.connect(self._add_shown)
        quick.addWidget(self.shown_button)
        self.random_button = QPushButton("10 au hasard", right)
        self.random_button.setToolTip("Dix vidéos tirées au hasard dans la collection "
                                      "(ou dans la recherche, si elle est remplie).")
        self.random_button.clicked.connect(self._add_random)
        quick.addWidget(self.random_button)
        quick.addStretch(1)
        right_lay.addLayout(quick)
        self.pick_hint = _label("", "sDim", right)
        self.pick_hint.setWordWrap(True)
        right_lay.addWidget(self.pick_hint)
        self.found = QListWidget(right)
        self._grid(self.found)
        self.found.itemClicked.connect(self._found_clicked)
        self.found.setContextMenuPolicy(Qt.CustomContextMenu)
        self.found.customContextMenuRequested.connect(self._found_menu)
        right_lay.addWidget(self.found, 1)
        self.adder.addWidget(right)
        self.adder.addWidget(self._question_panel())
        split.addWidget(self.adder)
        split.setSizes([560, 520])
        lay.addWidget(split, 1)
        self._search_soon = QTimer(self)
        self._search_soon.setSingleShot(True)
        self._search_soon.setInterval(250)
        self._search_soon.timeout.connect(self._search)
        return page

    def _question_panel(self) -> QWidget:
        """Ajouter des questions, et choisir les reponses proposees."""
        page = QWidget(self.adder)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(6, 0, 0, 0)
        lay.setSpacing(7)
        lay.addWidget(_label("Ajouter des questions", "sHead", page))
        self.q_text = QLineEdit(page)
        self.q_text.setPlaceholderText("La question ou la proposition (ex. : Tajine de poulet)")
        self.q_text.setMaxLength(sondages.QUESTION_MAX)
        self.q_text.returnPressed.connect(self._add_question)
        lay.addWidget(self.q_text)
        row = QHBoxLayout()
        self.q_detail = QLineEdit(page)
        self.q_detail.setPlaceholderText("Une précision, facultative (ex. : plat marocain, "
                                         "aux citrons confits)")
        self.q_detail.setMaxLength(sondages.DETAIL_MAX)
        self.q_detail.returnPressed.connect(self._add_question)
        row.addWidget(self.q_detail, 1)
        self.q_add = QPushButton("Ajouter", page)
        self.q_add.clicked.connect(self._add_question)
        row.addWidget(self.q_add)
        lay.addLayout(row)
        lay.addWidget(_label("Plusieurs d'un coup : une par ligne (« question | précision » "
                             "pour ajouter une précision).", "sDim", page))
        self.q_many = QPlainTextEdit(page)
        self.q_many.setPlaceholderText("Tajine de poulet | plat marocain\nPaella\nRaclette")
        self.q_many.setFixedHeight(96)
        lay.addWidget(self.q_many)
        self.q_many_add = QPushButton("Tout ajouter", page)
        self.q_many_add.clicked.connect(self._add_many)
        lay.addWidget(self.q_many_add, 0, Qt.AlignLeft)
        lay.addSpacing(8)
        # Les reponses : celles d'une question a choix, ou les bouts de l'echelle.
        self.answers_head = _label("", "sHead", page)
        lay.addWidget(self.answers_head)
        self.answers_hint = _label("", "sDim", page)
        self.answers_hint.setWordWrap(True)
        lay.addWidget(self.answers_hint)
        self.options_box = QWidget(page)
        options_lay = QVBoxLayout(self.options_box)
        options_lay.setContentsMargins(0, 0, 0, 0)
        options_lay.setSpacing(5)
        self.preset = QComboBox(self.options_box)
        self.preset.addItem("Réponses toutes faites…")
        for choice in PRESETS:
            self.preset.addItem(" / ".join(choice), choice)
        self.preset.activated.connect(self._apply_preset)
        options_lay.addWidget(self.preset)
        self.option_edits = []
        for number in range(sondages.OPTIONS_MAX):
            edit = QLineEdit(self.options_box)
            edit.setMaxLength(sondages.OPTION_MAX)
            edit.setPlaceholderText(f"Réponse {number + 1}"
                                    + (" (facultative)" if number >= sondages.OPTIONS_MIN
                                       else ""))
            edit.textEdited.connect(lambda _t: self._options_edited())
            options_lay.addWidget(edit)
            self.option_edits.append(edit)
        lay.addWidget(self.options_box)
        self.ends_box = QWidget(page)
        ends = QFormLayout(self.ends_box)
        ends.setContentsMargins(0, 0, 0, 0)
        self.low_edit = QLineEdit(self.ends_box)
        self.high_edit = QLineEdit(self.ends_box)
        for edit in (self.low_edit, self.high_edit):
            edit.setMaxLength(sondages.OPTION_MAX)
            edit.textEdited.connect(lambda _t: self._ends_edited())
        ends.addRow("0 =", self.low_edit)
        ends.addRow("5 =", self.high_edit)
        lay.addWidget(self.ends_box)
        lay.addStretch(1)
        return page

    def _add_question(self) -> None:
        text = " ".join(self.q_text.text().split())
        if not text:
            self.q_text.setFocus()
            return
        self._add_questions([(text, " ".join(self.q_detail.text().split()))])
        self.q_text.clear()
        self.q_detail.clear()
        self.q_text.setFocus()

    def _add_many(self) -> None:
        found = []
        for line in self.q_many.toPlainText().splitlines():
            text, _bar, detail = line.partition("|")
            text = " ".join(text.split())
            if text:
                found.append((text, " ".join(detail.split())))
        if found:
            self._add_questions(found)
            self.q_many.clear()

    def _add_questions(self, found: list) -> None:
        survey = self._survey()
        if survey is None or survey["state"] != "brouillon":
            return
        room = sondages.MAX_ITEMS - len(survey["items"])
        for text, detail in found[:max(0, room)]:
            survey["items"].append({"id": sondages.new_id("i"),
                                    "text": text[:sondages.QUESTION_MAX],
                                    "detail": detail[:sondages.DETAIL_MAX]})
        if len(found) > room:
            self.saved.setText(f"Un sondage tient au plus {sondages.MAX_ITEMS} questions.")
        self._edit()
        self._fill_items()
        self.items.scrollToBottom()

    def _apply_preset(self, index: int) -> None:
        choice = self.preset.itemData(index)
        self.preset.setCurrentIndex(0)
        if not choice:
            return
        for edit, text in zip(self.option_edits, choice + [""] * sondages.OPTIONS_MAX):
            edit.setText(text)
        self._options_edited()

    def _options_edited(self) -> None:
        options = [" ".join(edit.text().split()) for edit in self.option_edits]
        self._edit(options=[one for one in options if one])

    def _ends_edited(self) -> None:
        self._edit(ends=[" ".join(self.low_edit.text().split()) or sondages.DEFAULT_ENDS[0],
                         " ".join(self.high_edit.text().split()) or sondages.DEFAULT_ENDS[1]])

    def _edit_question(self, entry: dict) -> None:
        dialog = QuestionDialog(entry, self)
        if dialog.exec() != QDialog.Accepted:
            return
        text = " ".join(dialog.text.text().split())
        if not text:
            return
        entry["text"] = text
        entry["detail"] = " ".join(dialog.detail.text().split())
        self._edit()
        self._fill_items()

    def _grid(self, view: QListWidget) -> None:
        view.setViewMode(QListWidget.IconMode)
        view.setIconSize(THUMB)
        view.setGridSize(QSize(THUMB.width() + 14, THUMB.height() + 40))
        view.setResizeMode(QListWidget.Adjust)
        view.setMovement(QListWidget.Static)
        view.setWordWrap(True)
        view.setUniformItemSizes(True)
        view.setSelectionMode(QAbstractItemView.SingleSelection)

    def _results(self) -> QWidget:
        page = QWidget(self)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)
        head = QHBoxLayout()
        self.results_head = _label("", "sHead", page)
        head.addWidget(self.results_head, 1)
        self.by_person = QPushButton("Par personne", page)
        self.by_person.setObjectName("sToggle")
        self.by_person.setCheckable(True)
        self.by_person.setChecked(True)
        self.by_all = QPushButton("Synthèse", page)
        self.by_all.setObjectName("sToggle")
        self.by_all.setCheckable(True)
        self.by_person.clicked.connect(lambda: self._results_mode(False))
        self.by_all.clicked.connect(lambda: self._results_mode(True))
        head.addWidget(self.by_person)
        head.addWidget(self.by_all)
        lay.addLayout(head)
        self.results_stack = QStackedWidget(page)
        # Par personne : la liste, et le rapport de celle qu'on choisit.
        person_page = QSplitter(Qt.Horizontal, page)
        person_page.setChildrenCollapsible(False)
        self.persons = QListWidget(person_page)
        self.persons.setMinimumWidth(220)
        self.persons.setSpacing(3)
        self.persons.currentItemChanged.connect(lambda *_a: self._fill_report())
        person_page.addWidget(self.persons)
        report_box = QWidget(person_page)
        report_lay = QVBoxLayout(report_box)
        report_lay.setContentsMargins(6, 0, 0, 0)
        self.report_head = _label("", "sHead", report_box)
        self.report_head.setWordWrap(True)
        report_lay.addWidget(self.report_head)
        self.report = QTreeWidget(report_box)
        self.report.setRootIsDecorated(False)
        self.report.setIconSize(SMALL)
        self.report.setUniformRowHeights(True)
        self.report.header().setStretchLastSection(False)
        self.report.itemDoubleClicked.connect(lambda item, column: self._play_row(item, column))
        self.report.setContextMenuPolicy(Qt.CustomContextMenu)
        self.report.customContextMenuRequested.connect(
            lambda point: self._row_menu(self.report, point))
        report_lay.addWidget(self.report, 1)
        person_page.addWidget(report_box)
        person_page.setSizes([260, 760])
        self.results_stack.addWidget(person_page)
        # La synthese.
        self.synthesis = QTreeWidget(page)
        self.synthesis.setRootIsDecorated(False)
        self.synthesis.setIconSize(SMALL)
        self.synthesis.setUniformRowHeights(True)
        self.synthesis.header().setStretchLastSection(False)
        self.synthesis.itemDoubleClicked.connect(
            lambda item, column: self._play_row(item, column))
        self.synthesis.setContextMenuPolicy(Qt.CustomContextMenu)
        self.synthesis.customContextMenuRequested.connect(
            lambda point: self._row_menu(self.synthesis, point))
        self.results_stack.addWidget(self.synthesis)
        lay.addWidget(self.results_stack, 1)
        foot = QHBoxLayout()
        self.results_hint = _label("Double-clic : la vidéo dans le lecteur flottant. Clic "
                                   "droit : la montrer dans l'Explorateur.", "sDim", page)
        foot.addWidget(self.results_hint, 1)
        self.board_button = QPushButton("Montrer dans Prisme", page)
        self.board_button.setToolTip("Les vidéos de ce rapport en planche, dans Prisme : "
                                     "chaque carte porte sa note.")
        self.board_button.clicked.connect(self._show_in_prisme)
        foot.addWidget(self.board_button)
        lay.addLayout(foot)
        return page

    # -- la liste des sondages -------------------------------------------------
    def _visible(self) -> list:
        return [one for one in self.surveys if one["state"] != "supprime"]

    def _survey(self, survey_id: str = "") -> dict | None:
        survey_id = survey_id or self.current_id
        return next((one for one in self.surveys if one["id"] == survey_id), None)

    def _subline(self, survey: dict) -> str:
        count = len(survey["items"])
        many = "s" if count > 1 else ""
        what = {"duel": f"{count} duel{many}", "notes": f"{count} vidéo{many}"}.get(
            survey["kind"], f"{count} question{many}")
        parts = [what]
        if survey["state"] == "brouillon":
            parts.append("brouillon")
        else:
            rows = self.rows.get(survey["id"], [])
            done = sum(1 for row in rows if row["done_at"])
            if survey["sent"]:
                parts.append(when(survey["sent"])[:5])
            parts.append(f"{done} fini{'s' if done > 1 else ''}" + (
                f" · {len(rows) - done} en cours" if len(rows) > done else ""))
        return "  ·  ".join(parts)

    def _fill_list(self) -> None:
        keep = self.current_id
        self.list.blockSignals(True)
        self.list.clear()
        for survey in self._visible():
            item = QListWidgetItem(self.list)
            item.setData(Qt.UserRole, survey["id"])
            card = QFrame()
            card.setObjectName("sCard")
            lay = QVBoxLayout(card)
            lay.setContentsMargins(10, 7, 10, 7)
            lay.setSpacing(2)
            top = QHBoxLayout()
            title = _label(ICONS[survey["kind"]] + (survey["title"] or "Sans titre"),
                           "sHead", card)
            top.addWidget(title, 1)
            pill = _label(sondages.STATES[survey["state"]], "sPill", card)
            pill.setProperty("state", survey["state"])
            top.addWidget(pill)
            lay.addLayout(top)
            lay.addWidget(_label(self._subline(survey), "sDim", card))
            item.setSizeHint(QSize(260, 54))
            self.list.setItemWidget(item, card)
            if survey["id"] == keep:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        live = sum(1 for one in self.surveys if one["state"] == "ouvert")
        self.where.setText(
            (f"{live} sondage(s) en cours. " if live else "")
            + "Les visiteurs les voient sur la page du PC comme sur celle du NAS.")

    def _picked_survey(self, item, _previous=None) -> None:
        if item is not None:
            self.select(item.data(Qt.UserRole))

    def select(self, survey_id: str, person: tuple | None = None) -> None:
        survey = self._survey(survey_id)
        if survey is None or survey["state"] == "supprime":
            self.current_id = ""
            self.stack.setCurrentIndex(0)
            return
        self.current_id = survey_id
        self.half = None
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.data(Qt.UserRole) == survey_id and self.list.currentItem() is not item:
                self.list.blockSignals(True)
                self.list.setCurrentItem(item)
                self.list.blockSignals(False)
        self.stack.setCurrentIndex(1)
        self._paint_survey()
        self._fill_results(person)
        if person is not None:
            self.tabs.setCurrentIndex(1)
        elif survey["state"] == "brouillon" or not self.rows.get(survey_id):
            self.tabs.setCurrentIndex(0)

    def _paint_survey(self) -> None:
        survey = self._survey()
        if survey is None:
            return
        draft = survey["state"] == "brouillon"
        duel = survey["kind"] == "duel"
        text = survey["kind"] in sondages.TEXT_KINDS
        self.kind_label.setText(KIND_LABELS[survey["kind"]])
        self.adder.setCurrentIndex(1 if text else 0)
        if text:
            choice = survey["kind"] == "choix"
            self.options_box.setVisible(choice)
            self.ends_box.setVisible(not choice)
            self.answers_head.setText("Les réponses proposées" if choice
                                      else "Les deux bouts de l'échelle")
            self.answers_hint.setText(
                "De deux à six réponses ; le visiteur en touche une par question." if choice
                else "Le visiteur répond de 0 à 5 ; ces mots s'affichent sous l'échelle.")
            options = survey.get("options") or sondages.DEFAULT_OPTIONS
            for edit, value in zip(self.option_edits,
                                   list(options) + [""] * sondages.OPTIONS_MAX):
                if edit.text() != value:
                    edit.setText(value)
            ends = survey.get("ends") or sondages.DEFAULT_ENDS
            if self.low_edit.text() != ends[0]:
                self.low_edit.setText(ends[0])
            if self.high_edit.text() != ends[1]:
                self.high_edit.setText(ends[1])
            for widget in [self.q_text, self.q_detail, self.q_add, self.q_many,
                           self.q_many_add, self.preset, self.low_edit,
                           self.high_edit] + self.option_edits:
                widget.setEnabled(draft)
        if self.name.text() != survey["title"]:
            self.name.setText(survey["title"])
        self.name.setReadOnly(not draft)
        self.pill.setText(sondages.STATES[survey["state"]])
        self.pill.setProperty("state", survey["state"])
        self.pill.style().unpolish(self.pill)
        self.pill.style().polish(self.pill)
        if self.message.text() != survey["message"]:
            self.message.setText(survey["message"])
        self.message.setReadOnly(not draft)
        self.reward.blockSignals(True)
        self.reward.setValue(survey["reward"])
        self.reward.blockSignals(False)
        self.reward.setEnabled(draft)
        self.to_button.setEnabled(draft)
        self._paint_to()
        for widget in (self.query, self.found, self.picked_button, self.shown_button,
                       self.random_button, self.clear_button):
            widget.setEnabled(draft)
        self.send_button.setVisible(draft)
        self.send_button.setEnabled(bool(survey["items"]))
        self.close_button.setVisible(not draft)
        self.close_button.setText("Rouvrir le sondage" if survey["state"] == "clos"
                                  else "Clore le sondage")
        self.copy_button.setVisible(not draft)
        self.pick_hint.setText(
            "Un clic sur une vidéo : la vidéo A d'une nouvelle paire ; un second clic : "
            "la B, et la paire est faite. Clic droit : la regarder." if duel else
            "Un clic sur une vidéo l'ajoute au sondage. Clic droit : la regarder.")
        if draft:
            self.saved.setText("Brouillon, gardé au fil de l'eau. Les visiteurs ne le "
                               "verront qu'une fois envoyé.")
        elif survey["state"] == "ouvert":
            self.saved.setText(f"Envoyé le {when(survey['sent'])} — il ne se modifie plus "
                               "(« Dupliquer » pour en refaire un).")
        else:
            self.saved.setText(f"Clos le {when(survey['closed'])} : il n'est plus proposé.")
        self._fill_items()
        if draft and not text and not self.found.count():
            self._search()

    # -- les videos du sondage -------------------------------------------------
    def _collection(self) -> tuple:
        """({empreinte: chemin}, [(chemin, nom replie)]) de toute la
        collection, refaits quand elle change."""
        from .textfold import fold
        from .web import Library
        window = self.window
        key = window._collection_key()
        if self._paths is None or self._paths[0] != key:
            videos = list(window._videos_from_items())
            self._paths = (key, {Library.mark(path): Path(path) for path in videos},
                           [(Path(path), fold(Path(path).name)) for path in videos])
        return self._paths[1], self._paths[2]

    def _path_of(self, mark: str):
        marks, _names = self._collection()
        found = marks.get(mark)
        if found is None:
            found = self.window.video_for_mark(mark)
        return found

    def _icon(self, path, size: QSize = THUMB, holder=None) -> QIcon:
        """L'icone d'une video ; si l'image n'est pas encore faite, `holder`
        (un element de liste ou d'arbre) la recevra."""
        if path is None:
            return QIcon(_blank(size, "absente"))
        file = self.thumbs.ask(path)
        if not file and holder is not None:
            self._items_for.setdefault(str(path), []).append((holder, size))
        return QIcon(_cover(file, size) if file else _blank(size, "…"))

    def _thumb_ready(self, path: str, file: str) -> None:
        self._thumb_ready_pairs(path)
        for holder, size in self._items_for.pop(path, []):
            icon = QIcon(_cover(file, size) if file else _blank(size, "?"))
            try:
                if isinstance(holder, QListWidgetItem):
                    holder.setIcon(icon)
                else:
                    item, column = holder
                    item.setIcon(column, icon)
            except RuntimeError:
                pass                    # l'element a disparu entre-temps

    def _video(self, path) -> dict:
        from .web import Library
        return {"mark": Library.mark(path), "name": Path(path).name}

    def _fill_items(self) -> None:
        survey = self._survey()
        if survey is None:
            return
        draft = survey["state"] == "brouillon"
        duel = survey["kind"] == "duel"
        text = survey["kind"] in sondages.TEXT_KINDS
        self.items.clear()
        if text:
            self.items.setViewMode(QListWidget.ListMode)
            self.items.setGridSize(QSize())
            self.items.setUniformItemSizes(False)
            self.items.setSpacing(2)
            self.items.setWordWrap(True)
            self.items.setDragDropMode(QAbstractItemView.InternalMove if draft
                                       else QAbstractItemView.NoDragDrop)
            for number, entry in enumerate(survey["items"], 1):
                item = QListWidgetItem(self.items)
                item.setData(Qt.UserRole, entry["id"])
                item.setText(f"{number}.  {entry['text']}"
                             + (f"\n      {entry['detail']}" if entry["detail"] else ""))
                item.setToolTip("Double-clic : la modifier. Glisser : changer l'ordre. "
                                "Suppr ou clic droit : la retirer.")
        elif duel:
            self.items.setViewMode(QListWidget.ListMode)
            self.items.setGridSize(QSize())
            self.items.setUniformItemSizes(False)
            self.items.setIconSize(QSize(SMALL.width() * 2 + 30, SMALL.height()))
            self.items.setSpacing(3)
            self.items.setDragDropMode(QAbstractItemView.InternalMove if draft
                                       else QAbstractItemView.NoDragDrop)
            for number, entry in enumerate(survey["items"], 1):
                item = QListWidgetItem(self.items)
                item.setData(Qt.UserRole, entry["id"])
                item.setText(f"{number}.  A : {entry['a']['name']}\n      B : {entry['b']['name']}")
                item.setIcon(self._pair_icon(entry, item))
                item.setToolTip("Double-clic : regarder la A. Clic droit : plus.")
            if self.half is not None and draft:
                item = QListWidgetItem(self.items)
                item.setData(Qt.UserRole, "")
                item.setText(f"{len(survey['items']) + 1}.  A : {self.half['name']}\n"
                             "      B : choisissez-la à droite…")
                item.setIcon(self._pair_icon({"a": self.half, "b": None}, item))
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        else:
            self._grid(self.items)
            self.items.setMovement(QListWidget.Snap if draft else QListWidget.Static)
            self.items.setDragDropMode(QAbstractItemView.InternalMove if draft
                                       else QAbstractItemView.NoDragDrop)
            for number, entry in enumerate(survey["items"], 1):
                item = QListWidgetItem(self.items)
                item.setData(Qt.UserRole, entry["id"])
                item.setText(f"{number}. {entry['name']}")
                item.setToolTip(entry["name"] + "\nDouble-clic : la regarder. Glisser : "
                                "changer l'ordre. Suppr ou clic droit : la retirer.")
                item.setIcon(self._icon(self._path_of(entry["mark"]), THUMB, item))
        count = len(survey["items"])
        self.items_head.setText(
            f"Les questions du sondage ({count})" if text else
            f"Les duels du sondage ({count})" if duel else f"Les vidéos du sondage ({count})")
        self.items_hint.setText(
            "" if not draft else
            "Ajoutez-les à droite. Double-clic : modifier ; glisser : changer l'ordre ; "
            "Suppr : retirer." if text else
            "Glissez une paire pour changer l'ordre. Clic droit : échanger A et B, retirer."
            if duel else "Glissez une vidéo pour changer l'ordre ; Suppr la retire.")
        self.send_button.setEnabled(draft and bool(count))
        self._paint_warn()

    def _pair_icon(self, entry: dict, item) -> QIcon:
        """Les deux vignettes d'une paire, cote a cote, avec « contre » entre."""
        width = SMALL.width() * 2 + 30
        out = QPixmap(width, SMALL.height())
        out.fill(QColor("#11151b"))
        painter = QPainter(out)
        missing = False
        for side, x in (("a", 0), ("b", SMALL.width() + 30)):
            video = entry.get(side)
            if video is None:
                painter.drawPixmap(x, 0, _blank(SMALL, "B ?"))
                continue
            path = self._path_of(video["mark"])
            file = self.thumbs.ask(path) if path is not None else ""
            if path is not None and not file:
                missing = True
            painter.drawPixmap(x, 0, _cover(file, SMALL) if file else
                               _blank(SMALL, "…" if path is not None else "absente"))
            painter.setPen(QColor("#ffffff"))
            painter.fillRect(x + 4, 4, 18, 18, QColor("#239c8b" if side == "a" else "#8c62dc"))
            painter.drawText(x + 4, 4, 18, 18, Qt.AlignCenter, side.upper())
        painter.setPen(QColor("#8b94a1"))
        painter.drawText(SMALL.width(), 0, 30, SMALL.height(), Qt.AlignCenter, "vs")
        painter.end()
        if missing:
            # Redessinee quand les vignettes arrivent.
            for side in ("a", "b"):
                video = entry.get(side)
                path = self._path_of(video["mark"]) if video else None
                if path is not None and not self.thumbs.done.get(str(path)):
                    self._pending_pairs.setdefault(str(path), []).append((item, entry))
        return QIcon(out)

    def _paint_warn(self) -> None:
        """Les videos que le NAS ne pourra pas montrer : hors de son partage,
        ou dans un dossier masque (BIN…)."""
        survey = self._survey()
        if survey is None:
            return
        if survey["kind"] in sondages.TEXT_KINDS:
            self.warn.hide()
            return
        from . import telcache
        from .nas_publish import share_root, unc
        from .scan import under_veiled
        phone = telcache.Matcher(self.window.phone_hidden())
        share = share_root(self.window.top_root() or "")
        marks = ([entry["mark"] for entry in survey["items"]] if survey["kind"] == "notes"
                 else [entry[side]["mark"] for entry in survey["items"] for side in ("a", "b")])
        gone = away = veiled = 0
        for mark in marks:
            path = self._path_of(mark)
            if path is None:
                gone += 1
            elif under_veiled(path, strict=True) or (phone and phone.path(path)):
                veiled += 1
            elif share is not None and not unc(path).lower().startswith(
                    str(share).rstrip("\\/").lower()):
                away += 1
        notes = []
        if gone:
            notes.append(f"{gone} vidéo(s) ne sont plus dans la collection (déplacées ou "
                         "supprimées) : elles ne seront pas proposées.")
        if veiled:
            notes.append(f"{veiled} vidéo(s) sont masquées, ou cachées sur le téléphone : la "
                         "page des visiteurs ne les montrera pas.")
        if away:
            notes.append(f"{away} vidéo(s) ne sont pas sur le NAS : seule la page servie par "
                         "le PC pourra les proposer.")
        self.warn.setText("⚠ " + "  ".join(notes) if notes else "")
        self.warn.setVisible(bool(notes))

    def _thumb_ready_pairs(self, path: str) -> None:
        for item, entry in self._pending_pairs.pop(path, []):
            try:
                item.setIcon(self._pair_icon(entry, item))
            except RuntimeError:
                pass

    def _add(self, paths: list) -> int:
        """Ajoute ces videos au brouillon (une seule fois chacune). Pour un
        duel : elles forment des paires, dans l'ordre."""
        survey = self._survey()
        if survey is None or survey["state"] != "brouillon":
            return 0
        added = 0
        if survey["kind"] == "notes":
            have = {entry["mark"] for entry in survey["items"]}
            for path in paths:
                video = self._video(path)
                if video["mark"] in have or len(survey["items"]) >= sondages.MAX_ITEMS:
                    continue
                have.add(video["mark"])
                survey["items"].append(dict(video, id=sondages.new_id("i")))
                added += 1
        else:
            for path in paths:
                video = self._video(path)
                if self.half is None:
                    self.half = video
                    continue
                if video["mark"] == self.half["mark"] or len(survey["items"]) >= sondages.MAX_ITEMS:
                    continue
                survey["items"].append({"id": sondages.new_id("i"), "a": self.half, "b": video})
                self.half = None
                added += 1
        self._edit()
        self._fill_items()
        self.items.scrollToBottom()
        if len(survey["items"]) >= sondages.MAX_ITEMS:
            self.saved.setText(f"Un sondage tient au plus {sondages.MAX_ITEMS} questions.")
        return added

    def _found_clicked(self, item) -> None:
        path = item.data(Qt.UserRole)
        if path:
            self._add([path])
            # La vignette choisie se grise : deja dans le sondage.
            self._mark_found()

    def _mark_found(self) -> None:
        survey = self._survey()
        if survey is None:
            return
        if survey["kind"] == "notes":
            used = {entry["mark"] for entry in survey["items"]}
        else:
            used = {entry[side]["mark"] for entry in survey["items"] for side in ("a", "b")}
            if self.half is not None:
                used.add(self.half["mark"])
        from .web import Library
        for row in range(self.found.count()):
            item = self.found.item(row)
            inside = Library.mark(item.data(Qt.UserRole)) in used
            item.setForeground(QColor("#5d6672" if inside else "#cdd5df"))
            name = Path(item.data(Qt.UserRole)).name
            item.setText(("✓ " if inside else "") + name)

    def _search(self) -> None:
        """La recherche, dans toute la collection (les memes regles que le
        champ « chercher… » de Prisme)."""
        from .query import parse, tester
        text = self.query.text().strip()
        _marks, names = self._collection()
        if text:
            test = tester(parse(text))
            found = [path for path, folded in names if test(folded)][:200]
            head = f"{len(found)} résultat(s)" if found else "Aucune vidéo ne répond."
        else:
            found = [path for path, _f in random.Random(int(time.time() // 600)).sample(
                names, min(60, len(names)))]
            head = ""
        self.found.clear()
        for path in found:
            item = QListWidgetItem(self.found)
            item.setData(Qt.UserRole, str(path))
            item.setText(path.name)
            item.setToolTip(f"{path.name}\n{path.parent}\nUn clic : l'ajouter. "
                            "Clic droit : la regarder.")
            item.setIcon(self._icon(path, THUMB, item))
        self._mark_found()
        if text:
            self.pick_hint.setText(head + " — " + (
                "un clic pour la A, un autre pour la B." if self._survey() and
                self._survey()["kind"] == "duel" else "un clic pour l'ajouter."))

    def _add_picked(self) -> None:
        videos = self.window._picked_videos() if self.window.board.picked_ids else []
        if not videos:
            QMessageBox.information(self, "Les vidéos cochées",
                                    "Rien n'est coché sur la planche de Prisme. Cochez des "
                                    "vidéos (ou des dossiers), puis revenez ici.")
            return
        self._add(videos)

    def _add_shown(self) -> None:
        from .scan import MODE_FOLDERS
        item = self.window.current
        if item is None or item.kind == MODE_FOLDERS or not item.videos:
            QMessageBox.information(self, "La vidéo affichée",
                                    "Ouvrez d'abord la fiche d'une vidéo dans Prisme.")
            return
        self._add([Path(item.videos[0])])

    def _add_random(self) -> None:
        text = self.query.text().strip()
        _marks, names = self._collection()
        if text:
            from .query import parse, tester
            test = tester(parse(text))
            pool = [path for path, folded in names if test(folded)]
        else:
            pool = [path for path, _f in names]
        survey = self._survey()
        want = 20 if survey and survey["kind"] == "duel" else 10
        self._add(random.sample(pool, min(want, len(pool))))

    def _items_moved(self, *_args) -> None:
        """L'ordre glisse a la main : le sondage le suit."""
        survey = self._survey()
        if survey is None or survey["state"] != "brouillon":
            return
        order = [self.items.item(row).data(Qt.UserRole) for row in range(self.items.count())]
        by_id = {entry["id"]: entry for entry in survey["items"]}
        survey["items"] = [by_id[one] for one in order if one in by_id]
        self._edit()
        QTimer.singleShot(0, self._fill_items)

    def _item_menu(self, point) -> None:
        item = self.items.itemAt(point)
        survey = self._survey()
        if item is None or survey is None:
            return
        entry_id = item.data(Qt.UserRole)
        entry = next((one for one in survey["items"] if one["id"] == entry_id), None)
        draft = survey["state"] == "brouillon"
        menu = QMenu(self)
        plays = []
        if entry is None and self.half is not None:
            cancel = menu.addAction("Annuler cette paire")
            if menu.exec(self.items.viewport().mapToGlobal(point)) is cancel:
                self.half = None
                self._fill_items()
                self._mark_found()
            return
        if entry is None:
            return
        edit = None
        if survey["kind"] in sondages.TEXT_KINDS:
            edit = menu.addAction("Modifier la question…") if draft else None
            swap = None
        elif survey["kind"] == "duel":
            plays = [(menu.addAction("Regarder la A"), entry["a"]["mark"]),
                     (menu.addAction("Regarder la B"), entry["b"]["mark"])]
            swap = menu.addAction("Échanger A et B") if draft else None
        else:
            plays = [(menu.addAction("Regarder dans le lecteur flottant"), entry["mark"])]
            swap = None
        drop = menu.addAction("Retirer du sondage") if draft else None
        chosen = menu.exec(self.items.viewport().mapToGlobal(point))
        if chosen is not None and chosen is edit:
            self._edit_question(entry)
            return
        for action, mark in plays:
            if chosen is action:
                self._play_mark(mark)
        if chosen is not None and chosen is swap:
            entry["a"], entry["b"] = entry["b"], entry["a"]
            self._edit()
            self._fill_items()
        if chosen is not None and chosen is drop:
            survey["items"] = [one for one in survey["items"] if one["id"] != entry_id]
            self._edit()
            self._fill_items()
            self._mark_found()

    def keyPressEvent(self, event):                 # noqa: N802
        if event.key() == Qt.Key_Delete and self.items.hasFocus():
            survey = self._survey()
            item = self.items.currentItem()
            if survey is not None and survey["state"] == "brouillon" and item is not None:
                entry_id = item.data(Qt.UserRole)
                survey["items"] = [one for one in survey["items"] if one["id"] != entry_id]
                self._edit()
                self._fill_items()
                self._mark_found()
                return
        super().keyPressEvent(event)

    def _clear_items(self) -> None:
        survey = self._survey()
        if survey is None or not survey["items"]:
            return
        if QMessageBox.question(self, "Tout retirer",
                                "Retirer toutes les vidéos de ce brouillon ?") != QMessageBox.Yes:
            return
        survey["items"] = []
        self.half = None
        self._edit()
        self._fill_items()
        self._mark_found()

    def _found_menu(self, point) -> None:
        item = self.found.itemAt(point)
        if item is None:
            return
        menu = QMenu(self)
        play = menu.addAction("Regarder dans le lecteur flottant")
        show = menu.addAction("Montrer dans l'Explorateur")
        chosen = menu.exec(self.found.viewport().mapToGlobal(point))
        if chosen is play:
            self.window.play_floating_path(item.data(Qt.UserRole))
        elif chosen is show:
            self.window.reveal_path(item.data(Qt.UserRole))

    def _play_item(self, item) -> None:
        survey = self._survey()
        entry = next((one for one in (survey or {}).get("items", [])
                      if one["id"] == item.data(Qt.UserRole)), None)
        if entry is None:
            return
        if survey["kind"] in sondages.TEXT_KINDS:
            if survey["state"] == "brouillon":
                self._edit_question(entry)
            return
        self._play_mark(entry["a"]["mark"] if survey["kind"] == "duel" else entry["mark"])

    def _play_mark(self, mark: str) -> None:
        path = self._path_of(mark)
        if path is None:
            QMessageBox.information(self, "Vidéo introuvable",
                                    "Cette vidéo n'est plus dans la collection : déplacée, "
                                    "renommée ou supprimée depuis.")
            return
        self.window.play_floating_path(path)

    # -- a qui -----------------------------------------------------------------
    def _paint_to(self) -> None:
        survey = self._survey()
        if survey is None:
            return
        chosen = survey["to"]
        if not chosen:
            self.to_button.setText("Tout le monde")
            return
        names = [one["name"] for one in self.people if one["key"] in chosen]
        self.to_button.setText(
            f"{len(chosen)} personne(s)" + (f" : {', '.join(names[:2])}"
                                             + ("…" if len(names) > 2 else "") if names else ""))

    def _to_menu(self) -> None:
        survey = self._survey()
        if survey is None:
            return
        menu = QMenu(self)
        everyone = menu.addAction("Tout le monde")
        everyone.setCheckable(True)
        everyone.setChecked(not survey["to"])
        menu.addSeparator()
        if not self.people:
            empty = menu.addAction("Personne n'a encore de profil")
            empty.setEnabled(False)
        actions = []
        for person in self.people:
            text = person["name"] + (f"  ·  {person['email']}" if person["email"] else "") + (
                "  (NAS)" if person["where"] == "NAS" else "")
            action = menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(person["key"] in survey["to"])
            actions.append((action, person["key"]))
        chosen = menu.exec(self.to_button.mapToGlobal(self.to_button.rect().bottomLeft()))
        if chosen is None:
            return
        if chosen is everyone:
            survey["to"] = []
        else:
            keys = set(survey["to"])
            for action, key in actions:
                if action is chosen:
                    keys.symmetric_difference_update({key})
            survey["to"] = sorted(keys)
        self._edit()
        self._paint_to()

    # -- creer, envoyer, clore -------------------------------------------------
    def create(self, kind: str) -> None:
        now = time.time()
        survey = sondages.clean({
            "id": sondages.new_id(), "kind": kind, "state": "brouillon",
            "title": FIRST_TITLES[kind],
            "reward": sondages.REWARD, "created": now, "updated": now, "items": []})
        self.surveys.insert(0, survey)
        self._save_now()
        self._fill_list()
        self.select(survey["id"])
        self.tabs.setCurrentIndex(0)
        self.name.setFocus()
        self.name.selectAll()

    def _edit(self, **changes) -> None:
        """Une retouche du brouillon : gardee tout de suite (un instant apres
        la derniere frappe)."""
        survey = self._survey()
        if survey is None:
            return
        if survey["state"] != "brouillon" and set(changes) - {"state", "closed"}:
            return
        if "title" in changes:
            changes["title"] = " ".join(changes["title"].split())[:sondages.TITLE_MAX]
        survey.update(changes)
        survey["updated"] = time.time()
        self._save_timer.start()
        self._refresh_card(survey)

    def _refresh_card(self, survey: dict) -> None:
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.data(Qt.UserRole) == survey["id"]:
                card = self.list.itemWidget(item)
                if card is not None:
                    title = card.findChild(QLabel, "sHead")
                    if title is not None:
                        title.setText(ICONS[survey["kind"]] + (survey["title"] or "Sans titre"))
                    sub = card.findChild(QLabel, "sDim")
                    if sub is not None:
                        sub.setText(self._subline(survey))

    def _save_now(self) -> None:
        self._save_timer.stop()
        if not sondages.save(self.path, self.surveys):
            self.saved.setText("⚠ Le fichier des sondages n'a pas pu être écrit.")
            return
        self._sync()

    def _sync(self) -> None:
        """Les sondages, recopies sur le NAS (et ceux qu'un autre PC y a mis,
        repris ici), hors du fil de l'interface."""
        from .nas_publish import share_root
        from .tunnel import Chore
        share = share_root(self.window.top_root() or "")
        if share is None:
            return
        if self._syncing:
            self._sync_again = True
            return
        self._syncing = True
        from .nas_publish import sync_surveys

        def then(merged) -> None:
            if not shiboken6.isValid(self):
                return
            self._syncing = False
            # Ce qu'un autre PC a mis sur le NAS : repris, sans rien perdre de ce
            # qui vient d'etre retouche ici (le plus recent gagne).
            if (merged is not None and not self._sync_again
                    and not self._save_timer.isActive()):
                joined = sondages.merge(self.surveys, merged)
                if joined != self.surveys:
                    keep = self.current_id
                    self.surveys = joined
                    self._fill_list()
                    if keep:
                        self.select(keep)
            if self._sync_again:
                self._sync_again = False
                self._sync()
        Chore(lambda: sync_surveys(share), self, fallback=None, then=then).start()

    def send_current(self) -> None:
        survey = self._survey()
        if survey is None or survey["state"] != "brouillon":
            return
        if not survey["items"]:
            return
        if survey["kind"] == "duel" and self.half is not None:
            self.half = None
        who = ("tous les visiteurs" if not survey["to"]
               else f"{len(survey['to'])} personne(s)")
        if survey["kind"] == "choix" and len(survey.get("options") or []) < sondages.OPTIONS_MIN:
            QMessageBox.information(self, "Envoyer le sondage",
                                    "Proposez au moins deux réponses.")
            return
        what = {"duel": f"{len(survey['items'])} duel(s)",
                "notes": f"{len(survey['items'])} vidéo(s) à noter"}.get(
            survey["kind"], f"{len(survey['items'])} question(s)")
        if QMessageBox.question(
                self, "Envoyer le sondage",
                f"Envoyer « {survey['title'] or 'Sans titre'} » ({what}) à {who} ?\n\n"
                "Il s'annoncera sur leur page — tout de suite pour ceux qui y sont, à la "
                "prochaine ouverture pour les autres — et ne se modifiera plus.") \
                != QMessageBox.Yes:
            return
        if not survey["title"]:
            survey["title"] = "Laquelle préférez-vous ?" if survey["kind"] == "duel" \
                else "Notez ces vidéos"
        now = time.time()
        survey.update(state="ouvert", sent=now, updated=now)
        self._save_now()
        self._fill_list()
        self.select(survey["id"])
        self.window.show_banner(f"Sondage « {survey['title']} » envoyé : il s'annonce sur la "
                                "page des visiteurs.", "done")

    def toggle_closed(self) -> None:
        survey = self._survey()
        if survey is None or survey["state"] == "brouillon":
            return
        now = time.time()
        if survey["state"] == "ouvert":
            survey.update(state="clos", closed=now, updated=now)
        else:
            survey.update(state="ouvert", closed=0.0, updated=now)
        self._save_now()
        self._fill_list()
        self._paint_survey()

    def duplicate_current(self) -> None:
        survey = self._survey()
        if survey is None:
            return
        now = time.time()
        copy = sondages.clean(dict(survey, id=sondages.new_id(), state="brouillon",
                                   title=(survey["title"] + " (2)")[:sondages.TITLE_MAX],
                                   created=now, updated=now, sent=0.0, closed=0.0,
                                   items=[dict(entry, id=sondages.new_id("i"))
                                          for entry in survey["items"]]))
        self.surveys.insert(0, copy)
        self._save_now()
        self._fill_list()
        self.select(copy["id"])
        self.tabs.setCurrentIndex(0)

    def delete_current(self) -> None:
        survey = self._survey()
        if survey is None:
            return
        answered = len(self.rows.get(survey["id"], []))
        if QMessageBox.question(
                self, "Supprimer le sondage",
                f"Supprimer « {survey['title'] or 'Sans titre'} » ?"
                + (f" Les réponses de {answered} personne(s) ne seront plus montrées."
                   if answered else "")
                + (" Il ne sera plus proposé aux visiteurs." if survey["state"] == "ouvert"
                   else "")) != QMessageBox.Yes:
            return
        survey.update(state="supprime", updated=time.time(), items=[])
        self._save_now()
        self.current_id = ""
        self._fill_list()
        visible = self._visible()
        if visible:
            self.select(visible[0]["id"])
        else:
            self.stack.setCurrentIndex(0)

    # -- les reponses ----------------------------------------------------------
    def _sources(self) -> list:
        from .nas_publish import FOLDER, share_root
        sources = [("PC", self.answers_pc)]
        share = share_root(self.window.top_root() or "")
        if share is not None:
            sources.append(("NAS", Path(share) / FOLDER / "etat" / sondages.ANSWERS_NAME))
        return sources

    def _people_sources(self) -> list:
        from . import profils
        from .config import PRIVATE_DIR
        from .demandes import FILE_NAME
        from .nas_publish import FOLDER, share_root
        found = [("PC", profils.path_for(PRIVATE_DIR / FILE_NAME))]
        share = share_root(self.window.top_root() or "")
        if share is not None:
            found.append(("NAS", Path(share) / FOLDER / "etat" / profils.FILE_NAME))
        return found

    def _read_answers(self) -> None:
        """Les reponses des deux serveurs, et les profils (pour « Pour : »),
        relus hors du fil de l'interface toutes les cinq secondes."""
        if self._reading or not self.isVisible() and self.rows:
            return
        from . import profils
        from .tunnel import Chore
        self._reading = True
        sources, people_sources = self._sources(), self._people_sources()

        def work():
            rows = sondages.reports(sources)
            people = []
            for where, path in people_sources:
                for key, profile in profils.read_all(path).items():
                    people.append({"where": where, "key": key,
                                   "name": profile.get("name") or "Sans nom",
                                   "email": profile.get("email", "")})
            people.sort(key=lambda one: one["name"].casefold())
            return rows, people

        def then(result) -> None:
            if not shiboken6.isValid(self):
                return
            self._reading = False
            if result is None:
                return
            rows, people = result
            changed = repr(rows) != repr(self.rows)
            self.rows, self.people = rows, people
            if changed:
                self._fill_list()
                if self.current_id:
                    self._fill_results(self._chosen_person())
            self._paint_to()
        Chore(work, self, fallback=None, then=then).start()

    def _who(self, row: dict) -> str:
        label = row.get("label", "")
        given = self.window.alias(label) if label else ""
        if given and given != label:
            return given
        return row.get("name") or label or "Visiteur"

    def _chosen_person(self):
        item = self.persons.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def _results_mode(self, synthesis: bool) -> None:
        self.by_person.setChecked(not synthesis)
        self.by_all.setChecked(synthesis)
        self.results_stack.setCurrentIndex(1 if synthesis else 0)

    def _fill_results(self, person=None) -> None:
        survey = self._survey()
        if survey is None:
            return
        rows = self.rows.get(survey["id"], [])
        total = len(survey["items"])
        done = sum(1 for row in rows if row["done_at"])
        self.tabs.setTabText(1, f"Résultats ({len(rows)})" if rows else "Résultats")
        if survey["state"] == "brouillon":
            self.results_head.setText("Pas encore envoyé : les réponses arriveront ici.")
        elif not rows:
            self.results_head.setText("Personne n'a encore répondu.")
        else:
            self.results_head.setText(
                f"{len(rows)} participant(s) · {done} ont fini"
                + (f" · {len(rows) - done} en cours" if len(rows) > done else ""))
        # Par personne.
        keep = person if person is not None else self._chosen_person()
        self.persons.blockSignals(True)
        self.persons.clear()
        chosen = None
        for row in rows:
            answered = sum(1 for entry in survey["items"] if entry["id"] in row["votes"])
            state = (f"✓ fini le {when(row['done_at'])}" if row["done_at"]
                     else f"en cours : {answered} / {total}")
            item = QListWidgetItem(f"{self._who(row)}\n{state}"
                                   + ("  ·  NAS" if row["where"] == "NAS" else ""))
            item.setData(Qt.UserRole, (row["where"], row["me"]))
            item.setForeground(QColor("#ffffff" if row["done_at"] else "#aeb8c5"))
            self.persons.addItem(item)
            if keep is not None and tuple(keep) == (row["where"], row["me"]):
                chosen = item
        if chosen is None and self.persons.count():
            chosen = self.persons.item(0)
        if chosen is not None:
            self.persons.setCurrentItem(chosen)
        self.persons.blockSignals(False)
        self._fill_report()
        self._fill_synthesis()
        self.board_button.setEnabled(bool(survey["items"]))
        self.board_button.setVisible(survey["kind"] not in sondages.TEXT_KINDS)
        self.results_hint.setVisible(survey["kind"] not in sondages.TEXT_KINDS)

    def _row_for(self, key):
        survey = self._survey()
        if survey is None or key is None:
            return None
        return next((row for row in self.rows.get(survey["id"], [])
                     if (row["where"], row["me"]) == tuple(key)), None)

    def _fill_report(self) -> None:
        survey = self._survey()
        self.report.clear()
        if survey is None:
            return
        row = self._row_for(self._chosen_person())
        duel = survey["kind"] == "duel"
        if survey["kind"] in sondages.TEXT_KINDS:
            return self._fill_text_report(survey, row)
        self.report.setIconSize(SMALL)
        if duel:
            self._headers(self.report, ["Vidéo A", "", "Vidéo B", "Choix", "Quand"])
        else:
            self._headers(self.report, ["Vidéo", "Note", "Temps", "Quand"])
        if row is None:
            self.report_head.setText("")
            return
        votes = row["votes"]
        self.report_head.setText(
            f"Le rapport de {self._who(row)} — "
            + (f"fini le {when(row['done_at'])}" if row["done_at"] else "pas encore fini")
            + f" · par le {row['where']}")
        for entry in survey["items"]:
            vote = votes.get(entry["id"])
            if duel:
                side = vote["value"] if vote else ""
                item = QTreeWidgetItem(self.report, [
                    entry["a"]["name"], "vs", entry["b"]["name"],
                    {"a": "◀ A", "b": "B ▶"}.get(side, "—"),
                    when(vote["at"]) if vote else ""])
                item.setIcon(0, self._icon(self._path_of(entry["a"]["mark"]), SMALL,
                                           (item, 0)))
                item.setIcon(2, self._icon(self._path_of(entry["b"]["mark"]), SMALL,
                                           (item, 2)))
                item.setData(0, Qt.UserRole, entry["a"]["mark"])
                item.setData(2, Qt.UserRole, entry["b"]["mark"])
                for column, mine in ((0, "a"), (2, "b")):
                    if side == mine:
                        item.setForeground(column, QColor("#7bd88f"))
                    elif side:
                        item.setForeground(column, QColor("#6f7885"))
            else:
                value = vote["value"] if vote else None
                item = QTreeWidgetItem(self.report, [
                    entry["name"],
                    f"{stars(value)}  {value} — {NOTE_WORDS.get(value, '')}" if value else "—",
                    spell(vote["seconds"]) if vote and vote.get("seconds") else "",
                    when(vote["at"]) if vote else ""])
                item.setIcon(0, self._icon(self._path_of(entry["mark"]), SMALL, (item, 0)))
                item.setData(0, Qt.UserRole, entry["mark"])
                if value:
                    item.setForeground(1, QColor(
                        {1: "#e06c7c", 2: "#e0915f", 3: "#d8c05a", 4: "#8fd16f",
                         5: "#5fd3a0"}[value]))
        header = self.report.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        if duel:
            header.setSectionResizeMode(2, QHeaderView.Stretch)
            for column in (1, 3, 4):
                header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        else:
            for column in (1, 2, 3):
                header.setSectionResizeMode(column, QHeaderView.ResizeToContents)

    def _answer_text(self, survey: dict, value) -> str:
        if survey["kind"] == "choix":
            options = survey.get("options") or []
            return options[value] if isinstance(value, int) and 0 <= value < len(options) \
                else "—"
        if value in sondages.SCALE:
            words = {0: survey["ends"][0], 5: survey["ends"][1]}
            return f"{'▮' * value}{'▯' * (5 - value)}  {value} / 5" + (
                f" — {words[value]}" if value in words else "")
        return "—"

    def _fill_text_report(self, survey: dict, row) -> None:
        self._headers(self.report, ["Question", "Réponse", "Quand"])
        self.report.setIconSize(QSize(0, 0))
        if row is None:
            self.report_head.setText("")
            return
        self.report_head.setText(
            f"Les réponses de {self._who(row)} — "
            + (f"fini le {when(row['done_at'])}" if row["done_at"] else "pas encore fini")
            + f" · par le {row['where']}")
        for entry in survey["items"]:
            vote = row["votes"].get(entry["id"])
            value = vote["value"] if vote else None
            item = QTreeWidgetItem(self.report, [
                entry["text"] + (f"  ({entry['detail']})" if entry["detail"] else ""),
                self._answer_text(survey, value) if vote else "—",
                when(vote["at"]) if vote else ""])
            if vote and survey["kind"] == "echelle" and value in SCALE_COLORS:
                item.setForeground(1, QColor(SCALE_COLORS[value]))
            elif vote and survey["kind"] == "choix":
                item.setForeground(1, QColor(["#8fb4ff", "#c3a6e0", "#aeb8c5", "#e0b06a",
                                              "#7bd88f", "#ff9ca3"][value % 6]))
        header = self.report.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)

    def _fill_text_synthesis(self, survey: dict, rows: list) -> None:
        by_item = sondages.summary(survey, rows)
        self.synthesis.setIconSize(QSize(0, 0))
        if survey["kind"] == "choix":
            options = survey.get("options") or []
            self._headers(self.synthesis, ["Question"] + options + ["Réponses"])
            for entry in survey["items"]:
                counts = by_item.get(entry["id"], [0] * len(options))
                total = sum(counts)
                cells = [f"{n}  ({round(100 * n / total)} %)" if total else "—" for n in counts]
                item = QTreeWidgetItem(self.synthesis, [
                    entry["text"] + (f"  ({entry['detail']})" if entry["detail"] else "")]
                    + cells + [str(total)])
                if total:
                    best = max(range(len(counts)), key=lambda i: counts[i])
                    item.setForeground(1 + best, QColor("#7bd88f"))
                for column in range(1, len(options) + 2):
                    item.setTextAlignment(column, Qt.AlignCenter)
            header = self.synthesis.header()
            header.setSectionResizeMode(0, QHeaderView.Stretch)
            for column in range(1, len(options) + 2):
                header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
            return
        self._headers(self.synthesis, ["Question", "Intérêt moyen", "Réponses",
                                       "Répartition (0 → 5)"])
        scored = []
        for entry in survey["items"]:
            values = by_item.get(entry["id"], [])
            average = sum(values) / len(values) if values else -1.0
            scored.append((average, len(values), entry, values))
        scored.sort(key=lambda one: (-one[0], -one[1]))
        for average, count, entry, values in scored:
            item = QTreeWidgetItem(self.synthesis, [
                entry["text"] + (f"  ({entry['detail']})" if entry["detail"] else ""),
                f"{average:.1f} / 5".replace(".", ",") if values else "—", str(count),
                "   ".join(f"{n}:{values.count(n)}" for n in sondages.SCALE) if values else ""])
            if values:
                item.setForeground(1, QColor(SCALE_COLORS[round(average)]))
            item.setTextAlignment(2, Qt.AlignCenter)
        header = self.synthesis.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)

    @staticmethod
    def _headers(tree: QTreeWidget, labels: list) -> None:
        """Les colonnes d'un arbre : d'un duel aux notes, il en garde une de trop."""
        tree.setColumnCount(len(labels))
        tree.setHeaderLabels(labels)

    def _fill_synthesis(self) -> None:
        survey = self._survey()
        self.synthesis.clear()
        if survey is None:
            return
        rows = self.rows.get(survey["id"], [])
        if survey["kind"] in sondages.TEXT_KINDS:
            return self._fill_text_synthesis(survey, rows)
        self.synthesis.setIconSize(SMALL)
        by_item = sondages.summary(survey, rows)
        if survey["kind"] == "duel":
            self._headers(self.synthesis, ["Vidéo A", "Score", "Vidéo B", "Préférée"])
            for entry in survey["items"]:
                counts = by_item.get(entry["id"], {"a": 0, "b": 0})
                a, b = counts["a"], counts["b"]
                best = "A" if a > b else "B" if b > a else ("égalité" if a else "—")
                item = QTreeWidgetItem(self.synthesis, [
                    entry["a"]["name"], f"{a}  –  {b}", entry["b"]["name"],
                    best + (f"  ({round(100 * max(a, b) / (a + b))} %)" if a != b else "")])
                item.setIcon(0, self._icon(self._path_of(entry["a"]["mark"]), SMALL, (item, 0)))
                item.setIcon(2, self._icon(self._path_of(entry["b"]["mark"]), SMALL, (item, 2)))
                item.setData(0, Qt.UserRole, entry["a"]["mark"])
                item.setData(2, Qt.UserRole, entry["b"]["mark"])
                item.setTextAlignment(1, Qt.AlignCenter)
                if a != b:
                    item.setForeground(0 if a > b else 2, QColor("#7bd88f"))
            header = self.synthesis.header()
            header.setSectionResizeMode(0, QHeaderView.Stretch)
            header.setSectionResizeMode(2, QHeaderView.Stretch)
            for column in (1, 3):
                header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
            return
        self._headers(self.synthesis, ["Vidéo", "Moyenne", "Votes", "Répartition (1 → 5)"])
        scored = []
        for entry in survey["items"]:
            notes = by_item.get(entry["id"], [])
            average = sum(notes) / len(notes) if notes else 0.0
            scored.append((average, len(notes), entry, notes))
        scored.sort(key=lambda one: (-one[0], -one[1]))
        for average, count, entry, notes in scored:
            spread = "   ".join(f"{n}:{notes.count(n)}" for n in sondages.NOTES) if notes else ""
            item = QTreeWidgetItem(self.synthesis, [
                entry["name"],
                f"{stars(round(average))}  {average:.1f}".replace(".", ",") if notes else "—",
                str(count), spread])
            item.setIcon(0, self._icon(self._path_of(entry["mark"]), SMALL, (item, 0)))
            item.setData(0, Qt.UserRole, entry["mark"])
            item.setTextAlignment(2, Qt.AlignCenter)
        header = self.synthesis.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)

    def _play_row(self, item, column: int) -> None:
        mark = item.data(column, Qt.UserRole) or item.data(0, Qt.UserRole)
        if mark:
            self._play_mark(mark)

    def _row_menu(self, tree: QTreeWidget, point) -> None:
        item = tree.itemAt(point)
        if item is None:
            return
        marks = [(item.text(0), item.data(0, Qt.UserRole))]
        if item.data(2, Qt.UserRole):
            marks.append((item.text(2), item.data(2, Qt.UserRole)))
        menu = QMenu(self)
        actions = []
        for name, mark in marks:
            short = name if len(name) < 50 else name[:47] + "…"
            actions.append((menu.addAction(f"Regarder « {short} »"), mark, "play"))
            actions.append((menu.addAction(f"Montrer « {short} » dans l'Explorateur"), mark,
                            "show"))
        chosen = menu.exec(tree.viewport().mapToGlobal(point))
        for action, mark, what in actions:
            if chosen is action:
                if what == "play":
                    self._play_mark(mark)
                else:
                    path = self._path_of(mark)
                    if path is not None:
                        self.window.reveal_path(str(path))

    def _show_in_prisme(self) -> None:
        """Le rapport en planche, dans Prisme : la note (ou le choix) sur
        chaque carte. Synthese : la moyenne, de la mieux notee a la moins."""
        survey = self._survey()
        if survey is None:
            return
        synthesis = self.by_all.isChecked()
        entries = []                     # (chemin, note de la carte)
        if synthesis:
            tree = self.synthesis
            title = f"Sondage « {survey['title']} » — synthèse"
        else:
            tree = self.report
            row = self._row_for(self._chosen_person())
            title = (f"Sondage « {survey['title']} » — {self._who(row)}" if row
                     else f"Sondage « {survey['title']} »")
        for index in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(index)
            if survey["kind"] == "duel":
                for column, other in ((0, "B"), (2, "A")):
                    path = self._path_of(item.data(column, Qt.UserRole))
                    if path is None:
                        continue
                    side = "A" if column == 0 else "B"
                    if synthesis:
                        note = f"duel {index + 1} · {side} · {item.text(1)}"
                    else:
                        picked = item.text(3)
                        note = (f"duel {index + 1} · {side}"
                                + (" · ✓ choisie" if side in picked else
                                   " · écartée" if picked != "—" else ""))
                    entries.append((path, note))
            else:
                path = self._path_of(item.data(0, Qt.UserRole))
                if path is None:
                    continue
                note = item.text(1).strip()
                if synthesis and item.text(2) not in ("", "0"):
                    note += f" · {item.text(2)} vote(s)"
                entries.append((path, note if note != "—" else "pas encore notée"))
        if not entries:
            QMessageBox.information(self, "Montrer dans Prisme",
                                    "Aucune de ces vidéos n'est encore dans la collection.")
            return
        self.window.show_survey_board(entries, title)
        self.window.raise_()
        self.window.activateWindow()

    # -- la vie de la fenetre --------------------------------------------------
    def showEvent(self, event):                     # noqa: N802
        super().showEvent(event)
        if not self.beat.isActive():
            self.beat.start()
        self._read_answers()

    def hideEvent(self, event):                     # noqa: N802
        self.beat.stop()
        super().hideEvent(event)

    def closeEvent(self, event):                    # noqa: N802
        if self._save_timer.isActive():
            self._save_now()
        self.beat.stop()
        self.thumbs.close()
        super().closeEvent(event)

    def _goal(self) -> int:
        from . import profils
        return profils.GOAL
