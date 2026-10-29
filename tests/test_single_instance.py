"""Real local IPC, competing processes, crash recovery and existing-tab delivery."""
import json
import os
from pathlib import Path
import queue
import struct
import subprocess
import sys
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFileOpenEvent
from PySide6.QtNetwork import QLocalSocket
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog

from test_gui import app, text_pdf, window, wait_for
from ape_pdf_studio.ui.single_instance import (ACK, HEADER, MAX_FILES, MAX_MESSAGE,
    SingleInstance, decode_request, encode_request, pdf_arguments)


@pytest.fixture
def instances(app, tmp_path):
    created = []
    def create():
        instance = SingleInstance(directory=tmp_path/'ipc')
        created.append(instance)
        return instance
    yield create
    for instance in reversed(created):
        instance.close()
        instance.deleteLater()
    app.processEvents()


@pytest.mark.parametrize('payload', [
    {}, {'version': True, 'paths': []}, {'version': 2, 'paths': []},
    {'version': 1, 'paths': ['relative.pdf']}, {'version': 1, 'paths': ['/document.exe']},
    {'version': 1, 'paths': [None]}, {'version': 1, 'paths': ['/bad\x00.pdf']},
    {'version': 1, 'paths': [], 'command': 'run'}, {'version': 1, 'paths': [], 'args': []},
    {'version': 1, 'paths': ['/many.pdf'] * (MAX_FILES+1)},
])
def test_opening_protocol_rejects_commands_and_invalid_paths(payload):
    with pytest.raises(ValueError):
        decode_request(json.dumps(payload).encode('utf-8'))


def test_arguments_preserve_unicode_spaces_and_virtual_onedrive_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, 'resolve', lambda *_a, **_k: pytest.fail('Virtual user path resolved'))
    name = 'Documento con accenti è.PDF'
    paths = pdf_arguments([name, '--flag', 'not.txt', 'bad\x00.pdf'])
    assert paths == [str(tmp_path/name)]
    assert decode_request(encode_request(paths)[HEADER.size:]) == paths


def test_listener_acknowledges_without_gui_event_processing(instances, tmp_path):
    owner, sender = instances(), instances()
    assert owner.start([])
    paths = [str(tmp_path/'one.pdf'), str(tmp_path/'due con è.PDF')]
    assert not sender.start(paths)
    assert owner.requests.get_nowait() == paths
    sender.close()
    # Closing a secondary instance must not release the owner's lock.
    assert not instances().lock.tryLock(0)


@pytest.mark.parametrize('payload', [HEADER.pack(MAX_MESSAGE+1), HEADER.pack(2)+b'{}', HEADER.pack(1)+b'\xff'])
def test_bad_socket_request_is_discarded_without_stopping_listener(instances, tmp_path, payload):
    owner = instances()
    assert owner.start([])
    socket = QLocalSocket()
    socket.connectToServer(owner.name)
    assert socket.waitForConnected(1000)
    socket.write(payload)
    if socket.bytesToWrite():
        socket.waitForBytesWritten(1000)
    socket.waitForReadyRead(1000)
    assert bytes(socket.readAll()) != ACK
    socket.abort()
    assert owner.requests.empty()
    assert not instances().start([str(tmp_path/'valid.pdf')])
    assert owner.requests.get_nowait() == [str(tmp_path/'valid.pdf')]


def test_fragmented_packet_and_empty_activation_request(instances, tmp_path):
    owner = instances()
    assert owner.start([])
    path = str(tmp_path/'with spaces è.pdf')
    packet = encode_request([path])
    socket = QLocalSocket()
    socket.connectToServer(owner.name)
    assert socket.waitForConnected(1000)
    for part in (packet[:2], packet[2:9], packet[9:]):
        socket.write(part)
        if socket.bytesToWrite():
            socket.waitForBytesWritten(1000)
        time.sleep(.005)
    assert socket.waitForReadyRead(1000)
    assert bytes(socket.readAll()) == ACK
    assert owner.requests.get_nowait() == [path]
    socket.abort()
    assert not instances().start([])
    assert owner.requests.get_nowait() == []


def test_more_than_one_packet_of_files_is_delivered(instances, tmp_path):
    owner = instances()
    assert owner.start([])
    paths = [str(tmp_path/f'{index}.pdf') for index in range(MAX_FILES+3)]
    assert not instances().start(paths)
    assert owner.requests.get_nowait() + owner.requests.get_nowait() == paths


