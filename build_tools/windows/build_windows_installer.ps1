<#
.SYNOPSIS
Build a standalone Windows x64 application and its Inno Setup installer.
.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\build_tools\windows\build_windows_installer.ps1
.EXAMPLE
.\build_tools\windows\build_windows_installer.ps1 -PythonExe .\.venv\Scripts\python.exe -SkipDependencyInstall
#>
[CmdletBinding()]
param(
    [Alias('Python')][string]$PythonExe,
    [string]$InnoCompiler,
    [string]$LicenseFile,
    [string]$DistRoot,
    [switch]$OfflineSources,
    [switch]$SkipDependencyInstall,
    [switch]$SkipBuild,
    [switch]$SkipSmokeTest,
    [switch]$BundleOnly,
    [switch]$PrepareOnly,
    [switch]$Help,
    [switch]$Release,
    [string]$CertificateThumbprint,
    [string]$TimestampServer = 'https://timestamp.digicert.com'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
if ($Help) {
    Write-Host 'Uso: build_tools\windows\build_installer.bat [-PrepareOnly] [-BundleOnly] [-SkipDependencyInstall] [-SkipBuild]'
    Write-Host 'Opzioni: -PythonExe PATH -InnoCompiler PATH -OfflineSources -DistRoot PATH -LicenseFile PATH -SkipSmokeTest'
    Write-Host 'Release firmata: -Release -CertificateThumbprint THUMBPRINT [-TimestampServer HTTPS_URL]'
    Write-Host 'La build prepara il virtualenv, compila PYD e sandbox, verifica il pacchetto e crea installer e sorgenti.'
    Write-Host 'Log: build\windows-build.log. Guida: docs\distribution\README.md.'
    return
}
. (Join-Path $PSScriptRoot 'build_environment.ps1')
Push-Location -LiteralPath $projectRoot
$transcribing = $false
$previousLicense = [Environment]::GetEnvironmentVariable('APE_LICENSE_FILE', 'Process')
try {
    $null = New-Item -ItemType Directory -Path (Join-Path $projectRoot 'build') -Force
    Start-Transcript -LiteralPath (Join-Path $projectRoot 'build\windows-build.log') -Force | Out-Host
    $transcribing = $true
    if ($env:OS -ne 'Windows_NT') { throw 'La build richiede Windows e Python x64.' }
    if ($Release -and (-not $CertificateThumbprint -or $SkipSmokeTest)) {
        throw 'Una release richiede -CertificateThumbprint e tutti gli smoke test. Senza certificato crea solo una build di sviluppo.'
    }
    if (-not $LicenseFile) { $LicenseFile = Join-Path $projectRoot 'LICENSE' }
    if (-not $DistRoot) { $DistRoot = Join-Path $projectRoot 'dist' }
    $DistRoot = [IO.Path]::GetFullPath($DistRoot)
    $LicenseFile = (Resolve-Path -LiteralPath $LicenseFile).Path
    if (-not (Test-Path -LiteralPath $LicenseFile -PathType Leaf)) { throw 'File di licenza non valido.' }

    if (-not $BundleOnly -and -not $PrepareOnly) {
        if (-not $InnoCompiler) {
            $isccCommand = Get-Command ISCC.exe -ErrorAction SilentlyContinue
            if ($isccCommand) { $InnoCompiler = $isccCommand.Source }
            else {
                $compilerCandidates = @(
                    "${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe",
                    "$env:ProgramFiles\Inno Setup 7\ISCC.exe",
                    "$env:LOCALAPPDATA\Programs\Inno Setup 7\ISCC.exe"
                )
                $InnoCompiler = $compilerCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
            }
        }
        if (-not $InnoCompiler) { throw 'Installa Inno Setup 7.1.1 o successivo oppure specifica -InnoCompiler.' }
        $InnoCompiler = (Resolve-Path -LiteralPath $InnoCompiler).Path
    }

    $PythonExe = Initialize-APEBuildEnvironment $projectRoot $PythonExe ([bool]($SkipDependencyInstall -or $SkipBuild))
    $versionOutput = & $PythonExe (Join-Path $PSScriptRoot 'windows_build_info.py')
    if ($LASTEXITCODE -ne 0) { throw 'Interprete o versione progetto non supportati.' }
    $appVersion = ([string]$versionOutput).Trim()
    if ($appVersion -notmatch '^\d+\.\d+\.\d+$') { throw 'Versione progetto non valida per la build.' }
    $privacyCheck = Join-Path $projectRoot 'build_tools\common\release_privacy.py'
    if (-not $SkipBuild) {
        & $PythonExe $privacyCheck --quiet --mupdf-native
        if ($LASTEXITCODE -ne 0) {
            if ($SkipDependencyInstall) { throw 'La libreria MuPDF contiene percorsi locali. Prepara le dipendenze con build_patched_mupdf.ps1.' }
            & (Join-Path $PSScriptRoot 'build_patched_mupdf.ps1') -PythonExe $PythonExe
        }
    }
    if ($PrepareOnly) { Write-Host "Ambiente pronto: $PythonExe. Rilancia il BAT per compilare."; return }

    if (-not $SkipBuild) {
        $opencvArguments = @()
        if ($SkipDependencyInstall) { $opencvArguments += '--check' }
        Write-Host '[APE] Verifico OpenCV senza Intel IPP e FFmpeg...'
        & $PythonExe (Join-Path $projectRoot 'build_tools\common\build_opencv.py') @opencvArguments
        if ($LASTEXITCODE -ne 0) { throw 'OpenCV non pronto. Esegui build_opencv.py nel virtualenv oppure ricompila senza -SkipDependencyInstall.' }
        # Refresh application metadata even when third-party dependencies are
        # already installed: copy_metadata must bundle the current version.
        & $PythonExe -m pip install --no-index --no-build-isolation --no-deps --disable-pip-version-check -e '.'
        if ($LASTEXITCODE -ne 0) { throw 'Preparazione metadati applicazione fallita.' }
        $env:APE_LICENSE_FILE = $LicenseFile
        $sourceArguments = @()
        if ($OfflineSources) { $sourceArguments += '--offline' }
        Write-Host '[APE] Preparo licenze e sorgenti delle dipendenze...'
        & $PythonExe (Join-Path $projectRoot 'build_tools\common\prepare_release.py') @sourceArguments
        if ($LASTEXITCODE -ne 0) { throw 'Preparazione licenze/sorgenti fallita.' }
        & (Join-Path $PSScriptRoot 'build_windows_shell.ps1')
        & (Join-Path $PSScriptRoot 'build_windows_sandbox.ps1')
        Write-Host '[APE] Compilo tutti i moduli applicativi in PYD...'
        & $PythonExe (Join-Path $projectRoot 'build_tools\common\compile_app_modules.py')
        if ($LASTEXITCODE -ne 0) { throw 'Compilazione PYD fallita.' }
        if ($Release) {
            & (Join-Path $PSScriptRoot 'sign_windows_release.ps1') -BundleDir (Join-Path $projectRoot 'build\windows\cython\lib') -CompileStage -CertificateThumbprint $CertificateThumbprint -TimestampServer $TimestampServer
        }
        & $PythonExe (Join-Path $projectRoot 'build_tools\common\prepare_bundle_integrity.py')
        if ($LASTEXITCODE -ne 0) { throw 'Preparazione integrità applicazione fallita.' }
        Write-Host '[APE] Creo il pacchetto Windows...'
        & $PythonExe -m PyInstaller --noconfirm --distpath $DistRoot --workpath (Join-Path $projectRoot 'build\pyinstaller') (Join-Path $projectRoot 'build_tools\windows\APEPDFStudio.spec')
        if ($LASTEXITCODE -ne 0) { throw 'Build PyInstaller fallita.' }
    }

    $bundleDir = Join-Path $DistRoot 'APEPDFStudio'
    & $PythonExe $privacyCheck $bundleDir $LicenseFile
    if ($LASTEXITCODE -ne 0) { throw 'Il pacchetto contiene metadati di build locali o non è verificabile.' }
    & $PythonExe (Join-Path $projectRoot 'build_tools\common\prepare_bundle_integrity.py') --verify $bundleDir
    if ($LASTEXITCODE -ne 0) { throw 'Integrità del pacchetto non verificata. Ricrea la build.' }
    & $PythonExe (Join-Path $projectRoot 'scripts\security_inventory.py') --release --output (Join-Path $DistRoot 'security-inventory.json')
    if ($LASTEXITCODE -ne 0) { throw 'Gate delle dipendenze di sicurezza non superato.' }
    if (-not (Test-Path -LiteralPath (Join-Path $bundleDir 'APEPDFShell.dll'))) { throw 'Shell extension mancante. Ricrea il pacchetto Windows.' }
    if (-not (Test-Path -LiteralPath (Join-Path $bundleDir 'APEPDFSandbox.exe'))) { throw 'Launcher sandbox mancante. Ricrea il pacchetto Windows.' }
    foreach ($exeName in @('APEPDFStudio.exe', 'APEPDFWorker.exe', 'ape-pdf-cli.exe')) {
        $exePath = Join-Path $bundleDir $exeName
        if (-not (Test-Path -LiteralPath $exePath -PathType Leaf)) { throw "Eseguibile mancante: $exePath" }
        $fileVersion = [Diagnostics.FileVersionInfo]::GetVersionInfo($exePath).ProductVersion
        if ($fileVersion -ne $appVersion) { throw "Versione $exeName ($fileVersion) diversa da pyproject.toml ($appVersion). Ricrea il pacchetto." }
    }
    if (-not (Get-ChildItem -LiteralPath (Join-Path $bundleDir '_internal') -Filter 'python3*.dll' -File)) { throw 'Il pacchetto non contiene il runtime Python.' }
    Write-Host '[APE] Preparo gli archivi sorgenti corrispondenti al pacchetto...'
    $releaseDir = Join-Path $DistRoot 'release'
    & $PythonExe (Join-Path $projectRoot 'build_tools\common\prepare_release.py') --bundle $bundleDir --output $releaseDir
    if ($LASTEXITCODE -ne 0) { throw 'Verifica licenze e archivi sorgenti fallita.' }
    if (-not $SkipSmokeTest) {
        Write-Host '[APE] Verifico gli eseguibili...'
        & $PythonExe (Join-Path $projectRoot 'build_tools\checks\smoke_windows_bundle.py') --bundle $bundleDir
        if ($LASTEXITCODE -ne 0) { throw 'Verifica del pacchetto Windows fallita.' }
    }
    if ($Release) {
        & (Join-Path $PSScriptRoot 'sign_windows_release.ps1') -BundleDir $bundleDir -CertificateThumbprint $CertificateThumbprint -TimestampServer $TimestampServer
        & $PythonExe (Join-Path $projectRoot 'build_tools\common\prepare_bundle_integrity.py') --verify $bundleDir
        if ($LASTEXITCODE -ne 0) { throw 'Integrità alterata dopo la firma del pacchetto.' }
    } else { Write-Warning 'Build di sviluppo non firmata: non pubblicare come release autenticata.' }
    if ($BundleOnly) { Write-Host "Pacchetto pronto: $bundleDir"; return }

    if (-not $SkipSmokeTest) {
        Write-Host '[APE] Verifico backup e ripristino dei provider PDF...'
        & $PythonExe (Join-Path $projectRoot 'build_tools\checks\test_windows_shell_installer.py') --inno-compiler $InnoCompiler
        if ($LASTEXITCODE -ne 0) { throw 'Verifica delle registrazioni shell fallita.' }
    }

    $outputDir = Join-Path $DistRoot 'installer'
    $installerName = "APEPDFStudio-$appVersion-Setup-x64.exe"
    $installerStage = Join-Path $projectRoot ('build\windows\installer-' + [guid]::NewGuid().ToString('N'))
    $stagedInstaller = Join-Path $installerStage $installerName
    $signArguments=@()
    if ($Release) {
        # Inno invokes the same verified procedure for its generated uninstaller.
        # Thumbprints contain no private key material. $q/$f are Inno placeholders.
        if ($TimestampServer -match '["\r\n]') { throw 'Timestamp URL non valida.' }
        $signScript=Join-Path $PSScriptRoot 'sign_windows_release.ps1'
        $signCommand='powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $q'+$signScript+'$q -BundleDir $q'+$bundleDir+'$q -Installer $q$f$q -CertificateThumbprint '+$CertificateThumbprint+' -TimestampServer $q'+$TimestampServer+'$q'
        $signArguments=@('/DReleaseSign',('/SAPECodeSign='+$signCommand))
    }
    Write-Host '[APE] Compilo l''installer Inno Setup...'
    & $InnoCompiler @signArguments "/DAppVersion=$appVersion" "/DDistDir=$bundleDir" "/DOutputDir=$installerStage" "/DLicensePath=$LicenseFile" (Join-Path $projectRoot 'build_tools\windows\APEPDFStudio.iss')
    if ($LASTEXITCODE -ne 0) { throw 'Compilazione Inno Setup fallita.' }
    if ($Release) {
        & (Join-Path $PSScriptRoot 'sign_windows_release.ps1') -BundleDir $bundleDir -Installer $stagedInstaller -CertificateThumbprint $CertificateThumbprint -TimestampServer $TimestampServer
    }
    & $PythonExe $privacyCheck $stagedInstaller
    if ($LASTEXITCODE -ne 0) { throw "Controllo dei metadati locali nell'installer fallito." }
    & $PythonExe (Join-Path $projectRoot 'build_tools\common\prepare_release.py') --bundle $bundleDir --output $releaseDir --installer $stagedInstaller
    if ($LASTEXITCODE -ne 0) { throw 'Finalizzazione manifesti release fallita.' }
    $null = New-Item -ItemType Directory -Path $outputDir -Force
    $installer = Join-Path $outputDir $installerName
    Move-Item -LiteralPath $stagedInstaller -Destination $installer -Force
    Write-Host "Installer pronto: $installer"
}
finally {
    [Environment]::SetEnvironmentVariable('APE_LICENSE_FILE', $previousLicense, 'Process')
    if ($transcribing) { Stop-Transcript | Out-Host }
    Pop-Location
}
