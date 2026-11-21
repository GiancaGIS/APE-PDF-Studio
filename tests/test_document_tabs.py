"""Docking interactions and document ownership with multiple visible panes."""
from types import SimpleNamespace

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDrag, QDragEnterEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

from test_gui import app, text_pdf, window
from ape_pdf_studio.ui.document_tabs import DocumentTabs
from ape_pdf_studio.ui.icons import icon


def drag_tab(monkeypatch, workspace, source, page, position, label):
    bar = source.tabBar()
    start = bar.tabRect(source.indexOf(page)).center()
    observed = []

    def native_drag(drag, action):
        event = SimpleNamespace(
            source=drag.source, mimeData=drag.mimeData, position=lambda: QPointF(position),
            acceptProposedAction=lambda: observed.append("accept"),
            ignore=lambda: observed.append("ignore"),
        )
        workspace.dragEnterEvent(event)
        workspace.dragMoveEvent(event)
        if label is None:
            assert not workspace._preview.isVisible()
        else:
            assert workspace._preview.isVisible()
            assert workspace._preview.text() == label
        workspace.dropEvent(event)
        return action if observed[-1] == "accept" else Qt.DropAction.IgnoreAction

    monkeypatch.setattr(QDrag, "exec", native_drag)
    QTest.mousePress(bar, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(bar, start + QPoint(0, 25))
    if bar.isVisible():
        QTest.mouseRelease(bar, Qt.MouseButton.LeftButton, pos=start)
    assert len(observed) == 3
    assert not workspace._preview.isVisible()


def test_drag_docks_reorders_and_collapses_empty_group(app, monkeypatch):
    workspace = DocumentTabs()
    workspace.resize(900, 600)
    workspace.show()
    first, second, third = (QLabel(name) for name in ("first", "second", "third"))
    for page in (first, second, third):
        index = workspace.addTab(page, icon("page"), page.text())
        workspace.setTabToolTip(index, page.text() + " tooltip")
    app.processEvents()
    root = workspace._root

    drag_tab(monkeypatch, workspace, root, third, QPoint(workspace.width() - 15, 300), "Aggancia a destra")
    app.processEvents()
    assert workspace.group_count() == 2 and workspace.count() == 3
    left, right = tuple(workspace._groups())
    assert left is root and right.currentWidget() is third and workspace.currentWidget() is third
    assert left.currentWidget().isVisible() and third.isVisible()

    bar = left.tabBar()
    beginning = bar.mapTo(workspace, bar.tabRect(0).topLeft() + QPoint(4, 10))
    drag_tab(monkeypatch, workspace, left, second, beginning, "")
    assert workspace.widget(0) is second and workspace.widget(1) is first

    middle = left.mapTo(workspace, left.rect().center())
    drag_tab(monkeypatch, workspace, right, third, middle, "Sposta qui la scheda")
    app.processEvents()
    assert workspace.group_count() == 1 and workspace.count() == 3
    assert workspace.currentWidget() is third and workspace.tabToolTip(workspace.indexOf(third)) == "third tooltip"
    workspace.close()
    workspace.deleteLater()


def test_four_panes_keep_documents_and_route_actions_to_active_view(window, text_pdf, tmp_path, monkeypatch):
    workspace = window.tabs
    first = window.current

    def open_copy(name):
        path = tmp_path / (name + ".pdf")
        path.write_bytes(text_pdf.read_bytes())
        window.open_pdf(path)
        return window.current

    second = open_copy("second")
    second.jump_to_page(1)
    left = workspace._root
    workspace._dock(left, second, left, edge="right")
    left, right = tuple(workspace._groups())

    third = open_copy("third")
    workspace._dock(right, third, right, edge="bottom")
    workspace.setCurrentWidget(first)
    fourth = open_copy("fourth")
    workspace._dock(left, fourth, left, edge="bottom")
    QTest.qWait(140)
    groups = tuple(workspace._groups())
    assert [group.currentWidget() for group in groups] == [first, fourth, second, third]
    assert all(page.isVisible() for page in (first, second, third, fourth))
    centers = [group.mapTo(workspace, group.rect().center()) for group in groups]
    assert centers[0].x() < centers[2].x()
    assert centers[0].y() < centers[1].y() and centers[2].y() < centers[3].y()
    assert all(group.width() > workspace.width() * .4 and group.height() > workspace.height() * .4 for group in groups)
    assert second.page == 1
    current_page = second.viewer.page_rects()[1]
    scroll = second.viewer.verticalScrollBar().value()
    assert current_page.top() < scroll + second.viewer.viewport().height() and current_page.bottom() > scroll

    second.jump_to_page(0)
    QTest.mouseClick(second.viewer.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(4, 4))
    assert window.current is second
    window.a_next.trigger()
    assert second.page == 1 and first.page == 0
    window.a_rotate.trigger()
    assert second.editor.dirty and all(not page.editor.dirty for page in (first, third, fourth))
    revision = second.editor.path

    fifth = open_copy("fifth")
    workspace._dock(right, fifth, right, edge="left")
    assert workspace.group_count() == 4 and workspace.currentWidget() is fifth
    window.open_pdf(second.editor.source)
    assert workspace.count() == 5 and window.current is second and second.editor.path == revision

    group = groups[3]
    group.tabBar().tabButton(0, group.tabBar().ButtonPosition.RightSide).click()
    assert workspace.group_count() == 3 and workspace.currentWidget() is second
    assert workspace.indexOf(third) == -1

    window.a_reunite_tabs.trigger()
    assert workspace.group_count() == 1 and workspace.count() == 4
    assert window.current is second and second.editor.path == revision and second.page == 1
    root = workspace._root
    workspace._dock(root, first, root, edge="top")
    assert workspace.group_count() == 2
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Discard)
    assert window.close()
    assert workspace.count() == 0 and workspace.group_count() == 1


def test_workspace_leaves_file_drags_to_main_window(window, text_pdf):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(text_pdf))])
    event = QDragEnterEvent(QPointF(20, 80), Qt.DropAction.CopyAction, mime,
                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(window.tabs, event)
    assert event.isAccepted()
