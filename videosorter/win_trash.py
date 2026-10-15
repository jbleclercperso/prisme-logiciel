"""La corbeille de session et son badge."""
from __future__ import annotations

import os
import time
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog
from . import actions
from .config import APP_NAME, TRASH_FOLDER_NAME
from .widgets import TrashDialog


class TrashMixin:
    """Une part de la fenetre principale : MainWindow en herite."""

    # ------------------------------------------------------------------
    # Corbeille de session
    # ------------------------------------------------------------------
    def open_trash(self) -> None:
        """Liste ce qui a été écarté, avec de quoi le remettre en place."""
        if not self.trash.count:
            self.show_banner("La corbeille de session est vide", "quiet")
            return
        dialog = TrashDialog(self.trash, self)
        dialog.exec()
        self.setFocus()
        # Un élément restauré redevient triable.
        restored = list(self._restored_items())
        for entry_path, item in restored:
            item.status = ""
            item.status_detail = ""
            self._settle_board(item)
        if restored:
            # Ses videos reviennent dans les listes tirees de la collection.
            self._touch(sortable=False)
        self.update_counter()
        # Sur la planche, on y reste : redessiner la fiche courante la
        # remplacait par une video qu'on ne regardait pas.
        if self.current is not None and not self.browsing:
            self.show_item(self.index)

    def _trash_restored(self, stored: str, target: str) -> None:
        """Restaure depuis la corbeille : l'etoile revient avec l'element.

        Et Ctrl+Z oublie cette suppression : l'annuler encore echouait sur un
        element deja revenu, et masquait l'annulation d'avant.
        """
        self.ratings.rename(stored, target)
        from .dupes_memory import NOT_DUPES
        NOT_DUPES.renommer(stored, target)
        gone = os.path.normcase(str(stored))
        self.history = [entry for entry in self.history
                        if not (entry.dst is not None
                                and os.path.normcase(str(entry.dst)) == gone)]

    def _trash_purged(self, paths: list) -> None:
        """Detruits pour de bon : leurs favoris et leurs « pas des doublons »
        n'ont plus d'objet."""
        from .dupes_memory import NOT_DUPES
        for path in paths:
            self.ratings.forget_under(path)
            NOT_DUPES.oublier(path)

    def _flush_trash_on_close(self) -> None:
        """Vide la corbeille de session, sans rien demander.

        Sur un disque local, le contenu rejoint la corbeille de Windows ; sur
        le NAS, il est detruit pour de bon — c'est voulu, et une question posee
        a chaque fermeture finirait par etre approuvee sans etre lue.

        Hors du fil de l'interface : un gros dossier sur le NAS, c'est un
        aller-retour reseau par fichier, et la fenetre restait « Ne repond
        pas » des minutes. La fenetre est deja masquee ; une petite boite dit
        ce qui se passe si cela dure.
        """
        thread = self.trash.flush_in_background(self.cfg["delete_mode"])
        started = time.monotonic()
        waiter = None
        while thread.is_alive():
            thread.join(0.05)
            QApplication.processEvents()
            if waiter is None and time.monotonic() - started > 0.6:
                waiter = QProgressDialog(
                    "Vidage de la corbeille de session…", "", 0, 0, self)
                waiter.setWindowTitle(APP_NAME)
                waiter.setCancelButton(None)
                waiter.setMinimumDuration(0)
                waiter.show()
        if waiter is not None:
            waiter.close()
        # Les signaux du vidage (favoris a oublier) arrivent par la file
        # d'evenements : on les laisse passer avant d'ecrire les favoris.
        QApplication.processEvents()
        _done, problem = self.trash.flush_result or (0, "")
        if problem:
            QMessageBox.warning(
                self, "Corbeille incomplète",
                "La corbeille de session n'a pas pu être vidée entièrement. Ce "
                "qui reste est gardé et sera repris au prochain lancement "
                "(Ctrl+B).\n\n" + problem,
            )

    @staticmethod
    def _in_trash(key: str) -> bool:
        """Ce chemin (`normcase`) dort-il dans une corbeille ?

        L'analyse d'un dossier descend aussi dans sa corbeille de seance : ce
        qu'on venait de supprimer ressortait au tirage au hasard.
        """
        local_trash = os.path.normcase(str(actions.LOCAL_TRASH)).rstrip("\\/")
        return (os.sep + TRASH_FOLDER_NAME + os.sep in key
                or key.startswith(local_trash + os.sep))

    def on_trash_changed(self, count: int) -> None:
        self._show_trash_badge(count)
        for action in self.overflow.actions():
            if action.text().startswith("Corbeille"):
                action.setText(
                    f"Corbeille de session ({count})" if count
                    else "Corbeille de session"
                )

    def _show_trash_badge(self, count: int) -> None:
        """La pastille de la corbeille de session : son compte, et son sort."""
        badge = getattr(self, "trash_badge", None)
        if badge is None:
            return
        if not count:
            badge.hide()
            return
        mode = self.cfg["delete_mode"]
        doomed = mode == "permanent" or (mode != "local_trash" and any(
            self._is_remote(entry.origin) for entry in self.trash.entries))
        tone = "doomed" if doomed else "kept"
        if tone != self._trash_badge_tone:
            self._trash_badge_tone = tone
            badge.setStyleSheet(
                "QPushButton#trashBadge { background: #3a2226; border-color: #6b3036;"
                " color: #ffb4b8; font-weight: 600; padding: 4px 10px; }"
                if doomed else
                "QPushButton#trashBadge { color: #c9d1db; padding: 4px 10px; }")
        # Le poids, a cote du nombre : ce que la fermeture va liberer.
        weight = sum(int(entry.size or 0) for entry in self.trash.entries)
        badge.setText(f"{count} · {self._weight(weight)}" if weight else str(count))
        noun = "élément" if count == 1 else "éléments"
        badge.setToolTip(
            (f"{count} {noun} ({self._weight(weight)}) dans la corbeille de session — "
             if weight else f"{count} {noun} dans la corbeille de session — ")
            + ("détruit(s) définitivement à la fermeture de Prisme.\n"
               if doomed else "envoyé(s) à la corbeille de Windows à la fermeture.\n")
            + "Cliquer pour les revoir ou les reprendre   (Ctrl+B)")
        badge.show()
