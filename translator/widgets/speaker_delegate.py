"""Paints a small colored speaker chip before a dialogue line.

Rows stay one line tall: the chip sits at the start of the cell and the text
is elided after it.  Each speaker keeps the same soft color everywhere
(hashed from the name), so a scene reads at a glance.
"""

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from . import theme

SPEAKER_ROLE = Qt.ItemDataRole.UserRole + 42
_MAX_CHIP = 130  # px; longer names are elided inside the chip


class SpeakerDelegate(QStyledItemDelegate):

    def paint(self, painter: QPainter, option, index):
        speaker = index.data(SPEAKER_ROLE)
        if not speaker:
            super().paint(painter, option, index)
            return

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        widget = opt.widget
        style = widget.style() if widget else None
        text = opt.text

        # Background / selection / hairline for the whole cell, no text
        opt.text = ""
        if style:
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem,
                              opt, painter, widget)

        chip_font = QFont(opt.font)
        chip_font.setPointSizeF(max(7.5, opt.font.pointSizeF() - 1.5))
        chip_font.setWeight(QFont.Weight.DemiBold)
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
        color = (theme.qcolor("selection_text") if selected
                 else index.data(Qt.ItemDataRole.ForegroundRole)
                 or theme.qcolor("text"))
        painter.setPen(QColor(color))
        line = " ".join(text.split())
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            QFontMetrics(opt.font).elidedText(
                line, Qt.TextElideMode.ElideRight, text_rect.width()))
        painter.restore()
