"""On-page manipulation, background analysis and explicit creation confirmation."""
import sys
from pathlib import Path
from PySide6.QtCore import QPointF, QProcess, Qt
from PySide6.QtTest import QTest

from test_gui import app, text_pdf, window, wait_for
from test_form_preparation import flat_forms


def prepare(window,source):
    window.open_pdf(source)
    tab=window.current
    window.a_prepare_form.trigger()
    QTest.qWait(80)
    assert tab.form_designer.isVisible()
    return tab,tab.form_designer


def test_detection_review_no_mutation_until_confirmed(window,flat_forms,monkeypatch):
    tab,panel=prepare(window,flat_forms[0])
    monkeypatch.setattr('ape_pdf_studio.ui.form_designer.default_ocr_cache',lambda:flat_forms[0].parent/'models')
    panel.ocr.setChecked(False)
    panel.detect()
    wait_for(lambda:panel.process is None,15000)
    assert len(panel.proposals)==5,panel.feedback.text()
    assert not tab.editor.dirty and not tab.form_fields
    monkeypatch.setattr(panel,'_confirm',lambda *a:False)
    panel.apply()
    assert not tab.editor.dirty and len(panel.proposals)==5
    monkeypatch.setattr(panel,'_confirm',lambda *a:True)
    panel.apply()
    assert tab.editor.dirty and len(tab.form_fields)==5 and not panel.proposals
    panel.close_preparation()
    assert not panel.isVisible()
    window.fill_form()
    assert tab.inline_form.control is not None
    tab.inline_form.cancel()
    window.undo()
    assert not tab.form_fields and not tab.editor.dirty
    window.redo()
    assert len(tab.form_fields)==5


def test_draw_move_resize_rename_and_remove_on_page(window,flat_forms):
    tab,panel=prepare(window,flat_forms[0])
    panel.draw_kind.setCurrentIndex(panel.draw_kind.findData('text'))
    transform=tab.viewer.page_transform(0)
    start=transform.map(QPointF(100,180)).toPoint()
    end=transform.map(QPointF(280,206)).toPoint()
    QTest.mousePress(tab.viewer.viewport(),Qt.MouseButton.LeftButton,pos=start)
    QTest.mouseMove(tab.viewer.viewport(),end,20)
    QTest.mouseRelease(tab.viewer.viewport(),Qt.MouseButton.LeftButton,pos=end)
    assert len(panel.proposals)==1
    original=list(panel.proposals[0]['rect'])
    panel.name.setText('customer_name')
    panel.update_selected()
    assert panel.proposals[0]['name']=='customer_name'
    transform=tab.viewer.page_transform(0)
    point=transform.map(QPointF(150,190)).toPoint()
    target=point+transform.map(QPointF(30,20)).toPoint()-transform.map(QPointF(0,0)).toPoint()
    QTest.mousePress(tab.viewer.viewport(),Qt.MouseButton.LeftButton,pos=point)
    QTest.mouseMove(tab.viewer.viewport(),target,20)
    QTest.mouseRelease(tab.viewer.viewport(),Qt.MouseButton.LeftButton,pos=target)
    moved=panel.proposals[0]['rect']
    assert moved[0]>original[0]+20 and moved[1]>original[1]+10
    point=transform.map(QPointF(moved[2],moved[3])).toPoint()
    target=point+transform.map(QPointF(30,15)).toPoint()-transform.map(QPointF(0,0)).toPoint()
    width=moved[2]-moved[0]
    QTest.mousePress(tab.viewer.viewport(),Qt.MouseButton.LeftButton,pos=point)
    QTest.mouseMove(tab.viewer.viewport(),target,20)
    QTest.mouseRelease(tab.viewer.viewport(),Qt.MouseButton.LeftButton,pos=target)
    rect=panel.proposals[0]['rect']
    assert rect[2]-rect[0]>width+20
    panel.remove_selected()
    assert not panel.proposals and not tab.editor.dirty


def test_discard_cancel_and_invalid_proposals_preserve_review(window,flat_forms,monkeypatch):
    tab,panel=prepare(window,flat_forms[0])
    panel.mode='text'
    panel.add_manual(0,[40,180,250,205])
    monkeypatch.setattr(panel,'_confirm',lambda *a:False)
    panel.close_preparation()
    assert panel.isVisible() and panel.proposals
    assert not window._confirm_close(tab)
    panel.proposals[0]['name']='bad.name'
    panel.apply()
    assert not tab.editor.dirty and panel.proposals
    assert 'univoci' in panel.feedback.text()
    monkeypatch.setattr(panel,'_confirm',lambda *a:True)
    panel.close_preparation()
    assert not panel.isVisible() and not panel.proposals


def test_cancel_worker_retains_previous_proposals_and_removes_snapshot(window,flat_forms,monkeypatch):
    tab,panel=prepare(window,flat_forms[0])
    panel.mode='text'
    panel.add_manual(0,[40,177,250,205])
    monkeypatch.setattr(panel,'_confirm',lambda *a:True)
    monkeypatch.setattr('ape_pdf_studio.ui.form_designer.default_ocr_cache',lambda:flat_forms[0].parent/'models')
    monkeypatch.setattr('ape_pdf_studio.ui.form_designer.worker_command',lambda *a:[sys.executable,'-c','import time; time.sleep(30)'])
    panel.detect()
    work=Path(panel._work.name)
    wait_for(lambda:panel.process.state()==QProcess.ProcessState.Running)
    panel._timeout()
    wait_for(lambda:panel.process is None)
    assert len(panel.proposals)==1 and not tab.editor.dirty
    assert not work.exists()
    assert '90' in panel.feedback.text()
    panel.proposals=[]


def test_failed_detection_does_not_relabel_stale_proposals_as_current(window,flat_forms,monkeypatch):
    tab,panel=prepare(window,flat_forms[0])
    panel.mode='text'
    panel.add_manual(0,[40,177,250,205])
    previous=panel.revision
    tab.apply_edit('rotate',0)
    monkeypatch.setattr(panel,'_confirm',lambda *a:True)
    monkeypatch.setattr('ape_pdf_studio.ui.form_designer.default_ocr_cache',lambda:flat_forms[0].parent/'models')
    monkeypatch.setattr('ape_pdf_studio.ui.form_designer.worker_command',lambda *a:[sys.executable,'-c','print("invalid JSON")'])
    panel.detect()
    wait_for(lambda:panel.process is None)
    assert panel.revision==previous and panel.revision!=tab.editor.path
    assert len(panel.proposals)==1 and not panel.apply_button.isEnabled()
    panel.proposals=[]
