"""Compact drawing tools and an accessible vector palette/settings popup."""
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal, QSignalBlocker
from PySide6.QtGui import QAction, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractButton, QDoubleSpinBox, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy, QSlider, QSpinBox,
    QScrollArea, QToolBar, QToolButton, QVBoxLayout, QWidget, QWidgetAction)

from ..core.localization import tr
from .icons import icon
from .theme import theme_color

PALETTE = (
    '#000000', '#505050', '#808080', '#a9abad', '#d0d2d3', '#ffffff',
    '#b51066', '#ed1515', '#ff5900', '#ffa800', '#ffd400', '#fff000',
    '#a5eb18', '#29dc00', '#00876b', '#00a7c6', '#1765cf', '#4300b8',
    '#6900d4', '#610080', '#f7d6c2', '#bb9166', '#925a30', '#603e31',
    '#f17cf5', '#ffca89', '#fffd80', '#8dffa5', '#7ad8f8', '#b7a0f7',
)


class ColorSwatch(QAbstractButton):
    def __init__(self, color, parent):
        super().__init__(parent)
        self.color = QColor(color)
        self.setCheckable(True)
        self.setFixedSize(30, 30)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip(tr('Colore {0}', color.upper()))
        self.setAccessibleName(self.toolTip())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(theme_color('muted')), .7))
        painter.setBrush(self.color)
        painter.drawEllipse(QRectF(4, 4, 22, 22))
        if self.isChecked() or self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(theme_color('accent')), 2))
            painter.drawEllipse(QRectF(1, 1, 28, 28))


