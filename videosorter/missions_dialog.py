"""La fenêtre « Vidéo à la demande » : les missions, et les vidéos reçues.

À gauche, les missions ; à droite, celle qu'on a choisie, en deux onglets :

* **Mission** -- son titre, sa consigne, son prix (en bon d'achat), combien
  de vidéos on en attend au plus, et à qui elle s'adresse. « Publier » la
  montre aux visiteurs (Récompenses › Vidéo à la demande) ; « Clore » la
  retire.
* **Vidéos reçues** -- chaque envoi : qui, quand, la vidéo (double-clic :
  le lecteur flottant). On la valide (un bon d'achat est alors à envoyer à
  l'adresse de la personne), on la refuse en disant pourquoi, puis on note
  « bon envoyé ». La personne suit tout cela sur sa page.

Les missions et les verdicts vivent dans `missions.json` (`missions.py`),
réunis entre le PC et le NAS ; les vidéos arrivent dans `missions-recues`,
chez Prisme ou dans le partage du NAS, selon la page qui les a reçues.
"""
from __future__ import annotations

import time
from pathlib import Path

import shiboken6
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QFormLayout, QFrame, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit, QPushButton,
    QSpinBox, QStackedWidget, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget,
)

from . import missions
from .access import when
from .sondages_dialog import SMALL, STYLE, Thumbs, _blank, _cover, _label

MORE_STYLE = """
QLabel#sPill[state="ouverte"] { background: #173524; color: #7bd88f; }
QLabel#sPill[state="close"] { background: #2a2330; color: #c3a6e0; }
QLabel#mPrice { color: #f6e3b4; font-size: 12px; font-weight: 700; }
QLabel#mVerdict { font-size: 13px; font-weight: 600; }
QPlainTextEdit#mBrief { font-size: 14px; }
"""

VERDICT_COLORS = {"attente": "#9fc0ff", "validee": "#e8c068", "payee": "#7bd88f",
                  "refusee": "#ff9ca3", "envoi": "#8b94a1"}


def _weight(size: int) -> str:
    size = float(size or 0)
    if size >= 1024 ** 3:
        return f"{size / 1024 ** 3:.2f} Go".replace(".", ",")
    return f"{max(1, round(size / 1024 ** 2))} Mo"


