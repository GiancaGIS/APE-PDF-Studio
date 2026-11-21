@echo off
setlocal EnableExtensions DisableDelayedExpansion
title APE PDF Studio - Build Windows
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_windows_installer.ps1" %*
set "APE_BUILD_EXIT_CODE=%ERRORLEVEL%"
if not "%APE_BUILD_EXIT_CODE%"=="0" echo [ERRORE] Build interrotta. Controlla build\windows-build.log.
pause
exit /b %APE_BUILD_EXIT_CODE%
