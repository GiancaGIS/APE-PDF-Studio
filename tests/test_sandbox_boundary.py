"""Real Windows boundaries, using only synthetic files, keys and loopback peers."""
from contextlib import contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from ape_pdf_studio.core.private_work import PrivateDirectory, cleanup_profiles

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Real Windows AppContainer and Job Objects")
ROOT = Path(__file__).resolve().parents[1]
HIDDEN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@pytest.fixture(scope="module")
def boundary(tmp_path_factory):
    native = ROOT / "build/windows/sandbox"
    host = native / "APEPDFSandbox.exe"
    original = native / "APEPDFSandboxBoundaryProbe.exe"
    assert host.is_file() and original.is_file(), "Run scripts/build_windows_sandbox.ps1 first"
    code = tmp_path_factory.mktemp("boundary-code")
    probe = code / original.name
    shutil.copyfile(original, probe)
    sentinel = code / "runtime-sentinel.bin"
    sentinel.write_bytes(b"Synthetic runtime: must remain readable and immutable")
    before = {path: hashlib.sha256(path.read_bytes()).digest() for path in (probe, sentinel)}

    def command(work, mode, *args, timeout=5000, budget=65536):
        return [str(host), str(work), str(timeout), str(budget), "1", str(code), str(probe), mode, *map(str, args)]

    def run(work, mode, *args, timeout=5000, budget=65536, **kwargs):
        return subprocess.run(command(work, mode, *args, timeout=timeout, budget=budget),
            input=b"", capture_output=True, timeout=min(timeout / 1000 + 8, 30), creationflags=HIDDEN, **kwargs)

    yield SimpleNamespace(host=host, code=code, probe=probe, sentinel=sentinel, command=command, run=run)
    for path, digest in before.items():
        assert hashlib.sha256(path.read_bytes()).digest() == digest


def record(result):
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def ready(process):
    received = queue.Queue()
    threading.Thread(target=lambda: received.put(process.stdout.readline()), daemon=True).start()
    try:
        line = received.get(timeout=8)
    except queue.Empty:
        pytest.fail("Native test worker did not reach its ready checkpoint")
    assert line, (process.poll(), process.stderr.read() if process.poll() is not None else b"")
    return json.loads(line)


@contextmanager
def running(command, **kwargs):
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, creationflags=HIDDEN, **kwargs)
    try:
        yield process
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=8)


def assert_exited(pid):
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        assert ctypes.get_last_error() == 87  # Process no longer exists, not access denied.
        return
    try:
        assert kernel.WaitForSingleObject(handle, 5000) == 0, "Sandbox worker survived termination"
    finally:
        kernel.CloseHandle(handle)


def test_actual_token_job_and_mitigations(boundary, record_testsuite_property):
    with PrivateDirectory() as work:
        data = record(boundary.run(work, "info"))
    record_testsuite_property("sandbox_token", json.dumps(data, sort_keys=True))
    assert data["appcontainer"] == 1 and data["capabilities"] == 0
    # UAC elevation and AppContainer confinement are orthogonal. An elevated
    # Windows CI host can leave TokenElevation set on a low-integrity LowBox
    # token. Check actual restrictions, including privileges even if disabled.
    assert data["integrity"] <= 0x1000, data
    assert data["ui_access"] == 0 and data["mandatory_policy"] & 1, data  # No-write-up.
    assert set(data["privileges"]) <= {"SeChangeNotifyPrivilege", "SeIncreaseWorkingSetPrivilege"}, data
    assert data["in_job"] == 1 and data["active_process_limit"] == 1
    assert data["job_memory"] == data["process_memory"] == 768 * 1024 * 1024
    flags = 0x2000 | 0x100 | 0x200 | 0x8 | 0x4  # Kill-on-close, process/job RAM, active count, CPU time.
    assert data["job_flags"] & flags == flags
    assert data["cpu_ticks"] == 5000 * 10000
    assert data["dep"] == data["extension_points_disabled"] == 1
    assert data["sid"].startswith("S-1-15-2-")


def test_private_files_and_runtime_are_separate_capabilities(boundary):
    with PrivateDirectory() as work, PrivateDirectory() as foreign:
        secret = Path(foreign) / "synthetic-secret.txt"
        secret.write_bytes(b"Synthetic private document")
        assert secret.read_bytes() and boundary.sentinel.read_bytes()  # Host-side positive controls.
        data = record(boundary.run(work, "files", secret, boundary.sentinel))
        assert data == dict(workspace_write=1, foreign_read=0, foreign_write=0, code_read=1, code_write=0)
        assert (Path(work) / "created.txt").exists()
        assert secret.read_bytes() == b"Synthetic private document"


