"""Open untrusted worker artifacts by a verified, stable, non-reparse handle."""
from contextlib import contextmanager
import os
from pathlib import Path
import stat


@contextmanager
def open_artifact(path,root,limit=256*1024*1024):
    # The broker records a canonical root when it creates the session. Do not
    # resolve it again here: a replaced directory must not redefine the boundary.
    path,root=Path(path).absolute(),Path(root).absolute()
    stream=None
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes as w
        import msvcrt
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,ctypes.c_void_p,w.DWORD,w.DWORD,w.HANDLE]
        kernel.CreateFileW.restype=w.HANDLE
        kernel.CloseHandle.argtypes=[w.HANDLE]
        kernel.GetFinalPathNameByHandleW.argtypes=[w.HANDLE,w.LPWSTR,w.DWORD,w.DWORD]
        class Info(ctypes.Structure):
            _fields_=[('attributes',w.DWORD),('created',w.FILETIME),('accessed',w.FILETIME),('written',w.FILETIME),('volume',w.DWORD),('high',w.DWORD),('low',w.DWORD),('links',w.DWORD),('indexHigh',w.DWORD),('indexLow',w.DWORD)]
        kernel.GetFileInformationByHandle.argtypes=[w.HANDLE,ctypes.POINTER(Info)]
        handle=kernel.CreateFileW(str(path),0x80000000,1,None,3,0x08200000,None)
        if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
        try:
            info=Info();name=ctypes.create_unicode_buffer(32768)
            length=kernel.GetFinalPathNameByHandleW(handle,name,len(name),0)
            if not kernel.GetFileInformationByHandle(handle,ctypes.byref(info)) or not 0<length<len(name):raise OSError('Cannot verify artifact handle')
            final=name.value
            if final.startswith('\\\\?\\UNC\\'):final='\\\\'+final[8:]
            elif final.startswith('\\\\?\\'):final=final[4:]
            if not Path(final).is_relative_to(root) or info.attributes & (0x400|0x10) or info.links!=1 or not 0<(info.high<<32|info.low)<=limit:
                raise OSError('Artifact outside workspace, linked or oversized')
            fd=msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY)
            handle=None
            stream=os.fdopen(fd,'rb')
        finally:
            if handle is not None:kernel.CloseHandle(handle)
    else:
        fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
        try:
            info=os.fstat(fd)
            actual=Path(os.readlink(f'/proc/self/fd/{fd}')) if Path('/proc/self/fd').is_dir() else path.resolve()
            if not actual.is_relative_to(root) or not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or not 0<info.st_size<=limit:raise OSError('Invalid artifact handle')
            stream=os.fdopen(fd,'rb');fd=None
        finally:
            if fd is not None:os.close(fd)
    try:yield stream
    finally:
        if stream is not None:stream.close()


def read_artifact(path,root,limit):
    with open_artifact(path,root,limit) as stream:data=stream.read(limit+1)
    if len(data)>limit:raise OSError('Artifact grew beyond budget')
    return data
