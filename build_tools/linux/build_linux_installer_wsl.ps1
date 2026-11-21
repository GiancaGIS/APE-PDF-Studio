[CmdletBinding()]
param([string]$Distribution = 'Ubuntu', [switch]$PrepareOnly)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$wsl = (Get-Command wsl.exe -CommandType Application | Select-Object -First 1).Source
$source = & $wsl -d $Distribution --exec wslpath -a $root
if ($LASTEXITCODE -ne 0 -or -not $source) { throw 'Distribuzione WSL o checkout non accessibile.' }
$mode = if ($PrepareOnly) { 'prepare' } else { 'build' }
& $wsl -d $Distribution --exec bash "$source/build_tools/linux/build_linux_wsl.sh" $source $mode
exit $LASTEXITCODE
