@echo off
setlocal
cd /d "%~dp0.."

if not exist ".venv-build\Scripts\python.exe" (
    echo [APE PDF Studio] Prima installazione. Creo ambiente Python...
    where py >nul 2>&1
    if errorlevel 1 (
        python -m venv .venv-build
    ) else (
        py -3.14 -m venv .venv-build
    )
    if errorlevel 1 goto :error
)

".venv-build\Scripts\python.exe" -c "import PySide6, pypdf, pdf2docx, docx, cryptography, ape_pdf_studio, pymupdf; from packaging.version import Version; from ape_pdf_studio.core.security import configure_parser; configure_parser(); assert Version(pypdf.__version__) >= Version('6.21.2'); assert Version(pymupdf.mupdf_version) >= Version('1.28.5')" >nul 2>&1
if errorlevel 1 (
    echo [APE PDF Studio] Installo dipendenze...
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_patched_mupdf.ps1 -PythonExe .venv-build\Scripts\python.exe
    if errorlevel 1 goto :error
    ".venv-build\Scripts\python.exe" -m pip install --find-links .dev-deps\patched-wheels -r requirements-release.txt -e .
    if errorlevel 1 goto :error
)

powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_windows_sandbox.ps1
if errorlevel 1 goto :error
if not exist ".dev-deps\sandbox-python\python.exe" (
    ".venv-build\Scripts\python.exe" scripts\prepare_sandbox_runtime.py
    if errorlevel 1 goto :error
)
".venv-build\Scripts\python.exe" -m ape_pdf_studio %*
if errorlevel 1 goto :error
exit /b 0

:error
echo.
echo Impossibile avviare il programma. Verifica Python e connessione Internet.
pause
exit /b 1
