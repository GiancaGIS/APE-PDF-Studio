"""Per-user, per-session PDF opening through a bounded local Qt pipe.

The listener has its own thread so a slow PDF or password dialog cannot make
another launch time out. Only absolute PDF paths cross this channel; the normal
document broker still performs every input and sandbox check.
"""
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import queue
import stat
import struct
import sys
import tempfile
import threading
import time

from PySide6.QtCore import QEvent, QLockFile, QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from ..core.private_work import private_acl
from ..core.user_paths import user_path

HEADER = struct.Struct('!I')
MAX_MESSAGE = 128 * 1024
MAX_FILES = 128
MAX_REQUESTS = 32
ACK = b'OK\n'


class InstanceError(RuntimeError):
    pass


def pdf_arguments(arguments):
    """Normalize in the sender's cwd; never resolve OneDrive virtual paths."""
    paths = []
    for value in arguments:
        try:
            if (isinstance(value, str) and not any(ord(c) < 32 for c in value) and
                    Path(value).suffix.lower() == '.pdf'):
                path = str(user_path(value))
                if len(path) <= 32768:
                    paths.append(path)
        except (OSError, ValueError):
            continue
    return paths


def encode_request(paths):
    value = {'version': 1, 'paths': paths}
    data = json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode('ascii')
    if len(data) > MAX_MESSAGE:
        raise InstanceError('Opening request exceeds size limit')
    decode_request(data)
    return HEADER.pack(len(data)) + data


def decode_request(data):
    if len(data) > MAX_MESSAGE:
        raise ValueError('Opening request exceeds size limit')
    value = json.loads(data)
    if (not isinstance(value, dict) or set(value) != {'version', 'paths'} or
            type(value['version']) is not int or value['version'] != 1 or
            not isinstance(value['paths'], list) or len(value['paths']) > MAX_FILES):
        raise ValueError('Invalid opening request')
    for path in value['paths']:
        if (not isinstance(path, str) or not 1 <= len(path) <= 32768 or
                any(ord(c) < 32 for c in path) or not Path(path).is_absolute() or
                Path(path).suffix.lower() != '.pdf'):
            raise ValueError('Invalid PDF path')
    return value['paths']


def instance_directory():
    identity = private_acl(None)
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes
        session = wintypes.DWORD()
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        if not kernel.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
            raise ctypes.WinError(ctypes.get_last_error())
        identity += ':' + str(session.value)
    else:
        identity += ':' + os.environ.get('XDG_SESSION_ID', os.environ.get('DISPLAY', 'desktop'))
    # Bundle smoke tests must not send their sample PDF into the user's desktop.
    if QApplication.platformName() == 'offscreen':
        identity += ':offscreen'
    suffix = hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]
    return Path(tempfile.gettempdir()) / ('APE-PDF-Studio-IPC-' + suffix)


def allow_foreground(pid):
    if sys.platform == 'win32' and type(pid) is int and pid > 0:
        import ctypes
        from ctypes import wintypes
        user = ctypes.WinDLL('user32', use_last_error=True)
        user.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
        user.AllowSetForegroundWindow(pid)


class _Listener(QThread):
    available = Signal()

    def __init__(self, name, requests, gate, parent):
        super().__init__(parent)
        self.name, self.requests, self.gate = name, requests, gate
        self.ready = threading.Event()
        self.accepting = True
        self.error = ''

    def run(self):
        import shiboken6
        service = _PipeServer(self)
        if not service.server.listen(self.name):
            self.error = service.server.errorString()
            self.ready.set()
            shiboken6.delete(service)
            return
        self.ready.set()
        try:
            self.exec()
        finally:
            service.stop()
            shiboken6.delete(service)


