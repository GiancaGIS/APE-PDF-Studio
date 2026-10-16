"""Security boundaries: hostile IPC/artifacts, provenance, quotas and recovery."""
import io
import json
import os
from pathlib import Path
import struct

import pytest
from pypdf import PdfReader,PdfWriter

from ape_pdf_studio.core import rpc_codec
from ape_pdf_studio.core.artifacts import read_artifact
from ape_pdf_studio.core.broker_editor import BrokerEditor
from ape_pdf_studio.core.errors import PDFOperationError
from ape_pdf_studio.core.private_work import PrivateDirectory,cleanup_stale,tree_bytes,check_budget
from ape_pdf_studio.core.provenance import read_zone
from ape_pdf_studio.core.vector_protocol import validate
from ape_pdf_studio.core.worker_protocol import Messages,ProtocolError


@pytest.fixture
def pdf(tmp_path):
    path=tmp_path/'sample.pdf'
    w=PdfWriter();w.add_blank_page(100,200);w.write(path)
    return path


@pytest.mark.parametrize('payload',[
    {'$type':'__import__','fields':{}},
    {'$type':'SecurityReport','fields':{'findings':{'$tuple':['unknown']},'memory_limited':True}},
    {'$type':'SecurityReport','fields':{'findings':{'$tuple':[]},'memory_limited':'yes'}},
    {'$map':[['duplicate',1],['duplicate',2]]},
    float('inf'),
])
def test_untrusted_ipc_rejects_unknown_types_and_invalid_domains(payload):
    with pytest.raises((ValueError,TypeError)):rpc_codec.decode(payload)


def test_frame_length_is_checked_before_reading_payload():
    with pytest.raises(ValueError):rpc_codec.read(io.BytesIO(struct.pack('<I',rpc_codec.MAX_PACKET+1)))
    with pytest.raises(EOFError):rpc_codec.read(io.BytesIO(b'\x08\0\0\0{}'))


def test_artifact_handle_rejects_escape_and_hardlink(tmp_path):
    root=tmp_path/'worker';root.mkdir()
    outside=tmp_path/'private.txt';outside.write_bytes(b'synthetic secret')
    with pytest.raises(OSError):read_artifact(outside,root,1024)
    linked=root/'result.bin';linked.hardlink_to(outside)
    with pytest.raises(OSError):read_artifact(linked,root,1024)
    linked.unlink();linked.write_bytes(b'correct result')
    assert read_artifact(linked,root,1024)==b'correct result'
    with pytest.raises(OSError):read_artifact(linked,root,2)


def test_drawing_rejects_nonfinite_or_composed_overflow():
    drawing={'version':1,'size':[100,100],'layers':[{'clips':[{'rule':'nonzero','commands':[['M',[0,0]],['L',[1,1]]],
        'transforms':[[1e7,0,0,1e7,0,0],[1e7,0,0,1e7,0,0]]}],'draws':[]}]}
    with pytest.raises(ValueError):validate(drawing)
    drawing['layers'][0]['clips'][0]['transforms']=[]
    drawing['layers'][0]['clips'][0]['commands'][1][1][0]=float('nan')
    with pytest.raises(ValueError):validate(drawing)


def test_progress_protocol_is_bounded_and_schema_checked():
    parser=Messages()
    with pytest.raises(ProtocolError):list(parser.feed(b'{"progress":true,"total":1,"label":"bad"}\n'))
    with pytest.raises(ProtocolError):list(Messages().feed(b'x'*65537))


def test_private_quota_and_cleanup_preserve_live_leases(monkeypatch):
    import ape_pdf_studio.core.private_work as module
    with PrivateDirectory() as folder:
        path=Path(folder);(path/'bounded.bin').write_bytes(b'x'*16)
        cleanup_stale(max_age=-1)
        assert path.exists()
        monkeypatch.setattr(module,'MAX_DOCUMENT_BYTES',8)
        with pytest.raises(OSError):check_budget(path)
    assert not path.exists()


def test_broker_recovers_unsaved_revision_and_tracks_save_identity(pdf,tmp_path):
    editor=BrokerEditor(pdf)
    try:
        editor.rotate(0,90)
        saved=editor.save_as(tmp_path/'saved.pdf')
        assert not editor.dirty
        editor.undo();editor.rotate(0,180)
        assert editor.dirty
        revision=editor.path
        editor._stop()  # Simulate abrupt loss of the complete sandbox.
        assert editor.metadata().page_count==1 and editor.path==revision and editor.dirty
        editor.save_as(tmp_path/'recovered.pdf')
        with PdfReader(tmp_path/'recovered.pdf') as reader:assert reader.pages[0].rotation==180
        with PdfReader(saved) as reader:assert reader.pages[0].rotation==90
        editor.undo();assert editor.can_redo
    finally:editor.close()


def test_recovery_keeps_selected_revision_after_lost_branch_acknowledgement(pdf):
    editor=BrokerEditor(pdf)
    try:
        editor.rotate(0,90);editor.undo()
        accepted=editor._state
        editor.rotate(0,180)  # Deletes the discarded redo file.
        editor._state=accepted  # Simulate a crash before acknowledging that edit.
        editor._stop()
        assert editor.metadata().page_count==1
        assert editor.path==accepted['revisions'][0] and not editor.can_redo
    finally:editor.close()


@pytest.mark.skipif(os.name!='nt',reason='Windows ADS provenance')
def test_motw_survives_merge_extract_and_broker_save(pdf,tmp_path):
    incoming=tmp_path/'internet.pdf';incoming.write_bytes(pdf.read_bytes())
    Path(str(incoming)+':Zone.Identifier').write_text('[ZoneTransfer]\nZoneId=3\nHostUrl=https://private.invalid/document\n')
    editor=BrokerEditor(pdf)
    try:
        editor.insert_pdf(incoming,0)
        output=editor.save_as(tmp_path/'merged.pdf')
        extracted=editor.extract([0],tmp_path/'extracted.pdf')
        assert read_zone(output)==read_zone(extracted)==3
        assert 'HostUrl' not in Path(str(output)+':Zone.Identifier').read_text()
    finally:editor.close()
