<# Build the x64 native COM adapters and their registration-free smoke host. #>
[CmdletBinding()]
param([string]$OutputDir, [string]$VisualStudioPath)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
if (-not $OutputDir) { $OutputDir = Join-Path $projectRoot 'build\windows\shell' }
$null = New-Item -ItemType Directory -Path $OutputDir -Force
$OutputDir = (Resolve-Path -LiteralPath $OutputDir).Path
if (-not $VisualStudioPath) {
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path -LiteralPath $vswhere)) { throw 'Installa Visual Studio Build Tools con Desktop development with C++ e Windows SDK.' }
    $VisualStudioPath = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
}
if (-not $VisualStudioPath) { throw 'Installa Visual Studio Build Tools con Desktop development with C++ e Windows SDK.' }
$devShell = Join-Path $VisualStudioPath 'Common7\Tools\Launch-VsDevShell.ps1'
if (-not (Test-Path -LiteralPath $devShell)) { throw 'Visual C++ x64 e Windows SDK non disponibili.' }
& $devShell -Arch amd64 -HostArch amd64 -SkipAutomaticLocation
if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) { throw 'Compilatore Visual C++ non disponibile.' }
Push-Location -LiteralPath $OutputDir
try {
    $source = Join-Path $projectRoot 'native\windows_shell'
    & rc.exe /nologo /fo shell.res (Join-Path $source 'shell.rc')
    if ($LASTEXITCODE -ne 0) { throw 'Compilazione risorse della shell extension fallita.' }
    & cl.exe /nologo /std:c++17 /utf-8 /EHsc /W4 /WX /O2 /MT /guard:cf /DUNICODE /D_UNICODE /LD (Join-Path $source 'shell.cpp') shell.res /link "/DEF:$(Join-Path $source 'APEPDFShell.def')" /OUT:APEPDFShell.dll ole32.lib uuid.lib user32.lib gdi32.lib shell32.lib comctl32.lib /DYNAMICBASE /NXCOMPAT /GUARD:CF
    if ($LASTEXITCODE -ne 0) { throw 'Compilazione della shell extension fallita.' }
    & cl.exe /nologo /std:c++17 /utf-8 /EHsc /W4 /WX /O2 /MT /guard:cf /DUNICODE /D_UNICODE (Join-Path $source 'smoke.cpp') /link /OUT:APEPDFShellSmoke.exe ole32.lib shlwapi.lib uuid.lib user32.lib gdi32.lib advapi32.lib shell32.lib comctl32.lib /DYNAMICBASE /NXCOMPAT /GUARD:CF
    if ($LASTEXITCODE -ne 0) { throw 'Compilazione della verifica shell fallita.' }
}
finally { Pop-Location }
Write-Host "Shell extension: $(Join-Path $OutputDir 'APEPDFShell.dll')"
