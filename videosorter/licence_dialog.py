"""La fenetre de licence : l'essai fini, on y colle sa cle avant d'ouvrir
Prisme ; ou, a tout moment (Aide › Licence…), on y voit ou l'on en est.

« Quitter » ne touche a rien : ni reglages, ni index, ni fichiers.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton,
    QVBoxLayout,
)

from . import licence


def describe(state: licence.Status) -> str:
    if state.mode == "dev":
        return "Votre version personnelle : ni essai ni licence."
    if state.mode == "essai":
        return (f"Essai gratuit : encore {state.days_left} jour"
                f"{'s' if state.days_left > 1 else ''}.")
    if state.mode == "licence":
        plan = {"mois": "abonnement mensuel", "an": "abonnement annuel",
                "vie": "licence à vie"}.get(state.plan, "licence")
        if not state.expires:
            return f"Merci ! {plan.capitalize()}."
        until = time.strftime("%d/%m/%Y", time.localtime(state.expires))
        return f"Merci ! {plan.capitalize()}, valable jusqu'au {until}."
    return "L'essai de 14 jours est terminé."


class LicenceDialog(QDialog):
    def __init__(self, cfg, parent=None, required: bool = False):
        super().__init__(parent)
        self.cfg = cfg
        self.required = required
        self.setWindowTitle("Licence de Prisme")
        self.setMinimumWidth(520)
        box = QVBoxLayout(self)
        self.state_label = QLabel("", self)
        self.state_label.setWordWrap(True)
        self.state_label.setStyleSheet("font-size: 14px; font-weight: 600;")
        box.addWidget(self.state_label)
        told = QLabel(
            "Collez ici la clé reçue par e-mail après l'achat (elle commence par "
            "« PRISME1- »). Une licence s'active sur deux ordinateurs au plus : il faut "
            "une connexion internet la première fois, ensuite Prisme s'en passe.", self)
        told.setWordWrap(True)
        box.addWidget(told)
        self.field = QPlainTextEdit(self)
        self.field.setPlaceholderText("PRISME1-…")
        self.field.setFixedHeight(80)
        box.addWidget(self.field)
        self.error = QLabel("", self)
        self.error.setStyleSheet("color: #e0a040;")
        self.error.setWordWrap(True)
        box.addWidget(self.error)
        row = QHBoxLayout()
        buy = QPushButton("Obtenir une licence…", self)
        buy.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(licence.BUY)))
        row.addWidget(buy)
        # Deux ordinateurs par licence : on libere celui-ci pour en activer un autre.
        self.free = QPushButton("Libérer cet ordinateur", self)
        self.free.clicked.connect(self._release)
        row.addWidget(self.free)
        row.addStretch(1)
        self.leave = QPushButton("Quitter" if required else "Fermer", self)
        self.leave.clicked.connect(self.reject)
        row.addWidget(self.leave)
        ok = QPushButton("Valider la clé", self)
        ok.setDefault(True)
        ok.clicked.connect(self._validate)
        row.addWidget(ok)
        box.addLayout(row)
        self._refresh()

    def _refresh(self) -> None:
        state = licence.status(self.cfg)
        self.state_label.setText(describe(state))
        self.free.setVisible(state.mode == "licence")

    def _validate(self) -> None:
        # Appel au site (quelques secondes au plus) : la souris le montre.
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            said = licence.accept_key(self.cfg, self.field.toPlainText())
        finally:
            QApplication.restoreOverrideCursor()
        if said:
            self.error.setText(said)
            return
        self.error.setText("")
        self._refresh()
        if licence.status(self.cfg).usable:
            self.accept()

    def _release(self) -> None:
        asked = QMessageBox.question(
            self, "Libérer cet ordinateur",
            "Votre licence quittera cet ordinateur : vous pourrez l'activer sur un autre "
            "(deux au plus). Prisme repassera ici en essai, ou demandera la clé si l'essai "
            "est terminé. Continuer ?")
        if asked != QMessageBox.StandardButton.Yes:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            said = licence.release(self.cfg)
        finally:
            QApplication.restoreOverrideCursor()
        self.error.setText(said)
        self._refresh()


def gate(cfg, before=None, parent=None) -> bool:
    """Avant d'ouvrir Prisme : essai fini sans licence valable, on demande la
    cle. Rend faux si l'on a choisi de quitter."""
    if licence.status(cfg).usable:
        return True
    if before is not None:
        before()                     # l'ecran d'accueil s'efface d'abord
    dialog = LicenceDialog(cfg, parent, required=True)
    dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    return dialog.exec() == QDialog.DialogCode.Accepted and licence.status(cfg).usable
