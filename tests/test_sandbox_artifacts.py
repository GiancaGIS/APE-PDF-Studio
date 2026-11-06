"""Hostile paths and real interruption must not change trusted data or revisions."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import struct
from types import SimpleNamespace

import pytest
from pypdf import PdfReader, PdfWriter

from ape_pdf_studio.core.artifacts import open_artifact, read_artifact
from ape_pdf_studio.core.broker_editor import BrokerEditor
from ape_pdf_studio.core import rpc_codec
from ape_pdf_studio.core.document_info import DocumentInfo
from ape_pdf_studio.core.errors import PDFOperationError
from ape_pdf_studio.core.private_work import PrivateDirectory, tree_bytes
from ape_pdf_studio.core.sandbox import sandbox_command
from ape_pdf_studio.core.security import SecurityReport
from test_sandbox_boundary import boundary, ready, running, assert_exited


def junction(link, target):
    """Create a directory junction without requiring symbolic-link privileges."""
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
        "$ErrorActionPreference='Stop'; New-Item -ItemType Junction -Path $env:APE_TEST_LINK -Target $env:APE_TEST_TARGET | Out-Null"],
        env={**os.environ, "APE_TEST_LINK": str(link), "APE_TEST_TARGET": str(target)},
        capture_output=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0, result.stderr
    assert link.is_junction()


def dacl(path):
    import ctypes
    from ctypes import wintypes as w
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    adv.GetFileSecurityW.argtypes = [w.LPCWSTR, w.DWORD, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)]
    needed = w.DWORD()
    adv.GetFileSecurityW(str(path), 4, None, 0, ctypes.byref(needed))
    assert needed.value
    descriptor = ctypes.create_string_buffer(needed.value)
    assert adv.GetFileSecurityW(str(path), 4, descriptor, needed, ctypes.byref(needed))
    return descriptor.raw


@pytest.mark.parametrize("contents,limit,accepted", ((b"", 4, False), (b"1234", 4, True), (b"12345", 4, False)))
def test_empty_and_boundary_sized_artifacts(tmp_path, contents, limit, accepted):
    path = tmp_path / "artifact.bin"
    path.write_bytes(contents)
    if accepted:
        assert read_artifact(path, tmp_path, limit) == contents
    else:
        with pytest.raises(OSError):
            read_artifact(path, tmp_path, limit)
    assert path.read_bytes() == contents


def test_hardlinks_inside_workspace_are_also_rejected(tmp_path):
    first, second = tmp_path / "first.bin", tmp_path / "second.bin"
    first.write_bytes(b"synthetic result")
    second.hardlink_to(first)
    for path in (first, second):
        with pytest.raises(OSError):
            read_artifact(path, tmp_path, 1024)


@pytest.mark.skipif(os.name != "nt", reason="Windows file share mode")
def test_verified_open_handle_prevents_concurrent_replacement_and_writes(tmp_path):
    artifact = tmp_path / "result.bin"
    replacement = tmp_path / "replacement.bin"
    artifact.write_bytes(b"accepted data")
    replacement.write_bytes(b"different data")
    with open_artifact(artifact, tmp_path, 1024) as stream:
        with pytest.raises(PermissionError):
            artifact.write_bytes(b"tampered data")
        with pytest.raises(PermissionError):
            os.replace(replacement, artifact)
        assert stream.read() == b"accepted data"
    assert artifact.read_bytes() == b"accepted data"


@pytest.mark.skipif(os.name != "nt", reason="Windows junctions")
def test_junction_ancestor_cannot_redirect_artifact_reads(tmp_path):
    root, foreign = tmp_path / "worker", tmp_path / "foreign"
    root.mkdir(); foreign.mkdir()
    secret = foreign / "result.bin"
    secret.write_bytes(b"synthetic outside data")
    link = root / "linked"
    junction(link, foreign)
    try:
        with pytest.raises(OSError):
            read_artifact(link / secret.name, root, 1024)
        with pytest.raises(OSError, match="Reparse"):
            tree_bytes(root)
    finally:
        link.rmdir()  # Remove only the junction, never recurse into the target.
    assert secret.read_bytes() == b"synthetic outside data"


@pytest.mark.skipif(os.name != "nt", reason="Windows junctions")
def test_replaced_workspace_root_does_not_redefine_trusted_boundary(tmp_path):
    root, backup, foreign = tmp_path / "worker", tmp_path / "backup", tmp_path / "foreign"
    root.mkdir(); foreign.mkdir()
    (root / "result.bin").write_bytes(b"accepted result")
    (foreign / "result.bin").write_bytes(b"outside result")
    root.rename(backup)
    junction(root, foreign)
    try:
        with pytest.raises(OSError):
            read_artifact(root / "result.bin", root, 1024)
    finally:
        root.rmdir()
        backup.rename(root)
    assert read_artifact(root / "result.bin", root, 1024) == b"accepted result"
    assert (foreign / "result.bin").read_bytes() == b"outside result"


def test_real_broker_recovers_after_launcher_is_force_killed(tmp_path):
    source = tmp_path / "original.pdf"
    writer = PdfWriter(); writer.add_blank_page(200, 300); writer.write(source)
    original = hashlib.sha256(source.read_bytes()).digest()
    editor = BrokerEditor(source)
    try:
        editor.rotate(0, 90)
        revision = editor.path
        pid = editor._process.pid
        editor._process.kill(); editor._process.wait(timeout=5)
        assert editor.metadata().page_count == 1
        assert editor._process.pid != pid and editor.path == revision and editor.dirty
        saved = editor.save_as(tmp_path / "recovered.pdf")
        with PdfReader(saved) as reader:
            assert reader.pages[0].rotation == 90
        assert editor.undo() and not editor.can_undo and editor.can_redo
    finally:
        editor.close()
    assert hashlib.sha256(source.read_bytes()).digest() == original


@pytest.mark.skipif(os.name != "nt", reason="Native Windows launcher preflight")
@pytest.mark.parametrize("kind", ("junction", "hardlink"))
def test_launcher_rejects_linked_workspace_before_granting_access(boundary, tmp_path, kind):
    with PrivateDirectory() as work, PrivateDirectory() as foreign:
        folder = Path(work)
        secret = Path(foreign) / "private.bin"
        secret.write_bytes(b"private synthetic data")
        linked = folder / "linked"
        if kind == "junction":
            junction(linked, Path(foreign))
        else:
            linked.hardlink_to(secret)
        original_acl = dacl(secret)
        try:
            result = boundary.run(folder, "child")
            assert result.returncode == 129, (result.returncode, result.stdout, result.stderr)
            assert result.stdout == b""
            assert not (folder / "child-created.txt").exists(), "Worker ran before the unsafe workspace was rejected"
            assert dacl(secret) == original_acl, "Preflight changed the outside file's permissions"
        finally:
            if kind == "junction":
                linked.rmdir()
            else:
                linked.unlink()
        assert secret.read_bytes() == b"private synthetic data"


@pytest.mark.skipif(os.name != "nt", reason="Native Windows root junction validation")
def test_launcher_rejects_a_junction_as_workspace_root(boundary, tmp_path):
    with PrivateDirectory() as foreign:
        secret = Path(foreign) / "private.bin"
        secret.write_bytes(b"private synthetic data")
        original_acl = dacl(secret)
        alias = tmp_path / "workspace-alias"
        junction(alias, Path(foreign))
        try:
            assert boundary.run(alias, "child").returncode == 129
            assert not (Path(foreign) / "child-created.txt").exists()
            assert dacl(secret) == original_acl
        finally:
            alias.rmdir()


@pytest.mark.skipif(os.name != "nt", reason="Real Windows workspace watchdog")
@pytest.mark.parametrize("kind", ("junction", "hardlink"))
def test_watchdog_stops_links_added_after_worker_start(boundary, kind):
    with PrivateDirectory() as work, PrivateDirectory() as foreign:
        secret = Path(foreign) / "private.bin"
        secret.write_bytes(b"synthetic outside data")
        original_acl = dacl(secret)
        linked = Path(work) / "late-link"
        try:
            with running(boundary.command(work, "hang", timeout=15000)) as process:
                pid = ready(process)["pid"]
                if kind == "junction":
                    junction(linked, Path(foreign))
                else:
                    linked.hardlink_to(secret)
                process.communicate(timeout=8)
                assert process.returncode == 1
                assert_exited(pid)
                assert dacl(secret) == original_acl
        finally:
            if linked.is_junction():
                linked.rmdir()
            elif linked.exists():
                linked.unlink()
        assert secret.read_bytes() == b"synthetic outside data"


@pytest.mark.parametrize("kind", ("zero", "oversized", "invalid_utf8", "unknown_record", "deep", "nonfinite"))
def test_real_rpc_worker_rejects_invalid_frames_without_an_extra_reply(kind):
    payloads = {"invalid_utf8": b"\xff", "unknown_record": b'{"$type":"__import__","fields":{}}',
                "deep": b"[" * 70 + b"0" + b"]" * 70, "nonfinite": b"NaN"}
    if kind in ("zero", "oversized"):
        frame = struct.pack("<I", 0 if kind == "zero" else rpc_codec.MAX_PACKET + 1)
    else:
        payload = payloads[kind]
        frame = struct.pack("<I", len(payload)) + payload
    with PrivateDirectory() as work:
        source = Path(work) / "synthetic-control.pdf"
        writer = PdfWriter(); writer.add_blank_page(100, 100); writer.write(source)
        original = source.read_bytes()
        control = rpc_codec.dumps(dict(method="open", source=source))
        result = subprocess.run(sandbox_command("rpc", work, timeout=5000, output_limit=65536),
            input=control + frame, capture_output=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        assert result.returncode == 1, (result.returncode, result.stdout, result.stderr)
        replies = io.BytesIO(result.stdout)
        assert rpc_codec.read(replies)["ok"] is True  # Same worker accepted the valid control.
        assert replies.read() == b"" and source.read_bytes() == original


@pytest.fixture
def reply_fixture(tmp_path, monkeypatch):
    root = tmp_path / "worker"
    root.mkdir()
    revision = root / "revision.pdf"
    revision.write_bytes(b"Synthetic opaque revision; not parsed by the broker")
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"Synthetic outside canary")
    state = dict(revisions=[revision], index=0, encrypted=False,
                 document_info=DocumentInfo(), security_report=SecurityReport((), True))
    editor = BrokerEditor.__new__(BrokerEditor)
    editor._root = root
    editor._state = state
    stopped = []
    monkeypatch.setattr(editor, "_stop", lambda: stopped.append(True))
    def respond(response):
        # Intentionally permit NaN in hostile wire bytes, unlike rpc_codec.dumps.
        payload = json.dumps(rpc_codec.encode(response)).encode()
        editor._process = SimpleNamespace(stdin=io.BytesIO(),
            stdout=io.BytesIO(struct.pack("<I", len(payload)) + payload), pid=123,
            poll=lambda: 37, kill=lambda: None)
    return SimpleNamespace(editor=editor, state=state, stopped=stopped, respond=respond, outside=outside)


def test_valid_reply_control_is_accepted(reply_fixture):
    fixture = reply_fixture
    fixture.respond(dict(ok=True, state=fixture.state, value=[[0, 0, 10, 10]]))
    assert fixture.editor._exchange(dict(method="text_boxes")) == [[0, 0, 10, 10]]
    assert not fixture.stopped


@pytest.mark.parametrize("attack", ("outside_path", "boolean_index", "invalid_flag", "extra_state",
                                    "unknown_finding", "nonfinite", "overflow", "zero_area", "invalid_note"))
def test_hostile_worker_reply_keeps_last_accepted_state(reply_fixture, attack):
    fixture = reply_fixture
    state = dict(fixture.state)
    value, method = [[0, 0, 10, 10]], "text_boxes"
    if attack == "outside_path":
        state["revisions"] = [fixture.outside]
    elif attack == "boolean_index":
        state["index"] = False
    elif attack == "invalid_flag":
        state["encrypted"] = "false"
    elif attack == "extra_state":
        state["unexpected"] = "value"
    elif attack == "unknown_finding":
        state["security_report"] = SecurityReport(("unknown finding",), True)
    elif attack == "nonfinite":
        value = [[0, 0, float("nan"), 10]]
    elif attack == "overflow":
        value = [[0, 0, 1e20, 10]]
    elif attack == "zero_area":
        value = [[0, 0, 0, 10]]
    elif attack == "invalid_note":
        method, value = "notes", {0: [dict(index=0, rect=[0, 0, 10, 10], text=["invalid text"])]}
    fixture.respond(dict(ok=True, state=state, value=value))
    with pytest.raises(PDFOperationError, match="risposta non valida"):
        fixture.editor._exchange(dict(method=method))
    assert fixture.editor._state is fixture.state and fixture.stopped == [True]
    assert fixture.outside.read_bytes() == b"Synthetic outside canary"