def test_concurrent_document_workers_cannot_read_each_other(boundary):
    with PrivateDirectory() as left, PrivateDirectory() as right:
        left_secret, right_secret = Path(left) / "private.txt", Path(right) / "private.txt"
        left_secret.write_bytes(b"left synthetic document")
        right_secret.write_bytes(b"right synthetic document")
        with running(boundary.command(left, "cross", right_secret, boundary.sentinel, timeout=15000)) as first, \
             running(boundary.command(right, "cross", left_secret, boundary.sentinel, timeout=15000)) as second:
            first_info, second_info = ready(first), ready(second)
            assert first_info["sid"] != second_info["sid"]
            for process in (first, second):
                process.stdin.write(b"go\n"); process.stdin.flush()
            for process in (first, second):
                output, error = process.communicate(timeout=8)
                assert process.returncode == 0, error
                data = json.loads(output)
                assert data["workspace_write"] == data["code_read"] == 1
                assert data["foreign_read"] == data["foreign_write"] == data["code_write"] == 0


@pytest.mark.parametrize("language", ("it", "en", "es", "fr", "de", "fr-extra", "x" * 100))
def test_secrets_and_import_overrides_are_not_inherited(boundary, language):
    names = ("GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY", "PYTHONPATH", "PYTHONHOME", "QT_PLUGIN_PATH",
             "HTTP_PROXY", "SSLKEYLOGFILE", "APE_TEST_SECRET")
    environment = {**os.environ, **dict.fromkeys(names, "synthetic-canary-value"),
                   "QT_QPA_PLATFORM": "invalid-test-platform", "APE_UI_LANGUAGE": language}
    with PrivateDirectory() as work:
        data = record(boundary.run(work, "environment", env=environment))
    assert all(data[name] == 0 for name in names)
    assert data["APE_UI_LANGUAGE"] == int(language in ("it", "en", "es", "fr"))
    assert data["offscreen"] == data["workspace_matches"] == 1


def test_native_command_preserves_unicode_quotes_and_trailing_slashes(boundary):
    arguments = ["", "two words", 'a"quoted"value', "C:\\path with space\\", "中文と日本語", "&|<>$()`%!"]
    with PrivateDirectory() as work:
        assert record(boundary.run(work, "arguments", *arguments)) == arguments


@pytest.mark.parametrize("family", (socket.AF_INET, socket.AF_INET6), ids=("ipv4", "ipv6"))
@pytest.mark.parametrize("transport", ("tcp", "udp"))
def test_live_loopback_network_cannot_reach_listener(boundary, family, transport):
    address = "127.0.0.1" if family == socket.AF_INET else "::1"
    kind = socket.SOCK_STREAM if transport == "tcp" else socket.SOCK_DGRAM
    with socket.socket(family, kind) as listener:
        try:
            listener.bind((address, 0))
        except OSError:
            if family == socket.AF_INET6:
                pytest.skip("IPv6 loopback is unavailable on this test host")
            raise
        listener.settimeout(2)
        if transport == "tcp":
            listener.listen()
            with socket.create_connection(listener.getsockname()[:2], timeout=2):
                accepted, _ = listener.accept(); accepted.close()
        else:
            with socket.socket(family, kind) as control:
                control.sendto(b"positive-control", listener.getsockname())
                assert listener.recvfrom(1024)[0] == b"positive-control"
        native_control = subprocess.run([str(boundary.probe), "network", "6" if family == socket.AF_INET6 else "4",
            transport, str(listener.getsockname()[1])], capture_output=True, timeout=8, creationflags=HIDDEN)
        assert record(native_control)["error"] == 0
        if transport == "tcp":
            accepted, _ = listener.accept(); accepted.close()
        else:
            assert listener.recvfrom(1024)[0] == b"synthetic"
        with PrivateDirectory() as work:
            data = record(boundary.run(work, "network", 6 if family == socket.AF_INET6 else 4,
                                       transport, listener.getsockname()[1]))
        # WFP may reject explicitly or silently drop; require a live peer and
        # a successful identical native control, not just an arbitrary error.
        assert data["error"] in ((10013, 10035) if transport == "tcp" else (0, 10013))
        listener.settimeout(.15)
        with pytest.raises(TimeoutError):
            if transport == "tcp":
                accepted, _ = listener.accept()
                accepted.close()
            else:
                listener.recvfrom(1024)


