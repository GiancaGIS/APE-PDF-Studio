<# Sign with an existing Windows certificate; never stores keys or passwords. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$BundleDir,
    [string]$Installer,
    [string]$CertificateThumbprint,
    [string]$TimestampServer,
    [switch]$VerifyOnly
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$BundleDir = (Resolve-Path -LiteralPath $BundleDir).Path
$files = @('APEPDFStudio.exe','APEPDFWorker.exe','ape-pdf-cli.exe','APEPDFSandbox.exe','APEPDFShell.dll') |
    ForEach-Object { Join-Path $BundleDir $_ }
foreach ($path in $files) { if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing release binary: $path" } }
$owned=@($files)
$dependencies=@(Get-ChildItem -LiteralPath (Join-Path $BundleDir '_internal') -Recurse -File |
    Where-Object { $_.Extension -in @('.dll','.pyd','.exe') } | Select-Object -ExpandProperty FullName)
$files=@($files+$dependencies | Sort-Object -Unique)
if ($Installer) { $Installer = (Resolve-Path -LiteralPath $Installer).Path }
if (-not $VerifyOnly) {
    if ($CertificateThumbprint -notmatch '^[0-9a-fA-F]{40}$') { throw 'Specify the SHA-1 thumbprint of an existing code-signing certificate.' }
    $timestamp = [Uri]$TimestampServer
    if (-not $timestamp.IsAbsoluteUri -or $timestamp.Scheme -ne 'https' -or $timestamp.UserInfo -or $timestamp.Fragment) { throw 'Specify an HTTPS RFC3161 timestamp service.' }
    $certificate = Get-Item -LiteralPath "Cert:\CurrentUser\My\$CertificateThumbprint"
    if (-not $certificate.HasPrivateKey -or $certificate.NotBefore -gt (Get-Date) -or $certificate.NotAfter -le (Get-Date) -or
        -not ($certificate.EnhancedKeyUsageList.ObjectId.Value -contains '1.3.6.1.5.5.7.3.3')) { throw 'A valid code-signing certificate with a private key is required.' }
    $command = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($command) { $signTool = $command.Source }
    else {
        $signTool = Get-ChildItem -LiteralPath "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Filter signtool.exe -Recurse -File |
            Where-Object { $_.Directory.Name -eq 'x64' } | Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
    }
    if (-not $signTool) { throw 'Install the Windows SDK signing tools.' }
    # At the second stage only sign the installer: its bundled files must remain
    # byte-for-byte identical to the already signed and packaged application.
    $targets = if ($Installer) { @($Installer) } else {
        @($files | Where-Object { $owned -contains $_ -or (Get-AuthenticodeSignature -LiteralPath $_).Status -eq 'NotSigned' })
    }
    foreach ($path in $targets) {
        & $signTool sign /sha1 $CertificateThumbprint /s My /fd SHA256 /tr $TimestampServer /td SHA256 $path
        if ($LASTEXITCODE -ne 0) { throw "Signing failed: $path" }
    }
}
if ($Installer) { $files += $Installer }
foreach ($path in $files) {
    $signature = Get-AuthenticodeSignature -LiteralPath $path
    $applicationBinary=($owned -contains $path) -or ($Installer -and $path -eq $Installer)
    if ($signature.Status -ne 'Valid' -or ($applicationBinary -and -not $signature.TimeStamperCertificate)) { throw "Missing valid Authenticode signature: $path" }
    if ($applicationBinary -and $CertificateThumbprint -and $signature.SignerCertificate.Thumbprint -ne $CertificateThumbprint) { throw "Unexpected release signer: $path" }
}
Write-Host 'Release signatures verified; application binaries have trusted timestamps.'
