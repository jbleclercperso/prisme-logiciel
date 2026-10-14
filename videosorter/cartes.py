"""Une vignette de video dessinee comme celles de la planche (board.py) :
cadre arrondi, image 16:9, duree en haut a droite, case a cocher en haut a
gauche, et dessous « 1080p · nom ». Pour les listes des autres fenetres
(Telegram, Labo IA) : on y retrouve ses reperes de la fenetre principale.

Un QListWidget ordinaire, en mode icones, avec `CardDelegate` :
- l'image : l'icone de l'element (DecorationRole) ;
- le nom : la premiere ligne du texte (DisplayRole) ;
- ROLE_CHIP : le texte de la pastille (« 12:34 ») ;
- ROLE_RES : la hauteur de l'image (1080), dite « 1080p » ;
- ROLE_SUB : une ligne de plus, plus discrete (date, poids…).
La case n'existe que si l'element est cochable (Qt.ItemIsUserCheckable).
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QListView, QListWidget, QStyle, QStyledItemDelegate

from .scan import human_resolution

ROLE_CHIP = Qt.UserRole + 20
ROLE_RES = Qt.UserRole + 21
ROLE_SUB = Qt.UserRole + 22

# Les couleurs de la planche (widgets.STYLESHEET : #boardCard, #boardImage,
# #boardMeta, #tileDuration, #cardPick).
CARD_BG = QColor("#171b21")
CARD_BG_HOVER = QColor("#1c222b")
CARD_BORDER = QColor("#262c35")
CARD_BORDER_HOVER = QColor("#4c8dff")
CARD_BORDER_PICKED = QColor("#2f6fed")
IMAGE_BG = QColor("#0b0d10")
META = QColor("#8b95a3")
TITLE = QColor("#d5dde6")
SUB = QColor("#6f7a88")
CHIP_BG = QColor(0, 0, 0, 199)
PICK_ON = QColor("#2f6fed")
PICK_OFF = QColor("#6b7684")

GAP = 6          # entre deux vignettes, comme CARD_GAP
PAD = 4          # marge interieure du cadre
LINES = 2        # nom + ligne discrete


def card_size(width: int) -> QSize:
    """La case de grille pour une vignette de `width` pixels de large."""
    image_h = (width - 2 * PAD) * 9 // 16
    return QSize(width + GAP, image_h + 2 * PAD + 20 * LINES + GAP)


def use_cards(view: QListWidget, width: int = 236) -> "CardDelegate":
    """Fait d'une liste une grille de vignettes facon planche."""
    view.setViewMode(QListView.IconMode)
    view.setResizeMode(QListView.Adjust)
    view.setMovement(QListView.Static)
    view.setUniformItemSizes(True)
    view.setSpacing(0)
    view.setMouseTracking(True)
    view.setSelectionRectVisible(False)
    view.setStyleSheet("QListWidget { background: #101318; border: none; }"
                       "QListWidget::item { background: transparent; border: none; }")
    delegate = CardDelegate(view)
    view.setItemDelegate(delegate)
    set_card_width(view, width)
    return delegate


