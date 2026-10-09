"""Le curseur du volume, sous le haut-parleur de l'en-tête.

Le haut-parleur reste un seul bouton : un clic coupe ou rend le son. Le
survoler fait paraître, juste dessous, un curseur fin et le pourcentage ; on
le glisse, ou l'on tourne la molette (sur le bouton comme sur le curseur). Il
s'efface quand la souris s'en va : rien de plus dans l'en-tête.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer
from PySide6.QtWidgets import QFrame, QLabel, QSlider, QVBoxLayout

STYLE = """
QFrame#volumeFlyout { background: #161b22; border: 1px solid #2b3542; border-radius: 12px; }
QLabel#volumePct { color: #dbe3ee; font-size: 11px; font-weight: 600; }
QSlider { background: #161b22; }
QSlider::groove:vertical { width: 6px; border-radius: 3px; background: #2a323d; }
QSlider::add-page:vertical { width: 6px; border-radius: 3px;
    background: qlineargradient(x1:0, y1:1, x2:0, y2:0, stop:0 #4f8bf0, stop:1 #8fb4ff); }
QSlider::sub-page:vertical { width: 6px; border-radius: 3px; background: #2a323d; }
QSlider::handle:vertical { height: 16px; width: 16px; margin: 0 -5px; border-radius: 8px;
                           background: #ffffff; }
"""


class VolumeFlyout(QFrame):
    """`volume()` rend le volume (0 a 100), `muted()` dit si le son est
    coupe, `set_volume(n)` le regle (et rend le son)."""

    def __init__(self, button, volume, muted, set_volume):
        super().__init__(button.window(), Qt.ToolTip | Qt.FramelessWindowHint)
        self.setObjectName("volumeFlyout")
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(STYLE)
        self.button = button
        self.volume, self.muted, self.set_volume = volume, muted, set_volume
        box = QVBoxLayout(self)
        box.setContentsMargins(8, 12, 8, 8)
        box.setSpacing(6)
        self.slider = QSlider(Qt.Vertical, self)
        self.slider.setRange(0, 100)
        self.slider.setSingleStep(5)
        self.slider.setPageStep(10)
        self.slider.setFixedHeight(130)
        self.slider.setFocusPolicy(Qt.NoFocus)
        self.slider.valueChanged.connect(self._moved)
        box.addWidget(self.slider, 0, Qt.AlignHCenter)
        self.pct = QLabel("", self)
        self.pct.setObjectName("volumePct")
        self.pct.setAlignment(Qt.AlignCenter)
        box.addWidget(self.pct)
        self.setFixedWidth(52)
        self._show = QTimer(self)
        self._show.setSingleShot(True)
        self._show.setInterval(320)
        self._show.timeout.connect(self.pop)
        self._hide = QTimer(self)
        self._hide.setSingleShot(True)
        self._hide.setInterval(450)
        self._hide.timeout.connect(self._maybe_hide)
        button.installEventFilter(self)

    def eventFilter(self, watched, event):          # noqa: N802
        if watched is self.button:
            kind = event.type()
            if kind == QEvent.Enter:
                self._hide.stop()
                self._show.start()
            elif kind == QEvent.Leave:
                self._show.stop()
                self._hide.start()
            elif kind == QEvent.Wheel:
                # La fenetre regle le volume (son propre filtre) : le curseur
                # parait et suit.
                QTimer.singleShot(0, self.pop)
            elif kind == QEvent.MouseButtonPress:
                self._show.stop()
        return False

    def enterEvent(self, event):                    # noqa: N802
        self._hide.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):                    # noqa: N802
        self._hide.start()
        super().leaveEvent(event)

    def wheelEvent(self, event):                    # noqa: N802
        notches = event.angleDelta().y() / 120.0
        if notches:
            self.set_volume(int(self.volume()) + int(round(notches * 5)))
            self.sync()
        event.accept()

    def pop(self) -> None:
        if not self.button.isVisible():
            return
        self.sync()
        self.adjustSize()
        spot = self.button.mapToGlobal(QPoint(self.button.width() // 2 - self.width() // 2,
                                              self.button.height() + 4))
        self.move(spot)
        self.show()
        self.raise_()
        self._hide.start(2200)

    def sync(self) -> None:
        """Le curseur et le pourcentage, d'apres le volume du moment."""
        value = int(self.volume())
        self.slider.blockSignals(True)
        self.slider.setValue(value)
        self.slider.blockSignals(False)
        self.pct.setText("coupé" if self.muted() else f"{value} %")

    def _moved(self, value: int) -> None:
        self.set_volume(value)
        self.sync()

    def _maybe_hide(self) -> None:
        if self.slider.isSliderDown() or self.underMouse() or self.button.underMouse():
            self._hide.start(450)
            return
        self.hide()
