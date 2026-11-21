# Build and distribution

All commands below start at the repository root unless stated otherwise.
The packaging implementation is in [build_tools](../../build_tools/README.md).
Document processing stays local. Windows x64 is the verified platform.

## Build metadata

The frozen packages omit pip installation-origin records (`direct_url.json`)
and replace local PyMuPDF source locations and git diagnostics with neutral
build metadata. Native application compilation maps checkout paths to relative
paths. The Windows MuPDF build trims its source path with MSVC. Linux and macOS
compile MuPDF sources in a temporary directory outside the account's home.
An older cached MuPDF library with a profile path is rebuilt during environment
preparation.

Packaging checks the payload for the build account's paths in plain files,
compressed PyInstaller modules and ZIP entries. Windows checks the installer
bytes after assembling the verified payload. Linux also unpacks and checks the
finished Debian package before copying it to `dist/linux/`. Build logs and
virtualenv metadata belong in local build directories.

The Linux portable archive uses neutral ownership metadata in its tar headers.

## Windows

Install the exact **CPython x64** version in
[`requirements-release.txt`](../../requirements-release.txt), **Inno Setup 7.1.1+**,
and **Visual Studio / Build Tools** with **Desktop development with C++** and a
**Windows SDK**. The scripts do not install system packages or compilers.

Run:

```powershell
.\build_tools\windows\build_installer.bat
```

This prepares `.venv-build`, checks release dependency pins and native MuPDF,
then compiles the app modules into PYD, the AppContainer launcher and native shell
providers. It prepares matching source/license assets, seals application integrity,
builds the three EXEs, verifies the bundle and runs smoke tests before Inno Setup.
The selected application license is retained verbatim. Installer scope detection,
disclaimers and optional Explorer integrations use the same maintained ISS file.

Progress and errors remain in `build/windows-build.log`. Dependency inspection is
recorded in `build/windows-build-environment.json`. Python packages are installed
only in a virtualenv. Corrected MuPDF is built/reused through
`build_tools/windows/build_patched_mupdf.ps1`; an upstream wheel is not substituted.
Existing suitable environments are reused.

### Options

```powershell
# Prepare/check dependencies without compiling the application or installer.
.\build_tools\windows\build_installer.bat -PrepareOnly

# Standalone folder, source ZIPs and checks; no Inno Setup required.
.\build_tools\windows\build_installer.bat -BundleOnly

# Reuse a prepared environment without installing dependencies.
.\build_tools\windows\build_installer.bat -SkipDependencyInstall

# Reuse the existing bundle, still checking integrity, versions and smoke tests.
.\build_tools\windows\build_installer.bat -SkipBuild

# Same procedure via Python, or a noninteractive PowerShell call.
.\.venv-build\Scripts\python.exe scripts/build.py --help
.\build_tools\windows\build_windows_installer.ps1 -PythonExe .\.venv-build\Scripts\python.exe
```

`-PythonExe` also accepts `-Python`. A global interpreter is used only to create
the project's virtualenv. The prepared environment must match the pinned CPython
version and x64 architecture. `-SkipDependencyInstall`/`-SkipBuild` fail if the
selected environment needs preparation. `-OfflineSources` reuses verified cached
source archives and fails if any required archive is missing or changed; prepare
dependencies separately before using it for an offline build.

Other options: `-InnoCompiler PATH`, `-DistRoot PATH`, `-LicenseFile PATH`, and
`-SkipSmokeTest` for development builds only. The old `scripts/build_installer.bat`
command forwards to this procedure.

### Outputs and delivery

| Location | Contents |
| --- | --- |
| `dist/APEPDFStudio/` | Standalone GUI, private worker, CLI, sandbox, shell DLL, runtime and legal documents |
| `dist/installer/` | `APEPDFStudio-<version>-Setup-x64.exe` |
| `dist/release/` | Corresponding application/dependency source ZIPs, checksums and release notes |
| `build/windows/` | Intermediate native/PYD builds, inventory and smoke reports |
| `.dev-deps/` | Verified source/wheel/model caches and development worker runtime |