class _PipeServer(QObject):
    def __init__(self, listener):
        super().__init__()
        self.listener = listener
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.setMaxPendingConnections(MAX_REQUESTS)
        self.server.newConnection.connect(self._accept)
        self.connections = set()

    @Slot()
    def _accept(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if len(self.connections) >= 16:
                socket.abort()
                socket.deleteLater()
                continue
            connection = _Connection(socket, self)
            self.connections.add(connection)
            connection.closed.connect(self._remove)
            connection.read()

    @Slot(object)
    def _remove(self, connection):
        self.connections.discard(connection)

    def stop(self):
        self.server.close()
        for connection in list(self.connections):
            connection.socket.abort()


class _Connection(QObject):
    closed = Signal(object)

    def __init__(self, socket, service):
        super().__init__(service)
        self.socket, self.service = socket, service
        self.data, self.size, self.done = bytearray(), None, False
        socket.setParent(self)
        socket.setReadBufferSize(MAX_MESSAGE + HEADER.size)
        socket.readyRead.connect(self.read)
        socket.disconnected.connect(self._close)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._abort)
        self.timer.start(2000)

    @Slot()
    def _abort(self):
        self.socket.abort()

    @Slot()
    def _close(self):
        self.timer.stop()
        self.closed.emit(self)
        self.deleteLater()

    @Slot()
    def read(self):
        if self.done:
            return
        self.data.extend(bytes(self.socket.read(MAX_MESSAGE + HEADER.size + 1 - len(self.data))))
        if self.size is None and len(self.data) >= HEADER.size:
            self.size, = HEADER.unpack(self.data[:HEADER.size])
            if not 0 < self.size <= MAX_MESSAGE:
                self.socket.abort()
                return
        if self.size is None or len(self.data) < HEADER.size + self.size:
            return
        self.done = True
        if len(self.data) != HEADER.size + self.size:
            self.socket.abort()
            return
        try:
            paths = decode_request(self.data[HEADER.size:])
            listener = self.service.listener
            with listener.gate:
                if not listener.accepting:
                    self.socket.abort()
                    return
                listener.requests.put_nowait(paths)
        except (ValueError, TypeError, RecursionError, UnicodeError, queue.Full):
            self.socket.abort()
            return
        self.socket.write(ACK)
        self.socket.disconnectFromServer()
        listener.available.emit()


