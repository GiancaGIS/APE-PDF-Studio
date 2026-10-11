<#
.SYNOPSIS
Build a standalone Windows x64 application and its Inno Setup installer.
.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\scripts\build_installer.ps1
.EXAMPLE
.\scripts\build_installer.ps1 -PythonExe .\.venv\Scripts\python.exe -SkipDependencyInstall
#>
[CmdletBinding()]
param(
    [string]$PythonExe,
    [string]$InnoCompiler,
    [string]$LicenseFile,
    [string]$DistRoot,
    [switch]$OfflineSources,
    [switch]$SkipDependencyInstall,
    [switch]$SkipBuild,
    [switch]$SkipSmokeTest,
    [switch]$BundleOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Push-Location -LiteralPath $projectRoot
$previousLicense = [Environment]::GetEnvironmentVariable('APE_LICENSE_FILE', 'Process')
try {
    if ($env:OS -ne 'Windows_NT') { throw 'La build richiede Windows e Python x64.' }
    if (-not $LicenseFile) { $LicenseFile = Join-Path $projectRoot 'LICENSE' }
    if (-not $DistRoot) { $DistRoot = Join-Path $projectRoot 'dist' }
    $DistRoot = [IO.Path]::GetFullPath($DistRoot)
    $LicenseFile = (Resolve-Path -LiteralPath $LicenseFile).Path
    if (-not (Test-Path -LiteralPath $LicenseFile -PathType Leaf)) { throw 'File di licenza non valido.' }

    if (-not $BundleOnly) {
        if (-not $InnoCompiler) {
            $isccCommand = Get-Command ISCC.exe -ErrorAction SilentlyContinue
            if ($isccCommand) { $InnoCompiler = $isccCommand.Source }
            else {
                $compilerCandidates = @(
                    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
                    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
                    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
                )
                $InnoCompiler = $compilerCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
            }
        }
        if (-not $InnoCompiler) { throw 'Installa Inno Setup 6.3 o successivo oppure specifica -InnoCompiler.' }
        $InnoCompiler = (Resolve-Path -LiteralPath $InnoCompiler).Path
    }

    if (-not $PythonExe) {
        $PythonExe = Join-Path $projectRoot '.venv-build\Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $PythonExe)) {
            if ($SkipBuild -or $SkipDependencyInstall) { throw 'Specifica -PythonExe oppure crea prima .venv-build.' }
            $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
            if ($launcher) { & $launcher.Source -3 -m venv (Join-Path $projectRoot '.venv-build') }
            else { & python -m venv (Join-Path $projectRoot '.venv-build') }
            if ($LASTEXITCODE -ne 0) { throw 'Creazione ambiente Python fallita.' }
        }
    }
    $PythonExe = (Resolve-Path -LiteralPath $PythonExe).Path
    $versionOutput = & $PythonExe (Join-Path $projectRoot 'scripts\windows_build_info.py')
    if ($LASTEXITCODE -ne 0) { throw 'Interprete o versione progetto non supportati.' }
    $appVersion = ([string]$versionOutput).Trim()
    if ($appVersion -notmatch '^\d+\.\d+\.\d+$') { throw 'Versione progetto non valida per la build.' }

    if (-not $SkipBuild) {
        if (-not $SkipDependencyInstall) {
            Write-Host '[APE] Installo le dipendenze nell''ambiente di build...'
            & $PythonExe -m pip install -r (Join-Path $projectRoot 'requirements-release.txt')
            if ($LASTEXITCODE -ne 0) { throw 'Installazione dipendenze bloccate fallita.' }
        }
        # Refresh application metadata even when third-party dependencies are
        # already installed: copy_metadata must bundle the current version.
        & $PythonExe -m pip install --no-index --no-build-isolation --no-deps --disable-pip-version-check -e '.'
        if ($LASTEXITCODE -ne 0) { throw 'Preparazione metadati applicazione fallita.' }
        $env:APE_LICENSE_FILE = $LicenseFile
        $sourceArguments = @()
        if ($OfflineSources) { $sourceArguments += '--offline' }
        Write-Host '[APE] Preparo licenze e sorgenti delle dipendenze...'
        & $PythonExe (Join-Path $PSScriptRoot 'prepare_release.py') @sourceArguments
        if ($LASTEXITCODE -ne 0) { throw 'Preparazione licenze/sorgenti fallita.' }
        & (Join-Path $PSScriptRoot 'build_windows_shell.ps1')
        Write-Host '[APE] Creo il pacchetto Windows...'
        & $PythonExe -m PyInstaller --noconfirm --distpath $DistRoot --workpath (Join-Path $projectRoot 'build\pyinstaller') (Join-Path $projectRoot 'installer\APEPDFStudio.spec')
        if ($LASTEXITCODE -ne 0) { throw 'Build PyInstaller fallita.' }
    }

    $bundleDir = Join-Path $DistRoot 'APEPDFStudio'
    if (-not (Test-Path -LiteralPath (Join-Path $bundleDir 'APEPDFShell.dll'))) { throw 'Shell extension mancante. Ricrea il pacchetto Windows.' }
    foreach ($exeName in @('APEPDFStudio.exe', 'APEPDFWorker.exe', 'ape-pdf-cli.exe')) {
        $exePath = Join-Path $bundleDir $exeName
        if (-not (Test-Path -LiteralPath $exePath -PathType Leaf)) { throw "Eseguibile mancante: $exePath" }
        $fileVersion = [Diagnostics.FileVersionInfo]::GetVersionInfo($exePath).ProductVersion
        if ($fileVersion -ne $appVersion) { throw "Versione $exeName ($fileVersion) diversa da pyproject.toml ($appVersion). Ricrea il pacchetto." }
    }
    if (-not (Get-ChildItem -LiteralPath (Join-Path $bundleDir '_internal') -Filter 'python3*.dll' -File)) { throw 'Il pacchetto non contiene il runtime Python.' }
    Write-Host '[APE] Preparo gli archivi sorgenti corrispondenti al pacchetto...'
    $releaseDir = Join-Path $DistRoot 'release'
    & $PythonExe (Join-Path $PSScriptRoot 'prepare_release.py') --bundle $bundleDir --output $releaseDir
    if ($LASTEXITCODE -ne 0) { throw 'Verifica licenze e archivi sorgenti fallita.' }
    if (-not $SkipSmokeTest) {
        Write-Host '[APE] Verifico gli eseguibili...'
        & $PythonExe (Join-Path $projectRoot 'scripts\smoke_windows_bundle.py') --bundle $bundleDir
        if ($LASTEXITCODE -ne 0) { throw 'Verifica del pacchetto Windows fallita.' }
    }
    if ($BundleOnly) { Write-Host "Pacchetto pronto: $bundleDir"; return }

    if (-not $SkipSmokeTest) {
        Write-Host '[APE] Verifico backup e ripristino dei provider PDF...'
        & $PythonExe (Join-Path $projectRoot 'scripts\test_windows_shell_installer.py') --inno-compiler $InnoCompiler
        if ($LASTEXITCODE -ne 0) { throw 'Verifica delle registrazioni shell fallita.' }
    }

    $outputDir = Join-Path $DistRoot 'installer'
    Write-Host '[APE] Compilo l''installer Inno Setup...'
    & $InnoCompiler "/DAppVersion=$appVersion" "/DDistDir=$bundleDir" "/DOutputDir=$outputDir" "/DLicensePath=$LicenseFile" (Join-Path $projectRoot 'installer\APEPDFStudio.iss')
    if ($LASTEXITCODE -ne 0) { throw 'Compilazione Inno Setup fallita.' }
    & $PythonExe (Join-Path $PSScriptRoot 'prepare_release.py') --bundle $bundleDir --output $releaseDir --installer (Join-Path $outputDir "APEPDFStudio-$appVersion-Setup-x64.exe")
    if ($LASTEXITCODE -ne 0) { throw 'Finalizzazione manifesti release fallita.' }
    Write-Host "Installer pronto: $(Join-Path $outputDir "APEPDFStudio-$appVersion-Setup-x64.exe")"
}
finally {
    [Environment]::SetEnvironmentVariable('APE_LICENSE_FILE', $previousLicense, 'Process')
    Pop-Location
}
