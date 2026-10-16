[CmdletBinding()]
param([string]$OutputDir)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
if (-not $OutputDir) { $OutputDir = Join-Path $projectRoot 'build\windows\sandbox' }
$null = New-Item -ItemType Directory -Path $OutputDir -Force
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$vs = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
& (Join-Path $vs 'Common7\Tools\Launch-VsDevShell.ps1') -Arch amd64 -HostArch amd64 -SkipAutomaticLocation
Push-Location -LiteralPath $OutputDir
try {
    & cl.exe /nologo /std:c++17 /utf-8 /EHsc /W4 /WX /O2 /MT /guard:cf /DUNICODE /D_UNICODE (Join-Path $projectRoot 'native\windows_sandbox\host.cpp') /link /OUT:APEPDFSandbox.exe advapi32.lib userenv.lib ole32.lib shell32.lib uuid.lib /DYNAMICBASE /NXCOMPAT /GUARD:CF
    if ($LASTEXITCODE -ne 0) { throw 'Sandbox host compilation failed.' }
    & cl.exe /nologo /std:c++17 /utf-8 /EHsc /W4 /WX /O2 /MT /guard:cf /DUNICODE /D_UNICODE (Join-Path $projectRoot 'native\windows_sandbox\probe.cpp') /link /OUT:APEPDFSandboxProbe.exe ws2_32.lib /DYNAMICBASE /NXCOMPAT /GUARD:CF
    if ($LASTEXITCODE -ne 0) { throw 'Sandbox probe compilation failed.' }
} finally { Pop-Location }
