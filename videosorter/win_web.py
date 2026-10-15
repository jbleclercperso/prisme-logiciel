"""La recherche web et les pastilles des travaux en cours."""
from __future__ import annotations

import json
import shiboken6
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton, QSizePolicy
from .media import Tools


def _weight(size: int) -> str:
    """Un poids lisible : « 850 Mo », « 1,4 Go »."""
    if size >= 1e9:
        return f"{size / 1e9:.1f} Go".replace(".", ",")
    return f"{size / 1e6:.0f} Mo"


def web_recap(activity: dict) -> tuple:
    """(texte, avancement 0..1 ou None, infobulle) de ce que fait la recherche
    web -- ou ("", None, "") s'il n'y a rien a dire."""
    parts, tips = [], []
    if activity.get("searching"):
        parts.append(f"Recherche {activity['sites_done']}/{activity['sites_total']} sites"
                     f" · {activity['results']} résultats")
    elif activity.get("verifying"):
        parts.append(f"Vérifiées {activity['checked']}/{activity['results']}"
                     f" · {activity['good']} bonnes")
    downloads = activity.get("downloads") or {}
    fraction = None
    active = downloads.get("active", 0)
    if active:
        done, total = downloads.get("done", 0), downloads.get("total", 0)
        text = f"↓ {active}"
        if total:
            text += f" · {_weight(done)} / {_weight(total)}"
            if downloads.get("unknown"):
                text += " +"                    # des fichiers au poids encore inconnu
            fraction = min(1.0, done / total)
        elif done:
            text += f" · {_weight(done)}"
        parts.append(text)
    else:
        finished, failed = downloads.get("finished", 0), downloads.get("failed", 0)
        if finished or failed:
            text = f"✓ {finished} téléchargé{'s' if finished > 1 else ''}" if finished else ""
            if failed:
                text += (" · " if text else "") + f"{failed} échec{'s' if failed > 1 else ''}"
            parts.append(text)
    tips = list(activity.get("lines") or [])
    if not parts:
        return "", None, ""
    tip = "Recherche vidéo sur le web — cliquez pour y revenir"
    if tips:
        tip += "\n\n" + "\n".join(tips[:12])
        if len(tips) > 12:
            tip += f"\n… et {len(tips) - 12} autre(s)"
    return "  |  ".join(parts), fraction, tip


