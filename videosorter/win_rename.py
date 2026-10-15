"""Renommer sur place, et suivre un fichier renomme ou deplace."""
from __future__ import annotations

import os
import time
from pathlib import Path
from PySide6.QtCore import QPoint, QTimer, Qt, Signal
from PySide6.QtWidgets import QLineEdit
from . import actions
from .header import TAB_SPLIT
from .tagging import fold
from .scan import MODE_FOLDERS, PARENT_PREFIX, as_path as _as_path
from .transfer import Transfer


class _RenameField(QLineEdit):
    """Le nom en cours de renommage, pose sur le titre (F2).

    Entree valide ; Echap renonce, et un clic ailleurs aussi : rien ne se
    renomme sur le disque sans qu'on l'ait valide.
    """

    committed = Signal(str)
    cancelled = Signal()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.cancelled.emit()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if not event.isAutoRepeat():
                self.committed.emit(self.text())
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        # Le menu du clic droit prend le clavier un instant : ce n'est pas
        # partir ailleurs.
        if event.reason() != Qt.PopupFocusReason:
            self.cancelled.emit()


class RenameMixin:
    """Une part de la fenetre principale : MainWindow en herite."""

    def start_rename(self, text: str | None = None) -> None:
        """F2 : le nom de ce qu'on regarde devient un champ, sur place.

        Au bout du fil d'Ariane, la ou on le lit : aucune fenetre. Entree
        renomme, Echap renonce. Le renommage passe par la file des transferts,
        hors du fil de l'interface, et Ctrl+Z le defait.
        """
        item = self.current
        if self.browsing or self.tab == TAB_SPLIT or item is None:
            self.show_banner("F2 renomme la vidéo ou le dossier ouvert dans sa "
                             "fiche.", "quiet")
            return
        if item.locked:
            self.show_banner("Cet élément part, ou est déjà parti : il ne se "
                             "renomme plus.", "quiet")
            return
        if not item.movable or Path(item.path).name.startswith(PARENT_PREFIX):
            self.show_banner("Un mot-clé, une entrée « en vrac » ou un dossier de "
                             "tête ne se renomme pas d'ici.", "quiet")
            return
        if self.cinema:
            # Le titre est dans la barre que le cinema cache : on en sort, et
            # le champ se pose une fois la barre revenue a sa place. Sur la
            # fiche : venu du ⛶ d'une video ouverte a cote, la sortie rendait
            # la planche, et F2 n'avait plus rien a renommer.
            self._back_to_board = False
            self.toggle_cinema(False)
            QTimer.singleShot(0, lambda: self.start_rename(text))
            return
        self._cancel_rename()
        piece = self._title_piece()
        if piece is None:
            # Un nom refuse rouvre le champ au tour suivant : le titre n'est
            # pas toujours deja revenu a l'ecran, et le champ ne revenait pas
            # (on perdait ce qu'on avait tape). Quelques essais, puis rien.
            tries = getattr(self, "_rename_tries", 0)
            if text is not None and tries < 10:
                self._rename_tries = tries + 1
                QTimer.singleShot(50, lambda: self.start_rename(text))
            else:
                self._rename_tries = 0
            return
        self._rename_tries = 0
        field = _RenameField(self.top_bar)
        field.setObjectName("renameField")
        # La police du titre qu'il remplace (14 points, gras), et avant de
        # mesurer le texte : le champ ecrivait plus petit que le nom.
        field.ensurePolished()
        field.setText(Path(item.path).name if text is None else text)
        bar = self.top_bar.width()
        corner = piece.mapTo(self.top_bar, QPoint(0, 0))
        wanted = field.fontMetrics().horizontalAdvance(field.text()) + 40
        width = max(160, min(max(wanted, piece.width()), bar - 8))
        height = max(piece.height(), field.sizeHint().height())
        x = max(0, min(corner.x(), bar - width - 4))
        y = max(0, corner.y() + (piece.height() - height) // 2)
        field.setGeometry(x, y, width, height)
        # Ce qui suit le titre sur la ligne (« 240p · 76,7 Ko ») s'efface le
        # temps de l'edition, sans que rien ne bouge : le champ le recouvrait
        # a moitie et touchait la taille du fichier.
        subtitle = self.item_subtitle
        if subtitle.isVisible():
            policy = subtitle.sizePolicy()
            self._rename_subtitle = policy.retainSizeWhenHidden()
            policy.setRetainSizeWhenHidden(True)
            subtitle.setSizePolicy(policy)
            subtitle.hide()
        field.committed.connect(self._commit_rename)
        field.cancelled.connect(self._cancel_rename)
        self._rename_field, self._renaming = field, item
        field.show()
        field.raise_()
        field.setFocus(Qt.OtherFocusReason)
        # Le nom sans son extension, pret a etre remplace, comme dans
        # l'explorateur.
        name = field.text()
        stem = (len(Path(name).stem) if item.kind != MODE_FOLDERS and Path(name).suffix
                else len(name))
        field.setSelection(0, stem)

    def _cancel_rename(self) -> None:
        """Referme le champ de renommage, sans rien renommer."""
        field, self._rename_field = self._rename_field, None
        self._renaming = None
        retain, self._rename_subtitle = self._rename_subtitle, None
        if retain is not None:
            subtitle = self.item_subtitle
            subtitle.show()
            policy = subtitle.sizePolicy()
            policy.setRetainSizeWhenHidden(retain)
            subtitle.setSizePolicy(policy)
        if field is None:
            return
        field.blockSignals(True)
        field.hide()
        field.deleteLater()
        if self.isActiveWindow():
            self.setFocus()

    def _commit_rename(self, text: str) -> None:
        """Entree dans le champ : verifie le nom, puis envoie le renommage."""
        item = self._renaming
        self._cancel_rename()
        current = self.current
        # Le meme element, par son identifiant : une relecture de fond peut
        # avoir remplace l'objet par un neuf (meme chemin), et le renommage
        # etait alors abandonne sans un mot.
        if (item is None or current is None or current.item_id != item.item_id
                or current.locked):
            return
        item = current
        old = Path(item.path)
        name = text.strip()
        if not name or name.startswith("."):
            # Avant l'extension : un champ vide devenait « .mp4 », un nom
            # cache, et la video disparaissait de la collection.
            what = "le dossier" if item.kind == MODE_FOLDERS else "la vidéo"
            self.show_banner("Le nom ne peut pas être vide." if not name else
                             f"Un nom qui commence par « . » serait caché : {what} "
                             "disparaîtrait de Prisme.", "error")
            QTimer.singleShot(0, lambda: self.start_rename(text))
            return
        if (item.kind != MODE_FOLDERS and old.suffix
                and not name.lower().endswith(old.suffix.lower())):
            # L'extension reste : sans elle, la video ne s'ouvrirait plus.
            name += old.suffix
        if name == old.name:
            return
        problem = actions.name_problem(name) or self._name_taken(item, name)
        if problem:
            self.show_banner(problem, "error")
            # On reste dans le champ, avec ce qu'on avait tape, pour corriger.
            QTimer.singleShot(0, lambda: self.start_rename(text))
            return
        resume = 0
        if item.kind != MODE_FOLDERS and not self.browsing:
            resume = max(0, int(self.single.player.position()))
        item.status = "pending_rename"
        item.status_detail = name
        self._rename_resume = resume
        self.show_banner(f"Renommage : « {old.name} » → « {name} »…", "info")

        def send() -> None:
            # Windows ne renomme pas un fichier ouvert : le lecteur le lache.
            # Il le reprend a l'arrivee, au meme instant (`_reshow_renamed`).
            # Vider un lecteur coute jusqu'a cent millisecondes : au tour
            # suivant, une fois le champ parti et le bandeau pose, Entree
            # repond aussitot.
            self._release_media(old)
            self._submit(Transfer(kind="rename", src=old, new_name=name,
                                  label=old.name, item_id=item.item_id), item)
        QTimer.singleShot(0, send)

    def _reshow_renamed(self, item) -> None:
        """La fiche reprend sa video, sous son nom, a l'instant ou on l'a laissee."""
        ms, self._rename_resume = self._rename_resume, None
        if ms and item.kind != MODE_FOLDERS:
            self._pending_start = (os.path.normcase(str(item.path)), int(ms),
                                   time.monotonic())
        self.show_item(self.index)

    def _follow_rename(self, item, old, new) -> None:
        """La collection en memoire suit un renommage reussi.

        Contrairement a un rangement, l'element reste la ou il est, sous son
        nouveau nom : sa carte, sa fiche et ses voisines le montrent aussitot,
        sans attendre la relecture du dossier. Un dossier renomme emporte le
        chemin de tout ce qu'il contient.
        """
        old_s, new_s = str(old), str(new)
        if not old_s or not new_s or old_s == new_s:
            return
        if (item is not None and item.kind != MODE_FOLDERS and not item.is_tag
                and self._follow_file_rename(item, old_s, new_s)):
            return
        # Les dossiers qui le contiennent d'abord, tant que l'element porte
        # encore son ancien nom.
        self._follow_move(old_s, new_s)
        size = len(old_s)

        def moved(path):
            text = str(path)
            if text.startswith(old_s) and (len(text) == size
                                           or text[size] in "\\/"):
                return _as_path(new_s + text[size:])
            return None

        done = set()
        renamed = []
        for items in (self.all_items, self._plain_items,
                      [item] if item is not None else []):
            for other in items:
                if id(other) in done:
                    continue
                done.add(id(other))
                fresh = moved(other.path)
                if fresh is None:
                    continue
                was = other.item_id
                other.path = fresh
                other.videos = [moved(video) or video for video in other.videos]
                other.__dict__.pop("sort_name", None)
                renamed.append(other)
                if was in self.board.picked_ids:
                    self.board.picked_ids.discard(was)
                    self.board.picked_ids.add(other.item_id)
        # Sa carte, si elle est sur la page : la planche la croyait inchangee
        # (meme element), et gardait l'ancien nom et un apercu introuvable.
        first, last = self.board._page_bounds()
        shown = self.board.items
        for position in range(max(0, first), min(last, len(shown))):
            if any(shown[position] is other for other in renamed):
                card = self.board._card_for(position)
                if card is not None:
                    card.set_item(shown[position],
                                  self.ratings.get(shown[position].path))
                    if self.browsing:
                        self.on_board_preview(position)
        # Les niveaux empiles et l'historique de navigation passaient
        # peut-etre par un dossier renomme : ils y menaient a « introuvable ».
        for entry in list(self.levels) + list(self.visited):
            fresh = moved(entry.get("root", ""))
            if fresh is not None:
                entry["root"] = fresh
        # Le nom a change : la recherche et les tris sont a refaire.
        self._touch()

    def _follow_file_rename(self, item, old_s: str, new_s: str) -> bool:
        """Une video renommee : on ne suit que ce qui la porte.

        Le cas general balaye toute la collection (un dossier renomme emporte
        tout ce qu'il contient), puis fait tout replier et retrier : sur cent
        mille videos, deux a trois cents millisecondes de gel a la fin du
        renommage, et autant a son Ctrl+Z, plus une demi-seconde au geste
        suivant. Une video n'a rien sous elle : ses dossiers, ses fiches et
        son nom replie suffisent. Rend faux si ses dossiers sont introuvables
        tels quels -- le cas general s'en charge alors.
        """
        parents = {str(parent) for parent in Path(old_s).parents}
        holders = []
        for other in self._plain_items:
            if (not other.is_tag and other.kind == MODE_FOLDERS
                    and str(other.path) in parents):
                holders.append(other)
        if self._plain_items and not holders:
            return False
        fresh = _as_path(new_s)
        touched = set()
        for other in holders:
            videos = other.videos
            for at, video in enumerate(videos):
                if str(video) == old_s:
                    other.videos = list(videos)
                    other.videos[at] = fresh
                    touched.add(other.item_id)
                    break
        # Les fiches qui la montrent : elle-meme, celle de l'onglet Videos, et
        # celle de la liste affichee si c'en est une autre.
        renamed = [item]
        cached = self._flat_cache.pop(old_s, None)
        if cached is not None and str(cached.path) == old_s:
            self._flat_cache[new_s] = cached
            renamed.append(cached)
        renamed.extend(other for other in self.all_items
                       if str(other.path) == old_s)
        seen = set()
        for other in renamed:
            if id(other) in seen or str(other.path) != old_s:
                continue
            seen.add(id(other))
            was = other.item_id
            other.path = fresh
            other.videos = [fresh if str(video) == old_s else video
                            for video in other.videos]
            other.__dict__.pop("sort_name", None)
            if was in self.board.picked_ids:
                self.board.picked_ids.discard(was)
                self.board.picked_ids.add(other.item_id)
        # Les noms replies, a leur place : la liste triable ne change pas de
        # membres, seul ce nom-ci a change.
        for entry in self._sortable_memo.values():
            folded = entry[4]
            if folded is None:
                continue
            for position, other in enumerate(entry[2]):
                if id(other) in seen and position < len(folded):
                    name = fold(other.name)
                    folded[position] = name
                    if entry[5] is not None:
                        entry[5][id(other)] = name
        first, last = self.board._page_bounds()
        shown = self.board.items
        for position in range(max(0, first), min(last, len(shown))):
            if id(shown[position]) in seen:
                card = self.board._card_for(position)
                if card is not None:
                    card.set_item(shown[position],
                                  self.ratings.get(shown[position].path))
                    if self.browsing:
                        self.on_board_preview(position)
        if touched:
            # Les apercus retenus de ses dossiers montraient l'ancien chemin
            # (meme regle que `_follow_move`).
            def owner(key: str) -> str:
                for prefix in ("board@", "peek@"):
                    if key.startswith(prefix):
                        return key[len(prefix):]
                return key.rsplit("@", 1)[0]

            self.plans = {key: plan for key, plan in self.plans.items()
                          if owner(key) not in touched}
        # La collection a change de chemins (mur, hasard, voisines) ; la liste
        # triable, elle, garde ses membres et ses noms viennent d'etre suivis.
        self._touch(sortable=False)
        return True

    def _follow_move(self, old, new) -> None:
        """La collection en memoire suit un deplacement reussi.

        L'index oublie les dossiers touches, et la prochaine relecture les
        corrigera ; d'ici la, la video rangee restait a son ancien chemin dans
        l'onglet Videos, le mur ou les favoris, et n'apparaissait pas au
        nouveau. On retire donc ce qui est parti des dossiers qui le
        contenaient, et on l'ajoute a ceux qui le recoivent. L'element deplace
        lui-meme garde son etat « rangé » jusqu'a la relecture.
        """
        old_s, new_s = str(old), str(new)
        if not old_s or not new_s or old_s == new_s or not self._plain_items:
            return
        old_pref = old_s.rstrip("\\/") + os.sep
        new_pref = new_s.rstrip("\\/") + os.sep
        low_old = os.path.normcase(old_pref)
        low_new = os.path.normcase(new_pref)
        moved = None           # les videos parties, telles qu'elles etaient
        takers = []
        touched = set()
        for item in self._plain_items:
            if item.is_tag or item.kind != MODE_FOLDERS:
                continue
            base = os.path.normcase(str(item.path).rstrip("\\/") + os.sep)
            if low_old.startswith(base) and low_old != base:
                # Un dossier qui contenait ce qui est parti.
                keep, gone = [], []
                for video in item.videos:
                    text = str(video)
                    if text == old_s or os.path.normcase(text).startswith(low_old):
                        gone.append(text)
                    else:
                        keep.append(video)
                if gone:
                    item.videos = keep
                    item.video_count = max(0, item.video_count - len(gone))
                    item.file_count = max(0, item.file_count - len(gone))
                    if moved is None:
                        moved = gone
                    touched.add(item.item_id)
            if low_new.startswith(base) and low_new != base:
                takers.append(item)
        if moved is None:
            # Le dossier deplace etait lui-meme un element de la collection :
            # ce sont ses videos qui partent.
            for item in self._plain_items:
                if not item.is_tag and str(item.path) == old_s:
                    moved = [str(video) for video in item.videos]
                    break
        # Les fiches « video » de l'onglet Videos : celles de ce qui est parti
        # seulement, et non un balayage des cent mille.
        self._flat_cache.pop(old_s, None)
        for text in moved or ():
            self._flat_cache.pop(text, None)
        if moved and takers:
            arrived = [_as_path(new_s + text[len(old_s):]) for text in moved]
            for item in takers:
                known = {str(video) for video in item.videos}
                fresh = [video for video in arrived if str(video) not in known]
                if item.loose_only:
                    # L'entree « en vrac » d'un rayonnage ne porte que ses
                    # videos directes, pas celles de ses sous-dossiers.
                    here = os.path.normcase(str(item.path).rstrip("\\/"))
                    fresh = [video for video in fresh
                             if os.path.normcase(str(Path(video).parent)) == here]
                if fresh:
                    item.videos = list(item.videos) + fresh
                    item.video_count += len(fresh)
                    item.file_count += len(fresh)
                    touched.add(item.item_id)
        if touched or moved:
            # La collection a change : ce qu'on en a tire est a refaire. La
            # liste triable seulement si c'est elle qu'on regarde : un dossier
            # vide de ses videos cesse d'etre triable.
            self._touch(sortable=self.all_items is self._plain_items)
        if touched:
            # Les apercus retenus montraient l'ancienne composition.
            def owner(key: str) -> str:
                for prefix in ("board@", "peek@"):
                    if key.startswith(prefix):
                        return key[len(prefix):]
                return key.rsplit("@", 1)[0]

            self.plans = {key: plan for key, plan in self.plans.items()
                          if owner(key) not in touched}
