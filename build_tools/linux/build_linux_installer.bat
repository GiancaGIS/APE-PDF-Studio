@echo off
setlocal EnableExtensions DisableDelayedExpansion
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_linux_installer_wsl.ps1" %*
set "APE_BUILD_EXIT_CODE=%ERRORLEVEL%"
pause
exit /b %APE_BUILD_EXIT_CODE%
