"""Les bandeaux : ce qui se passe, dit en haut de la fenetre."""
from __future__ import annotations

import time
from PySide6.QtCore import QPoint, QTimer, Qt


# Les tons du bandeau d'etat. Ils etaient ecrits en dur a chaque appel, avec
# sept teintes pour quatre intentions.
BANNER_TONES = {
    "info": "#22303f",     # ce qui se passe
    "quiet": "#2a2f38",    # ce qui n'a rien donne
    "done": "#1f3326",     # ce qui a abouti
    "error": "#3a2226",    # ce qui a echoue
    # Ce qu'on vient d'ecarter : ambre, pas rouge. Une suppression voulue
    # s'affichait comme un echec, et l'on ne voyait plus le rouge le jour
    # d'un vrai echec.
    "trash": "#46381a",
}


class BannerMixin:
    """Une part de la fenetre principale : MainWindow en herite."""

    def show_banner(self, text: str, tone: str = "info", action=None,
                    seconds: int = 4, keep: bool = False) -> None:
        """Un bandeau, cinq tons : info, quiet, done, error, et trash (ce
        qu'on vient d'ecarter, qu'un clic reprend). Rien d'autre.

        `action` : ce que fait un clic sur le bandeau (« afficher les
        doublons ») ; il reste alors plus longtemps, le temps d'y aller.
        `keep` : comme un echec, il ne se laisse pas effacer aussitot. Un
        echec garde (`keep` et « error ») est une alerte : aucun autre bandeau,
        pas meme un echec, ne la remplace avant qu'on l'ait lue.
        """
        floating = getattr(self, "floating", None)
        if floating is not None and floating.active and not self._quiet:
            # Le lecteur flottant le dit aussi : Prisme est souvent reduit ou
            # recouvert pendant qu'il joue, et l'on triait sans rien lire.
            floating.say(text, BANNER_TONES.get(tone, BANNER_TONES["info"]), seconds)
            if self.isMinimized() and tone != "error":
                return
        if self._quiet or self.isMinimized():
            # Retenu, et montre au retour : un « Échec sur … » dit pendant le
            # repli ou fenetre reduite etait efface sans avoir ete lu, et l'on
            # croyait rangee une video qui n'avait pas bouge.
            self._held_banners.append((text, tone, action, seconds))
            del self._held_banners[:-12]
            return
        now = time.monotonic()
        alarm = keep and tone == "error"
        if (not alarm and now < self._alarm_until and self.banner.isVisible()):
            # Une alerte a l'ecran : ce qui suit -- la suppression suivante,
            # par exemple -- attend qu'elle ait ete lue au lieu de l'effacer.
            self._banner_next = (text, tone, action, seconds)
            QTimer.singleShot(int((self._alarm_until - now) * 1000) + 20,
                              self._flush_banner_next)
            return
        if (action is None and tone != "error" and not keep
                and now < self._banner_guard and self.banner.isVisible()):
            # Un echec encore frais : le message ordinaire attend son tour au
            # lieu de l'effacer avant qu'on l'ait lu.
            self._banner_next = (text, tone, action, seconds)
            QTimer.singleShot(int((self._banner_guard - now) * 1000) + 20,
                              self._flush_banner_next)
            return
        self._banner_next = None
        self._banner_guard = (now + self.BANNER_GUARD_S
                              if tone == "error" or keep else 0.0)
        self._alarm_until = now + max(1, int(seconds)) if alarm else 0.0
        self._banner_action = action
        self.banner.setCursor(Qt.PointingHandCursor if action is not None
                              else Qt.ArrowCursor)
        self.banner.setText(text)
        if tone != self._banner_tone:
            # Restyler coute : un comptage qui parlait trois fois par seconde
            # refaisait la feuille de style a chaque fois, pour le meme ton.
            self._banner_tone = tone
            color = BANNER_TONES.get(tone, BANNER_TONES["info"])
            self.banner.setStyleSheet(
                f"QLabel {{ background: {color}; color: #e9eef4; border-radius: 8px;"
                " padding: 8px 14px; font-weight: 600; }")
        self._place_banner()
        self.banner.show()
        self.banner.raise_()
        self.banner_timer.start(max(1, int(seconds)) * 1000)

    def _flush_banner_next(self) -> None:
        """Le message qui attendait la fin d'un echec, maintenant."""
        following, self._banner_next = self._banner_next, None
        if following is not None and not self._closing:
            self._banner_guard = 0.0
            self.show_banner(*following)

    def _show_held_banners(self) -> None:
        """Ce qui s'est dit pendant le repli ou fenetre reduite.

        Les echecs d'abord, ensemble, et plus longtemps : c'est ce qu'il ne
        faut pas manquer. A defaut, le dernier message seulement.
        """
        held, self._held_banners = self._held_banners, []
        if not held:
            return
        errors = []
        for text, tone, _action, _seconds in held:
            if tone == "error" and text not in errors:
                errors.append(text)
        if errors:
            action = next((a for _t, tone, a, _s in reversed(held)
                           if tone == "error"), None)
            self.show_banner("\n".join(errors[-3:]), "error", action, seconds=10)
            return
        text, tone, action, seconds = held[-1]
        self.show_banner(text, tone, action, seconds)

    def _place_banner(self) -> None:
        """En haut de la zone d'image, centre : il se lit sans rien pousser."""
        anchor = self.viewer if self.viewer.isVisible() else self.centralWidget()
        width = max(240, min(680, anchor.width() - 40))
        height = self.banner.heightForWidth(width)
        if height <= 0:
            height = self.banner.sizeHint().height()
        corner = anchor.mapToGlobal(QPoint((anchor.width() - width) // 2, 10))
        self.banner.setGeometry(corner.x(), corner.y(), width, height)