def set_card_width(view: QListWidget, width: int) -> None:
    size = card_size(width)
    view.setGridSize(size)
    view.setIconSize(QSize(width - 2 * PAD, (width - 2 * PAD) * 9 // 16))


class CardDelegate(QStyledItemDelegate):
    PICK = 18

    def sizeHint(self, option, index):
        view = self.parent()
        if isinstance(view, QListView) and view.gridSize().isValid():
            return view.gridSize()
        return card_size(236)

    # -- geometrie ------------------------------------------------------------
    @staticmethod
    def _card(rect: QRect) -> QRect:
        return rect.adjusted(GAP // 2, GAP // 2, -(GAP - GAP // 2), -(GAP - GAP // 2))

    @staticmethod
    def _image(card: QRect) -> QRect:
        width = card.width() - 2 * PAD
        return QRect(card.left() + PAD, card.top() + PAD, width, width * 9 // 16)

    def _pick(self, rect: QRect) -> QRect:
        image = self._image(self._card(rect))
        return QRect(image.left() + 6, image.top() + 6, self.PICK, self.PICK)

    # -- dessin ---------------------------------------------------------------
    def paint(self, painter: QPainter, option, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        hover = bool(option.state & QStyle.State_MouseOver)
        checkable = bool(index.flags() & Qt.ItemIsUserCheckable)
        checked = checkable and index.data(Qt.CheckStateRole) is not None and \
            Qt.CheckState(index.data(Qt.CheckStateRole)) == Qt.Checked
        selected = bool(option.state & QStyle.State_Selected)

        card = self._card(option.rect)
        border = CARD_BORDER_PICKED if (checked or selected) else (
            CARD_BORDER_HOVER if hover else CARD_BORDER)
        painter.setPen(QPen(border, 2 if (checked or selected) else 1))
        painter.setBrush(CARD_BG_HOVER if hover else CARD_BG)
        painter.drawRoundedRect(QRectF(card).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)

        image = self._image(card)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(image), 6, 6)
        painter.save()
        painter.setClipPath(clip)
        painter.fillRect(image, IMAGE_BG)
        icon = index.data(Qt.DecorationRole)
        if icon is not None and hasattr(icon, "pixmap"):
            pix = icon.pixmap(image.size() * 2)
            if not pix.isNull():
                scaled = pix.scaled(image.size() * painter.device().devicePixelRatioF(),
                                    Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                scaled.setDevicePixelRatio(painter.device().devicePixelRatioF())
                w = scaled.width() / scaled.devicePixelRatio()
                h = scaled.height() / scaled.devicePixelRatio()
                painter.drawPixmap(QPoint(int(image.center().x() - w / 2 + 1),
                                          int(image.center().y() - h / 2 + 1)), scaled)
        painter.restore()

        chip = index.data(ROLE_CHIP)
        if chip:
            font = QFont(option.font)
            font.setPixelSize(13)
            font.setBold(True)
            painter.setFont(font)
            fm = QFontMetrics(font)
            box = QRect(0, 0, fm.horizontalAdvance(str(chip)) + 16, fm.height() + 4)
            box.moveTopRight(QPoint(image.right() - 8, image.top() + 8))
            painter.setPen(Qt.NoPen)
            painter.setBrush(CHIP_BG)
            painter.drawRoundedRect(QRectF(box), 5, 5)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(box, Qt.AlignCenter, str(chip))

        if checkable and (hover or checked):
            box = self._pick(option.rect)
            painter.setPen(QPen(PICK_ON if checked else PICK_OFF, 1.5))
            painter.setBrush(PICK_ON if checked else QColor(8, 10, 13, 190))
            painter.drawRoundedRect(QRectF(box), 4, 4)
            if checked:
                pen = QPen(QColor("#ffffff"), 2.2)
                pen.setCapStyle(Qt.RoundCap)
                pen.setJoinStyle(Qt.RoundJoin)
                painter.setPen(pen)
                x, y, w, h = box.x(), box.y(), box.width(), box.height()
                painter.drawPolyline([QPoint(x + int(w * 0.24), y + int(h * 0.52)),
                                      QPoint(x + int(w * 0.43), y + int(h * 0.72)),
                                      QPoint(x + int(w * 0.78), y + int(h * 0.30))])

        # Dessous : « 1080p · nom », puis la ligne discrete.
        text_top = image.bottom() + 5
        line = QRect(card.left() + PAD + 2, text_top, card.width() - 2 * PAD - 4, 18)
        font = QFont(option.font)
        font.setPixelSize(12)
        painter.setFont(font)
        fm = QFontMetrics(font)
        head = human_resolution(int(index.data(ROLE_RES) or 0))
        x = line.left()
        if head:
            head += " ·"
            painter.setPen(META)
            painter.drawText(QRect(x, line.top(), line.width(), line.height()),
                             Qt.AlignLeft | Qt.AlignVCenter, head)
            x += fm.horizontalAdvance(head) + 5
        title = str(index.data(Qt.DisplayRole) or "").split("\n")[0]
        painter.setPen(TITLE)
        painter.drawText(QRect(x, line.top(), line.right() - x, line.height()),
                         Qt.AlignLeft | Qt.AlignVCenter,
                         fm.elidedText(title, Qt.ElideMiddle, line.right() - x))
        sub = index.data(ROLE_SUB)
        if sub:
            font.setPixelSize(11)
            painter.setFont(font)
            fm = QFontMetrics(font)
            painter.setPen(SUB)
            below = line.translated(0, 19)
            painter.drawText(below, Qt.AlignLeft | Qt.AlignVCenter,
                             fm.elidedText(str(sub), Qt.ElideRight, below.width()))
        painter.restore()

    # -- la case : un clic dessus coche, le reste de la vignette garde ses gestes
    def editorEvent(self, event, model, option, index):
        if (event.type() in (QEvent.MouseButtonRelease, QEvent.MouseButtonDblClick)
                and event.button() == Qt.LeftButton
                and index.flags() & Qt.ItemIsUserCheckable
                and self._pick(option.rect).adjusted(-4, -4, 4, 4).contains(
                    event.position().toPoint())):
            if event.type() == QEvent.MouseButtonRelease:
                state = index.data(Qt.CheckStateRole)
                state = Qt.CheckState(state) if state is not None else Qt.Unchecked
                model.setData(index, Qt.Unchecked if state == Qt.Checked else Qt.Checked,
                              Qt.CheckStateRole)
            return True
        return super().editorEvent(event, model, option, index)
