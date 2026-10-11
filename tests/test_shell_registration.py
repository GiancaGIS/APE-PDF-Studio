"""Installer registration must add one optional page without replacing providers."""
from pathlib import Path
import re


def test_property_sheet_registration_is_independent_and_owns_only_its_keys():
    root = Path(__file__).resolve().parents[1]
    source = (root / "installer/APEPDFStudio.iss").read_text(encoding="utf-8")
    guid = re.search(r'#define MetadataGuid "([A-F0-9-]+)"', source)[1]
    native = (root / "native/windows_shell/shell_ids.h").read_text(encoding="utf-8")
    parts = re.findall(r"0x([a-f0-9]+)", native.split("CLSID_APEMetadata =", 1)[1])
    native_guid = "".join(parts[:3] + parts[3:]).upper()
    assert guid.replace("-", "") == native_guid
    task = next(line for line in source.splitlines() if line.startswith('Name: "pdfmetadata"'))
    assert "Flags: unchecked" in task and "Check: IsX64" in task
    entries = [line for line in source.splitlines() if line.startswith("Root:") and "{#MetadataGuid}" in line]
    assert len(entries) == 5
    assert all("Root: HKA;" in line and "Tasks: pdfmetadata;" in line for line in entries)
    sheets = [line for line in entries if "PropertySheetHandlers" in line]
    assert len(sheets) == 1 and "SystemFileAssociations\\.pdf\\ShellEx\\PropertySheetHandlers\\{{{#MetadataGuid}}" in sheets[0]
    assert "uninsdeletekey" in sheets[0]
    assert all("UserChoice" not in line and "DefaultIcon" not in line and "pdfpreview" not in line for line in entries)