class WebMixin:
    """Une part de la fenetre principale : MainWindow en herite."""

    def search_web_for(self, text: str) -> None:
        """La recherche web, preremplie avec cette demande."""
        window = self._web_search_window()
        window.queries.setPlainText(text)
        window.showNormal()
        window.raise_()
        window.activateWindow()

    def _show_web_status(self) -> None:
        """La pastille de la recherche web, a jour (chaque seconde)."""
        window = getattr(self, "_web_window", None)
        text, fraction, tip = "", None, ""
        if window is not None and shiboken6.isValid(window):
            try:
                text, fraction, tip = web_recap(window.activity())
            except Exception:                           # noqa: BLE001
                text = ""
        self._show_task_pills()
        if not text:
            self.web_status.hide()
            return
        if self.web_status.text() != text:
            self.web_status.setText(text)
            # Sa largeur, prise au fil d'Ariane (qui cede la sienne), jamais
            # au point d'elargir la fenetre.
            self.web_status.setMinimumWidth(min(self.web_status.sizeHint().width(), 460))
        self.web_status.setToolTip(tip)
        # L'avancement des telechargements remplit la pastille, de gauche a
        # droite, comme une barre de progression.
        base = ("QPushButton#webStatus { border: 1px solid #2f4a78; border-radius: 11px;"
                " padding: 2px 11px; color: #dfe8f5; font-size: 12px; background: %s; }"
                "QPushButton#webStatus:hover { border-color: #4f8bf0; color: #ffffff; }")
        if fraction is None:
            fill = "#151a21"
        else:
            edge = max(0.0, min(0.999, fraction))
            fill = ("qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1f3d6e, "
                    f"stop:{edge:.3f} #1f3d6e, stop:{edge + 0.001:.3f} #151a21, stop:1 #151a21)")
        style = base % fill
        if style != self._web_status_style:
            self._web_status_style = style
            self.web_status.setStyleSheet(style)
        self.web_status.show()

    @staticmethod
    def _pill_style(fraction) -> str:
        """Une pastille qui se remplit de gauche a droite, comme celle du web."""
        if fraction is None:
            fill = "#151a21"
        else:
            edge = max(0.0, min(0.999, float(fraction)))
            fill = ("qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1f3d6e, "
                    f"stop:{edge:.3f} #1f3d6e, stop:{edge + 0.001:.3f} #151a21, stop:1 #151a21)")
        return ("QPushButton#taskPill { border: 1px solid #2f4a78; border-radius: 11px;"
                " padding: 2px 11px; color: #dfe8f5; font-size: 12px; background: %s; }"
                "QPushButton#taskPill:hover { border-color: #4f8bf0; color: #ffffff; }"
                % fill)

    def _task_pill_states(self) -> list:
        """Ce qui tourne en fond : [(clef, texte, avancement, infobulle, clic)]."""
        found = []
        lab = getattr(self, "_lab_window", None)
        if lab is not None and shiboken6.isValid(lab):
            try:
                said = lab.pill_activity()
            except Exception:                           # noqa: BLE001
                said = None
            if said:
                found.append(("labo", said[0], said[1], said[2], self.open_ai_lab))
        if getattr(self, "sig_scan", None) is not None:
            done, total = getattr(self, "_sig_counts", (0, 0))
            text = self.__dict__.get("_task_text", {}).get("sig_scan") or "empreintes…"
            found.append(("sigs", f"⧉ {text}", done / total if total else None,
                          "Les empreintes des vidéos, pour la recherche de doublons.\n"
                          "⋯ › Doublons pour l'arrêter.", None))
        if getattr(self, "ultra", False):
            found.append(("ultra", "⚡ Ultra tri — gauche : garder · droit : supprimer",
                          None, "Sur la fiche d'une vidéo : clic gauche, on la garde et on "
                          "passe à la suivante ; clic droit, on la supprime (Ctrl+Z annule).\n"
                          "Cliquer ici (ou Ctrl+U) pour arrêter.",
                          lambda: self.toggle_ultra(False)))
        state = getattr(self, "_solo_state", None)
        if state is not None and getattr(self, "_solo_busy", False):
            total = max(1, state["total"])
            found.append(("solos", f"⇢ « 1 » : {state['done']} / {state['total']}",
                          state["done"] / total,
                          f"Regroupement des vidéos seules dans « 1 » — {state['name']}",
                          None))
        dialog = getattr(self, "_repair_dialog", None)
        if dialog is not None and shiboken6.isValid(dialog) and dialog.busy:
            done, total, whole, name = dialog.progress_state
            found.append(("repair", f"🛠 réparation {min(done + 1, max(total, 1))} / "
                          f"{max(total, 1)} · {int(whole * 100)} %", whole,
                          f"Vérification ou réparation de vidéos abîmées — {name}\n"
                          "Cliquer pour ouvrir la fenêtre.", self._show_repair))
        from . import voice
        part = voice.download_progress()
        if part is not None:
            found.append(("voix", f"🎤 voix : {int(part * 100)} %", part,
                          "Téléchargement du modèle de reconnaissance vocale (970 Mo)",
                          None))
        return found

    def _show_task_pills(self) -> None:
        states = self._task_pill_states()
        alive = set()
        for key, text, fraction, tip, slot in states:
            alive.add(key)
            pill = self.task_pills.get(key)
            if pill is None:
                pill = QPushButton("", self.web_status.parentWidget())
                pill.setObjectName("taskPill")
                pill.setFocusPolicy(Qt.NoFocus)
                pill.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
                pill.setMaximumWidth(420)
                if slot is not None:
                    pill.setCursor(Qt.PointingHandCursor)
                    pill.clicked.connect(slot)
                pill._style = None
                self._pill_row.addWidget(pill)
                self.task_pills[key] = pill
            if pill.text() != text:
                pill.setText(text)
                pill.setMinimumWidth(min(pill.sizeHint().width(), 380))
            pill.setToolTip(tip)
            style = self._pill_style(fraction)
            if key == "ultra":
                style = style.replace("#2f4a78", "#b8691f").replace("#151a21", "#2a1a0c")
            if style != pill._style:
                pill._style = style
                pill.setStyleSheet(style)
            pill.show()
        for key, pill in self.task_pills.items():
            if key not in alive and pill.isVisible():
                pill.hide()

    def open_web_search(self) -> None:
        """Recherche de videos hors des grandes plateformes, en lien seul.

        Importee a la premiere ouverture seulement (requests, bs4) : pres
        d'une seconde, qu'on payait sinon a chaque lancement.
        """
        # Une fenetre a part, qu'on garde ouverte a cote : elle etait une
        # boite modale, petite, qui bloquait Prisme tant qu'on cherchait.
        window = self._web_search_window()
        window.showNormal()
        window.raise_()
        window.activateWindow()

    def _web_search_window(self, busy_cursor: bool = True):
        """La fenetre de recherche web, creee au premier besoin (cachee)."""
        window = getattr(self, "_web_window", None)
        if window is not None and shiboken6.isValid(window):
            return window
        if busy_cursor:
            QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            from .search_dialog import WebSearchWindow
            window = WebSearchWindow(self.cfg, self, ffmpeg=Tools.ffmpeg)
        finally:
            if busy_cursor:
                QApplication.restoreOverrideCursor()
        window.setAttribute(Qt.WA_DeleteOnClose)
        self._web_window = window
        return window

    def _resume_web_downloads(self) -> None:
        """Au lancement : des telechargements interrompus a la derniere
        fermeture ? La recherche web se rouvre en coulisse et les reprend (la
        pastille de la barre les montre) -- sans attendre qu'on ouvre sa
        fenetre."""
        import json
        from .config import PRIVATE_DIR
        try:
            data = json.loads((PRIVATE_DIR / "recherche-web.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        pending = [d for d in (data.get("downloads") or [])
                   if isinstance(d, dict) and d.get("state") == "running"]
        if pending and not getattr(self, "_closing", False):
            self._web_search_window(busy_cursor=False)
