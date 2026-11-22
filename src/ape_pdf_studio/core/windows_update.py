"""Identify an Inno installation and configure the Windows signature check."""
import ctypes
from pathlib import Path
import sys

UNINSTALL_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{F890B690-6157-47AE-94CC-1194928D89EC}_is1"

_SIGNER_FUNCTION = r"""
$ErrorActionPreference = 'Stop'
$env:PSModulePath = Join-Path $PSHOME 'Modules'
function Get-ApeSignerHash([string]$Path, [string]$Version) {
    $signature = Microsoft.PowerShell.Security\Get-AuthenticodeSignature -LiteralPath $Path
    if ($signature.Status -ne 'Valid' -or -not $signature.SignerCertificate) { throw 'Invalid signature' }
    if ($Version) {
        $info = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($Path)
        if ($info.ProductName -cne 'APE PDF Studio' -or $info.ProductVersion -cne $Version) { throw 'Invalid product version' }
    }
    $hash = [System.Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString($hash.ComputeHash($signature.SignerCertificate.RawData)).Replace('-', '')
    } finally {
        $hash.Dispose()
    }
}
"""

SIGNATURE_COMMAND = _SIGNER_FUNCTION + r"""
try {
    [Console]::WriteLine((Get-ApeSignerHash $env:APE_UPDATE_VERIFY_PATH $env:APE_UPDATE_VERIFY_VERSION))
} catch {
    exit 1
}
"""

# Run outside the GUI process so setup can replace all its files. Recheck the
# on-disk file at handoff before granting it execution or triggering UAC.
INSTALL_COMMAND = _SIGNER_FUNCTION + r"""
$path = $env:APE_UPDATE_VERIFY_PATH
try {
    $owner = Get-Process -Id ([int]$env:APE_UPDATE_OWNER) -ErrorAction SilentlyContinue
    if ($owner -and -not $owner.WaitForExit(120000)) { throw 'The application is still running' }
    if ($env:APE_UPDATE_SCOPE -cnotin @('/CURRENTUSER', '/ALLUSERS')) { throw 'Invalid installation scope' }
    if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ine $env:APE_UPDATE_SHA256 -or
        (Get-ApeSignerHash $path $env:APE_UPDATE_VERIFY_VERSION) -cne $env:APE_UPDATE_SIGNER) {
        throw 'Installer verification failed'
    }
    $flags = @($env:APE_UPDATE_SCOPE, '/SP-', '/SILENT', '/NOCLOSEAPPLICATIONS', '/NORESTART')
    $setup = Start-Process -FilePath $path -ArgumentList $flags -WorkingDirectory (Split-Path -LiteralPath $path) -Wait -PassThru
    if ($setup.ExitCode -notin @(0, 2, 5)) { throw 'Installation failed' }
} catch {
    Add-Type -AssemblyName System.Windows.Forms
    [void][System.Windows.Forms.MessageBox]::Show($env:APE_UPDATE_FAILURE, $env:APE_UPDATE_TITLE)
} finally {
    Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
}
"""


def installation_scope() -> str | None:
    """Return the setup switch only for the running, registered Windows install."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False) or sys.maxsize <= 2**32:
        return None
    executable = Path(sys.executable)
    if executable.name.lower() != "apepdfstudio.exe":
        return None
    import winreg

    scopes = []
    for hive, switch in ((winreg.HKEY_CURRENT_USER, "/CURRENTUSER"), (winreg.HKEY_LOCAL_MACHINE, "/ALLUSERS")):
        try:
            with winreg.OpenKey(hive, UNINSTALL_KEY, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                location, kind = winreg.QueryValueEx(key, "InstallLocation")
            if kind == winreg.REG_SZ and Path(location).is_absolute() and executable.parent.samefile(location):
                scopes.append(switch)
        except OSError:
            continue
    return scopes[0] if len(scopes) == 1 else None


def system_powershell() -> Path:
    system = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetSystemDirectoryW(system, len(system))
    if not 0 < length < len(system):
        raise OSError("Cannot locate the Windows system directory")
    return Path(system.value) / "WindowsPowerShell" / "v1.0" / "powershell.exe"
