# Shared bootstrap. Python package installation stays inside a virtualenv.
function Initialize-APEBuildEnvironment([string]$ProjectRoot, [string]$RequestedPython, [bool]$CheckOnly) {
    $prefixArguments = @()
    if ($RequestedPython) {
        $command = Get-Command $RequestedPython -CommandType Application -ErrorAction SilentlyContinue
        if (-not $command) { throw "Interprete non trovato: $RequestedPython" }
        $basePython = $command.Source
        if ([IO.Path]::GetFileName($basePython) -ieq 'py.exe') { $prefixArguments = @('-3.14') }
    } else {
        $basePython = Join-Path $ProjectRoot '.venv-build\Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $basePython -PathType Leaf)) {
            $command = Get-Command 'py.exe' -CommandType Application -ErrorAction SilentlyContinue
            if ($command) { $basePython = $command.Source; $prefixArguments = @('-3.14') }
            else {
                $command = Get-Command 'python.exe' -CommandType Application -ErrorAction SilentlyContinue
                if (-not $command) { throw 'Installa la versione Python x64 indicata in requirements-release.txt.' }
                $basePython = $command.Source
            }
        }
    }
    $report = Join-Path $ProjectRoot 'build\windows-build-environment.json'
    Remove-Item -LiteralPath $report -ErrorAction SilentlyContinue
    $arguments = @('--result', $report)
    if ($CheckOnly) { $arguments += '--check-only' }
    & $basePython @prefixArguments (Join-Path $ProjectRoot 'scripts\prepare_build.py') @arguments | Out-Host
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $report -PathType Leaf)) {
        throw 'Preparazione virtualenv fallita. Controlla build\windows-build.log; Python globale non viene modificato.'
    }
    $environment = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
    return [string]$environment.python
}
