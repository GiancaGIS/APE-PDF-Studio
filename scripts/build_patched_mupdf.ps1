<# Build an explicitly identified PyMuPDF wrapper against corrected MuPDF sources. #>
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$PythonExe)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$root=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$PythonExe=(Resolve-Path -LiteralPath $PythonExe).Path
$wheels=Join-Path $root '.dev-deps\patched-wheels'
$null=New-Item -ItemType Directory -Path $wheels -Force
$existing=Get-ChildItem -LiteralPath $wheels -Filter 'pymupdf-1.28.2.post1285-*-win_amd64.whl' -File
if (-not $existing) {
    & $PythonExe (Join-Path $PSScriptRoot 'prepare_patched_mupdf.py')
    if ($LASTEXITCODE -ne 0) { throw 'Preparazione sorgenti MuPDF fallita.' }
    $keys=@('PIPCL_SHOW_ENV','PYMUPDF_SETUP_MUPDF_BUILD','PYMUPDF_SETUP_MUPDF_VS_UPGRADE','PYMUPDF_SETUP_MUPDF_REBUILD')
    $previous=@{}
    foreach($key in $keys) { $previous[$key]=[Environment]::GetEnvironmentVariable($key,'Process') }
    try {
        $env:PIPCL_SHOW_ENV='0'
        $env:PYMUPDF_SETUP_MUPDF_BUILD=Join-Path $root '.dev-deps\patched-mupdf\mupdf-1.28.5-source'
        $env:PYMUPDF_SETUP_MUPDF_VS_UPGRADE='1'
        $env:PYMUPDF_SETUP_MUPDF_REBUILD='1'
        & $PythonExe -m pip wheel --build-constraint (Join-Path $root 'requirements-native-build.txt') --no-deps --wheel-dir $wheels (Join-Path $root '.dev-deps\patched-mupdf\pymupdf-1.28.2')
        if ($LASTEXITCODE -ne 0) { throw 'Compilazione PyMuPDF/MuPDF corretti fallita.' }
    } finally { foreach($key in $keys) { [Environment]::SetEnvironmentVariable($key,$previous[$key],'Process') } }
}
& $PythonExe -m pip install --no-index --find-links $wheels --no-deps 'pymupdf==1.28.2.post1285'
if ($LASTEXITCODE -ne 0) { throw 'Installazione wheel MuPDF locale fallita.' }
& $PythonExe -c "import pymupdf; assert pymupdf.mupdf_version == '1.28.5', 'Wrong native MuPDF runtime'"
if ($LASTEXITCODE -ne 0) { throw 'Versione MuPDF nativa inattesa.' }
