"""Mises a jour, licence et essai."""
from __future__ import annotations

import threading
from pathlib import Path
import shiboken6
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox
from . import licence
from . import update
from .widgets import app_icon


class UpdateMixin:
    """Une part de la fenetre principale : MainWindow en herite."""

    # -- les mises a jour -------------------------------------------------------
    def check_updates(self, quiet: bool = True) -> None:
        """Regarde, hors du fil de l'interface, s'il existe une version plus
        recente. `quiet` (au lancement, toutes les six heures) : rien n'est dit
        quand il n'y a rien -- ni quand la source ne repond pas."""
        if self._update_busy or self._closing:
            return
        where = update.source(self.cfg["update_source"] or "")
        if not where:
            if not quiet:
                self.show_banner(
                    f"Prisme {update.__version__} — "
                    + ("lancé depuis ses sources : il se met à jour par git."
                       if not update.frozen() else
                       "aucune source de mises à jour n'est réglée."),
                    "quiet", seconds=8)
            return
        from .tunnel import Chore

        def work():
            try:
                return update.check(where)
            except update.UpdateError as trouble:
                return trouble

        Chore(work, self, fallback=None,
              then=lambda found: self._update_checked(found, quiet)).start()

    @staticmethod
    def _update_notes(found: dict) -> str:
        notes = found.get("notes") or []
        if isinstance(notes, str):
            notes = [notes]
        return "\n".join(f"• {line}" for line in notes if str(line).strip())

    def _update_checked(self, found, quiet: bool) -> None:
        if not shiboken6.isValid(self) or self._closing or self._update_busy:
            return
        if isinstance(found, update.UpdateError):
            if not quiet:
                self.show_banner(str(found), "error", seconds=8)
            return
        self._update_found = found
        if not found:
            self.update_badge.hide()
            if getattr(self, "_update_tray", None) is not None:
                self._update_tray.hide()
            if not quiet:
                self.show_banner(f"Prisme est à jour (version {update.__version__}).",
                                 "done")
            return
        version = found["version"]
        self.update_badge.setText(f"⬆ Mise à jour {version}")
        notes = self._update_notes(found)
        self.update_badge.setToolTip(
            f"Prisme {version} est disponible — cliquez pour mettre à jour"
            + (f"\n\n{notes}" if notes else ""))
        self.update_badge.show()
        if not quiet:
            return self.install_update()
        if self._update_said != version:
            self._update_said = version
            self.show_banner(
                f"Prisme {version} est disponible (vous avez la {update.__version__}). "
                "Cliquez ici pour mettre à jour — ou plus tard, par la pastille "
                "en haut.", "info", action=self.install_update, seconds=20)
            self._notify_update(version, notes)

    def _notify_update(self, version: str, notes: str) -> None:
        """Une notification Windows, et Prisme qui clignote dans la barre des
        taches : le bandeau seul passait inapercu, fenetre reduite ou cachee
        derriere une autre."""
        QApplication.alert(self, 0)
        from PySide6.QtWidgets import QSystemTrayIcon
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        tray = getattr(self, "_update_tray", None)
        if tray is None:
            tray = QSystemTrayIcon(app_icon(), self)
            tray.messageClicked.connect(self._update_from_tray)
            tray.activated.connect(lambda _why: self._update_from_tray())
            self._update_tray = tray
        tray.setToolTip(f"Prisme {version} est disponible — cliquez pour mettre à jour")
        tray.show()
        first = notes.splitlines()[0].lstrip("• ") if notes else ""
        tray.showMessage(f"Prisme {version} est disponible",
                         (first + "\n" if first else "")
                         + "Cliquez pour mettre à jour.",
                         QSystemTrayIcon.Information, 15000)

    def _update_from_tray(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()
        self.install_update()

    def install_update(self) -> None:
        """Dit ce qui change, puis telecharge, verifie et pose -- d'un clic."""
        ready = getattr(self, "_update_script", None)
        if ready is not None and ready.exists():
            return self._close_for_update(ready)
        found = self._update_found
        if found is None:
            return self.check_updates(quiet=False)
        if self._update_busy:
            self.show_banner("Mise à jour en cours de téléchargement…", "quiet")
            return
        if not update.frozen():
            self.show_banner("Prisme tourne depuis ses sources : mettez-le à jour "
                             "par git.", "quiet", seconds=8)
            return
        version = found["version"]
        notes = self._update_notes(found)
        box = QMessageBox(self)
        box.setWindowTitle("Mettre à jour Prisme")
        box.setIcon(QMessageBox.Information)
        box.setText(f"<b>Prisme {version} est disponible</b> "
                    f"(vous avez la {update.__version__}).")
        box.setInformativeText(
            (f"Nouveautés :\n{notes}\n\n" if notes else "")
            + "Prisme télécharge la nouvelle version, se ferme, puis se rouvre "
            "tout seul quelques secondes plus tard. Réglages, index, vignettes "
            "et favoris ne bougent pas.")
        go = box.addButton("Mettre à jour maintenant", QMessageBox.AcceptRole)
        box.addButton("Plus tard", QMessageBox.RejectRole)
        box.setDefaultButton(go)
        box.exec()
        if box.clickedButton() is not go:
            return
        from .tunnel import Chore
        where = update.source(self.cfg["update_source"] or "")
        self._update_busy = True
        self._update_progress = (0, 0)

        def progress(done: int, total: int) -> None:
            self._update_progress = (done, total)

        def work():
            try:
                return update.prepare(where, found, progress,
                                      should_stop=lambda: self._closing)
            except update.UpdateError as trouble:
                return trouble
            except Exception as trouble:            # noqa: BLE001
                return update.UpdateError(f"Mise à jour impossible : {trouble}")

        self._update_ticker = QTimer(self)
        self._update_ticker.setInterval(400)
        self._update_ticker.timeout.connect(self._update_tick)
        self._update_ticker.start()
        self._update_tick()
        Chore(work, self, fallback=None, then=self._update_ready).start()

    def _update_tick(self) -> None:
        done, total = getattr(self, "_update_progress", (0, 0))
        share = f" {int(100 * done / total)} %" if total else ""
        self.update_badge.setText(f"⬆ Téléchargement{share}")

    def _update_ready(self, script) -> None:
        if not shiboken6.isValid(self):
            return
        self._update_ticker.stop()
        self._update_busy = False
        found = self._update_found or {}
        self.update_badge.setText(f"⬆ Mise à jour {found.get('version', '')}")
        if self._closing:
            return
        if not isinstance(script, Path):
            self.show_banner(str(script) or "Mise à jour impossible.", "error",
                             seconds=12)
            return
        # Fermer d'abord, et ne lancer la pose qu'une fois la fermeture
        # acceptee : refusee (des telechargements en cours), la pose aurait
        # attendu pour rien, puis renonce sans rien dire.
        self.show_banner("Mise à jour prête : Prisme se ferme et se rouvre…", "done")
        QTimer.singleShot(400, lambda: self._close_for_update(script))

    def _close_for_update(self, script: Path) -> None:
        if not self.close():
            # Deja telechargee et verifiee : la pastille la posera sans tout
            # refaire.
            self._update_script = script
            self.update_badge.setText("⬆ Poser la mise à jour")
            self.show_banner("Mise à jour prête : cliquez la pastille en haut quand "
                             "Prisme peut se fermer.", "info", seconds=10)
            return
        try:
            self.cfg["update_tried"] = (self._update_found or {}).get("version", "")
            self.cfg.save()
            update.start_swap(script)
        except OSError as trouble:
            # Trop tard pour le dire a l'ecran : la fenetre est fermee.
            try:
                (script.parent / "pose.log").open("a", encoding="utf-8").write(
                    f"Lancement de la pose impossible : {trouble}\n")
            except OSError:
                pass

    def _said_new_version(self) -> None:
        """Au lancement : la version a change depuis la derniere fois ? On le
        dit, avec ce que la pose a note. Et l'on range ce qu'elle a laisse."""
        update.tidy()
        before = self.cfg["version_seen"] or ""
        if before != update.__version__:
            self.cfg["version_seen"] = update.__version__
            self.cfg.save_soon()
            if before and update.newer(update.__version__, before):
                self.show_banner(f"Prisme est passé à la version {update.__version__}.",
                                 "done", seconds=10)
        # Une pose lancee a la fermeture, et pourtant toujours l'ancienne
        # version : on le dit, avec ce que la pose a note.
        tried = self.cfg["update_tried"] or ""
        if tried:
            self.cfg["update_tried"] = ""
            self.cfg.save_soon()
            if update.newer(tried, update.__version__):
                said = update.last_log_line()
                self.show_banner(
                    f"La mise à jour {tried} n'a pas pu se poser"
                    + (f" : {said.split(' ', 2)[-1]}" if said else ".")
                    + " Réessayez par la pastille en haut.", "error", seconds=20)

    # -- l'essai et la licence -------------------------------------------------
    def _show_trial(self) -> None:
        """« Essai · N jours » en haut de la fenetre, pendant l'essai."""
        try:
            state = licence.status(self.cfg)
        except Exception:                                   # noqa: BLE001
            return self.trial_badge.hide()
        if state.mode == "essai":
            self.trial_badge.setText(f"Essai · {state.days_left} jour"
                                     f"{'s' if state.days_left > 1 else ''}")
            self.trial_badge.show()
        else:
            self.trial_badge.hide()

    def open_licence(self) -> None:
        from .licence_dialog import LicenceDialog
        LicenceDialog(self.cfg, self).exec()
        self._show_trial()
        self.setFocus()

    def renew_licence(self) -> None:
        """Un abonnement dans ses dix derniers jours : une cle prolongee est
        demandee au site, hors du fil de l'interface."""
        if not licence.PUBLIC_KEY or not licence.renewal_due(self.cfg):
            return
        import threading
        self._renewal_said = None

        def ask() -> None:
            self._renewal_said = licence.renew(self.cfg)
        threading.Thread(target=ask, daemon=True, name="prisme-licence").start()
        QTimer.singleShot(12000, self._took_renewal)