@pytest.mark.parametrize("variant", ("normal", "breakaway"))
def test_child_process_creation_is_blocked_even_for_granted_executable(boundary, tmp_path, variant):
    # A control with this same executable distinguishes the Job limit from a bad path.
    control = subprocess.run([str(boundary.probe), "spawn", "normal"], cwd=tmp_path,
        capture_output=True, timeout=8, creationflags=HIDDEN)
    assert record(control)["launched"] == 1 and (tmp_path / "child-created.txt").exists()
    with PrivateDirectory() as work:
        data = record(boundary.run(work, "spawn", variant))
        assert data["launched"] == 0 and data["error"] != 0
        assert not (Path(work) / "child-created.txt").exists()


def test_worker_cannot_read_write_or_duplicate_host_process_memory(boundary):
    with PrivateDirectory() as work:
        data = record(boundary.run(work, "process", os.getpid()))
    assert data and all(value == 0 for value in data.values())


def test_host_registry_canary_cannot_be_opened(boundary):
    import winreg
    key_name = "Software\\APE-PDF-Studio-Sandbox-Test-" + uuid4().hex
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_name, access=winreg.KEY_ALL_ACCESS) as key:
        winreg.SetValueEx(key, "Canary", 0, winreg.REG_SZ, "synthetic registry canary")
    try:
        control = subprocess.run([str(boundary.probe), "registry", key_name], capture_output=True, timeout=8, creationflags=HIDDEN)
        assert record(control) == {"read": 1, "write": 1}
        with PrivateDirectory() as work:
            assert record(boundary.run(work, "registry", key_name)) == {"read": 0, "write": 0}
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_name) as key:
            assert winreg.QueryValueEx(key, "Canary")[0] == "synthetic registry canary"
    finally:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_name)


def test_other_inheritable_handles_are_removed_at_worker_boundary(boundary, tmp_path):
    import msvcrt
    from ctypes import wintypes as w
    class FileInfo(ctypes.Structure):
        _fields_ = [("attributes", w.DWORD), ("created", w.FILETIME), ("accessed", w.FILETIME),
                    ("written", w.FILETIME), ("volume", w.DWORD), ("sizeHigh", w.DWORD), ("sizeLow", w.DWORD),
                    ("links", w.DWORD), ("indexHigh", w.DWORD), ("indexLow", w.DWORD)]
    canary = tmp_path / "private-handle-canary.bin"
    canary.write_bytes(b"Synthetic handle canary")
    with canary.open("rb") as stream:
        handle = msvcrt.get_osfhandle(stream.fileno())
        os.set_handle_inheritable(handle, True)
        try:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetFileInformationByHandle.argtypes = [w.HANDLE, ctypes.POINTER(FileInfo)]
            info = FileInfo()
            assert kernel.GetFileInformationByHandle(handle, ctypes.byref(info))
            arguments = [str(handle), str(info.volume), str(info.indexHigh), str(info.indexLow)]
            startup = subprocess.STARTUPINFO()
            startup.lpAttributeList = {"handle_list": [handle]}
            control = subprocess.run([str(boundary.probe), "handle", *arguments], capture_output=True,
                timeout=8, startupinfo=startup, close_fds=True, creationflags=HIDDEN)
            assert record(control) == {"canary_inherited": 1}
            with PrivateDirectory() as work:
                assert record(boundary.run(work, "handle", *arguments, startupinfo=startup, close_fds=True)) == {"canary_inherited": 0}
        finally:
            os.set_handle_inheritable(handle, False)


def test_real_committed_memory_quota(boundary):
    with PrivateDirectory() as work:
        assert record(boundary.run(work, "memory")) == {"small_allowed": 1, "large_denied": 1}


@pytest.mark.parametrize("mode", ("hang", "spin"))
def test_wall_clock_and_busy_workers_are_terminated(boundary, mode):
    with PrivateDirectory() as work:
        start = time.monotonic()
        result = boundary.run(work, mode, timeout=1000)
        # The OS CPU quota can win the race with the wall-clock watchdog.
        expected_statuses = (1, 1816) if mode == "spin" else (1,)
        assert result.returncode in expected_statuses, (result.returncode, result.stderr)
        pid = json.loads(result.stdout)["pid"]
        assert time.monotonic() - start < 7
        assert_exited(pid)
        assert not list(Path(work).parent.glob("." + Path(work).name + ".profile-*"))