class StrokePreview(QWidget):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setFixedHeight(64)
        self.setAccessibleName(tr('Anteprima del tratto'))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.owner.tool == 'eraser':
            radius = min(27, self.owner.width_control.value()/2)
            painter.setPen(QPen(QColor(theme_color('accent')), 1.5, Qt.PenStyle.DashLine))
            painter.drawEllipse(QPointF(self.width()/2, 32), radius, radius)
            return
        color = QColor(self.owner.color)
        color.setAlphaF(self.owner.opacity.value()/100)
        painter.setPen(QPen(color, self.owner.width_control.value(), Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        path = QPainterPath(QPointF(15, 36))
        path.cubicTo(self.width()*.4, -7, self.width()*.55, 79, self.width()-15, 25)
        painter.drawPath(path)


class DrawingToolbar(QToolBar):
    tool_changed = Signal(str)
    style_changed = Signal()
    custom_color_requested = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName('InkToolbar')
        self.setMovable(False)
        self.setFloatable(False)
        self.setIconSize(QSize(21, 21))
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.tool = 'marker'
        self.color = QColor('#1765cf')
        self.profiles = {
            'marker': [3., 100, '#1765cf'], 'pencil': [1.2, 75, '#344256'],
            'highlighter': [12., 35, '#ffd400'], 'eraser': [16., 100, '#1765cf'],
        }
        self.actions_by_tool = {}
        self.buttons = {}
        for code, title, glyph in (
                ('marker', tr('Pennarello'), 'draw'), ('pencil', tr('Matita'), 'pencil'),
                ('highlighter', tr('Evidenziatore'), 'highlight'), ('eraser', tr('Gomma'), 'eraser'),
                ('select', tr('Seleziona disegni'), 'select_ink')):
            action = QAction(icon(glyph), title, self)
            action.setCheckable(True)
            action.setProperty('glyph', glyph)
            action.triggered.connect(lambda _checked=False, tool=code: self.set_tool(tool))
            self.addAction(action)
            button = self.widgetForAction(action)
            button.setObjectName('InkTool')
            button.setAccessibleName(title)
            self.actions_by_tool[code] = action
            self.buttons[code] = button
        self.addSeparator()
        self.settings_action = QAction(icon('style'), tr('Stile'), self)
        self.addAction(self.settings_action)
        self.settings_button = self.widgetForAction(self.settings_action)
        self.settings_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.settings_button.setToolTip(tr('Colori, spessore e opacità'))
        self.settings_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.settings_button.setMinimumWidth(85)
        self.menu = QMenu(self)
        self.settings_button.setMenu(self.menu)
        self.settings_action.triggered.connect(lambda: self.menu.popup(
            self.settings_button.mapToGlobal(self.settings_button.rect().bottomLeft())))
        panel = QFrame()
        self.panel = panel
        panel.setObjectName('InkStylePanel')
        panel.setFixedWidth(270)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        self.colors = QWidget(panel)
        colors_layout = QVBoxLayout(self.colors)
        colors_layout.setContentsMargins(0, 0, 0, 0)
        heading = QLabel(tr('Colori'))
        heading.setObjectName('SectionTitle')
        colors_layout.addWidget(heading)
        grid = QGridLayout()
        grid.setSpacing(7)
        self.swatches = []
        for index, color in enumerate(PALETTE):
            swatch = ColorSwatch(color, self.colors)
            swatch.clicked.connect(lambda _checked=False, value=color: self.set_color(QColor(value)))
            grid.addWidget(swatch, index//6, index%6)
            self.swatches.append(swatch)
        colors_layout.addLayout(grid)
        custom = QPushButton(tr('Altri colori…'))
        custom.clicked.connect(self.custom_color_requested.emit)
        colors_layout.addWidget(custom)
        layout.addWidget(self.colors)
        self.preview = StrokePreview(self)
        layout.addWidget(self.preview)
        self.width_label = QLabel(tr('Spessore'))
        self.width_label.setObjectName('SectionTitle')
        layout.addWidget(self.width_label)
        row = QHBoxLayout()
        self.width_slider = QSlider(Qt.Orientation.Horizontal)
        self.width_slider.setAccessibleName(tr('Spessore'))
        row.addWidget(self.width_slider, 1)
        self.width_control = QDoubleSpinBox()
        self.width_control.setDecimals(1)
        self.width_control.setSingleStep(.5)
        self.width_control.setSuffix(' pt')
        self.width_control.setMinimumWidth(78)
        self.width_control.setAccessibleName(tr('Spessore'))
        self.width_label.setBuddy(self.width_control)
        row.addWidget(self.width_control)
        layout.addLayout(row)
        ends = QHBoxLayout()
        ends.addWidget(QLabel(tr('Sottile')))
        ends.addStretch()
        ends.addWidget(QLabel(tr('Spesso')))
        layout.addLayout(ends)
        self.opacity_area = QWidget(panel)
        opacity_layout = QVBoxLayout(self.opacity_area)
        opacity_layout.setContentsMargins(0, 0, 0, 0)
        heading = QLabel(tr('Opacità'))
        heading.setObjectName('SectionTitle')
        opacity_layout.addWidget(heading)
        row = QHBoxLayout()
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(10, 100)
        self.opacity_slider.setAccessibleName(tr('Opacità'))
        row.addWidget(self.opacity_slider, 1)
        self.opacity = QSpinBox()
        self.opacity.setRange(10, 100)
        self.opacity.setSuffix('%')
        self.opacity.setMinimumWidth(72)
        self.opacity.setAccessibleName(tr('Opacità'))
        row.addWidget(self.opacity)
        opacity_layout.addLayout(row)
        layout.addWidget(self.opacity_area)
        self.scroll = QScrollArea(self.menu)
        self.scroll.setObjectName('InkStyleScroll')
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(panel)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFixedWidth(286)
        action = QWidgetAction(self.menu)
        action.setDefaultWidget(self.scroll)
        self.menu.addAction(action)
        self.menu.aboutToShow.connect(self._fit_popup)
        for code in self.profiles:
            button = self.buttons[code]
            button.setMenu(self.menu)
            button.setMinimumWidth(50)
            button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
            button.pressed.connect(lambda tool=code: self.set_tool(tool))
        self.width_control.valueChanged.connect(self._style_changed)
        self.opacity.valueChanged.connect(self._style_changed)
        self.width_slider.valueChanged.connect(lambda value: self.width_control.setValue(value/10))
        self.opacity_slider.valueChanged.connect(self.opacity.setValue)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.addWidget(spacer)
        self.delete_action = QAction(icon('delete'), tr('Elimina disegno'), self)
        self.addAction(self.delete_action)
        self.delete_button = self.widgetForAction(self.delete_action)
        self.done_action = QAction(icon('close'), tr('Fine'), self)
        self.addAction(self.done_action)
        self.widgetForAction(self.done_action).setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._sync()

    def _fit_popup(self):
        # A tall QWidgetAction makes QMenu create unusable columns on small or
        # highly scaled screens. Keep a single bounded, keyboard-scrollable panel.
        self.panel.setMinimumHeight(0)
        self.panel.setMaximumHeight(16777215)
        self.panel.setFixedHeight(self.panel.sizeHint().height())
        available = self.settings_button.screen().availableGeometry().height()
        self.scroll.setFixedHeight(min(self.panel.height(), max(180, available-30)))
        self.scroll.verticalScrollBar().setValue(0)

    def set_tool(self, code):
        if code not in self.buttons:
            return
        changed = self.tool != code
        self.tool = code
        self._sync()
        if changed:
            self.tool_changed.emit(code)

    def _sync(self):
        for code, action in self.actions_by_tool.items():
            action.setChecked(code == self.tool)
            self.buttons[code].setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon if code == self.tool
                else Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.settings_action.setEnabled(self.tool != 'select')
        if self.tool == 'select':
            return
        width, opacity, color = self.profiles[self.tool]
        blockers = [QSignalBlocker(widget) for widget in (self.width_control, self.width_slider, self.opacity, self.opacity_slider)]
        self.width_control.setRange(2 if self.tool == 'eraser' else .5, 60 if self.tool == 'eraser' else 30)
        self.width_slider.setRange(20 if self.tool == 'eraser' else 5, 600 if self.tool == 'eraser' else 300)
        self.width_control.setValue(width)
        self.width_slider.setValue(round(width*10))
        self.opacity.setValue(opacity)
        self.opacity_slider.setValue(opacity)
        self.color = QColor(color)
        self.colors.setVisible(self.tool != 'eraser')
        self.opacity_area.setVisible(self.tool != 'eraser')
        self.width_label.setText(tr('Dimensione gomma') if self.tool == 'eraser' else tr('Spessore'))
        self.width_control.setAccessibleName(self.width_label.text())
        self.width_slider.setAccessibleName(self.width_label.text())
        for swatch in self.swatches:
            swatch.setChecked(swatch.color == self.color)
        self.settings_action.setIcon(icon('eraser' if self.tool == 'eraser' else 'style', self.color.name()))
        self.preview.update()
        del blockers

    def _style_changed(self, *_):
        if self.tool not in self.profiles:
            return
        self.profiles[self.tool] = [self.width_control.value(), self.opacity.value(), self.color.name()]
        with QSignalBlocker(self.width_slider), QSignalBlocker(self.opacity_slider):
            self.width_slider.setValue(round(self.width_control.value()*10))
            self.opacity_slider.setValue(self.opacity.value())
        self.preview.update()
        self.style_changed.emit()

    def set_color(self, color):
        if self.tool in self.profiles and self.tool != 'eraser' and color.isValid():
            self.color = QColor(color)
            self.profiles[self.tool][2] = color.name()
            self._sync()
            self.style_changed.emit()