def test_existing_window_gets_new_tabs_reuses_open_file_and_restores(window, instances, text_pdf, tmp_path):
    owner = instances()
    assert owner.start([])
    owner.attach(window)
    original = window.current
    original.apply_edit('add_note', 0, 'Draft retained', (80, 160))
    paths = [tmp_path/'Secondo documento è.PDF', tmp_path/'Third.pdf']
    for path in paths:
        path.write_bytes(text_pdf.read_bytes())
    window.showMinimized()
    assert not instances().start(list(map(str, paths)) + [str(text_pdf)])
    wait_for(lambda: window.tabs.count() == 3 and window.current is original, timeout=15000)
    assert not window.isMinimized() and original.dirty
    assert all(window.tabs.widget(i).pdf.pageCount() == 3 for i in range(3))
    assert not instances().start([])
    wait_for(lambda: owner.requests.empty())
    assert window.tabs.count() == 3


def test_modal_dialog_defers_new_documents_without_losing_them(window, instances, text_pdf, tmp_path):
    owner = instances()
    assert owner.start([])
    owner.attach(window)
    path = tmp_path/'after-dialog.pdf'
    path.write_bytes(text_pdf.read_bytes())
    dialog = QDialog(window)
    dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
    dialog.show()
    try:
        QTest.qWait(30)
        assert not instances().start([str(path)])
        QTest.qWait(120)
        assert window.tabs.count() == 1 and not owner.requests.empty()
    finally:
        dialog.reject()
    wait_for(lambda: window.tabs.count() == 2, timeout=10000)


def test_close_waits_for_acknowledged_opening_request(window, instances, text_pdf, tmp_path):
    owner = instances()
    assert owner.start([])
    owner.attach(window)
    path = tmp_path/'during-close.pdf'
    path.write_bytes(text_pdf.read_bytes())
    assert not instances().start([str(path)])
    assert not window.close()
    wait_for(lambda: window.tabs.count() == 2, timeout=10000)
    assert window.isVisible()


def test_os_file_open_events_are_delivered_to_existing_tabs(app, window, instances, text_pdf, tmp_path):
    owner = instances()
    assert owner.start([])
    owner.attach(window)
    path = tmp_path/'Finder document è.pdf'
    path.write_bytes(text_pdf.read_bytes())
    assert app.sendEvent(app, QFileOpenEvent(str(path)))
    wait_for(lambda: window.tabs.count() == 2, timeout=10000)
    assert window.current.editor.source == path


@pytest.mark.skipif(sys.platform != 'win32', reason='Real Windows AppContainer boundary')
def test_pdf_appcontainer_cannot_send_open_requests_to_gui(instances):
    from ape_pdf_studio.core.private_work import PrivateDirectory
    from ape_pdf_studio.core.sandbox import wrap_command
    owner = instances()
    assert owner.start([])
    with PrivateDirectory() as work:
        script = Path(work)/'probe.py'
        script.write_text('''
import json, sys
from PySide6.QtCore import QCoreApplication
from PySide6.QtNetwork import QLocalSocket
app=QCoreApplication([])
socket=QLocalSocket()
socket.connectToServer(sys.argv[1])
print(json.dumps({'connected':socket.waitForConnected(1000)}))
''', encoding='utf-8')
        result = subprocess.run(wrap_command([sys.executable, str(script), owner.name], work),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        assert result.returncode == 0, result.stderr.decode(errors='replace')
        assert json.loads(result.stdout) == {'connected': False}
    assert owner.requests.empty()


CHILD = r'''
import json, os, queue, sys
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from ape_pdf_studio.ui.single_instance import SingleInstance
app=QApplication([])
root=Path(sys.argv[1])
instance=SingleInstance(directory=root)
paths=[sys.argv[2]]
try:
    if instance.start(paths):
        log=root/'received.jsonl'
        def write(value):
            with log.open('a', encoding='utf-8') as stream: stream.write(json.dumps(value)+'\n')
        write({'primary':os.getpid(),'paths':paths})
        (root/'ready').write_text(str(os.getpid()))
        def poll():
            while True:
                try: value=instance.requests.get_nowait()
                except queue.Empty: break
                write({'paths':value})
            if (root/'stop').exists(): app.quit()
        timer=QTimer()
        timer.timeout.connect(poll)
        timer.start(20)
        app.exec()
    else: print('forwarded', flush=True)
finally: instance.close()
'''


def wait_until(predicate, timeout=15):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.025)
    assert predicate()