class SingleInstance(QObject):
    def __init__(self, parent=None, *, directory=None):
        super().__init__(parent)
        self.directory = Path(directory) if directory is not None else instance_directory()
        self.directory.mkdir(parents=True, exist_ok=True)
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise InstanceError('Invalid instance directory')
        private_acl(self.directory)
        suffix = hashlib.sha256(str(self.directory).encode('utf-8')).hexdigest()[:24]
        self.name = ('APE-PDF-Studio-' + suffix if os.name == 'nt' else str(self.directory / 'open.sock'))
        self.lock = QLockFile(str(self.directory / 'instance.lock'))
        # A live instance remains the owner however long it has been running.
        self.lock.setStaleLockTime(0)
        self.requests = queue.Queue(MAX_REQUESTS)
        self.gate = threading.Lock()
        self.listener = None
        self.window = None
        self._paths = deque()
        self._busy = False
        self._closed = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._drain)

    def start(self, paths):
        """True owns the UI; False means the existing UI acknowledged the request."""
        # Multiple selected files can exceed a single packet; send bounded batches.
        batches = []
        batch = []
        for path in paths:
            if len(batch) == MAX_FILES:
                batches.append(batch)
                batch = []
            try:
                encode_request(batch + [path])
            except InstanceError:
                if not batch:
                    raise
                batches.append(batch)
                batch = []
                encode_request([path])
            batch.append(path)
        batches.append(batch)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.lock.tryLock(0):
                # Only the lock owner may remove a stale Unix socket after a crash.
                QLocalServer.removeServer(self.name)
                self.listener = _Listener(self.name, self.requests, self.gate, self)
                self.listener.available.connect(self._schedule)
                self.listener.start()
                if not self.listener.ready.wait(3) or self.listener.error:
                    self.close()
                    raise InstanceError('Cannot start the local opening service')
                return True
            if self.lock.error() != QLockFile.LockError.LockFailedError:
                raise InstanceError('Cannot acquire the application instance lock')
            info = self.lock.getLockInfo()
            if info[0] > 0:
                allow_foreground(info[0])
            if self._forward(batches[0]):
                for batch in batches[1:]:
                    if not self._forward(batch):
                        raise InstanceError('Some opening requests could not be delivered')
                return False
            # Also handles the owner starting up or shutting down concurrently.
            QThread.msleep(75)
        raise InstanceError('The running application did not accept the opening request')

    def _forward(self, paths):
        socket = QLocalSocket()
        try:
            socket.connectToServer(self.name)
            if not socket.waitForConnected(250):
                return False
            socket.write(encode_request(paths))
            deadline = time.monotonic() + 3
            while socket.bytesToWrite() and time.monotonic() < deadline:
                socket.waitForBytesWritten(50)
                # A write notification can cover only one pipe buffer. Yield
                # between chunks rather than treating a partial write as failure.
                time.sleep(.001)
            if socket.bytesToWrite():
                return False
            response = bytearray()
            while len(response) < len(ACK) and time.monotonic() < deadline:
                response.extend(bytes(socket.read(len(ACK) - len(response))))
                if len(response) == len(ACK):
                    return response == ACK
                if not socket.waitForReadyRead(50) and socket.state() == QLocalSocket.LocalSocketState.UnconnectedState:
                    response.extend(bytes(socket.read(len(ACK) - len(response))))
                    return response == ACK
                time.sleep(.001)
            return False
        finally:
            socket.abort()

    def attach(self, window):
        self.window = window
        window.close_guard = self.prepare_close
        QApplication.instance().installEventFilter(self)
        self._schedule()

    def eventFilter(self, watched, event):
        # Finder delivers document opens as Qt events rather than argv on macOS.
        if not self._closed and event.type() == QEvent.Type.FileOpen:
            paths = pdf_arguments([event.file()])
            if paths:
                with self.gate:
                    if not self.listener or not self.listener.accepting:
                        return False
                    try:
                        self.requests.put_nowait(paths)
                    except queue.Full:
                        return False
                self._schedule()
                return True
        return False

    def open_initial(self, paths):
        self._busy = True
        try:
            for path in paths:
                self.window.open_pdf(Path(path))
        finally:
            self._busy = False
            self._schedule()

    @Slot()
    def _schedule(self):
        if not self._closed:
            if (self.window is not None and not self.requests.empty() and
                    QApplication.activeModalWidget() is not None):
                self._activate()
            self.timer.start(0)

    def _activate(self):
        if self.window.isMinimized():
            self.window.setWindowState(self.window.windowState() & ~Qt.WindowState.WindowMinimized)
        self.window.show()
        target = QApplication.activeModalWidget() or self.window
        target.raise_()
        target.activateWindow()

    @Slot()
    def _drain(self):
        if self._closed or self.window is None:
            return
        if self._busy or QApplication.activeModalWidget() is not None:
            self.timer.start(100)
            return
        if not self._paths:
            try:
                self._paths.extend(self.requests.get_nowait())
            except queue.Empty:
                return
            self._activate()
        if self._paths:
            path = self._paths.popleft()
            self._busy = True
            try:
                self.window.open_pdf(Path(path))
            finally:
                self._busy = False
        if self._paths or not self.requests.empty():
            self.timer.start(0)

    def prepare_close(self):
        # Do not acknowledge a request and then discard it during window closing.
        with self.gate:
            if self._busy or self._paths or not self.requests.empty():
                self._schedule()
                return False
            if self.listener:
                self.listener.accepting = False
                self.listener.requestInterruption()
                self.listener.quit()
        return True

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.timer.stop()
        if self.window is not None:
            QApplication.instance().removeEventFilter(self)
        if self.listener:
            with self.gate:
                self.listener.accepting = False
            self.listener.requestInterruption()
            self.listener.quit()
            self.listener.wait()
            self.listener = None
        self.lock.unlock()
