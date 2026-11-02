"""Paints a small colored speaker chip before a dialogue line.

Rows stay one line tall: the chip sits at the start of the cell and the text
is elided after it.  Each speaker keeps the same soft color everywhere
(hashed from the name), so a scene reads at a glance.
"""

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from . import theme

SPEAKER_ROLE = Qt.ItemDataRole.UserRole + 42
_MAX_CHIP = 130  # px; longer names are elided inside the chip
_CHIP_PT = 9.0   # chip text size (never below the readable minimum)


class SpeakerDelegate(QStyledItemDelegate):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._chip_font: QFont | None = None
        self._chip_base = None   # the cell font the chip font was built from

    def _font_for_chip(self, base: QFont) -> QFont:
        """One chip font, rebuilt only if the cell font changes."""
        if self._chip_font is None or self._chip_base != base:
            font = QFont(base)
            font.setPointSizeF(_CHIP_PT)
            font.setWeight(QFont.Weight.DemiBold)
            self._chip_font = font
            self._chip_base = QFont(base)
        return self._chip_font

    @staticmethod
    def _text_color(index) -> QColor:
        """The cell's ForegroundRole as a QColor (it may be a brush or unset)."""
        fg = index.data(Qt.ItemDataRole.ForegroundRole)
        if fg is None:
            return theme.qcolor("text")
        if isinstance(fg, QBrush):
            return fg.color()
        return QColor(fg)

    def paint(self, painter: QPainter, option, index):
        speaker = index.data(SPEAKER_ROLE)
        if not speaker:
            super().paint(painter, option, index)
            return

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        widget = opt.widget
        style = widget.style() if widget else None
        text = opt.text  # the model already folds it onto one line

        # Background / selection / hairline for the whole cell, no text
        opt.text = ""
        if style:
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem,
                              opt, painter, widget)

        chip_font = self._font_for_chip(opt.font)
        fm = QFontMetrics(chip_font)
        label = fm.elidedText(speaker, Qt.TextElideMode.ElideRight,
                              _MAX_CHIP - 12)
        chip_w = fm.horizontalAdvance(label) + 12
        chip_h = fm.height() + 2
        rect = opt.rect
        x = rect.x() + 8
        y = rect.y() + (rect.height() - chip_h) / 2
        bg, fg = theme.speaker_chip(speaker)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(bg))
        chip = QRectF(x, y, chip_w, chip_h)
        painter.drawRoundedRect(chip, chip_h / 2, chip_h / 2)
        painter.setFont(chip_font)
        painter.setPen(QColor(fg))
        painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, label)

        # The line itself, elided after the chip
        text_rect = rect.adjusted(int(8 + chip_w + 8), 0, -8, 0)
        painter.setFont(opt.font)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        painter.setPen(theme.qcolor("selection_text") if selected
                       else self._text_color(index))
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            QFontMetrics(opt.font).elidedText(
                text, Qt.TextElideMode.ElideRight, text_rect.width()))
        painter.restore()
