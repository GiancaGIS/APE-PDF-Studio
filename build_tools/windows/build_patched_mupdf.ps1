<# Build an explicitly identified PyMuPDF wrapper against corrected MuPDF sources. #>
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$PythonExe)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$root=(Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$PythonExe=(Resolve-Path -LiteralPath $PythonExe).Path
$wheels=Join-Path $root '.dev-deps\patched-wheels'
$null=New-Item -ItemType Directory -Path $wheels -Force
$privacyCheck=Join-Path $root 'build_tools\common\release_privacy.py'
$wheelPatterns='pymupdf-1.28.2.post1285-*-win_amd64.whl'
$wheel=$null
foreach ($candidate in (Get-ChildItem -LiteralPath $wheels -Filter $wheelPatterns -File)) {
    & $PythonExe $privacyCheck --quiet --mupdf-wheel $candidate.FullName
    if ($LASTEXITCODE -eq 0) { $wheel=$candidate.FullName; break }
}
if (-not $wheel) {
    # Isolate this compilation from object files built with older flags.
    $stage=Join-Path $root ('.dev-deps\mu-' + [guid]::NewGuid().ToString('N').Substring(0,8))
    & $PythonExe (Join-Path $root 'build_tools\common\prepare_patched_mupdf.py') --build-root $stage
    if ($LASTEXITCODE -ne 0) { throw 'Preparazione sorgenti MuPDF fallita.' }
    $stageWheels=Join-Path $stage 'wheels'
    $keys=@('PIPCL_SHOW_ENV','PYMUPDF_SETUP_MUPDF_BUILD','PYMUPDF_SETUP_MUPDF_VS_UPGRADE','PYMUPDF_SETUP_MUPDF_REBUILD','_CL_','LINK')
    $previous=@{}
    foreach($key in $keys) { $previous[$key]=[Environment]::GetEnvironmentVariable($key,'Process') }
    try {
        $env:PIPCL_SHOW_ENV='0'
        $env:PYMUPDF_SETUP_MUPDF_BUILD=Join-Path $stage 'mupdf-1.28.5-source'
        $env:PYMUPDF_SETUP_MUPDF_VS_UPGRADE='1'
        $env:PYMUPDF_SETUP_MUPDF_REBUILD='1'
        # MSVC expands __FILE__ even in release builds. Keep only paths
        # relative to the checkout and a filename in the PDB reference.
        $env:_CL_ = ($previous['_CL_'] + ' /d1trimfile:"' + $root + '"').Trim()
        $env:LINK = ($previous['LINK'] + ' /PDBALTPATH:%_PDB%').Trim()
        & $PythonExe -m pip wheel --no-cache-dir --build-constraint (Join-Path $root 'requirements-native-build.txt') --no-deps --wheel-dir $stageWheels (Join-Path $stage 'pymupdf-1.28.2')
        if ($LASTEXITCODE -ne 0) { throw 'Compilazione PyMuPDF/MuPDF corretti fallita.' }
        $built=Get-ChildItem -LiteralPath $stageWheels -Filter $wheelPatterns -File
        if (@($built).Count -ne 1) { throw 'Wheel MuPDF di build mancante o ambigua.' }
        & $PythonExe $privacyCheck --mupdf-wheel $built.FullName
        if ($LASTEXITCODE -ne 0) { throw 'La wheel MuPDF contiene percorsi di build locali.' }
        $wheel=Join-Path $wheels $built.Name
        Move-Item -LiteralPath $built.FullName -Destination $wheel -Force
    } finally { foreach($key in $keys) { [Environment]::SetEnvironmentVariable($key,$previous[$key],'Process') } }
}
& $PythonExe $privacyCheck --quiet --mupdf-native
if ($LASTEXITCODE -ne 0) {
    & $PythonExe -m pip install --no-index --no-deps --force-reinstall $wheel
}
if ($LASTEXITCODE -ne 0) { throw 'Installazione wheel MuPDF locale fallita.' }
& $PythonExe $privacyCheck --mupdf-native
if ($LASTEXITCODE -ne 0) { throw 'Versione MuPDF nativa inattesa o con percorsi locali.' }
