"""Detect and create standard fields while preserving original PDF structures."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, BooleanObject, DictionaryObject, NameObject, NumberObject, TextStringObject

from ape_pdf_studio.core.editor import PDFEditor
from ape_pdf_studio.core.errors import PDFOperationError
from ape_pdf_studio.core.form_detector import detect_fields
from ape_pdf_studio.core.worker_process import worker_command
from test_pdf_forms import form_pdf

spec = importlib.util.spec_from_file_location('generate_form_preparation_example', Path(__file__).resolve().parents[1]/'scripts/generate_form_preparation_example.py')
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


@pytest.fixture
def flat_forms(tmp_path):
    return generator.create_examples(tmp_path)


@pytest.mark.parametrize('scanned',(False,True))
def test_detection_finds_empty_boxes_line_and_checkbox_without_modifying_pdf(flat_forms,scanned):
    source = flat_forms[int(scanned)]
    original = source.read_bytes()
    result = detect_fields(source,[0],use_ocr=False)
    assert len(result['proposals']) == 5
    assert sum(p['kind']=='checkbox' for p in result['proposals']) == 1
    assert sum(p['multiline'] for p in result['proposals']) == 1
    if not scanned:
        assert {p['name'] for p in result['proposals']} == {'Nome_e_cognome','Email','Data','Accetto_le_condizioni','Note'}
    assert source.read_bytes() == original
    with PdfReader(source) as reader:
        assert not reader.get_fields()


def test_create_fill_save_undo_redo_and_existing_metadata(flat_forms,tmp_path):
    source = flat_forms[0]
    original = source.read_bytes()
    proposals = detect_fields(source,[0],use_ocr=False)['proposals']
    editor = PDFEditor(source)
    try:
        revision = editor.path
        editor.create_form_fields(proposals,revision)
        created = editor.path
        assert len(editor.interactions()[1]) == 5 and editor.dirty
        editor.fill_form({'Nome_e_cognome':'Giulia Bianchi','Accetto_le_condizioni':'/Yes','Note':'Line one\nLine two'},editor.path)
        output = editor.save_as(tmp_path/'prepared.pdf')
        with PdfReader(output) as reader:
            fields=reader.get_fields()
            assert fields['Nome_e_cognome']['/V']=='Giulia Bianchi'
            assert fields['Accetto_le_condizioni']['/V']=='/Yes'
            assert reader.metadata.title=='Esempio di preparazione modulo'
        assert editor.undo() and editor.path==created
        assert editor.undo() and editor.path==revision
        assert not editor.interactions()[1]
        assert editor.redo() and len(editor.interactions()[1])==5
        assert source.read_bytes()==original
    finally:
        editor.close()


@pytest.mark.parametrize('rotation',(0,90,180,270))
def test_coordinates_on_rotated_cropped_scaled_pages(flat_forms,tmp_path,rotation):
    writer=PdfWriter(clone_from=flat_forms[0])
    page=writer.pages[0]
    page.cropbox.lower_left=(20,20)
    page.cropbox.upper_right=(575,822)
    page.rotate(rotation)
    page[NameObject('/UserUnit')]=NumberObject(2)
    path=tmp_path/'rotated.pdf'
    writer.write(path)
    editor=PDFEditor(path)
    try:
        proposal=dict(page=0,rect=[40,80,180,110],kind='text',name='new_field',label='New',multiline=False)
        editor.create_form_fields([proposal],editor.path)
        field=editor.interactions()[1][0]
        assert field.widgets[0][1]==pytest.approx(proposal['rect'])
        editor.fill_form({'new_field':'test'},editor.path)
        assert editor.interactions()[1][0].value=='test'
    finally:
        editor.close()


def test_existing_forms_values_and_names_are_preserved(form_pdf):
    editor=PDFEditor(form_pdf)
    try:
        editor.fill_form({'nome':'Existing'},editor.path)
        revision=editor.path
        proposal=dict(page=0,rect=[380,720,550,740],kind='text',name='extra',label='Extra',multiline=False)
        editor.create_form_fields([proposal],revision)
        fields={f.name:f for f in editor.interactions()[1]}
        assert len(fields)==9 and fields['nome'].value=='Existing'
        assert fields['riferimento'].readonly
        proposal['name']='nome'
        with pytest.raises(PDFOperationError,match='univoci'):
            editor.create_form_fields([proposal],editor.path)
    finally:
        editor.close()


@pytest.mark.parametrize('change',[
    {'rect':[40,80,40,100]}, {'rect':[-1,80,100,100]}, {'rect':[40,80,float('nan'),100]},
    {'kind':'javascript'}, {'page':100}, {'name':'bad.name'}, {'label':'\x00'}, {'multiline':'yes'},
])
def test_invalid_proposals_never_create_revision(flat_forms,change):
    editor=PDFEditor(flat_forms[0])
    try:
        proposal=dict(page=0,rect=[40,80,180,110],kind='text',name='new_field',label='New',multiline=False)
        proposal.update(change)
        with pytest.raises(PDFOperationError):
            editor.create_form_fields([proposal],editor.path)
        assert not editor.dirty and not editor.can_undo
    finally:
        editor.close()


def test_stale_revision_and_overlapping_proposals_are_rejected(flat_forms):
    editor=PDFEditor(flat_forms[0])
    try:
        proposals=detect_fields(editor.path,[0],use_ocr=False)['proposals']
        revision=editor.path
        editor.rotate(0)
        with pytest.raises(PDFOperationError,match='cambiato'):
            editor.create_form_fields(proposals,revision)
        editor.undo()
        duplicate=dict(proposals[0],name='duplicate')
        with pytest.raises(PDFOperationError,match='sovrappone'):
            editor.create_form_fields(proposals+[duplicate],editor.path)
        assert not editor.dirty
    finally:
        editor.close()


def test_existing_widgets_are_not_proposed_again(form_pdf):
    assert not detect_fields(form_pdf,[0],use_ocr=False)['proposals']


def test_worker_protocol_and_limits(flat_forms):
    result=subprocess.run(worker_command('form_detect'),input=json.dumps(dict(path=str(flat_forms[0]),pages=[0],use_ocr=False)).encode(),
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    assert result.returncode==0,result.stderr
    assert len(json.loads(result.stdout)['proposals'])==5
    with pytest.raises(ValueError):
        detect_fields(flat_forms[0],[],use_ocr=False)
    with pytest.raises(ValueError):
        detect_fields(flat_forms[0],[0]*51,use_ocr=False)


def test_scan_labels_use_local_rgb_ocr_when_models_available(flat_forms):
    models=Path(__file__).resolve().parents[1]/'.dev-deps/tessdata'
    if not all((models/(name+'.traineddata')).is_file() for name in ('ita','eng')):
        pytest.skip('Local OCR models are not available')
    result=detect_fields(flat_forms[1],[0],cache_dir=models,use_ocr=True)
    assert len(result['proposals'])==5 and not result['warnings']
    names={p['name'] for p in result['proposals']}
    assert 'Nome_e_cognome' in names and 'Accetto_le_condizioni' in names


def test_hidden_field_names_cannot_be_overwritten(flat_forms,tmp_path):
    writer=PdfWriter(clone_from=flat_forms[0])
    hidden=writer._add_object(DictionaryObject({NameObject('/FT'):NameObject('/Tx'),
        NameObject('/T'):TextStringObject('reserved'),NameObject('/V'):TextStringObject('keep me')}))
    writer.root_object[NameObject('/AcroForm')]=writer._add_object(DictionaryObject({NameObject('/Fields'):ArrayObject([hidden])}))
    source=tmp_path/'hidden.pdf'
    writer.write(source)
    editor=PDFEditor(source)
    try:
        proposal=dict(page=0,rect=[40,177,555,208],kind='text',name='reserved',label='New',multiline=False)
        with pytest.raises(PDFOperationError,match='univoci'):
            editor.create_form_fields([proposal],editor.path)
        assert not editor.dirty
    finally:
        editor.close()


def test_active_content_is_rejected_by_detection(flat_forms,tmp_path):
    writer=PdfWriter(clone_from=flat_forms[0])
    writer.root_object[NameObject('/OpenAction')]=writer._add_object(DictionaryObject({
        NameObject('/S'):NameObject('/JavaScript'),NameObject('/JS'):TextStringObject("app.alert('synthetic')")}))
    source=tmp_path/'active.pdf'
    writer.write(source)
    with pytest.raises(ValueError,match='Unsupported'):
        detect_fields(source,[0],use_ocr=False)


def test_existing_need_appearances_flag_is_preserved(form_pdf,tmp_path):
    writer=PdfWriter(clone_from=form_pdf)
    writer.root_object['/AcroForm'][NameObject('/NeedAppearances')]=BooleanObject(True)
    path=tmp_path/'regenerate.pdf'
    writer.write(path)
    editor=PDFEditor(path)
    try:
        editor.create_form_fields([dict(page=0,rect=[380,720,550,740],kind='text',name='extra',label='Extra',multiline=False)],editor.path)
        with PdfReader(editor.path) as reader:
            assert reader.root_object['/AcroForm']['/NeedAppearances'].value is True
    finally:
        editor.close()
