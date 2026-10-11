"""Document tabs with compact labels and consistent close controls."""
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QTabBar, QTabWidget, QToolButton

from ..core.localization import tr
from .icons import icon


class DocumentTabs(QTabWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DocumentTabs")
        self.setDocumentMode(True)
        self.setTabsClosable(True)
        self.setMovable(True)
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
