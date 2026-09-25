# -*- coding: utf-8 -*-
"""喵喵转码器 Qt 6 desktop interface.

The audio/decryption backend remains in the established modules.  This file is
an independent, model-driven GUI implementation; it intentionally does not
reuse the former Tk/CustomTkinter widget tree.
"""
from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor, as_completed

from PyQt6.QtCore import (
    QEasingCurve,
    QAbstractTableModel,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDropEvent,
    QFont,
    QIcon,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStackedWidget,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import converter
import main as core


APP_NAME = "喵喵转码器"
# One source of truth for the desktop visual system.  Business modules do not
# depend on these tokens, which keeps this refactor strictly in the UI layer.
SPACING = {"xs": 4, "sm": 6, "md": 8, "lg": 12, "xl": 16, "2xl": 20, "3xl": 24, "4xl": 32}
RADIUS = {"small": 5, "medium": 8, "large": 12}
MOTION = {"fast": 120, "normal": 160, "slow": 220}
COLORS = {
    "canvas": "#F6F8F5",
    "surface": "#FFFFFF",
    "surface_alt": "#FBFCFB",
    "field": "#F3F7F2",
    "field_hover": "#EEF4EE",
    "border": "#D8E2DA",
    "border_subtle": "#E4EAE5",
    "border_strong": "#BACDBE",
    "text": "#17231C",
    "muted": "#53645A",
    "muted_2": "#8A988F",
    "very_muted": "#ADB7B0",
    "accent": "#4E9668",
    "accent_hover": "#43875C",
    "accent_pressed": "#39774F",
    "accent_soft": "#EAF4EC",
    "accent_soft_hover": "#E2F0E5",
    "success": "#419861",
    "success_soft": "#EAF5ED",
    "warning": "#C68A38",
    "warning_soft": "#FBF4E8",
    "danger": "#D65C5C",
    "danger_soft": "#FCEEEE",
    "info": "#5183B8",
    "info_soft": "#EDF4FB",
    "nav": "#253128",
    "nav_hover": "#344238",
    "white": "#FFFFFF",
    "black": "#000000",
    "disabled": "#F4F6F4",
    "toggle_off": "#CCD5CE",
    "progress_track": "#E7EEE8",
    "row_hover": "#F7FAF7",
    "waiting_soft": "#F1F5F1",
    "waiting_text": "#627168",
    "finished_text": "#3C8855",
    "error_text": "#C94D4D",
    "header_text": "#6F7E74",
    "drop_border": "#B9D1BF",
    "menu_border": "#DCE5DE",
    "menu_hover": "#F1F6F2",
    "scrollbar": "#CBD6CE",
    "scrollbar_hover": "#AEBEB3",
    "drop_overlay": "rgba(241,247,242,242)",
}


def resource_path(relative: str) -> str:
    return core.resource_path(relative)


def _mix(a: QColor, b: QColor, amount: float) -> QColor:
    amount = max(0.0, min(1.0, amount))
    return QColor(
        round(a.red() + (b.red() - a.red()) * amount),
        round(a.green() + (b.green() - a.green()) * amount),
        round(a.blue() + (b.blue() - a.blue()) * amount),
        round(a.alpha() + (b.alpha() - a.alpha()) * amount),
    )


@lru_cache(maxsize=160)
def vector_icon(name: str, color: str = COLORS["text"], size: int = 24) -> QIcon:
    """Render the application's crisp, consistent line-icon family."""
    scale = 3
    canvas = size * scale
    pixmap = QPixmap(canvas, canvas)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(canvas / 24.0, canvas / 24.0)
    pen = QPen(QColor(color), 1.8, Qt.PenStyle.SolidLine,
               Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    def line(x1, y1, x2, y2):
        painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

    def rect(x, y, w, h, r=2):
        painter.drawRoundedRect(QRectF(x, y, w, h), r, r)

    def path(points, closed=False):
        p = QPainterPath(QPointF(*points[0]))
        for point in points[1:]:
            p.lineTo(QPointF(*point))
        if closed:
            p.closeSubpath()
        painter.drawPath(p)

    if name == "file_add":
        path([(7, 3), (14, 3), (18, 7), (18, 21), (7, 21)], True)
        path([(14, 3), (14, 7), (18, 7)])
        line(12.5, 11, 12.5, 17); line(9.5, 14, 15.5, 14)
    elif name == "folder_add":
        path([(3, 7), (9, 7), (11, 9), (21, 9), (20, 19), (4, 19)], True)
        path([(3, 7), (4, 5), (9, 5), (11, 7)])
        line(12, 11.5, 12, 16.5); line(9.5, 14, 14.5, 14)
    elif name == "duplicate":
        rect(7, 6, 12, 13, 3); rect(4, 3, 12, 13, 3)
        line(10, 9, 10, 14); line(7.5, 11.5, 12.5, 11.5)
    elif name == "remove":
        rect(4, 7, 16, 11, 3); line(8, 11, 16, 11); line(8, 14, 13, 14)
        line(8, 4, 16, 4)
    elif name == "trash":
        path([(6, 7), (7, 21), (17, 21), (18, 7)])
        line(4, 7, 20, 7); line(9, 4, 15, 4); line(10, 10, 10, 17); line(14, 10, 14, 17)
    elif name == "play":
        path([(8, 5), (19, 12), (8, 19)], True)
    elif name == "pause":
        painter.setBrush(QColor(color)); painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(7, 5, 3.5, 14), 1.5, 1.5)
        painter.drawRoundedRect(QRectF(13.5, 5, 3.5, 14), 1.5, 1.5)
    elif name == "reset":
        # A single counter-clockwise return arrow: visually quieter than the
        # previous two-headed sync glyph and closer to the familiar reset cue.
        reset_pen = QPen(QColor(color), 2.7, Qt.PenStyle.SolidLine,
                         Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(reset_pen)
        sweep = QPainterPath(QPointF(8.2, 6.4))
        sweep.cubicTo(QPointF(12.0, 4.2), QPointF(17.0, 5.2), QPointF(19.1, 9.2))
        sweep.cubicTo(QPointF(21.3, 13.8), QPointF(18.1, 18.9), QPointF(13.2, 19.7))
        sweep.cubicTo(QPointF(9.9, 20.2), QPointF(7.1, 18.7), QPointF(5.8, 16.2))
        painter.drawPath(sweep)
        arrow = QPainterPath(QPointF(4.5, 9.2))
        arrow.lineTo(QPointF(10.2, 8.5))
        arrow.lineTo(QPointF(7.4, 3.9))
        arrow.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawPath(arrow)
    elif name == "browse":
        painter.drawEllipse(QPointF(10.5, 10.5), 6.5, 6.5); line(15.3, 15.3, 20, 20)
    elif name == "folder_open":
        path([(3, 8), (9, 8), (11, 10), (21, 10), (18, 19), (5, 19)], True)
        path([(3, 8), (4, 5), (9, 5), (11, 8)])
    elif name == "settings":
        painter.drawEllipse(QPointF(12, 12), 3.2, 3.2)
        painter.drawEllipse(QPointF(12, 12), 7.3, 7.3)
        for angle in range(0, 360, 45):
            rad = math.radians(angle); line(12 + 7.8 * math.cos(rad), 12 + 7.8 * math.sin(rad),
                                             12 + 10 * math.cos(rad), 12 + 10 * math.sin(rad))
    elif name == "history":
        painter.drawEllipse(QPointF(12, 12), 8, 8); line(12, 7.5, 12, 12); line(12, 12, 16, 14)
    elif name == "warning":
        path([(12, 3), (21, 20), (3, 20)], True); line(12, 9, 12, 14)
        painter.setBrush(QColor(color)); painter.drawEllipse(QPointF(12, 17), .8, .8)
    elif name == "info":
        painter.drawEllipse(QPointF(12, 12), 9, 9); line(12, 10.5, 12, 17)
        painter.setBrush(QColor(color)); painter.drawEllipse(QPointF(12, 7), .9, .9)
    elif name == "check":
        path([(5, 12), (10, 17), (19, 7)])
    elif name == "music":
        path([(10, 6), (19, 4), (19, 15)]); line(10, 6, 10, 17)
        painter.drawEllipse(QPointF(7, 18), 3, 2); painter.drawEllipse(QPointF(16, 16), 3, 2)
    elif name == "chevron":
        path([(7, 9), (12, 14), (17, 9)])
    else:
        painter.drawEllipse(QPointF(12, 12), 7, 7)
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return QIcon(pixmap)


class MotionButton(QAbstractButton):
    """Vector button with lightweight colour and press feedback animation."""

    def __init__(self, text="", icon_name=None, *, kind="secondary", parent=None):
        super().__init__(parent)
        self.setText(text)
        self.icon_name = icon_name
        self.icon_size = 20
        self.kind = kind
        self._hover = 0.0
        self._hover_animation = QPropertyAnimation(self, b"hoverAmount", self)
        self._hover_animation.setDuration(MOTION["fast"])
        self._hover_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(38)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.toggled.connect(lambda _checked: self.update())

    @pyqtProperty(float)
    def hoverAmount(self):
        return self._hover

    @hoverAmount.setter
    def hoverAmount(self, value):
        self._hover = value
        self.update()

    def enterEvent(self, event):
        self._animate_hover(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._animate_hover(0.0)
        super().leaveEvent(event)

    def _animate_hover(self, target):
        self._hover_animation.stop()
        self._hover_animation.setStartValue(self._hover)
        self._hover_animation.setEndValue(target)
        self._hover_animation.start()

    def sizeHint(self):
        width = 30 + self.fontMetrics().horizontalAdvance(self.text())
        if self.icon_name:
            width += 27
        return QSize(max(42, width), 40)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(.75, .75, -.75, -.75)
        enabled = self.isEnabled()
        checked = self.isChecked()
        if self.kind == "primary":
            base, hover, border, text = (QColor(COLORS["accent"]),
                                         QColor(COLORS["accent_hover"]),
                                         QColor(Qt.GlobalColor.transparent), QColor(COLORS["white"]))
        elif self.kind == "danger":
            base, hover, border, text = (QColor(COLORS["surface"]), QColor(COLORS["danger_soft"]),
                                         QColor(COLORS["border"]), QColor(COLORS["danger"]))
        elif self.kind == "accent_outline":
            base, hover, border, text = (QColor(COLORS["surface"]), QColor(COLORS["accent_soft"]),
                                         QColor(COLORS["accent"]), QColor(COLORS["accent_pressed"]))
        elif self.kind == "dark":
            base, hover, border, text = (QColor(COLORS["accent_soft"]), QColor(COLORS["accent_soft_hover"]),
                                         QColor(Qt.GlobalColor.transparent), QColor(COLORS["accent_pressed"]))
        elif self.kind == "nav":
            base = QColor(COLORS["accent"] if checked else COLORS["nav"])
            hover = QColor(COLORS["accent"] if checked else COLORS["nav_hover"])
            border, text = QColor(Qt.GlobalColor.transparent), QColor(COLORS["white"])
        elif self.kind == "soft":
            base, hover, border, text = (QColor(COLORS["accent_soft"]), QColor(COLORS["accent_soft_hover"]),
                                         QColor(Qt.GlobalColor.transparent), QColor(COLORS["accent_pressed"]))
        else:
            base, hover, border, text = (QColor(COLORS["surface"]), QColor(COLORS["field_hover"]),
                                         QColor(COLORS["border"]), QColor(COLORS["text"]))
        if not enabled:
            base, hover, border, text = (QColor(COLORS["disabled"]), QColor(COLORS["disabled"]),
                                         QColor(COLORS["border_subtle"]), QColor(COLORS["very_muted"]))
        color = _mix(base, hover, self._hover)
        if self.isDown() and enabled:
            color = QColor(COLORS["accent_pressed"]) if self.kind == "primary" else _mix(color, QColor(COLORS["black"]), .06)
        painter.setBrush(color)
        if self.hasFocus() and enabled:
            border = QColor(COLORS["accent"])
        painter.setPen(QPen(border, 1.4 if self.hasFocus() else 1.0))
        radius = RADIUS["medium"]
        painter.drawRoundedRect(rect, radius, radius)

        icon_size = self.icon_size
        text_width = self.fontMetrics().horizontalAdvance(self.text())
        gap = 8 if self.icon_name and self.text() else 0
        total_width = text_width + (icon_size if self.icon_name else 0) + gap
        x = (self.width() - total_width) / 2
        y_shift = 1 if self.isDown() else 0
        if self.icon_name:
            icon = vector_icon(self.icon_name, text.name(), icon_size)
            icon.paint(painter, round(x), round((self.height() - icon_size) / 2 + y_shift),
                       icon_size, icon_size)
            x += icon_size + gap
        painter.setPen(text)
        font = self.font(); font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.drawText(QRectF(x, y_shift, text_width + 2, self.height()),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         self.text())


class ToggleSwitch(QAbstractButton):
    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setText(text)
        self._offset = 0.0
        self._animation = QPropertyAnimation(self, b"offset", self)
        self._animation.setDuration(MOTION["normal"])
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._toggle)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(34)

    @pyqtProperty(float)
    def offset(self):
        return self._offset

    @offset.setter
    def offset(self, value):
        self._offset = value; self.update()

    def _toggle(self, checked):
        self._animation.stop()
        self._animation.setStartValue(self._offset)
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        track = QRectF(0, (self.height() - 22) / 2, 40, 22)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(COLORS["accent"] if self.isChecked() else COLORS["toggle_off"]))
        painter.drawRoundedRect(track, 11, 11)
        x = 3 + self._offset * 18
        painter.setBrush(QColor(COLORS["white"]))
        painter.drawEllipse(QRectF(x, track.y() + 3, 16, 16))
        painter.setPen(QColor(COLORS["text"] if self.isEnabled() else COLORS["muted_2"]))
        painter.setFont(self.font())
        painter.drawText(QRectF(50, 0, self.width() - 50, self.height()),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         self.text())

    def sizeHint(self):
        return QSize(50 + self.fontMetrics().horizontalAdvance(self.text()), 34)


class SmoothProgress(QWidget):
    def __init__(self, parent=None, height=7):
        super().__init__(parent)
        self._value = 0.0
        self._animation = QPropertyAnimation(self, b"displayValue", self)
        self._animation.setDuration(MOTION["normal"])
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setFixedHeight(height)

    @pyqtProperty(float)
    def displayValue(self):
        return self._value

    @displayValue.setter
    def displayValue(self, value):
        self._value = value; self.update()

    def setValue(self, value):
        value = max(0.0, min(100.0, float(value)))
        self._animation.stop()
        self._animation.setStartValue(self._value)
        self._animation.setEndValue(value)
        self._animation.start()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(COLORS["progress_track"]))
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        if self._value > 0:
            fill = QRectF(rect.x(), rect.y(), max(rect.height(), rect.width() * self._value / 100), rect.height())
            painter.setBrush(QColor(COLORS["accent"]))
            painter.drawRoundedRect(fill, rect.height() / 2, rect.height() / 2)


