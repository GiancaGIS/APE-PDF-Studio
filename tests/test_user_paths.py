"""Cloud/Vault paths remain usable without relaxing worker artifact checks."""
from pathlib import Path

import pytest

from test_gui import app, text_pdf, window
from ape_pdf_studio.core.broker_editor import BrokerEditor
from ape_pdf_studio.core.errors import PDFOperationError
from ape_pdf_studio.core.user_paths import same_file, user_path


def unresolved_vault(monkeypatch, source):
    resolve = Path.resolve
    def resolve_path(path, *args, **kwargs):
        if path == source:
            return Path('Volume{synthetic-vault}/VaultData/document.pdf')
        return resolve(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'resolve', resolve_path)


def test_external_path_is_absolute_without_resolving_provider(monkeypatch, text_pdf):
    unresolved_vault(monkeypatch, text_pdf)
    assert user_path(text_pdf) == text_pdf
    assert user_path(text_pdf).is_file()


def test_broker_opens_vault_style_path_and_keeps_source_unchanged(monkeypatch, text_pdf):
    original = text_pdf.read_bytes()
    unresolved_vault(monkeypatch, text_pdf)
    editor = BrokerEditor(text_pdf)
    try:
        assert editor.source == text_pdf and editor.metadata().page_count == 3
        assert not editor.path.is_relative_to(text_pdf.parent)
    finally:
        editor.close()
    assert text_pdf.read_bytes() == original


def test_window_opens_vault_style_path_and_reuses_same_tab(window, text_pdf, monkeypatch):
    unresolved_vault(monkeypatch, text_pdf)
    window.open_pdf(text_pdf)
    assert window.tabs.count() == 1 and window.current.editor.source == text_pdf


def test_extract_cannot_overwrite_an_alias_of_active_source(text_pdf, tmp_path):
    alias = tmp_path / 'alias.pdf'
    alias.hardlink_to(text_pdf)
    assert same_file(alias, text_pdf)
    editor = BrokerEditor(text_pdf)
    try:
        with pytest.raises(PDFOperationError):
            editor.extract([0], alias)
        assert alias.read_bytes() == text_pdf.read_bytes()
    finally:
        editor.close()


def test_missing_destination_is_not_mistaken_for_existing_file(text_pdf, tmp_path):
    assert not same_file(tmp_path / 'new.pdf', text_pdf)
