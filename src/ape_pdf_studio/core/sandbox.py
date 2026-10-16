"""Native Windows AppContainer command construction; no permissive fallback."""
from pathlib import Path
import os
import sys

from .errors import PDFSecurityError


def in_appcontainer():
    if os.name != 'nt':
        return False
    import ctypes
    from ctypes import wintypes as w
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    adv=ctypes.WinDLL('advapi32',use_last_error=True)
    kernel.GetCurrentProcess.restype=w.HANDLE
    kernel.CloseHandle.argtypes=[w.HANDLE]
    adv.OpenProcessToken.argtypes=[w.HANDLE,w.DWORD,ctypes.POINTER(w.HANDLE)]
    adv.GetTokenInformation.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD)]
    token=w.HANDLE(); value=w.DWORD(); length=w.DWORD()
    if not adv.OpenProcessToken(kernel.GetCurrentProcess(),8,ctypes.byref(token)):
        return False
    try:
        return bool(adv.GetTokenInformation(token,29,ctypes.byref(value),ctypes.sizeof(value),ctypes.byref(length)) and value.value)
    finally:
        kernel.CloseHandle(token)


def sandbox_command(kind, work, *arguments, timeout=12000, output_limit=4*1024*1024):
    from .worker_process import worker_command
    return wrap_command(worker_command(kind,*arguments), work, timeout=timeout, output_limit=output_limit)


def wrap_command(command, work, *, timeout=12000, output_limit=4*1024*1024):
    raw=list(command)
    if os.name != 'nt':
        # Other platforms retain process/resource isolation and are explicitly
        # reported as lacking an OS sandbox. Never advertise Windows protections.
        return raw
    if getattr(sys,'frozen',False):
        folder=Path(sys.executable).resolve().parent
        host=folder/'APEPDFSandbox.exe'
        code=[Path(raw[0]), folder/'_internal']
    else:
        root=Path(__file__).resolve().parents[3]
        host=root/'build/windows/sandbox/APEPDFSandbox.exe'
        runtime=root/'.dev-deps/sandbox-python'
        code=[runtime,root/'src',root/'.venv-build/Lib/site-packages']
        raw[0]=str(runtime/'python.exe')
    if not host.is_file() or any(not p.exists() for p in code) or not Path(raw[0]).is_file():
        raise PDFSecurityError('Sandbox Windows non disponibile: prepara il runtime e il launcher prima di aprire documenti.')
    if not 100 <= timeout <= 3600000 or not 0 <= output_limit <= 256*1024*1024:
        raise ValueError('Invalid sandbox budget')
    return [str(host),str(Path(work).resolve()),str(timeout),str(output_limit),str(len(code)),*map(str,code),*raw]