def test_simultaneous_process_launches_elect_one_owner_and_deliver_all_files(tmp_path):
    root = tmp_path/'race'
    paths = [str(tmp_path/f'PDF numero {i} è.pdf') for i in range(5)]
    env = {**os.environ, 'QT_QPA_PLATFORM': 'offscreen'}
    children = [subprocess.Popen([sys.executable, '-c', CHILD, str(root), path], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE) for path in paths]
    try:
        def complete():
            if not (root/'received.jsonl').is_file():
                return False
            try:
                rows = [json.loads(line) for line in (root/'received.jsonl').read_text().splitlines()]
            except json.JSONDecodeError:
                return False
            return len(rows) == len(paths)
        wait_until(complete)
        rows = [json.loads(line) for line in (root/'received.jsonl').read_text().splitlines()]
        assert sum('primary' in row for row in rows) == 1
        assert sorted(p for row in rows for p in row['paths']) == sorted(paths)
    finally:
        if root.is_dir():
            (root/'stop').touch()
        for child in children:
            try:
                out, err = child.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.communicate()
                raise
            assert child.returncode == 0, err.decode(errors='replace')


def test_crashed_owner_is_recovered_without_age_based_lock_stealing(app, tmp_path):
    root = tmp_path/'crash'
    child = subprocess.Popen([sys.executable, '-c', CHILD, str(root), str(tmp_path/'first.pdf')],
                             env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        wait_until(lambda: (root/'ready').is_file())
    finally:
        child.kill()
        child.communicate(timeout=5)
    assert (root/'instance.lock').exists()
    recovered = SingleInstance(directory=root)
    try:
        assert recovered.start([])
    finally:
        recovered.close()


GUI_CHILD = r'''
import json, os, sys
from pathlib import Path
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QMessageBox
import ape_pdf_studio.app as entrypoint
from ape_pdf_studio.ui.single_instance import SingleInstance
root=Path(sys.argv[1])
root.mkdir(exist_ok=True)
documents=sys.argv[2:]
QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(root/'settings'))
entrypoint.SingleInstance=lambda parent: SingleInstance(parent, directory=root/'ipc')
OriginalWindow=entrypoint.MainWindow
def fail(parent, title, text): raise RuntimeError(text)
QMessageBox.critical=fail
class Window(OriginalWindow):
    def __init__(self):
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
        super().__init__()
        with (root/'windows.jsonl').open('a') as stream: stream.write(str(os.getpid())+'\n')
        self.timer=QTimer(self)
        self.timer.timeout.connect(lambda: self.close() if (root/'stop').exists() else None)
        self.timer.start(25)
    def schedule_update_check(self): pass
    def open_pdf(self, path):
        super().open_pdf(path)
        data={'pid':os.getpid(),'tabs':self.tabs.count(),
              'paths':[str(self.tabs.widget(i).editor.source) for i in range(self.tabs.count())]}
        temporary=root/'state.partial'
        temporary.write_text(json.dumps(data))
        temporary.replace(root/'state.json')
entrypoint.MainWindow=Window
sys.argv=['APEPDFStudio.exe', *documents]
raise SystemExit(entrypoint.main())
'''


def test_full_application_launch_forwards_to_same_window_without_another_gui(text_pdf, tmp_path):
    root = tmp_path/'gui'
    second = tmp_path/'Secondo PDF con è.PDF'
    second.write_bytes(text_pdf.read_bytes())
    env = {**os.environ, 'QT_QPA_PLATFORM': 'offscreen'}
    primary = subprocess.Popen([sys.executable, '-c', GUI_CHILD, str(root), str(text_pdf)],
                               env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        wait_until(lambda: (root/'state.json').is_file(), timeout=30)
        initial_pid = json.loads((root/'state.json').read_text())['pid']
        result = subprocess.run([sys.executable, '-c', GUI_CHILD, str(root), str(second), str(text_pdf)],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        assert result.returncode == 0, result.stderr.decode(errors='replace')
        def delivered():
            state = json.loads((root/'state.json').read_text())
            return state['tabs'] == 2
        wait_until(delivered, timeout=20)
        state = json.loads((root/'state.json').read_text())
        # Windows venv launchers can delegate to another interpreter PID.
        assert state['pid'] == initial_pid
        assert sorted(state['paths']) == sorted([str(text_pdf), str(second)])
        assert len((root/'windows.jsonl').read_text().splitlines()) == 1
    finally:
        if root.is_dir():
            (root/'stop').touch()
        try:
            out, err = primary.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            primary.kill()
            primary.communicate()
            raise
        assert primary.returncode == 0, err.decode(errors='replace')
