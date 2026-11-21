# Build procedures

APE PDF Studio follows the same entry-point layout as OpenScan Studio.
Run each packaging procedure on its native target platform, including Linux
under WSL where available.

From the repository root:

| Task | Command |
| --- | --- |
| Build Windows application and installer | `build_tools\windows\build_installer.bat` |
| Prepare dependencies only | `build_tools\windows\build_installer.bat -PrepareOnly` |
| Build standalone folder only | `build_tools\windows\build_installer.bat -BundleOnly` |
| Forward through Python | `.venv-build\Scripts\python.exe scripts/build.py` |
| Inspect environment without changes | `.venv-build\Scripts\python.exe scripts/prepare_build.py --inspect` |
| Show packaging options | `.venv-build\Scripts\python.exe scripts/build.py --help` |
| Build Linux installer and archive | `bash build_tools/linux/build_linux_installer.sh --python .venv-build/bin/python` |
| Build Linux from Windows through WSL | `build_tools\linux\build_linux_installer.bat -Distribution Ubuntu` |
| Build macOS app ZIP and installer | `bash build_tools/macos/build_macos_app.sh --python .venv-build/bin/python` |

The BAT preserves the build exit code and leaves the console open. Preparation
uses a virtualenv, checks exact release pins and corrected native MuPDF, and
installs missing/incompatible dependencies only when needed. Progress and errors
are retained in `build/windows-build.log`; the environment report is
`build/windows-build-environment.json`. No system tools are installed automatically.

## Layout

- `windows/`: the main PowerShell procedure, bootstrap, native builds, signing,
  runtime preparation, PyInstaller specification and Inno Setup installer.
- `linux/` and `macos/`: native packaging entry points, with a WSL bridge for Linux.
- `common/`: Cython compilation, integrity checks, dependency sources and release
  archive generation. Tools execute from any current directory.
- `checks/`: executable/native import, isolation and shell-installer smoke tests.
- `launchers/`: the three tiny entry points bundled into the Windows EXEs.
- `branding/`: icon regeneration from the repository's SVG master.

Source startup commands live at the root: `run_windows.bat`, `run_linux.sh`, and
`run_mac.command`. Example generators and QA tools are under `scripts/examples/`
and `scripts/qa/`. The old `scripts/build_installer.bat`, `scripts/run_windows.bat`
and `scripts/run_mac_linux.sh` are compatibility forwarders only.

See the [distribution guide](../docs/distribuzione/README.md) for prerequisites,
options, signing, legal assets, outputs and platform limits.
