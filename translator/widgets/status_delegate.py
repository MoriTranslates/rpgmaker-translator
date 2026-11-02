"""Status column painter: a colored stripe on the row's left edge plus a
shape-coded marker, so rows stay neutral and status never relies on color.

Works for any view whose status cell carries the entry status string in
``STATUS_ROLE`` (the main table's model and the Event Viewer's items).
"""

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate

from . import theme

STATUS_ROLE = Qt.ItemDataRole.UserRole + 41
STRIPE_WIDTH = 3


class StatusDelegate(QStyledItemDelegate):

    def paint(self, painter: QPainter, option, index):
        status = index.data(STATUS_ROLE)
        if not status:
            super().paint(painter, option, index)
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(rect, theme.qcolor("selection"))
        else:
            bg = index.data(Qt.ItemDataRole.BackgroundRole)
            if bg is not None:
                painter.fillRect(rect, bg)

        color = theme.status_fg(status)
        painter.fillRect(rect.x(), rect.y(), STRIPE_WIDTH, rect.height(), color)

        # Marker centered in the remaining space
        size = 11.0
        cx = rect.x() + STRIPE_WIDTH + (rect.width() - STRIPE_WIDTH) / 2
        cy = rect.y() + rect.height() / 2
        box = QRectF(cx - size / 2, cy - size / 2, size, size)
        pen = QPen(color, 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if status == "untranslated":      # hollow ring
            painter.drawEllipse(box.adjusted(0.8, 0.8, -0.8, -0.8))
        elif status == "translated":      # filled dot
            painter.setBrush(color)
            painter.drawEllipse(box.adjusted(1.5, 1.5, -1.5, -1.5))
        elif status == "reviewed":        # check mark
            path = QPainterPath(QPointF(box.left() + 1, cy + 0.5))
            path.lineTo(QPointF(cx - 1.2, box.bottom() - 1.5))
            path.lineTo(QPointF(box.right() - 0.5, box.top() + 1.5))
            pen.setWidthF(2.0)
            painter.setPen(pen)
            painter.drawPath(path)
        else:                             # skipped: dash
            painter.drawLine(QPointF(box.left() + 1.5, cy),
                             QPointF(box.right() - 1.5, cy))
        painter.restore()
