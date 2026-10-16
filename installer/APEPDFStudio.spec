# -*- mode: python ; coding: utf-8 -*-
"""Build the GUI, public CLI and private worker with shared dependencies."""
from pathlib import Path
import os
import sys
import tomllib

from PyInstaller.utils.hooks import copy_metadata, collect_data_files

root = Path(SPECPATH).parent
app_icon = root / "src/ape_pdf_studio/assets/app-icon.ico"
if not app_icon.is_file():
    raise FileNotFoundError("Generate the application icon with scripts/generate_app_icon.py")
project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
version = project["version"]
publisher = project["authors"][0]["name"]
version_tuple = tuple(int(part) for part in version.split("."))
version_tuple += (0,) * (4 - len(version_tuple))
license_file = Path(os.environ.get("APE_LICENSE_FILE", os.environ.get("APEX_LICENSE_FILE", str(root / "LICENSE")))).resolve()
if not license_file.is_file():
    raise FileNotFoundError(f"License file does not exist: {license_file}")

resource_dir = root / "build" / "windows"
resource_dir.mkdir(parents=True, exist_ok=True)
version_file = resource_dir / "version_info.txt"
version_file.write_text(f'''VSVersionInfo(
  ffi=FixedFileInfo(filevers={version_tuple!r}, prodvers={version_tuple!r},
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('041004B0', [
    StringStruct('CompanyName', {publisher!r}),
    StringStruct('FileDescription', 'APE PDF Studio'),
    StringStruct('FileVersion', '{version}'),
    StringStruct('ProductName', 'APE PDF Studio'),
    StringStruct('ProductVersion', '{version}')
  ])]), VarFileInfo([VarStruct('Translation', [1040, 1200])])]
)''', encoding="utf-8")

datas = [(str(license_file), "licenses/APE PDF Studio")]
legal_dir = root / "build/windows/legal"
if not (legal_dir / "source-manifest.json").is_file():
    raise FileNotFoundError("Prepare verified licenses and sources with scripts/prepare_release.py")
shell_dll = root / "build/windows/shell/APEPDFShell.dll"
sandbox_exe = root / "build/windows/sandbox/APEPDFSandbox.exe"
if not sandbox_exe.is_file():
    raise FileNotFoundError("Build the AppContainer launcher with scripts/build_windows_sandbox.ps1")
if not shell_dll.is_file():
    raise FileNotFoundError("Build the native shell extension with scripts/build_windows_shell.ps1")
datas += collect_data_files("ape_pdf_studio", includes=["locales/*.json", "assets/*"])
datas += collect_data_files("PySide6", includes=[
    "translations/qtbase_" + language + ".qm" for language in ("it", "es", "en", "fr")])
# Include installed dependency metadata and their license/notice files.
for package in ("PySide6", "pypdf", "PyMuPDF", "pdf2docx", "python-docx", "cryptography"):
    datas += copy_metadata(package, recursive=True)
python_license = Path(sys.base_prefix) / "LICENSE.txt"
if python_license.is_file():
    datas.append((str(python_license), "licenses/Python"))

analysis = Analysis(
    [str(root / "scripts" / name) for name in
     ("gui_launcher.py", "worker_launcher.py", "cli_launcher.py")],
    pathex=[str(root / "src")],
    binaries=[], datas=datas, hiddenimports=[], hookspath=[], hooksconfig={},
    runtime_hooks=[], excludes=["PyQt5", "PyQt6", "PySide2", "tkinter", "pytest"],
    noarchive=False,
)
# PDF image processing does not use OpenCV's optional video codecs. Avoid
# redistributing its separately licensed, dynamically loaded FFmpeg helper.
analysis.binaries = [entry for entry in analysis.binaries
                     if not Path(entry[0]).name.lower().startswith("opencv_videoio_ffmpeg")]
analysis.datas = [entry for entry in analysis.datas
                 if not Path(entry[0]).name.lower().startswith("opencv_videoio_ffmpeg")]
pyz = PYZ(analysis.pure)
# Retain the same runtime hooks, but only the intended entry point per EXE.
entry_points = {"gui_launcher", "worker_launcher", "cli_launcher"}
def scripts_for(name):
    return [entry for entry in analysis.scripts
            if entry[0] not in entry_points or entry[0] == name]
gui = EXE(pyz, scripts_for("gui_launcher"), [], exclude_binaries=True, name="APEPDFStudio",
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, version=str(version_file), icon=str(app_icon))
worker = EXE(pyz, scripts_for("worker_launcher"), [], exclude_binaries=True, name="APEPDFWorker",
             debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
             console=True, version=str(version_file), icon=str(app_icon))
cli = EXE(pyz, scripts_for("cli_launcher"), [], exclude_binaries=True, name="ape-pdf-cli",
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=True, version=str(version_file), icon=str(app_icon))
COLLECT(gui, worker, cli, analysis.binaries, analysis.datas,
        strip=False, upx=False, name="APEPDFStudio")
# COM must find the DLL beside the helper, outside PyInstaller's _internal.
import shutil
shutil.copy2(shell_dll, Path(DISTPATH) / "APEPDFStudio/APEPDFShell.dll")
shutil.copy2(sandbox_exe, Path(DISTPATH) / "APEPDFStudio/APEPDFSandbox.exe")
bundle = Path(DISTPATH) / "APEPDFStudio"
shutil.copytree(legal_dir, bundle / "licenses", dirs_exist_ok=True)
for document in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md", "SOURCE_CODE.md"):
    shutil.copy2(root / document, bundle / document)
