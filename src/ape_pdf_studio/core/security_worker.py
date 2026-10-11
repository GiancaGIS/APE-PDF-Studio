"""Preflight worker. Resource limits are containment, not an OS security sandbox."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys


def limit_memory():
    limit = 768 * 1024 * 1024
    if os.name != "nt":
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
            return True
        except (ImportError, OSError, ValueError):
            return False
    try:
        import ctypes
        from ctypes import wintypes
        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ("ReadOperationCount", "WriteOperationCount",
                        "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IO),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        job = kernel.CreateJobObjectW(None, None)
        if not job:
            return False
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
        info.ProcessMemoryLimit = limit
        success = kernel.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)) and kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess())
        if not success:
            kernel.CloseHandle(job)
        # Keep the handle alive for the worker's lifetime; the OS closes it on exit.
        return bool(success)
    except (OSError, AttributeError):
        return False


def main():
    memory_limited = limit_memory()
    # -I ignores PYTHONPATH and the current directory. Import only our application
    # source directory and the interpreter's installed dependencies.
    if not getattr(sys, "frozen", False):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from pypdf import PdfReader
    from ape_pdf_studio.core.errors import PDFSecurityError
    from ape_pdf_studio.core.localization import tr
    from ape_pdf_studio.core.security import MAX_FILE_BYTES, configure_parser, inspect_reader
    configure_parser()
    try:
        request = json.loads(sys.stdin.buffer.read(65536))
        source = Path(request["path"])
        if source.stat().st_size > MAX_FILE_BYTES:
            raise PDFSecurityError(tr("dimensione superiore al limite di 256 MiB."))
        with PdfReader(source) as reader:
            if reader.is_encrypted:
                # Permissions-only encryption has an empty user password.
                # Being encrypted alone does not imply a password is required
                # to read the PDF; inspect it after successful authentication.
                if not reader.decrypt(request.get("password") or ""):
                    return {"status": "password"}
            findings = inspect_reader(reader)
        return {"status": "checked", "findings": findings, "memory_limited": memory_limited}
    except PDFSecurityError as exc:
        return {"status": "blocked", "reason": str(exc)}
    except Exception:
        return {"status": "blocked", "reason": tr("file non leggibile o analisi incompleta.")}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=True), flush=True)
