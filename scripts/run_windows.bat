@echo off
setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo [APE PDF Studio] Prima installazione. Creo ambiente Python...
    where py >nul 2>&1
    if errorlevel 1 (
        python -m venv .venv
    ) else (
        py -3 -m venv .venv
    )
    if errorlevel 1 goto :error
)

".venv\Scripts\python.exe" -c "import PySide6, pypdf, pdf2docx, docx, cryptography, ape_pdf_studio; from pypdf.xmp import XmpInformation; from ape_pdf_studio.core.security import configure_parser; configure_parser(); assert tuple(map(int,pypdf.__version__.split('.')[:2])) >= (6,19); assert XmpInformation.dc_title.fset is not None" >nul 2>&1
if errorlevel 1 (
    echo [APE PDF Studio] Installo dipendenze...
    ".venv\Scripts\python.exe" -m pip install -e .
    if errorlevel 1 goto :error
)

".venv\Scripts\python.exe" -m ape_pdf_studio %*
if errorlevel 1 goto :error
exit /b 0

:error
echo.
echo Impossibile avviare il programma. Verifica Python e connessione Internet.
pause
exit /b 1