def test_stdout_quota_kills_flood_but_zero_budget_keeps_rpc_semantics(boundary):
    with PrivateDirectory() as work:
        result = boundary.run(work, "flood", budget=1024)
        assert result.returncode == 128 and len(result.stdout) <= 1024
    with PrivateDirectory() as work:
        unlimited = boundary.run(work, "flood", budget=0)
        assert unlimited.returncode == 0 and unlimited.stdout == b"X" * (128 * 8192)


def test_disk_quota_stops_sparse_logical_growth_without_writing_a_gigabyte(boundary):
    with PrivateDirectory() as work:
        start = time.monotonic()
        result = boundary.run(work, "disk", timeout=12000)
        assert result.returncode == 1, (result.returncode, result.stderr)
        assert_exited(json.loads(result.stdout)["pid"])
        assert time.monotonic() - start < 8  # Must be the disk watchdog, not the 12 s wall deadline.
        sparse = Path(work) / "sparse-quota.bin"
        assert sparse.stat().st_size > 1024**3
        assert sparse.stat().st_file_attributes & 0x200  # FILE_ATTRIBUTE_SPARSE_FILE.
        # The next launch must fail in preflight, before granting another SID.
        assert boundary.run(work, "child").returncode == 129
        assert not (Path(work) / "child-created.txt").exists()


@pytest.mark.parametrize("mode", ("profile_disk", "split_disk"))
def test_profile_storage_and_combined_disk_quota_are_also_enforced(boundary, mode):
    with PrivateDirectory() as work:
        start = time.monotonic()
        result = boundary.run(work, mode, timeout=12000)
        assert result.returncode == 1, (result.returncode, result.stderr)
        data = json.loads(result.stdout)
        assert data["work_bytes"] + data["profile_bytes"] > 1024**3
        if mode == "split_disk":
            assert 0 < data["work_bytes"] < 1024**3 and 0 < data["profile_bytes"] < 1024**3
        assert time.monotonic() - start < 8
        assert_exited(data["pid"])


def test_killing_launcher_kills_its_worker_and_profile_can_be_cleaned(boundary):
    with PrivateDirectory() as work:
        with running(boundary.command(work, "hang", timeout=20000)) as process:
            pid = ready(process)["pid"]
            process.kill(); process.communicate(timeout=8)
            assert_exited(pid)
        cleanup_profiles(work)
        assert not list(Path(work).parent.glob("." + Path(work).name + ".profile-*"))


def test_loss_of_calling_process_terminates_host_and_worker(boundary):
    wrapper = """import json,os,subprocess,sys
child=subprocess.Popen(json.loads(sys.argv[1]),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
record=json.loads(child.stdout.readline());record['host_pid']=child.pid
print(json.dumps(record),flush=True)
sys.stdin.buffer.read(1)
os._exit(0)
"""
    with PrivateDirectory() as work:
        command = boundary.command(work, "hang", timeout=20000)
        with running([sys.executable, "-I", "-c", wrapper, json.dumps(command)]) as parent:
            ids = ready(parent)
            parent.communicate(b"x", timeout=8)
            assert parent.returncode == 0
            assert_exited(ids["pid"])
            assert_exited(ids["host_pid"])
        cleanup_profiles(work)


def test_abrupt_worker_exit_is_propagated_and_profile_removed(boundary):
    with PrivateDirectory() as work:
        result = boundary.run(work, "crash")
        assert result.returncode == 37
        assert not list(Path(work).parent.glob("." + Path(work).name + ".profile-*"))


@pytest.mark.parametrize("timeout", (0, 99, 3600001))
def test_invalid_native_timeout_never_runs_worker(boundary, timeout):
    with PrivateDirectory() as work:
        result = boundary.run(work, "child", timeout=timeout)
        assert result.returncode == 120 and not (Path(work) / "child-created.txt").exists()


def test_missing_executable_fails_without_launching_a_fallback(boundary):
    with PrivateDirectory() as work:
        command = boundary.command(work, "child")
        command[6] = str(Path(work) / "missing-test-worker.exe")
        result = subprocess.run(command, input=b"", capture_output=True, timeout=10, creationflags=HIDDEN)
        assert result.returncode == 126 and not (Path(work) / "child-created.txt").exists()
        assert not list(Path(work).parent.glob("." + Path(work).name + ".profile-*"))