class MissionsWindow(QWidget):
    """Les missions video, a cote de Prisme (pas modale)."""

    def __init__(self, window):
        super().__init__(window, Qt.Window)
        self.window = window
        self.setObjectName("surveys")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setWindowTitle("Vidéo à la demande — missions")
        self.setStyleSheet(STYLE + MORE_STYLE)
        self.resize(1120, 760)
        from .config import PRIVATE_DIR
        self.path = PRIVATE_DIR / missions.FILE_NAME
        self.book = missions.load(self.path)
        self.sends: list = []
        self.people: list = []
        self.current_id = ""
        self._reading = False
        self._syncing = False
        self._sync_again = False
        self._painting = False
        self.thumbs = Thumbs(int(window.cfg["thumb_width"] or 480), self)
        self.thumbs.ready.connect(self._thumb_ready)
        self._waiting_thumbs: dict = {}

        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(12)
        outer.addWidget(self._column(), 0)
        self.stack = QStackedWidget(self)
        self.stack.addWidget(self._welcome())
        self.stack.addWidget(self._editor())
        outer.addWidget(self.stack, 1)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self._save_now)
        self.beat = QTimer(self)
        self.beat.setInterval(5000)
        self.beat.timeout.connect(self._read)
        self.beat.start()
        self._fill_list()
        self._read()
        visible = self._visible()
        if visible:
            self.select(visible[0]["id"])

    # -- la colonne --------------------------------------------------------------
    def _column(self) -> QWidget:
        box = QWidget(self)
        box.setFixedWidth(300)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(_label("Vidéo à la demande", "sTitle", box))
        new = QPushButton("＋  Nouvelle mission", box)
        new.setObjectName("sNew")
        new.clicked.connect(self.create)
        lay.addWidget(new)
        self.list = QListWidget(box)
        self.list.setObjectName("sList")
        self.list.currentItemChanged.connect(
            lambda item, _old: item is not None and self.select(item.data(Qt.UserRole)))
        lay.addWidget(self.list, 1)
        self.where = _label("", "sDim", box)
        self.where.setWordWrap(True)
        lay.addWidget(self.where)
        return box

    def _welcome(self) -> QWidget:
        page = QWidget(self)
        lay = QVBoxLayout(page)
        lay.addStretch(1)
        big = _label("Des petites missions, payées en bons d'achat", "sBig", page)
        big.setAlignment(Qt.AlignCenter)
        lay.addWidget(big)
        text = _label(
            "Une mission, c'est une vidéo que vous demandez à vos visiteurs : un titre, une "
            "consigne précise (ce qu'il faut filmer, la durée, la lumière…) et un prix, en bon "
            "d'achat Amazon.\n\nPubliée, elle paraît sur leur page (Récompenses › « Vidéo à la "
            "demande » › « Voir les offres »). Ils filment, envoient leur vidéo depuis le "
            "téléphone ; elle arrive ici. Vous la regardez, la validez ou la refusez, et "
            "notez quand le bon est envoyé : ils suivent tout depuis leur page.",
            "sEmpty", page)
        text.setWordWrap(True)
        text.setMaximumWidth(560)
        lay.addWidget(text, 0, Qt.AlignHCenter)
        lay.addStretch(2)
        return page

    # -- l'editeur ----------------------------------------------------------------
    def _editor(self) -> QWidget:
        page = QWidget(self)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        top = QHBoxLayout()
        self.name = QLineEdit(page)
        self.name.setObjectName("sName")
        self.name.setPlaceholderText("Le titre de la mission (ex. : Ice challenge)")
        self.name.setMaxLength(missions.TITLE_MAX)
        self.name.textEdited.connect(lambda text: self._edit(title=text))
        top.addWidget(self.name, 1)
        self.pill = _label("", "sPill", page)
        top.addWidget(self.pill)
        lay.addLayout(top)
        self.tabs = QTabWidget(page)
        self.tabs.addTab(self._mission_tab(), "Mission")
        self.tabs.addTab(self._sends_tab(), "Vidéos reçues")
        lay.addWidget(self.tabs, 1)
        foot = QHBoxLayout()
        self.saved = _label("", "sDim", page)
        self.saved.setWordWrap(True)
        foot.addWidget(self.saved, 1)
        self.delete_button = QPushButton("Supprimer", page)
        self.delete_button.setObjectName("sDanger")
        self.delete_button.clicked.connect(self.delete_current)
        foot.addWidget(self.delete_button)
        self.copy_button = QPushButton("Dupliquer", page)
        self.copy_button.clicked.connect(self.duplicate_current)
        foot.addWidget(self.copy_button)
        self.close_button = QPushButton("Clore la mission", page)
        self.close_button.clicked.connect(self.toggle_closed)
        foot.addWidget(self.close_button)
        self.publish_button = QPushButton("Publier la mission", page)
        self.publish_button.setObjectName("sGo")
        self.publish_button.clicked.connect(self.publish_current)
        foot.addWidget(self.publish_button)
        lay.addLayout(foot)
        return page

    def _mission_tab(self) -> QWidget:
        page = QWidget(self)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        prices = QHBoxLayout()
        self.price = QSpinBox(page)
        self.price.setRange(0, missions.PRICE_MAX)
        self.price.setSuffix(" € en bon d'achat Amazon")
        self.price.setMinimumWidth(220)
        self.price.valueChanged.connect(lambda value: self._edit(price=value))
        prices.addWidget(self.price)
        prices.addSpacing(20)
        prices.addWidget(_label("Au plus :", "sDim", page))
        prices.addSpacing(4)
        self.places = QSpinBox(page)
        self.places.setRange(0, 10_000)
        self.places.setSpecialValueText("sans limite")
        self.places.setSuffix(" vidéo(s) validée(s)")
        self.places.setMinimumWidth(190)
        self.places.setToolTip("Quand ce nombre de vidéos est validé, la mission se dit "
                               "« complète » et n'accepte plus d'envoi.")
        self.places.valueChanged.connect(lambda value: self._edit(places=value))
        prices.addWidget(self.places)
        prices.addStretch(1)
        form.addRow(_label("Prix", "sDim", page), prices)
        self.to_button = QPushButton("Tout le monde", page)
        self.to_button.clicked.connect(self._to_menu)
        whom = QHBoxLayout()
        whom.addWidget(self.to_button)
        whom.addStretch(1)
        form.addRow(_label("Pour", "sDim", page), whom)
        # Le site pour lequel la video est realisee : la page l'annonce
        # (« Vidéo réalisée pour le site … »), a la place de « l'organisateur ».
        self.site = QLineEdit(page)
        self.site.setPlaceholderText("Le nom du site — les participants liront "
                                     "« Vidéo réalisée pour le site … »")
        self.site.setMaxLength(missions.SITE_MAX)
        self.site.textEdited.connect(lambda text: self._edit(site=text))
        form.addRow(_label("Réalisée pour", "sDim", page), self.site)
        lay.addLayout(form)
        lay.addWidget(_label("La consigne", "sHead", page))
        lay.addWidget(_label(
            "Ce qu'il faut filmer, comment, combien de temps, ce qui fera refuser la vidéo… "
            "Les retours à la ligne sont gardés.", "sDim", page))
        self.brief = QPlainTextEdit(page)
        self.brief.setObjectName("mBrief")
        self.brief.setPlaceholderText(
            "Ex. : Filmez-vous de face en train de réaliser l'ice challenge.\n"
            "• La vidéo dure au moins une minute.\n"
            "• La lumière est suffisante : on doit bien vous voir.\n"
            "• Le téléphone est tenu à l'horizontale.")
        self.brief.textChanged.connect(self._brief_edited)
        lay.addWidget(self.brief, 1)
        self.facts = _label("", "sDim", page)
        self.facts.setWordWrap(True)
        lay.addWidget(self.facts)
        return page

    def _sends_tab(self) -> QWidget:
        page = QWidget(self)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)
        self.sends_head = _label("", "sHead", page)
        lay.addWidget(self.sends_head)
        self.tree = QTreeWidget(page)
        self.tree.setRootIsDecorated(False)
        self.tree.setIconSize(SMALL)
        self.tree.setUniformRowHeights(True)
        self.tree.setHeaderLabels(["Vidéo", "Personne", "Envoyée le", "Poids", "État"])
        self.tree.header().setStretchLastSection(False)
        self.tree.itemDoubleClicked.connect(lambda item, _c: self._play(item))
        self.tree.currentItemChanged.connect(lambda *_a: self._paint_actions())
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._send_menu)
        lay.addWidget(self.tree, 1)
        self.verdict = _label("", "mVerdict", page)
        self.verdict.setWordWrap(True)
        lay.addWidget(self.verdict)
        row = QHBoxLayout()
        self.play_button = QPushButton("Regarder", page)
        self.play_button.clicked.connect(lambda: self._play(self.tree.currentItem()))
        row.addWidget(self.play_button)
        self.show_button = QPushButton("Explorateur", page)
        self.show_button.clicked.connect(self._reveal)
        row.addWidget(self.show_button)
        self.mail_button = QPushButton("Copier l'email", page)
        self.mail_button.clicked.connect(self._copy_mail)
        row.addWidget(self.mail_button)
        row.addStretch(1)
        self.refuse_button = QPushButton("Refuser…", page)
        self.refuse_button.setObjectName("sDanger")
        self.refuse_button.clicked.connect(lambda: self._judge("refusee"))
        row.addWidget(self.refuse_button)
        self.wait_button = QPushButton("Remettre à examiner", page)
        self.wait_button.clicked.connect(lambda: self._judge("attente"))
        row.addWidget(self.wait_button)
        self.accept_button = QPushButton("Valider", page)
        self.accept_button.setObjectName("sGo")
        self.accept_button.clicked.connect(lambda: self._judge("validee"))
        row.addWidget(self.accept_button)
        self.paid_button = QPushButton("Bon envoyé ✓", page)
        self.paid_button.setObjectName("sGo")
        self.paid_button.clicked.connect(lambda: self._judge("payee"))
        row.addWidget(self.paid_button)
        lay.addLayout(row)
        return page

    # -- la liste ---------------------------------------------------------------
    def _visible(self) -> list:
        return sorted((one for one in self.book["missions"].values()
                       if one["state"] != "supprime"),
                      key=lambda one: -(one["sent"] or one["created"]))

    def _mission(self, mission_id: str = "") -> dict | None:
        return self.book["missions"].get(mission_id or self.current_id)

    def _state_of(self, send: dict) -> str:
        if not send["done_at"]:
            return "envoi"
        return self.book["verdicts"].get(send["id"], {}).get("state", "attente")

    def _sends_of(self, mission_id: str) -> list:
        return [send for send in self.sends if send["mission"] == mission_id]

    def _fill_list(self) -> None:
        keep = self.current_id
        self.list.blockSignals(True)
        self.list.clear()
        for mission in self._visible():
            item = QListWidgetItem(self.list)
            item.setData(Qt.UserRole, mission["id"])
            card = QFrame()
            card.setObjectName("sCard")
            lay = QVBoxLayout(card)
            lay.setContentsMargins(10, 7, 10, 7)
            lay.setSpacing(2)
            top = QHBoxLayout()
            title = _label(mission["title"] or "Sans titre", "sHead", card)
            top.addWidget(title, 1)
            pill = _label(missions.STATES[mission["state"]], "sPill", card)
            pill.setProperty("state", mission["state"])
            top.addWidget(pill)
            lay.addLayout(top)
            sends = [one for one in self._sends_of(mission["id"]) if one["done_at"]]
            waiting = sum(1 for one in sends if self._state_of(one) == "attente")
            to_pay = sum(1 for one in sends if self._state_of(one) == "validee")
            bottom = QHBoxLayout()
            bottom.setSpacing(10)
            bottom.addWidget(_label(f"{mission['price']} €", "mPrice", card))
            parts = [f"{len(sends)} reçue(s)"]
            if waiting:
                parts.append(f"{waiting} à examiner")
            if to_pay:
                parts.append(f"{to_pay} bon(s) à envoyer")
            bottom.addWidget(_label("  ·  ".join(parts), "sDim", card), 1)
            lay.addLayout(bottom)
            item.setSizeHint(QSize(260, 56))
            self.list.setItemWidget(item, card)
            if mission["id"] == keep:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        live = sum(1 for one in self.book["missions"].values() if one["state"] == "ouverte")
        waiting = sum(1 for one in self.sends if one["done_at"]
                      and self._state_of(one) == "attente")
        self.where.setText((f"{live} mission(s) publiée(s). " if live else "")
                           + (f"{waiting} vidéo(s) à examiner. " if waiting else "")
                           + "Les visiteurs les voient dans Récompenses › Vidéo à la demande.")

    def select(self, mission_id: str, send_id: str = "") -> None:
        mission = self._mission(mission_id)
        if mission is None or mission["state"] == "supprime":
            self.current_id = ""
            self.stack.setCurrentIndex(0)
            return
        self.current_id = mission_id
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.data(Qt.UserRole) == mission_id and self.list.currentItem() is not item:
                self.list.blockSignals(True)
                self.list.setCurrentItem(item)
                self.list.blockSignals(False)
        self.stack.setCurrentIndex(1)
        self._paint_mission()
        self._fill_sends(send_id)
        if send_id or (mission["state"] != "brouillon" and self._sends_of(mission_id)):
            self.tabs.setCurrentIndex(1)
        else:
            self.tabs.setCurrentIndex(0)

    def _paint_mission(self) -> None:
        mission = self._mission()
        if mission is None:
            return
        self._painting = True
        if self.name.text() != mission["title"]:
            self.name.setText(mission["title"])
        self.pill.setText(missions.STATES[mission["state"]])
        self.pill.setProperty("state", mission["state"])
        self.pill.style().unpolish(self.pill)
        self.pill.style().polish(self.pill)
        self.price.setValue(mission["price"])
        self.places.setValue(mission["places"])
        if self.brief.toPlainText() != mission["brief"]:
            self.brief.setPlainText(mission["brief"])
        if self.site.text() != mission["site"]:
            self.site.setText(mission["site"])
        # Le prix ne bouge plus une fois des videos recues : ce serait changer
        # les regles en cours de route.
        received = any(one["done_at"] for one in self._sends_of(mission["id"]))
        self.price.setEnabled(not received)
        self.price.setToolTip("Des vidéos ont déjà été reçues : le prix ne change plus "
                              "(« Dupliquer » pour une nouvelle mission)." if received else "")
        self._paint_to()
        draft = mission["state"] == "brouillon"
        self.publish_button.setVisible(draft)
        self.close_button.setVisible(not draft)
        self.close_button.setText("Rouvrir la mission" if mission["state"] == "close"
                                  else "Clore la mission")
        if draft:
            self.saved.setText("Brouillon, gardé au fil de l'eau : les visiteurs ne le "
                               "verront qu'une fois publié.")
        elif mission["state"] == "ouverte":
            self.saved.setText(f"Publiée le {when(mission['sent'])}. Les retouches de "
                               "la consigne paraissent aussitôt chez les visiteurs.")
        else:
            self.saved.setText("Close : elle n'est plus proposée ; les vidéos reçues "
                               "restent ici.")
        self.facts.setText(
            "Les visiteurs doivent avoir donné leur email pour envoyer une vidéo : c'est à "
            "cette adresse que part le bon. Une personne a au plus "
            f"{missions.PENDING_MAX} vidéos en attente de réponse par mission ; une vidéo "
            "peut peser jusqu'à 4 Go.")
        self._painting = False

    def _paint_to(self) -> None:
        mission = self._mission()
        if mission is None:
            return
        chosen = mission["to"]
        if not chosen:
            self.to_button.setText("Tout le monde")
            return
        names = [one["name"] for one in self.people if one["key"] in chosen]
        self.to_button.setText(f"{len(chosen)} personne(s)" + (
            f" : {', '.join(names[:2])}" + ("…" if len(names) > 2 else "") if names else ""))

    def _to_menu(self) -> None:
        mission = self._mission()
        if mission is None:
            return
        menu = QMenu(self)
        everyone = menu.addAction("Tout le monde")
        everyone.setCheckable(True)
        everyone.setChecked(not mission["to"])
        menu.addSeparator()
        actions = []
        for person in self.people:
            action = menu.addAction(person["name"] + (f"  ·  {person['email']}"
                                                      if person["email"] else "")
                                    + ("  (NAS)" if person["where"] == "NAS" else ""))
            action.setCheckable(True)
            action.setChecked(person["key"] in mission["to"])
            actions.append((action, person["key"]))
        if not actions:
            empty = menu.addAction("Personne n'a encore de profil")
            empty.setEnabled(False)
        chosen = menu.exec(self.to_button.mapToGlobal(self.to_button.rect().bottomLeft()))
        if chosen is None:
            return
        if chosen is everyone:
            self._edit(to=[])
        else:
            keys = set(mission["to"])
            for action, key in actions:
                if action is chosen:
                    keys ^= {key}
            self._edit(to=sorted(keys))
        self._paint_to()

    # -- retoucher, publier ---------------------------------------------------------
    def _brief_edited(self) -> None:
        if not self._painting:
            self._edit(brief=self.brief.toPlainText())

    def _edit(self, **changes) -> None:
        mission = self._mission()
        if mission is None or self._painting:
            return
        if "title" in changes:
            changes["title"] = " ".join(changes["title"].split())[:missions.TITLE_MAX]
        mission.update(changes)
        mission["updated"] = time.time()
        self._save_timer.start()
        if "title" in changes or "price" in changes:
            self._fill_list()

    def _save_now(self) -> None:
        self._save_timer.stop()
        if not missions.save(self.path, self.book):
            self.saved.setText("⚠ Le fichier des missions n'a pas pu être écrit.")
            return
        self._sync()

    def _sync(self) -> None:
        from .nas_publish import share_root, sync_missions
        from .tunnel import Chore
        share = share_root(self.window.top_root() or "")
        if share is None:
            return
        if self._syncing:
            self._sync_again = True
            return
        self._syncing = True

        def then(merged) -> None:
            if not shiboken6.isValid(self):
                return
            self._syncing = False
            if merged is not None and not self._sync_again and not self._save_timer.isActive():
                joined = missions.merge(self.book, merged)
                if joined != self.book:
                    self.book = joined
                    self._fill_list()
                    if self.current_id:
                        self._paint_mission()
                        self._fill_sends()
            if self._sync_again:
                self._sync_again = False
                self._sync()
        Chore(lambda: sync_missions(share), self, fallback=None, then=then).start()

    def create(self) -> None:
        now = time.time()
        # Le site de la derniere mission : on ne le retape pas a chaque fois.
        latest = sorted(self.book["missions"].values(), key=lambda one: one["created"],
                        reverse=True)
        site = next((one["site"] for one in latest if one.get("site")), "")
        mission = missions.clean_mission({"id": missions.new_id(), "title": "Nouvelle mission",
                                          "site": site, "price": 10,
                                          "created": now, "updated": now})
        self.book["missions"][mission["id"]] = mission
        self._save_now()
        self._fill_list()
        self.select(mission["id"])
        self.tabs.setCurrentIndex(0)
        self.name.setFocus()
        self.name.selectAll()

    def publish_current(self) -> None:
        mission = self._mission()
        if mission is None or mission["state"] != "brouillon":
            return
        if not mission["title"] or len(mission["brief"]) < 10:
            QMessageBox.information(self, "Publier la mission",
                                    "Donnez-lui un titre et une consigne : c'est tout ce que "
                                    "le visiteur saura de ce qu'on attend de lui.")
            return
        who = "tous les visiteurs" if not mission["to"] else f"{len(mission['to'])} personne(s)"
        if QMessageBox.question(
                self, "Publier la mission",
                f"Publier « {mission['title']} » ({mission['price']} € en bon d'achat) pour "
                f"{who} ?\n\nElle paraît aussitôt dans leurs récompenses.") != QMessageBox.Yes:
            return
        now = time.time()
        mission.update(state="ouverte", sent=now, updated=now)
        self._save_now()
        self._fill_list()
        self._paint_mission()
        self.window.show_banner(f"Mission « {mission['title']} » publiée.", "done")

    def toggle_closed(self) -> None:
        mission = self._mission()
        if mission is None or mission["state"] == "brouillon":
            return
        mission.update(state="close" if mission["state"] == "ouverte" else "ouverte",
                       updated=time.time())
        self._save_now()
        self._fill_list()
        self._paint_mission()

    def duplicate_current(self) -> None:
        mission = self._mission()
        if mission is None:
            return
        now = time.time()
        copy = missions.clean_mission(dict(mission, id=missions.new_id(), state="brouillon",
                                           title=(mission["title"] + " (2)")
                                           [:missions.TITLE_MAX],
                                           created=now, updated=now, sent=0.0))
        self.book["missions"][copy["id"]] = copy
        self._save_now()
        self._fill_list()
        self.select(copy["id"])
        self.tabs.setCurrentIndex(0)

    def delete_current(self) -> None:
        mission = self._mission()
        if mission is None:
            return
        received = len([one for one in self._sends_of(mission["id"]) if one["done_at"]])
        if QMessageBox.question(
                self, "Supprimer la mission",
                f"Supprimer « {mission['title'] or 'Sans titre'} » ?"
                + (f" Les {received} vidéo(s) reçue(s) restent sur le disque, mais ne sont "
                   "plus listées ici." if received else "")) != QMessageBox.Yes:
            return
        mission.update(state="supprime", updated=time.time())
        self._save_now()
        self.current_id = ""
        self._fill_list()
        visible = self._visible()
        if visible:
            self.select(visible[0]["id"])
        else:
            self.stack.setCurrentIndex(0)

    # -- les videos recues -----------------------------------------------------------
    def _sources(self) -> list:
        from .config import PRIVATE_DIR
        from .nas_publish import FOLDER, share_root
        found = [("PC", PRIVATE_DIR / missions.SENDS_NAME, PRIVATE_DIR / missions.FOLDER_NAME)]
        share = share_root(self.window.top_root() or "")
        if share is not None:
            state = Path(share) / FOLDER / "etat"
            found.append(("NAS", state / missions.SENDS_NAME, state / missions.FOLDER_NAME))
        return found

    def _read(self) -> None:
        """Les envois des deux serveurs, la taille deja arrivee des envois en
        cours, et les profils : relus hors du fil de l'interface."""
        if self._reading:
            return
        from . import profils
        from .config import PRIVATE_DIR
        from .demandes import FILE_NAME
        from .nas_publish import FOLDER, share_root
        from .tunnel import Chore
        self._reading = True
        sources = self._sources()
        share = share_root(self.window.top_root() or "")
        people_sources = [("PC", profils.path_for(PRIVATE_DIR / FILE_NAME))]
        if share is not None:
            people_sources.append(("NAS", Path(share) / FOLDER / "etat" / profils.FILE_NAME))

        def work():
            sends = missions.received(sources)
            for send in sends:
                if not send["done_at"]:
                    try:
                        send["have"] = Path(send["path"]).stat().st_size
                    except OSError:
                        send["have"] = 0
            people = []
            for where, path in people_sources:
                for key, profile in profils.read_all(path).items():
                    people.append({"where": where, "key": key,
                                   "name": profile.get("name") or "Sans nom",
                                   "email": profile.get("email", "")})
            people.sort(key=lambda one: one["name"].casefold())
            return sends, people

        def then(result) -> None:
            if not shiboken6.isValid(self):
                return
            self._reading = False
            if result is None:
                return
            sends, people = result
            changed = repr(sends) != repr(self.sends)
            self.sends, self.people = sends, people
            if changed:
                self._fill_list()
                if self.current_id:
                    self._fill_sends()
            self._paint_to()
        Chore(work, self, fallback=None, then=then).start()

    def _who(self, send: dict) -> str:
        label = send.get("label", "")
        given = self.window.alias(label) if label else ""
        name = given if given and given != label else (send.get("name") or "Un visiteur")
        return name + (f"  ·  {send['email']}" if send.get("email") else "")

    def _fill_sends(self, keep_id: str = "") -> None:
        mission = self._mission()
        if mission is None:
            return
        current = self.tree.currentItem()
        keep_id = keep_id or (current.data(0, Qt.UserRole) if current is not None else "")
        sends = self._sends_of(mission["id"])
        done = [one for one in sends if one["done_at"]]
        self.tabs.setTabText(1, f"Vidéos reçues ({len(done)})" if done else "Vidéos reçues")
        waiting = sum(1 for one in done if self._state_of(one) == "attente")
        to_pay = sum(1 for one in done if self._state_of(one) == "validee")
        self.sends_head.setText(
            "Aucune vidéo reçue pour l'instant." if not sends else
            f"{len(done)} vidéo(s) reçue(s)" + (f" · {waiting} à examiner" if waiting else "")
            + (f" · {to_pay} bon(s) à envoyer" if to_pay else "")
            + (f" · {len(sends) - len(done)} envoi(s) en cours" if len(sends) > len(done)
               else ""))
        self.tree.blockSignals(True)
        self.tree.clear()
        chosen = None
        for send in sends:
            state = self._state_of(send)
            if state == "envoi":
                part = send.get("have", 0) / send["size"] if send["size"] else 0
                status = f"envoi en cours ({round(100 * part)} %)"
            else:
                status = missions.VERDICTS[state]
            item = QTreeWidgetItem(self.tree, [send["original"] or Path(send["file"]).name,
                                               self._who(send), when(send["at"]),
                                               _weight(send["size"]), status])
            item.setData(0, Qt.UserRole, send["id"])
            item.setForeground(4, QColor(VERDICT_COLORS[state]))
            item.setToolTip(0, send["path"])
            if state == "envoi":
                item.setIcon(0, QIcon(_blank(SMALL, "…")))
                for column in range(4):
                    item.setForeground(column, QColor("#6f7885"))
            else:
                file = self.thumbs.ask(send["path"])
                if not file:
                    self._waiting_thumbs.setdefault(send["path"], []).append(item)
                item.setIcon(0, QIcon(_cover(file, SMALL) if file else _blank(SMALL, "…")))
            if send["id"] == keep_id:
                chosen = item
        if chosen is None and self.tree.topLevelItemCount():
            chosen = self.tree.topLevelItem(0)
        if chosen is not None:
            self.tree.setCurrentItem(chosen)
        self.tree.blockSignals(False)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2, 3, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self._paint_actions()

    def _thumb_ready(self, path: str, file: str) -> None:
        for item in self._waiting_thumbs.pop(path, []):
            try:
                item.setIcon(0, QIcon(_cover(file, SMALL) if file else _blank(SMALL, "?")))
            except RuntimeError:
                pass

    def _send(self, item=None) -> dict | None:
        item = item if item is not None else self.tree.currentItem()
        if item is None:
            return None
        send_id = item.data(0, Qt.UserRole)
        return next((one for one in self.sends if one["id"] == send_id), None)

    def _paint_actions(self) -> None:
        send = self._send()
        mission = self._mission()
        state = self._state_of(send) if send is not None else ""
        ready = send is not None and state != "envoi"
        for button in (self.play_button, self.show_button, self.mail_button):
            button.setEnabled(ready)
        self.mail_button.setEnabled(ready and bool(send and send.get("email")))
        self.accept_button.setVisible(ready and state in ("attente", "refusee"))
        self.refuse_button.setVisible(ready and state in ("attente", "validee"))
        self.paid_button.setVisible(ready and state == "validee")
        self.wait_button.setVisible(ready and state in ("refusee", "payee"))
        if send is None:
            self.verdict.setText("")
            return
        verdict = self.book["verdicts"].get(send["id"], {})
        price = mission["price"] if mission else 0
        text = {
            "envoi": "L'envoi n'est pas terminé : la vidéo arrive encore (ou la personne a "
                     "abandonné).",
            "attente": "À examiner : regardez la vidéo, puis validez-la ou refusez-la.",
            "validee": f"Validée le {when(verdict.get('at', 0))} : un bon d'achat Amazon de "
                       f"{price} € est à envoyer à {send.get('email') or 'cette personne'}. "
                       "Une fois envoyé : « Bon envoyé ✓ ».",
            "payee": f"Bon de {price} € envoyé (noté le {when(verdict.get('at', 0))}).",
            "refusee": "Refusée" + (f" : « {verdict['note']} »" if verdict.get("note") else
                                    ".") + " La personne le voit sur sa page.",
        }[state]
        self.verdict.setText(text)
        self.verdict.setStyleSheet(f"color: {VERDICT_COLORS[state]};")

    def _judge(self, state: str) -> None:
        send = self._send()
        if send is None:
            return
        note = ""
        if state == "refusee":
            note, ok = QInputDialog.getText(
                self, "Refuser la vidéo",
                "Pourquoi ? (la personne le lira sur sa page — facultatif)\n"
                "Ex. : la vidéo dure moins d'une minute.", QLineEdit.Normal, "")
            if not ok:
                return
        if state == "validee":
            mission = self._mission()
            if QMessageBox.question(
                    self, "Valider la vidéo",
                    f"Valider la vidéo de {self._who(send)} ?\n\nUn bon d'achat Amazon de "
                    f"{mission['price'] if mission else 0} € sera à envoyer à "
                    f"{send.get('email') or 'cette personne'} ; elle le voit sur sa page.") \
                    != QMessageBox.Yes:
                return
        now = time.time()
        self.book["verdicts"][send["id"]] = {"state": state, "note": note[:missions.NOTE_MAX],
                                             "at": now, "updated": now}
        self._save_now()
        self._fill_list()
        self._fill_sends(send["id"])
        if state == "validee" and send.get("email"):
            QGuiApplication.clipboard().setText(send["email"])
            self.window.show_banner(f"Adresse copiée : {send['email']} — à coller dans le "
                                    "bon d'achat Amazon.", "done", seconds=8)

    def _play(self, item) -> None:
        send = self._send(item)
        if send is None or not send["done_at"]:
            return
        if not Path(send["path"]).exists():
            QMessageBox.information(self, "Vidéo introuvable",
                                    f"La vidéo n'est plus à sa place :\n{send['path']}")
            return
        self.window.play_floating_path(send["path"])

    def _reveal(self) -> None:
        send = self._send()
        if send is not None:
            self.window.reveal_path(send["path"])

    def _copy_mail(self) -> None:
        send = self._send()
        if send is not None and send.get("email"):
            QGuiApplication.clipboard().setText(send["email"])
            self.window.show_banner(f"Adresse copiée : {send['email']}", "done")

    def _send_menu(self, point) -> None:
        item = self.tree.itemAt(point)
        if item is None:
            return
        self.tree.setCurrentItem(item)
        menu = QMenu(self)
        play = menu.addAction("Regarder dans le lecteur flottant")
        show = menu.addAction("Montrer dans l'Explorateur")
        mail = menu.addAction("Copier l'email")
        chosen = menu.exec(self.tree.viewport().mapToGlobal(point))
        if chosen is play:
            self._play(item)
        elif chosen is show:
            self._reveal()
        elif chosen is mail:
            self._copy_mail()

    # -- la vie de la fenetre --------------------------------------------------------
    def showEvent(self, event):                     # noqa: N802
        super().showEvent(event)
        if not self.beat.isActive():
            self.beat.start()
        self._read()
        self._sync()

    def hideEvent(self, event):                     # noqa: N802
        self.beat.stop()
        super().hideEvent(event)

    def closeEvent(self, event):                    # noqa: N802
        if self._save_timer.isActive():
            self._save_now()
        self.beat.stop()
        self.thumbs.close()
        super().closeEvent(event)