class FadeStack(QStackedWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._animation = None

    def setCurrentIndexAnimated(self, index):
        if index == self.currentIndex():
            return
        self.setCurrentIndex(index)
        page = self.currentWidget()
        effect = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(MOTION["normal"])
        animation.setStartValue(0.15)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.finished.connect(lambda: page.setGraphicsEffect(None))
        self._animation = animation
        animation.start()


class AnimatedComboBox(QComboBox):
    """Styled combo with a custom vector chevron instead of bitmap arrows."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._arrow_rotation = 0.0
        self._arrow_animation = QPropertyAnimation(self, b"arrowRotation", self)
        self._arrow_animation.setDuration(MOTION["normal"])
        self._arrow_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._popup_animation = None
        self.setMaxVisibleItems(8)
        self.view().setFrameShape(QFrame.Shape.NoFrame)
        self.view().setStyleSheet(
            f"QAbstractItemView {{ background:{COLORS['surface']};"
            f" border:1px solid {COLORS['border']}; border-radius:{RADIUS['medium']}px;"
            " padding:6px; outline:none; }}"
            f"QAbstractItemView::item {{ min-height:34px; padding:2px 10px; border-radius:{RADIUS['small']}px; }}"
            f"QAbstractItemView::item:selected {{ background:{COLORS['accent_soft']};"
            f" color:{COLORS['accent_pressed']}; }}")

    @pyqtProperty(float)
    def arrowRotation(self):
        return self._arrow_rotation

    @arrowRotation.setter
    def arrowRotation(self, value):
        self._arrow_rotation = float(value)
        self.update()

    def _animate_arrow(self, target):
        self._arrow_animation.stop()
        self._arrow_animation.setStartValue(self._arrow_rotation)
        self._arrow_animation.setEndValue(target)
        self._arrow_animation.start()

    def showPopup(self):
        """Open below the field instead of covering the selected value."""
        self._animate_arrow(180.0)
        popup = self.view().window()
        popup.setObjectName("ComboPopup")
        popup.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        popup.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        popup.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)
        popup.setStyleSheet("QFrame#ComboPopup { background:transparent; border:none; }")
        super().showPopup()
        target = self.mapToGlobal(QPoint(0, self.height() + 5))
        popup.move(target)
        popup.resize(max(self.width(), popup.width()), popup.height())
        popup.setWindowOpacity(0.0)
        animation = QPropertyAnimation(popup, b"windowOpacity", self)
        animation.setDuration(MOTION["normal"])
        animation.setStartValue(0.0); animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._popup_animation = animation
        animation.start()

    def hidePopup(self):
        self._animate_arrow(0.0)
        super().hidePopup()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(COLORS["muted"]), 1.8, Qt.PenStyle.SolidLine,
                   Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        x = self.width() - 21; y = self.height() / 2 - 1
        painter.translate(x, y)
        painter.rotate(self._arrow_rotation)
        painter.drawLine(QPointF(-4, -2), QPointF(0, 2))
        painter.drawLine(QPointF(0, 2), QPointF(4, -2))


class EmptyState(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.primary_text = "拖放音频文件或文件夹"
        self.secondary_text = "支持 MP3、FLAC、WAV、AAC、MP4、M4A 等格式"
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        center = QPointF(self.width() / 2, self.height() / 2 - 18)
        halo = 31
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(COLORS["accent_soft"]))
        painter.drawEllipse(center, halo, halo)
        icon = vector_icon("music", COLORS["accent"], 26)
        icon.paint(painter, round(center.x() - 13), round(center.y() - 13), 26, 26)
        painter.setPen(QColor(COLORS["text"]))
        font = self.font(); font.setPointSize(12); font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.drawText(QRectF(0, center.y() + 45, self.width(), 26),
                         Qt.AlignmentFlag.AlignCenter, self.primary_text)
        painter.setPen(QColor(COLORS["muted"]))
        font.setPointSize(9); font.setWeight(QFont.Weight.Normal); painter.setFont(font)
        painter.drawText(QRectF(0, center.y() + 72, self.width(), 24),
                         Qt.AlignmentFlag.AlignCenter, self.secondary_text)


class QueueModel(QAbstractTableModel):
    HEADERS = ("文件名", "格式", "大小", "状态")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[dict] = []

    def rowCount(self, _parent=None):
        return len(self.items)

    def columnCount(self, _parent=None):
        return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.items):
            return None
        item = self.items[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            values = (item["name"], item["type"], core.human_size(item["size"]), item["status"])
            return values[index.column()]
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return (Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignLeft
                    if index.column() == 0 else Qt.AlignmentFlag.AlignCenter))
        if role == Qt.ItemDataRole.ForegroundRole:
            if index.column() == 3:
                status = item["status"]
                if status.startswith("完成"):
                    return QColor(COLORS["success"])
                if status.startswith("失败"):
                    return QColor(COLORS["danger"])
                if status.startswith("处理中"):
                    return QColor(COLORS["accent"])
            return QColor(COLORS["text"] if index.column() == 0 else COLORS["muted"])
        if role == Qt.ItemDataRole.UserRole:
            return item
        if role == Qt.ItemDataRole.ToolTipRole:
            return item.get("path", item.get("name", ""))
        return None

    def add_items(self, items: list[dict]):
        if not items:
            return
        first = len(self.items)
        self.beginInsertRows(self.index(-1, -1), first, first + len(items) - 1)
        self.items.extend(items)
        self.endInsertRows()

    def remove_rows(self, rows: list[int]):
        for row in sorted(set(rows), reverse=True):
            if 0 <= row < len(self.items):
                self.beginRemoveRows(self.index(-1, -1), row, row)
                self.items.pop(row)
                self.endRemoveRows()

    def remove_id(self, item_id: str):
        for row, item in enumerate(self.items):
            if item["id"] == item_id:
                self.remove_rows([row])
                return item
        return None

    def update_status(self, item_id: str, status: str):
        for row, item in enumerate(self.items):
            if item["id"] == item_id:
                item["status"] = status
                index = self.index(row, 3)
                self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole,
                                                       Qt.ItemDataRole.ForegroundRole])
                return

    def clear(self):
        if not self.items:
            return
        self.beginResetModel(); self.items.clear(); self.endResetModel()


class SimpleListModel(QAbstractTableModel):
    def __init__(self, header: str, parent=None, columns=1):
        super().__init__(parent)
        self.header = header
        self.columns = columns
        self.items: list[dict] = []

    def rowCount(self, _parent=None):
        return len(self.items)

    def columnCount(self, _parent=None):
        return self.columns

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.header
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.items):
            return None
        item = self.items[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            if self.columns == 1:
                return item["name"]
            values = (item["name"], item.get("type", ""),
                      core.human_size(item.get("size", 0)), item.get("status", "已完成"))
            return values[index.column()]
        if role == Qt.ItemDataRole.ForegroundRole:
            if self.columns > 1 and index.column() == 3:
                return QColor(COLORS["success"])
            if self.columns == 1:
                return QColor(COLORS["danger"])
            return QColor(COLORS["text"] if index.column() == 0 else COLORS["muted"])
        if role == Qt.ItemDataRole.UserRole:
            return item
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return (Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignLeft
                    if index.column() == 0 else Qt.AlignmentFlag.AlignCenter))
        if role == Qt.ItemDataRole.ToolTipRole:
            return item.get("detail") or item.get("path") or item.get("name", "")
        return None

    def append(self, item: dict):
        row = len(self.items)
        self.beginInsertRows(self.index(-1, -1), row, row)
        self.items.append(item)
        self.endInsertRows()

    def remove_rows(self, rows: list[int]):
        for row in sorted(set(rows), reverse=True):
            if 0 <= row < len(self.items):
                self.beginRemoveRows(self.index(-1, -1), row, row)
                self.items.pop(row)
                self.endRemoveRows()

    def clear(self):
        if self.items:
            self.beginResetModel(); self.items.clear(); self.endResetModel()


class QueueItemDelegate(QStyledItemDelegate):
    """Compact desktop rows with clear filename hierarchy and status tags."""

    def paint(self, painter, option, index):
        item = index.data(Qt.ItemDataRole.UserRole) or {}
        if index.column() not in (0, 3) or not item:
            super().paint(painter, option, index)
            return
        painter.save()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected:
            painter.fillRect(option.rect, QColor(COLORS["accent_soft"]))
        elif hovered:
            painter.fillRect(option.rect, QColor(COLORS["row_hover"]))

        if index.column() == 0:
            icon_rect = option.rect.adjusted(10, 0, 0, 0)
            vector_icon("music", COLORS["accent"], 18).paint(
                painter, icon_rect.x(), icon_rect.center().y() - 9, 18, 18)
            text_left = option.rect.x() + 38
            text_width = max(20, option.rect.right() - text_left - 8)
            title_font = QFont(option.font); title_font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(title_font); painter.setPen(QColor(COLORS["text"]))
            title = option.fontMetrics.elidedText(str(item.get("name", "")), Qt.TextElideMode.ElideRight, text_width)
            painter.drawText(QRectF(text_left, option.rect.y() + 8, text_width, 22),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, title)
            detail_font = QFont(option.font); detail_font.setPointSize(max(8, detail_font.pointSize() - 1))
            detail_font.setWeight(QFont.Weight.Normal); painter.setFont(detail_font)
            painter.setPen(QColor(COLORS["muted_2"]))
            source = item.get("source") or item.get("path", "")
            detail = f"{item.get('type', '')} · {os.path.dirname(source)}".strip(" ·")
            detail_metrics = painter.fontMetrics()
            detail = detail_metrics.elidedText(detail, Qt.TextElideMode.ElideMiddle, text_width)
            painter.drawText(QRectF(text_left, option.rect.y() + 31, text_width, 20),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, detail)
        else:
            status = str(item.get("status", ""))
            if status.startswith("完成") or status == "已完成":
                bg, fg = COLORS["success_soft"], COLORS["finished_text"]
            elif status.startswith("失败"):
                bg, fg = COLORS["danger_soft"], COLORS["error_text"]
            elif status.startswith("处理中"):
                bg, fg = COLORS["accent_soft"], COLORS["accent_pressed"]
            else:
                bg, fg = COLORS["waiting_soft"], COLORS["waiting_text"]
            metrics = option.fontMetrics
            width = min(option.rect.width() - 14, max(58, metrics.horizontalAdvance(status) + 20))
            badge = QRectF(option.rect.center().x() - width / 2, option.rect.center().y() - 13, width, 26)
            painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor(bg))
            painter.drawRoundedRect(badge, RADIUS["small"], RADIUS["small"])
            painter.setPen(QColor(fg)); painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, status)
        painter.restore()


class DropTable(QTableView):
    pathsDropped = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QTableView.DragDropMode.DropOnly)
        self.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.setShowGrid(False)
        self.setAlternatingRowColors(False)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(64)
        self.horizontalHeader().setHighlightSections(False)
        self.setSortingEnabled(False)
        self.empty_state = EmptyState(self.viewport())
        self.drop_overlay = QLabel("松开以添加到转换队列", self.viewport())
        self.drop_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_overlay.setStyleSheet(
            f"background: {COLORS['drop_overlay']}; color:{COLORS['accent_pressed']};"
            f"border:1px dashed {COLORS['accent']}; border-radius:{RADIUS['medium']}px;"
            "font-size:15px; font-weight:600;")
        self.drop_overlay.hide()
        self._overlay_effect = QGraphicsOpacityEffect(self.drop_overlay)
        self.drop_overlay.setGraphicsEffect(self._overlay_effect)
        self._overlay_animation = QPropertyAnimation(self._overlay_effect, b"opacity", self)
        self._overlay_animation.setDuration(MOTION["normal"])

    def setModel(self, model):
        old = self.model()
        if old is not None:
            try:
                old.rowsInserted.disconnect(self._sync_empty)
                old.rowsRemoved.disconnect(self._sync_empty)
                old.modelReset.disconnect(self._sync_empty)
            except Exception:
                pass
        super().setModel(model)
        model.rowsInserted.connect(self._sync_empty)
        model.rowsRemoved.connect(self._sync_empty)
        model.modelReset.connect(self._sync_empty)
        self._sync_empty()

    def _sync_empty(self, *_args):
        empty = self.model() is not None and self.model().rowCount() == 0
        self.empty_state.setVisible(empty)
        self.setProperty("empty", empty)
        self.style().unpolish(self); self.style().polish(self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        viewport = self.viewport().rect()
        state_width = min(440, max(260, viewport.width() - 40))
        state_height = min(280, max(210, viewport.height() - 40))
        self.empty_state.setGeometry(
            round((viewport.width() - state_width) / 2),
            round((viewport.height() - state_height) / 2),
            state_width, state_height)
        self.drop_overlay.setGeometry(self.viewport().rect().adjusted(16, 16, -16, -16))

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.drop_overlay.show(); self.drop_overlay.raise_()
            self._overlay_animation.stop(); self._overlay_animation.setStartValue(0.0)
            self._overlay_animation.setEndValue(1.0); self._overlay_animation.start()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragLeaveEvent(self, event: QDragLeaveEvent):
        self.drop_overlay.hide()
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent):
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        self.drop_overlay.hide()
        if paths:
            self.pathsDropped.emit(paths)
            event.acceptProposedAction()


class ImportBridge(QObject):
    discovered = pyqtSignal(int, list)
    finished = pyqtSignal(int, str, int)


class BatchController(QObject):
    fileStarted = pyqtSignal(str, str)
    fileProgress = pyqtSignal(str, float)
    fileCompleted = pyqtSignal(str, str, str, str)
    fileFailed = pyqtSignal(str, str, str)
    aggregate = pyqtSignal(float, float, int, int, int, str)
    finished = pyqtSignal(int, int, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.running = False
        self.cancel_event = threading.Event()
        self.pause_event = threading.Event()
        self._lock = threading.RLock()
        self._active: dict[str, dict] = {}
        self._done = 0
        self._errors = 0
        self._total = 0
        self._last_emit: dict[str, float] = {}

    def start(self, items: list[dict], params: dict):
        if self.running:
            return
        self.running = True
        self.cancel_event.clear(); self.pause_event.clear()
        self._done = 0; self._errors = 0; self._total = len(items)
        with self._lock:
            self._active.clear(); self._last_emit.clear()
        threading.Thread(target=self._run, args=(list(items), dict(params)),
                         name="qt-batch-coordinator", daemon=True).start()

    def toggle_pause(self) -> bool:
        paused = not self.pause_event.is_set()
        if paused:
            self.pause_event.set(); converter.pause_active_process(True)
        else:
            converter.pause_active_process(False); self.pause_event.clear()
        return paused

    def cancel(self):
        converter.pause_active_process(False)
        self.pause_event.clear()
        self.cancel_event.set()

    def _emit_aggregate(self):
        with self._lock:
            active = [dict(value) for value in self._active.values()]
            done, total = self._done, self._total
        count = len(active)
        average = sum(item["pct"] for item in active) / count if count else 0.0
        total_pct = min(100.0, (done + sum(item["pct"] for item in active) / 100.0)
                        * 100.0 / max(1, total))
        names = "、".join(item["name"] for item in active[:2])
        if count > 2:
            names += f" 等 {count} 项"
        self.aggregate.emit(average, total_pct, count, done, total, names)

    def _run(self, items: list[dict], params: dict):
        reserved = set()
        jobs = []
        os.makedirs(params["out_dir"], exist_ok=True)
        out_ext = converter.EXT[params["format"]]
        for item in items:
            base = os.path.splitext(item["name"])[0]
            output = core.unique_path(os.path.join(params["out_dir"], f"{base}.{out_ext}"), reserved)
            jobs.append((item, output))

        def run_one(item, output_path):
            if self.cancel_event.is_set():
                return
            item_id = item["id"]
            temp_dir = tempfile.mkdtemp(prefix="miaomiao_qt_task_")
            with self._lock:
                self._active[item_id] = {"name": item["name"], "pct": 0.0}
            self.fileStarted.emit(item_id, item["name"])
            self._emit_aggregate()

            def progress(value):
                while self.pause_event.is_set() and not self.cancel_event.is_set():
                    time.sleep(.06)
                now = time.monotonic()
                with self._lock:
                    task = self._active.get(item_id)
                    if task is not None:
                        task["pct"] = float(value)
                    last = self._last_emit.get(item_id, 0.0)
                    if now - last < .055 and float(value) < 100:
                        return
                    self._last_emit[item_id] = now
                self.fileProgress.emit(item_id, float(value))
                self._emit_aggregate()

            try:
                try:
                    backend_item = dict(item)
                    backend_item["item"] = item_id
                    ok, message = core.process_one(
                        backend_item, temp_dir, params["out_dir"], params["format"],
                        params["bitrate"], params["sample_rate"], params["channels"],
                        progress, self.cancel_event, output_path=output_path)
                except Exception as exc:
                    ok, message = False, f"{type(exc).__name__}: {exc}"
                with self._lock:
                    self._done += 1
                    self._active.pop(item_id, None)
                    self._last_emit.pop(item_id, None)
                    if not ok and not self.cancel_event.is_set():
                        self._errors += 1
                if ok:
                    if params.get("copy_lrc"):
                        try:
                            core.copy_matching_lrc(item["path"], message)
                        except OSError:
                            pass
                    self.fileCompleted.emit(item_id, message, item["name"], item["path"])
                elif not self.cancel_event.is_set():
                    self.fileFailed.emit(item_id, item["name"], str(message))
                self._emit_aggregate()
            finally:
                with self._lock:
                    self._active.pop(item_id, None)
                shutil.rmtree(temp_dir, ignore_errors=True)

        try:
            workers = max(1, min(int(params["parallel"]), len(jobs)))
            with ThreadPoolExecutor(max_workers=workers,
                                    thread_name_prefix="qt-audio-convert") as executor:
                futures = [executor.submit(run_one, item, output) for item, output in jobs]
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception:
                        with self._lock:
                            self._errors += 1
        finally:
            with self._lock:
                self._active.clear()
                success = self._done - self._errors
                errors = self._errors
            cancelled = self.cancel_event.is_set()
            self.running = False
            self.finished.emit(success, errors, cancelled)


class InfoDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("QQ 音乐加密格式说明")
        self.setModal(True)
        self.setFixedWidth(480)
        self.setStyleSheet(f"background:{COLORS['surface']};")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        layout.setSpacing(14)
        icon = QLabel()
        icon.setPixmap(vector_icon("info", COLORS["accent"], 28).pixmap(28, 28))
        title_row = QHBoxLayout(); title_row.addWidget(icon)
        title = QLabel("QQ 音乐加密格式转换")
        title.setStyleSheet(f"color:{COLORS['text']};font-size:18px;font-weight:700;")
        title_row.addWidget(title); title_row.addStretch()
        layout.addLayout(title_row)
        text = QLabel(
            "转换 MFLAC、MGG 等新版 QQ 音乐加密文件前，需要先运行本机 QQ 音乐客户端，"
            "并确保文件来自当前客户端、登录账号仍拥有该歌曲的播放权限。\n\n"
            "首次取得有效密钥后，同一首歌曲可以使用本机加密缓存离线转换。")
        text.setWordWrap(True)
        text.setStyleSheet(f"color:{COLORS['muted']};font-size:13px;line-height:1.6;")
        layout.addWidget(text)
        close = MotionButton("知道了", "check", kind="primary")
        close.clicked.connect(self.accept)
        layout.addWidget(close)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(resource_path(os.path.join("assets", "miaomiao_icon.ico"))))
        self.resize(1100, 700)
        # Logical pixels: Qt performs the physical high-DPI scaling.
        self.setMinimumSize(1100, 700)
        self.queue_model = QueueModel(self)
        self.completed_model = SimpleListModel("双击文件定位输出", self, columns=4)
        self.error_model = SimpleListModel("点击文件复制错误详情", self)
        self.queue_mode = "pending"
        self.batch = BatchController(self)
        self.import_bridge = ImportBridge(self)
        self.import_generation = 0
        self.scanning = 0
        self.reset_pending = False
        self.close_pending = False
        self._entry_animations = []
        self._toast_animations = []
        self._resize_idle_timer = QTimer(self)
        self._resize_idle_timer.setSingleShot(True)
        self._resize_idle_timer.setInterval(130)
        self._resize_idle_timer.timeout.connect(self._resume_idle_animations)
        self._build_ui()
        self._connect_signals()
        QTimer.singleShot(80, self._animate_entrance)

    # ------------------------------ UI construction ----------------------
    def _build_ui(self):
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        content = QWidget()
        content.setObjectName("Content")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(32, 24, 32, 24)
        content_layout.setSpacing(20)
        content_layout.addLayout(self._build_header())

        workspace = QWidget()
        workspace_layout = QHBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(16)
        self.queue_card = self._build_queue_card()
        self.inspector_card = self._build_inspector()
        workspace_layout.addWidget(self.queue_card, 62)
        workspace_layout.addWidget(self.inspector_card, 38)
        content_layout.addWidget(workspace, 1)
        shell.addWidget(content, 1)
        self._apply_style()

    def _build_header(self):
        header = QHBoxLayout()
        header.setSpacing(12)
        avatar = QLabel()
        avatar.setFixedSize(48, 48)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar_path = resource_path(os.path.join("assets", "miaomiao_avatar_rounded.png"))
        avatar.setPixmap(QPixmap(avatar_path).scaled(
            46, 46, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))
        header.addWidget(avatar)
        title = QLabel(APP_NAME); title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch()
        output_button = MotionButton("打开输出文件夹", "folder_open")
        output_button.icon_size = 18
        output_button.setMinimumHeight(38)
        output_button.clicked.connect(self.open_output)
        header.addWidget(output_button)
        header_divider_1 = QFrame(); header_divider_1.setObjectName("HeaderDivider")
        header_divider_1.setFixedSize(1, 22); header.addWidget(header_divider_1)
        self.queue_chip = QLabel("队列 0/0")
        self.queue_chip.setObjectName("NeutralChip")
        self.queue_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.queue_chip.setMinimumWidth(74)
        header_divider_2 = QFrame(); header_divider_2.setObjectName("HeaderDivider")
        header_divider_2.setFixedSize(1, 22)
        self.status_chip = QLabel("●  就绪")
        self.status_chip.setObjectName("StatusChip")
        self.status_chip.setProperty("tone", "ready")
        self.status_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_chip.setMinimumWidth(72)
        header.addWidget(self.queue_chip)
        header.addWidget(header_divider_2)
        header.addWidget(self.status_chip)
        return header

    def _card(self, name="Card"):
        card = QFrame()
        card.setObjectName(name)
        return card

    def _build_queue_card(self):
        card = self._card("Card")
        card.setMinimumWidth(600)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)
        header = QHBoxLayout(); header.setSpacing(8)
        title = QLabel("转换队列"); title.setObjectName("CardTitle")
        header.addWidget(title)
        header.addStretch()
        self.import_button = MotionButton("导入", "file_add", kind="accent_outline")
        self.import_button.clicked.connect(self.show_import_menu)
        header.addWidget(self.import_button)
        self.dedupe_button = MotionButton("去重", "duplicate")
        self.dedupe_button.clicked.connect(self.deduplicate)
        self.dedupe_button.setEnabled(False)
        self.remove_button = MotionButton("移除", "remove")
        self.remove_button.clicked.connect(self.remove_selected)
        self.clear_button = MotionButton("清空", "trash", kind="danger")
        self.clear_button.clicked.connect(self.clear_queue)
        header.addWidget(self.dedupe_button)
        header.addWidget(self.remove_button); header.addWidget(self.clear_button)
        layout.addLayout(header)

        columns = QFrame(); columns.setObjectName("QueueHeader")
        column_layout = QHBoxLayout(columns)
        column_layout.setContentsMargins(8, 5, 8, 5); column_layout.setSpacing(8)
        self.queue_switch = QFrame(); self.queue_switch.setObjectName("QueueTabs")
        switch_layout = QHBoxLayout(self.queue_switch)
        switch_layout.setContentsMargins(3, 3, 3, 3); switch_layout.setSpacing(3)
        self.pending_button = MotionButton("待转换", kind="dark")
        self.completed_button = MotionButton("已完成", kind="secondary")
        for button in (self.pending_button, self.completed_button):
            button.setCheckable(True); button.setMinimumHeight(34); button.setMinimumWidth(86)
            switch_layout.addWidget(button)
        self.pending_button.setChecked(True)
        self.pending_button.clicked.connect(lambda: self.show_queue_page("pending"))
        self.completed_button.clicked.connect(lambda: self.show_queue_page("completed"))
        column_layout.addWidget(self.queue_switch, 1, alignment=Qt.AlignmentFlag.AlignLeft)
        self.format_header = QLabel("格式"); self.size_header = QLabel("大小")
        self.status_header = QLabel("状态")
        for label, width in ((self.format_header, 86), (self.size_header, 92), (self.status_header, 126)):
            label.setObjectName("QueueColumnLabel")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setFixedWidth(width)
            column_layout.addWidget(label)
        layout.addWidget(columns)

        self.queue_view = DropTable()
        self.queue_view.setObjectName("QueueTable")
        self.queue_view.setItemDelegate(QueueItemDelegate(self.queue_view))
        self.queue_view.setModel(self.queue_model)
        self.queue_view.horizontalHeader().hide()
        self.queue_view.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.queue_view.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.queue_view.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.queue_view.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.queue_view.setColumnWidth(1, 86); self.queue_view.setColumnWidth(2, 92)
        self.queue_view.setColumnWidth(3, 126)
        layout.addWidget(self.queue_view, 1)

        footer = QHBoxLayout(); footer.setSpacing(8)
        formats = QLabel("MP3 · FLAC · WAV · AAC · MP4 · NCM · KGG · QMC · MFLAC · MGG")
        formats.setObjectName("MiniLabel")
        info = MotionButton("MFLAC/MGG 转换说明", "info")
        info.setToolTip("查看 QQ 音乐加密格式的转换要求")
        info.clicked.connect(lambda: InfoDialog(self).exec())
        footer.addWidget(info); footer.addWidget(formats); footer.addStretch()
        layout.addLayout(footer)
        return card

    def _build_inspector(self):
        card = self._card("Card")
        card.setMinimumWidth(360)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)
        title_row = QHBoxLayout()
        title = QLabel("任务面板"); title.setObjectName("CardTitle")
        title_row.addWidget(title); title_row.addStretch()
        layout.addLayout(title_row)

        segment = QFrame(); segment.setObjectName("Segment")
        segment_layout = QHBoxLayout(segment)
        segment_layout.setContentsMargins(4, 4, 4, 4); segment_layout.setSpacing(4)
        self.segment_group = QButtonGroup(self); self.segment_group.setExclusive(True)
        self.segment_buttons = []
        for index, label in enumerate(("输出参数", "错误日志")):
            button = MotionButton(label, kind="secondary")
            button.setCheckable(True); button.setMinimumHeight(34)
            button.clicked.connect(lambda _checked=False, i=index: self.set_inspector_page(i))
            self.segment_group.addButton(button, index)
            self.segment_buttons.append(button)
            segment_layout.addWidget(button, 1)
        self.segment_buttons[0].setChecked(True)
        self.segment_buttons[0].kind = "soft"
        layout.addWidget(segment)

        self.inspector_stack = FadeStack()
        self.settings_page = self._build_settings_page()
        self.error_view = self._build_result_view(self.error_model, "warning")
        self.inspector_stack.addWidget(self.settings_page)
        self.inspector_stack.addWidget(self.error_view)
        layout.addWidget(self.inspector_stack, 1)
        layout.addWidget(self._build_actions())
        return card

    def _field(self, title: str, values: list[str], current=0):
        box = QWidget()
        layout = QVBoxLayout(box); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(4)
        label = QLabel(title); label.setObjectName("FieldLabel")
        combo = AnimatedComboBox(); combo.addItems(values); combo.setCurrentIndex(current)
        combo.setMinimumHeight(40)
        layout.addWidget(label); layout.addWidget(combo)
        return box, combo

    def _build_settings_page(self):
        page = QScrollArea()
        page.setWidgetResizable(True); page.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget(); grid = QGridLayout(content)
        grid.setContentsMargins(2, 2, 6, 4); grid.setHorizontalSpacing(12); grid.setVerticalSpacing(8)
        fmt_box, self.format_combo = self._field("输出格式", [name.upper() for name in core.OUT_FMTS])
        parallel_box, self.parallel_combo = self._field("并行任务", ["2", "4", "8", "16", "24", "32"])
        self.parallel_combo.setToolTip("同时进行的转换任务数量")
        bitrate_box, self.bitrate_combo = self._field("码率", [item[0] for item in core.BITRATE_OPTS])
        sample_box, self.sample_combo = self._field("采样率", [item[0] for item in core.SR_OPTS])
        channel_box, self.channel_combo = self._field("声道", [item[0] for item in core.CH_OPTS])
        grid.addWidget(fmt_box, 0, 0); grid.addWidget(parallel_box, 0, 1)
        grid.addWidget(bitrate_box, 1, 0); grid.addWidget(sample_box, 1, 1)
        grid.addWidget(channel_box, 2, 0, 1, 2)

        output_box = QWidget(); output_layout = QVBoxLayout(output_box)
        output_layout.setContentsMargins(0, 0, 0, 0); output_layout.setSpacing(4)
        output_label = QLabel("输出位置"); output_label.setObjectName("FieldLabel")
        self.output_edit = QLineEdit(core.DEFAULT_OUTPUT_DIR)
        self.output_edit.setMinimumHeight(40)
        self.output_edit.setToolTip(core.DEFAULT_OUTPUT_DIR)
        self.output_edit.textChanged.connect(self.output_edit.setToolTip)
        output_layout.addWidget(output_label)
        output_actions = QHBoxLayout(); output_actions.setSpacing(8)
        browse = MotionButton("浏览...", "browse"); browse.clicked.connect(self.browse_output)
        browse.setMinimumWidth(92)
        output_actions.addWidget(self.output_edit, 1); output_actions.addWidget(browse)
        output_layout.addLayout(output_actions)
        grid.addWidget(output_box, 3, 0, 1, 2)
        self.lrc_switch = ToggleSwitch("自动匹配并复制 LRC 歌词")
        self.lrc_switch.setToolTip("自动查找与音频同名的 LRC 文件，并复制到输出目录。")
        grid.addWidget(self.lrc_switch, 4, 0, 1, 2)
        grid.setRowStretch(5, 1)
        page.setWidget(content)
        return page

    def _build_result_view(self, model, icon_name):
        page = QWidget(); layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 4, 2, 2); layout.setSpacing(10)
        hint = QLabel("双击文件可定位输出" if model is self.completed_model else "单击文件即可复制完整错误详情")
        hint.setObjectName("CardSubtitle")
        layout.addWidget(hint)
        view = QTableView(); view.setObjectName("ResultTable")
        view.setModel(model); view.setShowGrid(False); view.verticalHeader().hide()
        view.horizontalHeader().hide()
        view.setWordWrap(False)
        view.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        view.verticalHeader().setDefaultSectionSize(46)
        view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        view.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        view.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(view, 1)
        if model is self.completed_model:
            self.completed_table = view
        else:
            self.error_table = view
            tools = QHBoxLayout(); tools.setSpacing(8); tools.addStretch()
            copy_button = MotionButton("复制日志", "duplicate")
            clear_button = MotionButton("清空日志", "trash", kind="danger")
            copy_button.clicked.connect(self._copy_selected_error)
            clear_button.clicked.connect(self._clear_error_log)
            tools.addWidget(copy_button); tools.addWidget(clear_button)
            layout.addLayout(tools)
        return page

    def _build_actions(self):
        actions = QFrame(); actions.setObjectName("ActionDock")
        layout = QGridLayout(actions); layout.setContentsMargins(8, 8, 8, 8); layout.setSpacing(8)
        self.start_button = MotionButton("开始转换", "play", kind="primary")
        self.start_button.setMinimumHeight(48); self.start_button.clicked.connect(self.start_conversion)
        self.pause_button = MotionButton("暂停", "pause")
        self.pause_button.setEnabled(False); self.pause_button.clicked.connect(self.toggle_pause)
        self.reset_button = MotionButton("重置", "reset")
        self.reset_button.clicked.connect(self.reset_application)
        layout.addWidget(self.start_button, 0, 0, 1, 2)
        layout.addWidget(self.pause_button, 1, 0)
        layout.addWidget(self.reset_button, 1, 1)
        return actions

    def _apply_style(self):
        self.setStyleSheet(f"""
            * {{ font-family:'Segoe UI','Microsoft YaHei UI'; font-size:14px; color:{COLORS['text']}; }}
            QMainWindow, QWidget#Root, QWidget#Content {{ background:{COLORS['canvas']}; }}
            QLabel#PageTitle {{ font-size:23px; font-weight:600; color:{COLORS['text']}; }}
            QLabel#PageSubtitle, QLabel#CardSubtitle {{ color:{COLORS['muted']}; font-size:12px; }}
            QLabel#CardTitle {{ font-size:18px; font-weight:600; color:{COLORS['text']}; }}
            QLabel#FieldLabel {{ color:{COLORS['muted']}; font-size:13px; }}
            QLabel#MiniLabel {{ color:{COLORS['muted']}; font-size:11px; }}
            QLabel#QueueColumnLabel {{ color:{COLORS['header_text']}; font-size:12px; font-weight:500; }}
            QLabel#CurrentLabel {{ color:{COLORS['text']}; font-weight:600; }}
            QLabel#NeutralChip, QLabel#StatusChip, QLabel#SoftChip {{
                background:transparent; border:none; padding:5px 7px; font-size:12px; font-weight:600;
            }}
            QLabel#NeutralChip {{ color:{COLORS['muted']}; }}
            QLabel#StatusChip {{ color:{COLORS['success']}; }}
            QLabel#StatusChip[tone="working"] {{ color:{COLORS['accent']}; }}
            QLabel#StatusChip[tone="paused"] {{ color:{COLORS['warning']}; }}
            QLabel#StatusChip[tone="error"] {{ color:{COLORS['danger']}; }}
            QLabel#SoftChip {{ background:{COLORS['accent_soft']}; color:{COLORS['accent']}; }}
            QFrame#HeaderDivider {{ background:{COLORS['border']}; border:none; }}
            QFrame#Card {{ background:{COLORS['surface']}; border:1px solid {COLORS['border']}; border-radius:{RADIUS['large']}px; }}
            QFrame#Segment {{ background:{COLORS['surface_alt']}; border:1px solid {COLORS['border_subtle']}; border-radius:{RADIUS['medium']}px; }}
            QFrame#QueueTabs, QFrame#ActionDock {{ background:transparent; border:none; }}
            QFrame#QueueHeader {{ background:{COLORS['surface_alt']}; border:1px solid {COLORS['border']};
                border-radius:{RADIUS['medium']}px; }}
            QTableView {{ background:{COLORS['surface_alt']}; alternate-background-color:{COLORS['surface_alt']};
                border:1px solid {COLORS['border_subtle']}; border-radius:{RADIUS['medium']}px;
                selection-background-color:{COLORS['accent_soft']}; selection-color:{COLORS['text']}; outline:none; padding:3px; }}
            QTableView#QueueTable[empty="true"] {{ border:1px dashed {COLORS['drop_border']}; background:{COLORS['surface_alt']}; }}
            QHeaderView::section {{ background:{COLORS['surface_alt']}; color:{COLORS['muted']}; border:none;
                border-bottom:1px solid {COLORS['border_subtle']}; padding:9px 8px; font-size:12px; font-weight:500; }}
            QTableView::item {{ border:none; border-bottom:1px solid {COLORS['progress_track']}; padding:8px; }}
            QTableView::item:hover {{ background:{COLORS['row_hover']}; }}
            QTableView::item:selected {{ background:{COLORS['accent_soft']}; color:{COLORS['text']}; }}
            QComboBox, QLineEdit {{ background:{COLORS['field']}; border:1px solid {COLORS['border']};
                border-radius:{RADIUS['medium']}px; padding:7px 12px; color:{COLORS['text']}; }}
            QComboBox:hover, QLineEdit:hover {{ border-color:{COLORS['border_strong']}; }}
            QComboBox:focus, QLineEdit:focus {{ border:1px solid {COLORS['accent']}; }}
            QComboBox::drop-down {{ border:none; width:36px; }}
            QComboBox::down-arrow {{ image:none; }}
            QComboBox QAbstractItemView {{ background:{COLORS['surface']}; border:1px solid {COLORS['border']};
                border-radius:{RADIUS['medium']}px; selection-background-color:{COLORS['accent_soft']}; padding:5px; outline:none; }}
            QMenu {{ background:{COLORS['surface']}; border:1px solid {COLORS['menu_border']}; border-radius:{RADIUS['medium']}px;
                padding:6px; }}
            QMenu::item {{ min-height:34px; padding:2px 24px 2px 10px; border-radius:{RADIUS['small']}px; }}
            QMenu::item:selected {{ background:{COLORS['menu_hover']}; color:{COLORS['accent_pressed']}; }}
            QScrollArea {{ background:transparent; border:none; }}
            QScrollArea > QWidget > QWidget {{ background:transparent; }}
            QScrollBar:vertical {{ background:transparent; width:7px; margin:2px; }}
            QScrollBar::handle:vertical {{ background:{COLORS['scrollbar']}; border-radius:3px; min-height:30px; }}
            QScrollBar::handle:vertical:hover {{ background:{COLORS['scrollbar_hover']}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background:transparent; }}
            QMessageBox {{ background:{COLORS['surface']}; }}
            QMessageBox QLabel {{ color:{COLORS['text']}; min-width:260px; }}
            QMessageBox QPushButton {{ min-width:76px; min-height:34px; background:{COLORS['surface']};
                border:1px solid {COLORS['border']}; border-radius:{RADIUS['medium']}px; padding:3px 12px; }}
            QMessageBox QPushButton:hover {{ background:{COLORS['field_hover']}; border-color:{COLORS['border_strong']}; }}
            QToolTip {{ background:{COLORS['nav']}; color:white; border:none; border-radius:6px; padding:6px 8px; font-size:12px; }}
        """)

    # ------------------------------ signals and animation ----------------
    def _connect_signals(self):
        self.queue_view.pathsDropped.connect(self.add_paths)
        self.import_bridge.discovered.connect(self._receive_discovered)
        self.import_bridge.finished.connect(self._scan_finished)
        self.batch.fileStarted.connect(self._file_started)
        self.batch.fileProgress.connect(self._file_progress)
        self.batch.fileCompleted.connect(self._file_completed)
        self.batch.fileFailed.connect(self._file_failed)
        self.batch.aggregate.connect(self._aggregate_progress)
        self.batch.finished.connect(self._batch_finished)
        self.queue_view.doubleClicked.connect(self._queue_double_clicked)
        self.error_table.clicked.connect(self._copy_error)

    def _animate_entrance(self):
        for index, widget in enumerate((self.queue_card, self.inspector_card)):
            effect = QGraphicsOpacityEffect(widget)
            widget.setGraphicsEffect(effect)
            animation = QPropertyAnimation(effect, b"opacity", self)
            animation.setDuration(MOTION["slow"])
            animation.setStartValue(0.0); animation.setEndValue(1.0)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            animation.finished.connect(lambda w=widget: w.setGraphicsEffect(None))
            self._entry_animations.append(animation)
            QTimer.singleShot(index * 65, animation.start)

    def show_toast(self, message: str, kind="info"):
        toast = QLabel(message, self.centralWidget())
        tone = {"info": (COLORS["nav"], COLORS["white"]),
                "ok": (COLORS["success"], COLORS["white"]),
                "error": (COLORS["danger"], COLORS["white"])}.get(kind, (COLORS["nav"], COLORS["white"]))
        toast.setStyleSheet(f"background:{tone[0]};color:{tone[1]};padding:10px 18px;"
                            f"border-radius:{RADIUS['medium']}px;font-weight:600;")
        toast.adjustSize()
        x = self.centralWidget().width() - toast.width() - 28
        y = self.centralWidget().height() - toast.height() - 28
        toast.move(x, y + 10); toast.show(); toast.raise_()
        effect = QGraphicsOpacityEffect(toast); toast.setGraphicsEffect(effect)
        opacity = QPropertyAnimation(effect, b"opacity", toast)
        opacity.setDuration(MOTION["normal"]); opacity.setStartValue(0.0); opacity.setEndValue(1.0)
        position = QPropertyAnimation(toast, b"pos", toast)
        position.setDuration(MOTION["normal"]); position.setStartValue(QPoint(x, y + 10)); position.setEndValue(QPoint(x, y))
        position.setEasingCurve(QEasingCurve.Type.OutCubic)
        opacity.start(); position.start()

        def fade_out():
            fade = QPropertyAnimation(effect, b"opacity", toast)
            fade.setDuration(MOTION["slow"]); fade.setStartValue(1.0); fade.setEndValue(0.0)
            fade.finished.connect(toast.deleteLater)
            toast._fade = fade; fade.start()
        toast._animations = (opacity, position)
        QTimer.singleShot(2800, fade_out)
        self._toast_animations.append(toast)

    def _set_status(self, text: str, tone="ready"):
        self.status_chip.setText(f"●  {text}")
        self.status_chip.setProperty("tone", tone)
        self.status_chip.style().unpolish(self.status_chip)
        self.status_chip.style().polish(self.status_chip)

    # ------------------------------ inspector navigation -----------------
    def set_inspector_page(self, index: int):
        for number, button in enumerate(self.segment_buttons):
            button.setChecked(number == index)
            button.kind = "soft" if number == index else "secondary"
            button.update()
        self.inspector_stack.setCurrentIndexAnimated(index)

    def _update_result_tabs(self):
        labels = ("输出参数", f"错误日志 {len(self.error_model.items)}")
        for button, text in zip(self.segment_buttons, labels):
            button.setText(text); button.updateGeometry(); button.update()
        self.completed_button.setText(f"已完成 {len(self.completed_model.items)}")
        self.completed_button.updateGeometry(); self.completed_button.update()
        self.queue_chip.setText(
            f"队列 {len(self.queue_model.items)}/{len(self.completed_model.items)}")

    def show_queue_page(self, mode: str):
        mode = "completed" if mode == "completed" else "pending"
        self.queue_mode = mode
        pending = mode == "pending"
        self.pending_button.setChecked(pending)
        self.completed_button.setChecked(not pending)
        self.pending_button.kind = "dark" if pending else "secondary"
        self.completed_button.kind = "secondary" if pending else "dark"
        self.pending_button.update(); self.completed_button.update()
        self.queue_view.setModel(self.queue_model if pending else self.completed_model)
        self.queue_view.setAcceptDrops(pending)
        self.queue_view.horizontalHeader().hide()
        if pending:
            self.queue_view.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            for column in (1, 2, 3):
                self.queue_view.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.queue_view.setColumnWidth(1, 86); self.queue_view.setColumnWidth(2, 92)
            self.queue_view.setColumnWidth(3, 126)
            self.queue_view.empty_state.primary_text = "拖放音频文件或文件夹"
            self.queue_view.empty_state.secondary_text = ""
            self.remove_button.setText("移除"); self.clear_button.setText("清空")
        else:
            self.queue_view.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            self.queue_view.empty_state.primary_text = "暂无已完成文件"
            self.queue_view.empty_state.secondary_text = ""
            self.remove_button.setText("移除记录"); self.clear_button.setText("清空记录")
        self.queue_view.empty_state.update()
        self.dedupe_button.setEnabled(
            pending and len(self.queue_model.items) > 1 and not self.batch.running)

    def show_import_menu(self):
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        menu.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        menu.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)
        file_action = menu.addAction(vector_icon("file_add", COLORS["text"], 20), "导入文件")
        folder_action = menu.addAction(vector_icon("folder_add", COLORS["text"], 20), "导入文件夹")
        file_action.triggered.connect(self.choose_files)
        folder_action.triggered.connect(self.choose_folder)
        menu.exec(self.import_button.mapToGlobal(QPoint(0, self.import_button.height() + 6)))

    # ------------------------------ importing ----------------------------
    def _busy_guard(self) -> bool:
        if self.batch.running:
            self.show_toast("转换进行中，暂时不能修改队列")
            return True
        return False

    def choose_files(self):
        if self._busy_guard():
            return
        files, _ = QFileDialog.getOpenFileNames(self, "选择音频文件", "", "音频文件 (*.*)")
        if files:
            self.add_paths(files)

    def choose_folder(self):
        if self._busy_guard():
            return
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹（递归扫描）")
        if folder:
            self.add_paths([folder])

    def add_paths(self, paths: list[str]):
        if self._busy_guard():
            return
        direct = []
        for path in paths:
            if os.path.isdir(path):
                self._scan_folder(path)
            elif os.path.isfile(path):
                direct.append(path)
        if direct:
            self._add_files(direct)

    def _scan_folder(self, folder: str):
        generation = self.import_generation
        self.scanning += 1
        self._set_status("扫描中", "working")

        def scan():
            found = []
            supported = core.AUDIO_EXTS | {core.NCM_EXT} | core.KUGOU_EXTS | core.QMC_EXTS
            for root, _dirs, files in os.walk(folder):
                if generation != self.import_generation:
                    return
                for name in files:
                    if os.path.splitext(name)[1].lower() in supported:
                        found.append(os.path.join(root, name))
                        if len(found) >= 160:
                            self.import_bridge.discovered.emit(generation, found)
                            found = []
            if found:
                self.import_bridge.discovered.emit(generation, found)
            self.import_bridge.finished.emit(generation, folder, len(found))
        threading.Thread(target=scan, name="qt-folder-scan", daemon=True).start()

    def _receive_discovered(self, generation: int, paths: list[str]):
        if generation == self.import_generation:
            self._add_files(paths)

    def _scan_finished(self, generation: int, folder: str, _count: int):
        if generation != self.import_generation:
            return
        self.scanning = max(0, self.scanning - 1)
        self._set_status("就绪" if not self.scanning else "扫描中",
                         "ready" if not self.scanning else "working")
        self.show_toast(f"已完成扫描：{os.path.basename(folder) or folder}", "ok")

    def _add_files(self, paths: list[str]):
        supported = core.AUDIO_EXTS | {core.NCM_EXT} | core.KUGOU_EXTS | core.QMC_EXTS
        items = []
        for path in paths:
            try:
                path = os.path.abspath(path)
                ext = os.path.splitext(path)[1].lower()
                if ext not in supported:
                    continue
                name = os.path.basename(path)
                size = os.path.getsize(path)
            except OSError:
                continue
            if ext == core.NCM_EXT:
                type_label = "NCM"
            elif ext in core.KUGOU_EXTS:
                type_label = "KGM"
            elif ext in core.QMC_EXTS:
                type_label = "QMC"
            else:
                type_label = ext.lstrip(".").upper()
            items.append({
                "id": uuid.uuid4().hex,
                "item": "",
                "path": path,
                "path_key": os.path.normcase(os.path.realpath(path)),
                "name": name,
                "ext": ext,
                "type": type_label,
                "size": size,
                "status": "排队中",
                "normalized_name": core._normalized_file_stem(name),
            })
        self.queue_model.add_items(items)
        self._update_queue_count()
        if items:
            self.show_toast(f"已添加 {len(items)} 个文件", "ok")

    def _update_queue_count(self):
        count = len(self.queue_model.items)
        self.queue_chip.setText(f"队列 {count}/{len(self.completed_model.items)}")
        self.dedupe_button.setEnabled(
            self.queue_mode == "pending" and count > 1 and not self.batch.running)

    def remove_selected(self):
        if self._busy_guard():
            return
        rows = [index.row() for index in self.queue_view.selectionModel().selectedRows()]
        if not rows:
            self.show_toast("请先选择要移除的文件")
            return
        if self.queue_mode == "completed":
            self.completed_model.remove_rows(rows); self._update_result_tabs()
        else:
            self.queue_model.remove_rows(rows); self._update_queue_count()

    def clear_queue(self):
        if self._busy_guard():
            return
        if self.queue_mode == "completed":
            if self.completed_model.items and not self._confirm_clear("清空完成记录？", "这将移除当前所有已完成记录。"):
                return
            self.completed_model.clear(); self._update_result_tabs()
            return
        if self.queue_model.items and not self._confirm_clear("清空转换队列？", "这将移除当前所有待转换任务。"):
            return
        self.import_generation += 1; self.scanning = 0
        self.queue_model.clear(); self._update_queue_count()
        self._set_status("就绪", "ready")

    def _confirm_clear(self, title: str, text: str) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle(APP_NAME)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(title)
        box.setInformativeText(text)
        cancel = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        clear = box.addButton("清空", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(cancel)
        box.exec()
        return box.clickedButton() is clear

    def deduplicate(self):
        if self._busy_guard():
            return
        kept, removed_rows = [], []
        for row, item in enumerate(self.queue_model.items):
            reason = core._duplicate_reason(item["name"], item["size"], kept)
            if reason:
                removed_rows.append(row)
            else:
                kept.append(item)
        if removed_rows:
            self.queue_model.remove_rows(removed_rows); self._update_queue_count()
            self.show_toast(f"已移除 {len(removed_rows)} 个重复文件", "ok")
        else:
            self.show_toast("未发现重复文件")

    # ------------------------------ output and conversion ----------------
    def browse_output(self):
        folder = QFileDialog.getExistingDirectory(self, "选择输出位置", self.output_edit.text())
        if folder:
            self.output_edit.setText(folder)
            self.output_edit.setToolTip(folder)

    def open_output(self):
        path = self.output_edit.text().strip()
        if not path:
            self.show_toast("请先选择输出目录", "error"); return
        os.makedirs(path, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(path)

    @staticmethod
    def _option_value(options, label):
        return next((value for name, value in options if name == label), 0)

    def start_conversion(self):
        if self.batch.running:
            return
        if self.scanning:
            self.show_toast("文件夹仍在扫描，请稍后开始", "error"); return
        if not self.queue_model.items:
            self.show_toast("请先添加音频文件", "error"); return
        out_dir = self.output_edit.text().strip()
        if not out_dir:
            self.show_toast("请先选择输出目录", "error"); return
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as exc:
            self.show_toast(f"无法创建输出目录：{exc}", "error"); return
        params = {
            "out_dir": out_dir,
            "format": self.format_combo.currentText().lower(),
            "parallel": int(self.parallel_combo.currentText()),
            "bitrate": self._option_value(core.BITRATE_OPTS, self.bitrate_combo.currentText()),
            "sample_rate": self._option_value(core.SR_OPTS, self.sample_combo.currentText()),
            "channels": self._option_value(core.CH_OPTS, self.channel_combo.currentText()),
            "copy_lrc": self.lrc_switch.isChecked(),
        }
        self.start_button.setEnabled(False); self.pause_button.setEnabled(True)
        self.reset_button.setEnabled(True)
        self._set_status("转换中", "working")
        self.batch.start(self.queue_model.items, params)
        self.show_toast(f"已启动 {params['parallel']} 个并行任务", "ok")

    def toggle_pause(self):
        if not self.batch.running:
            return
        paused = self.batch.toggle_pause()
        self.pause_button.setText("继续" if paused else "暂停")
        self.pause_button.icon_name = "play" if paused else "pause"
        self.pause_button.update()
        self._set_status("已暂停" if paused else "转换中", "paused" if paused else "working")

    def _file_started(self, item_id, _name):
        self.queue_model.update_status(item_id, "处理中 0%")

    def _file_progress(self, item_id, value):
        self.queue_model.update_status(item_id, f"处理中 {round(value)}%")

    def _file_completed(self, item_id, output_path, source_name, source_path):
        self.queue_model.remove_id(item_id)
        try:
            size = os.path.getsize(output_path)
        except OSError:
            size = 0
        self.completed_model.append({"name": os.path.basename(output_path), "path": output_path,
                                     "source": source_path, "source_name": source_name,
                                     "type": os.path.splitext(output_path)[1].lstrip(".").upper(),
                                     "size": size, "status": "已完成"})
        self._update_queue_count(); self._update_result_tabs()

    def _file_failed(self, item_id, source_name, detail):
        self.queue_model.remove_id(item_id)
        stamp = time.strftime("%H:%M:%S")
        self.error_model.append({"name": f"{stamp}  ×  {source_name}",
                                 "detail": f"文件: {source_name}\n错误: {detail}"})
        self._update_queue_count(); self._update_result_tabs()

    def _aggregate_progress(self, current, total_pct, active, done, total, names):
        self.queue_chip.setText(f"队列 {done}/{total}")
        if not self.batch.pause_event.is_set():
            self._set_status(f"转换中 {done}/{total}", "working")
            self.start_button.setText(f"转换中 {done} / {total}")
        self.status_chip.setToolTip(
            f"已完成 {done} / {total} · 活动 {active}\n{names}" if names
            else f"已完成 {done} / {total} · 正在调度任务"
        )

    def _batch_finished(self, success, errors, cancelled):
        self.start_button.setEnabled(True); self.pause_button.setEnabled(False)
        self.pause_button.setText("暂停"); self.pause_button.icon_name = "pause"
        self._set_status("有任务失败" if errors and not cancelled else "就绪",
                         "error" if errors and not cancelled else "ready")
        self.status_chip.setToolTip("")
        self.start_button.setText("转换完成" if not cancelled and not errors else "开始转换")
        if not cancelled and not errors:
            QTimer.singleShot(1300, lambda: self.start_button.setText("开始转换"))
        if errors and not cancelled:
            self.set_inspector_page(1)
        elif not cancelled:
            self.show_queue_page("completed")
        self.show_toast("转换任务已结束", "ok" if not errors else "error")
        if self.reset_pending:
            self._clear_everything()
        if self.close_pending:
            self.close_pending = False
            QTimer.singleShot(0, self.close)

    def _queue_double_clicked(self, index):
        if self.queue_mode == "completed":
            self._open_completed(index)

    def _open_completed(self, index):
        if not index.isValid():
            return
        path = self.completed_model.items[index.row()].get("path")
        if path and os.path.exists(path) and sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])

    def _copy_error(self, index):
        if not index.isValid():
            return
        detail = self.error_model.items[index.row()].get("detail", "")
        QApplication.clipboard().setText(detail)
        self.show_toast("错误详情已复制到剪贴板", "ok")

    def _copy_selected_error(self):
        rows = self.error_table.selectionModel().selectedRows()
        if rows:
            self._copy_error(rows[0])
        else:
            self.show_toast("请先选择一条错误记录")

    def _clear_error_log(self):
        if not self.error_model.items:
            return
        if self._confirm_clear("清空错误日志？", "这将移除当前所有错误记录。"):
            self.error_model.clear()
            self._update_result_tabs()

    # ------------------------------ reset and close ----------------------
    def reset_application(self):
        if self.batch.running:
            self.reset_pending = True
            self.batch.cancel()
            self._set_status("正在重置", "paused")
            self.start_button.setEnabled(False); self.pause_button.setEnabled(False)
        else:
            self._clear_everything()

    def _clear_everything(self):
        self.reset_pending = False
        self.import_generation += 1; self.scanning = 0
        self.queue_model.clear(); self.completed_model.clear(); self.error_model.clear()
        self.format_combo.setCurrentIndex(0); self.parallel_combo.setCurrentIndex(0)
        self.bitrate_combo.setCurrentIndex(0); self.sample_combo.setCurrentIndex(0)
        self.channel_combo.setCurrentIndex(0); self.lrc_switch.setChecked(False)
        self.output_edit.setText(core.DEFAULT_OUTPUT_DIR)
        self.start_button.setEnabled(True); self.pause_button.setEnabled(False)
        self.pause_button.setText("暂停"); self.pause_button.icon_name = "pause"
        self._set_status("就绪", "ready")
        self.set_inspector_page(0); self.show_queue_page("pending")
        self._update_queue_count(); self._update_result_tabs()
        self.show_toast("已恢复到初始状态", "ok")

    def resizeEvent(self, event):
        """Resize only the layout; no heavy rendering or business work occurs here."""
        self._resize_idle_timer.start()
        super().resizeEvent(event)

    def _resume_idle_animations(self):
        pass

    def closeEvent(self, event):
        if self.batch.running:
            self.close_pending = True
            self.batch.cancel()
            self._set_status("正在退出", "paused")
            event.ignore()
            return
        event.accept()


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "Miaomiao.Transcoder.Desktop.Qt6.1")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("Miaomiao")
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
