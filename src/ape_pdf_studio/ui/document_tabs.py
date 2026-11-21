"""Tabbed document groups that can be docked in a shared workspace."""
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QMimeData, QPoint, QRect, QSignalBlocker, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDrag
from PySide6.QtWidgets import (
    QApplication, QLabel, QMenu, QSplitter, QTabBar, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from ..core.localization import tr
from .icons import icon
from .theme import theme_color

_TAB_MIME = "application/x-ape-pdf-studio-tab"
_MAX_GROUPS = 4


def _edges():
    return {
        "left": tr("Aggancia a sinistra"),
        "right": tr("Aggancia a destra"),
        "top": tr("Aggancia in alto"),
        "bottom": tr("Aggancia in basso"),
    }


class _DocumentTabBar(QTabBar):
    def __init__(self, group):
        super().__init__(group)
        self.group = group
        self._pressed = None

    def mousePressEvent(self, event):
        self._pressed = None
        if event.button() == Qt.MouseButton.LeftButton:
            index = self.tabAt(event.position().toPoint())
            if index >= 0:
                self._pressed = self.group.widget(index), event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._pressed and event.buttons() & Qt.MouseButton.LeftButton:
            page, start = self._pressed
            if (event.position().toPoint() - start).manhattanLength() >= QApplication.startDragDistance():
                self._pressed = None
                self.group.workspace.drag_tab(self.group, page, start)
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._pressed = None
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        index = self.currentIndex() if event.reason() == event.Reason.Keyboard else self.tabAt(event.pos())
        if index < 0:
            return
        self.group.workspace.tab_menu(self.group, index, event.globalPos())
        event.accept()


class _TabGroup(QTabWidget):
    def __init__(self, workspace):
        super().__init__(workspace)
        self.workspace = workspace
        self.setObjectName("DocumentTabs")
        self.setTabBar(_DocumentTabBar(self))
        self.setDocumentMode(True)
        self.setTabsClosable(True)
        self.setIconSize(QSize(16, 16))
        bar = self.tabBar()
        bar.setObjectName("DocumentTabBar")
        bar.setExpanding(False)
        bar.setElideMode(Qt.TextElideMode.ElideMiddle)
        bar.setUsesScrollButtons(True)
        bar.setDrawBase(False)
        # Qt configures the arrow directions when it lays out these controls.
        for button in bar.findChildren(QToolButton):
            button.setProperty("tabScroll", True)

    def tabInserted(self, index):
        super().tabInserted(index)
        bar = self.tabBar()
        previous = bar.tabButton(index, QTabBar.ButtonPosition.RightSide)
        close = QToolButton(bar)
        close.setObjectName("DocumentTabClose")
        close.setProperty("glyph", "close")
        close.setIcon(icon("close"))
        close.setIconSize(QSize(14, 14))
        close.setFixedSize(24, 24)
        close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close.setToolTip(tr("Chiudi scheda"))
        close.setAccessibleName(tr("Chiudi scheda"))
        page = self.widget(index)
        close.clicked.connect(lambda: self._request_close(page))
        bar.setTabButton(index, QTabBar.ButtonPosition.RightSide, close)
        if previous is not None:
            previous.deleteLater()

    def _request_close(self, page):
        index = self.indexOf(page)
        if index >= 0:
            self.tabCloseRequested.emit(index)

    def refresh_icons(self):
        for index in range(self.count()):
            self.setTabIcon(index, icon("page"))


@dataclass(frozen=True)
class _DropTarget:
    group: _TabGroup
    area: QRect
    label: str = ""
    edge: str | None = None
    index: int | None = None


class DocumentTabs(QWidget):
    """Keep the window's tab indices global while each group shows one document."""
    currentChanged = Signal(int)
    tabCloseRequested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._root = self._active = self._new_group()
        self._layout.addWidget(self._root)
        self._moving = False
        self._dragging = None
        self._drop = None
        self._preview = QLabel(self)
        self._preview.setObjectName("DocumentDockPreview")
        self._preview.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._preview.setTextFormat(Qt.TextFormat.PlainText)
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setWordWrap(True)
        self._preview.hide()
        QApplication.instance().installEventFilter(self)

    def _new_group(self):
        group = _TabGroup(self)
        group.currentChanged.connect(lambda _index: self._activate(group, force=True))
        group.tabCloseRequested.connect(lambda index: self.tabCloseRequested.emit(self.indexOf(group.widget(index))))
        return group

    def _groups(self, node=None):
        node = self._root if node is None else node
        if isinstance(node, _TabGroup):
            yield node
        else:
            for index in range(node.count()):
                yield from self._groups(node.widget(index))

    def _locate(self, index):
        if index >= 0:
            for group in self._groups():
                if index < group.count():
                    return group, index
                index -= group.count()
        return None, -1

    def _activate(self, group, *, force=False):
        if self._moving or (group is self._active and not force):
            return
        self._active = group
        for pane in self._groups():
            bar = pane.tabBar()
            active = pane is group
            if bar.property("activePane") != active:
                bar.setProperty("activePane", active)
                bar.style().unpolish(bar)
                bar.style().polish(bar)
                bar.update()
        self.currentChanged.emit(self.currentIndex())

    def eventFilter(self, receiver, event):
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.FocusIn) and isinstance(receiver, QWidget):
            widget = receiver
            while widget is not None:
                if isinstance(widget, _TabGroup) and widget.workspace is self:
                    self._activate(widget)
                    break
                widget = widget.parentWidget()
        return False

    def count(self):
        return sum(group.count() for group in self._groups())

    def group_count(self):
        return sum(1 for _ in self._groups())

    def widget(self, index):
        group, local = self._locate(index)
        return group.widget(local) if group is not None else None

    def indexOf(self, page):
        offset = 0
        for group in self._groups():
            index = group.indexOf(page) if page is not None else -1
            if index >= 0:
                return offset + index
            offset += group.count()
        return -1

    def currentIndex(self):
        return self.indexOf(self.currentWidget())

    def currentWidget(self):
        return self._active.currentWidget()

    def setCurrentIndex(self, index):
        group, local = self._locate(index)
        if group is not None:
            with QSignalBlocker(group):
                group.setCurrentIndex(local)
            self._activate(group, force=True)

    def setCurrentWidget(self, page):
        self.setCurrentIndex(self.indexOf(page))

    def tabBar(self):
        return self._active.tabBar()

    def addTab(self, page, glyph, label):
        self._active.addTab(page, glyph, label)
        return self.indexOf(page)

    def setTabText(self, index, label):
        group, local = self._locate(index)
        if group is not None:
            group.setTabText(local, label)

    def tabText(self, index):
        group, local = self._locate(index)
        return group.tabText(local) if group is not None else ""

    def setTabToolTip(self, index, tooltip):
        group, local = self._locate(index)
        if group is not None:
            group.setTabToolTip(local, tooltip)

    def tabToolTip(self, index):
        group, local = self._locate(index)
        return group.tabToolTip(local) if group is not None else ""

    def removeTab(self, index):
        group, local = self._locate(index)
        if group is None:
            return
        active = self._active
        focus = QApplication.focusWidget()
        restore_focus = focus is not None and active.isAncestorOf(focus)
        self._moving = True
        try:
            with QSignalBlocker(group):
                page = group.widget(local)
                group.removeTab(local)
                page.hide()
                page.setParent(self)
            remaining = self._remove_empty(group)
        finally:
            self._moving = False
        target = remaining if group is active else active
        self._activate(target, force=True)
        if restore_focus:
            if target.isAncestorOf(focus) and focus.isVisible():
                focus.setFocus()
            else:
                target.tabBar().setFocus()

    def refresh_icons(self):
        for group in self._groups():
            group.refresh_icons()

    def _detach(self, node):
        """Detach a node and return the insertion point with its original sizes."""
        parent = node.parentWidget()
        if isinstance(parent, QSplitter):
            index = parent.indexOf(node)
            sizes = parent.sizes()
            node.hide()
            node.setParent(self)

            def insert(replacement):
                parent.insertWidget(index, replacement)
                replacement.show()
                parent.setSizes(sizes)
        else:
            node.hide()
            self._layout.removeWidget(node)

            def insert(replacement):
                self._layout.addWidget(replacement)
                self._root = replacement
                replacement.show()
        return insert

    def _remove_empty(self, group):
        if group.count() or group is self._root:
            return group
        branch = group.parentWidget()
        sibling = branch.widget(1 - branch.indexOf(group))
        insert = self._detach(branch)
        sibling.setParent(self)
        insert(sibling)
        branch.deleteLater()
        return next(self._groups(sibling))

    def _split(self, target, edge):
        new_group = self._new_group()
        orientation = Qt.Orientation.Horizontal if edge in ("left", "right") else Qt.Orientation.Vertical
        extent = target.width() if orientation == Qt.Orientation.Horizontal else target.height()
        branch = QSplitter(orientation)
        branch.setObjectName("DocumentSplit")
        branch.setChildrenCollapsible(False)
        branch.setHandleWidth(5)
        branch.setOpaqueResize(False)
        insert = self._detach(target)
        for child in ((new_group, target) if edge in ("left", "top") else (target, new_group)):
            branch.addWidget(child)
            child.show()
        insert(branch)
        branch.setSizes([max(1, extent // 2)] * 2)
        return new_group

    def _can_split(self, source, target):
        if source is target:
            return source.count() > 1 and self.group_count() < _MAX_GROUPS
        return source.count() == 1 or self.group_count() < _MAX_GROUPS

    def _transfer(self, source, page, target, index):
        local = source.indexOf(page)
        with QSignalBlocker(source), QSignalBlocker(target):
            glyph, label, tooltip = source.tabIcon(local), source.tabText(local), source.tabToolTip(local)
            source.removeTab(local)
            new_index = target.insertTab(index, page, glyph, label)
            target.setTabToolTip(new_index, tooltip)

    def _dock(self, source, page, target, *, edge=None, index=None):
        groups = tuple(self._groups())
        if source not in groups or target not in groups:
            return
        local = source.indexOf(page)
        if local < 0 or (edge is not None and not self._can_split(source, target)):
            return
        self._moving = True
        try:
            if edge is not None:
                target = self._split(target, edge)
            position = target.count() if index is None else index
            if source is target:
                position -= local < position
                source.tabBar().moveTab(local, position)
            else:
                self._transfer(source, page, target, position)
                self._remove_empty(source)
            target.setCurrentWidget(page)
        finally:
            self._moving = False
        self._activate(target, force=True)
        target.tabBar().setFocus()

    def reunite(self):
        target = self._active
        selected = target.currentWidget()
        self._moving = True
        try:
            for source in list(self._groups()):
                if source is target:
                    continue
                while source.count():
                    self._transfer(source, source.widget(0), target, target.count())
                self._remove_empty(source)
            target.setCurrentWidget(selected)
        finally:
            self._moving = False
        self._activate(target, force=True)

    def tab_menu(self, group, index, position):
        page = group.widget(index)
        menu = QMenu(self)
        for edge, label in _edges().items():
            action = menu.addAction(label)
            action.setEnabled(self._can_split(group, group))
            action.triggered.connect(lambda _checked=False, side=edge: self._dock(group, page, group, edge=side))
        menu.addSeparator()
        action = menu.addAction(tr("Riunisci tutte le schede"), self.reunite)
        action.setEnabled(self.group_count() > 1)
        menu.exec(position)
        menu.deleteLater()

    def drag_tab(self, group, page, start):
        index = group.indexOf(page)
        if index < 0 or self._dragging is not None:
            return
        bar = group.tabBar()
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(_TAB_MIME, b"tab")
        drag.setMimeData(mime)
        rect = bar.tabRect(index)
        drag.setPixmap(bar.grab(rect))
        drag.setHotSpot(start - rect.topLeft())
        self._dragging = group, page
        try:
            action = drag.exec(Qt.DropAction.MoveAction)
            target = self._drop
        finally:
            self._dragging = None
            self._drop = None
            self._preview.hide()
            drag.deleteLater()
        if action == Qt.DropAction.MoveAction and target is not None:
            self._dock(group, page, target.group, edge=target.edge, index=target.index)

    def _drop_target(self, source, point):
        for group in self._groups():
            area = QRect(group.mapTo(self, QPoint()), group.size())
            if not area.contains(point):
                continue
            bar = group.tabBar()
            bar_origin = bar.mapTo(self, QPoint())
            if point.y() < bar_origin.y() + bar.height():
                local = point - bar_origin
                index = bar.tabAt(local)
                if index >= 0:
                    index += local.x() > bar.tabRect(index).center().x()
                else:
                    index = group.count()
                border = bar.tabRect(index).left() if index < group.count() else bar.tabRect(index - 1).right()
                x = min(max(bar_origin.x() + border, area.left() + 2), area.right() - 2)
                return _DropTarget(group, QRect(x - 2, bar_origin.y() + 5, 4, bar.height() - 8), index=index)
            area.setTop(bar_origin.y() + bar.height())
            x = (point.x() - area.left()) / max(1, area.width())
            y = (point.y() - area.top()) / max(1, area.height())
            distance, edge = min(((x, "left"), (1 - x, "right"), (y, "top"), (1 - y, "bottom")))
            if distance < .23 and self._can_split(source, group):
                if edge == "left":
                    area.setWidth(area.width() // 2)
                elif edge == "right":
                    area.setLeft(area.center().x() + 1)
                elif edge == "top":
                    area.setHeight(area.height() // 2)
                else:
                    area.setTop(area.center().y() + 1)
                return _DropTarget(group, area.adjusted(4, 4, -4, -4), _edges()[edge], edge=edge)
            if group is not source:
                return _DropTarget(group, area.adjusted(4, 4, -4, -4), tr("Sposta qui la scheda"))
            return None
        return None

    def _is_tab_drag(self, event):
        return (self._dragging is not None and event.source() is self
                and event.mimeData().hasFormat(_TAB_MIME))

    def dragEnterEvent(self, event):
        if self._is_tab_drag(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        target = self._drop_target(self._dragging[0], event.position().toPoint()) if self._is_tab_drag(event) else None
        if target is None:
            self._preview.hide()
            event.ignore()
            return
        if (self._preview.isVisible() and self._preview.geometry() == target.area
                and self._preview.text() == target.label):
            event.acceptProposedAction()
            return
        accent = QColor(theme_color("accent"))
        fill = accent.name() if target.index is not None else f"rgba({accent.red()}, {accent.green()}, {accent.blue()}, 65)"
        self._preview.setStyleSheet(
            f"QLabel#DocumentDockPreview {{ background: {fill}; border: 2px solid {accent.name()}; "
            f"border-radius: 4px; color: {theme_color('accent_dark')}; font-weight: 700; }}"
        )
        self._preview.setGeometry(target.area)
        self._preview.setText(target.label)
        self._preview.raise_()
        self._preview.show()
        event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self._preview.hide()
        event.accept()

    def dropEvent(self, event):
        self._preview.hide()
        target = self._drop_target(self._dragging[0], event.position().toPoint()) if self._is_tab_drag(event) else None
        if target is None:
            event.ignore()
            return
        # Wait until QDrag.exec returns before moving the source widget.
        self._drop = target
        event.acceptProposedAction()