Distribute the whole standalone folder, or its installer, with the matching
source assets. Do not publish caches, virtualenvs, compiler intermediates or
personal files. Source archives include the maintained build tools, entry points,
native sources, exact dependency lock and this guide. See
[`SOURCE_CODE.md`](../../SOURCE_CODE.md) and
[`THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md).

### Signing

Development builds may be unsigned. An authenticated release requires an existing
code-signing certificate, all smoke checks and the prescribed signing steps:

```powershell
.\build_tools\windows\build_installer.bat -Release -CertificateThumbprint CERTIFICATE_THUMBPRINT
```

No key or password is stored in the repository. `-Release` retains signing of
application/native files, installer and uninstaller, timestamp verification and
integrity verification after signing. `-SkipSmokeTest` is rejected for releases.

## Linux

Build natively on glibc Linux with Python 3.11+, a C/C++ compiler, make, the
Qt desktop libraries, and `dpkg-deb`. The reference build host is Ubuntu 26.04.
The `.deb` records its architecture and the build host's glibc version as the
minimum supported version. For the Qt X11 platform plugin on Ubuntu, install
`libgl1 libegl1 libx11-xcb1 libxcb-cursor0 libxkbcommon-x11-0 libxcb-icccm4
libxcb-keysyms1 libxcb-render-util0 libxcb-xkb1`.

```bash
python3 -m venv .venv-build
bash build_tools/common/prepare_unix.sh .venv-build/bin/python
.venv-build/bin/python scripts/build.py
```

The last command forwards to `build_tools/linux/build_linux_installer.sh`.
Use `--skip-deb` to create just the portable archive. Output is in
`dist/linux/`. The Debian package installs under `/opt/ape-pdf-studio`, adds
the desktop launcher and PDF MIME registration, and provides
`ape-pdf-studio` and `ape-pdf-cli` in the usual command path.

From Windows with an existing Ubuntu WSL distribution, Python with venv,
`rsync` and the Linux prerequisites installed:

```powershell
.\build_tools\linux\build_linux_installer.bat -Distribution Ubuntu
```

This stages build inputs and a dedicated virtualenv on the Linux filesystem,
then copies the finished files to the Windows checkout's `dist/linux/`.
`-PrepareOnly` prepares the WSL virtualenv without packaging the application.
The script does not install system packages or change another project's venv.

## macOS

Use a Mac with Xcode Command Line Tools and Python 3.11+ of the target
architecture. Apple Silicon and Intel use separate native builds.

```bash
python3 -m venv .venv-build
bash build_tools/common/prepare_unix.sh .venv-build/bin/python
.venv-build/bin/python scripts/build.py
```

This invokes `build_tools/macos/build_macos_app.sh`. In `dist/macos/`, the ZIP
contains `APE PDF Studio.app` and the PKG installs it in `/Applications` with a
license page. Optional `--codesign-identity` and `--installer-identity` use
already configured Developer ID certificates. The default app is signed ad hoc
and the PKG is unsigned. Notarization is a separate release step.

## Unix build checks and artifacts

Both flows compile the application modules with Cython, package Python and
Qt, and exercise the frozen CLI, PDF preflight and Qt PDF renderer before
packaging. The environment report, corresponding application source ZIP, and
SHA-256 checksums accompany the local development build. Prepare and verify
the matching third-party source and notices for the actual build environment
before public distribution. Windows AppContainer and its integrity bootstrap
are Windows-specific. Linux/macOS keep the separate workers and resource limits.
On Linux the renderer limits writable data to 768 MiB and virtual address space
to 48 GiB to accommodate Qt PDF's inaccessible address-space reservation.

The [Linux and macOS installers workflow](../../.github/workflows/unix-build.yml)
can run on demand on native GitHub runners and produces development artifacts.
Source startup remains available through `bash run_linux.sh` and
`bash run_mac.command`.

## Verification

The GitHub security workflow uses these same maintained paths. Useful focused
checks from a prepared Windows environment:

```powershell
.\.venv-build\Scripts\python.exe scripts/prepare_build.py --inspect
.\build_tools\windows\build_windows_sandbox.ps1
.\.venv-build\Scripts\python.exe build_tools/checks/test_windows_sandbox.py
.\.venv-build\Scripts\python.exe build_tools/common/compile_app_modules.py
.\.venv-build\Scripts\python.exe build_tools/checks/test_compiled_modules.py
.\.venv-build\Scripts\python.exe scripts/qa/fuzz_security_protocol.py --cases 10000
```

Running a source test, building a local installer and running the GitHub pipeline
are separate checks. Report which were actually performed.
