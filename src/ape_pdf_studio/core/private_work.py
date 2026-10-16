"""Private leased workspaces, conservative stale cleanup and disk budgets."""
from __future__ import annotations

import ctypes
import hashlib
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import weakref
from uuid import uuid4

MAX_DOCUMENT_BYTES = 1024 * 1024 * 1024
MAX_SESSION_BYTES = 4 * MAX_DOCUMENT_BYTES
MIN_FREE_BYTES = 128 * 1024 * 1024
ROOT_NAME = "APE-PDF-Studio-Sessions"


def private_acl(path):
    if os.name != "nt":
        if path is None:
            return str(os.getuid())
        os.chmod(path, 0o700)
        return
    from ctypes import wintypes as w
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    adv.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    adv.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)]
    adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(w.LPWSTR)]
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [w.LPCWSTR,w.DWORD,ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p]
    adv.SetFileSecurityW.argtypes = [w.LPCWSTR,w.DWORD,ctypes.c_void_p]
    token = w.HANDLE()
    if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    sids = []
    try:
        for info in (1, 31):  # TOKEN_USER and optional TOKEN_APPCONTAINER_INFORMATION
            size = w.DWORD()
            adv.GetTokenInformation(token, info, None, 0, ctypes.byref(size))
            if not size.value:
                if info == 1:
                    raise ctypes.WinError(ctypes.get_last_error())
                continue
            data = ctypes.create_string_buffer(size.value)
            if not adv.GetTokenInformation(token, info, data, size, ctypes.byref(size)):
                if info == 1:
                    raise ctypes.WinError(ctypes.get_last_error())
                continue
            sid = ctypes.cast(data, ctypes.POINTER(ctypes.c_void_p))[0]
            if sid:
                text = w.LPWSTR()
                if not adv.ConvertSidToStringSidW(sid, ctypes.byref(text)):
                    raise ctypes.WinError(ctypes.get_last_error())
                sids.append(text.value)
                kernel.LocalFree(text)
        if path is None:
            return sids[0]
        descriptor = ctypes.c_void_p()
        sddl = "D:P(A;OICI;FA;;;SY)" + "".join(f"(A;OICI;FA;;;{sid})" for sid in sids)
        if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not adv.SetFileSecurityW(str(path), 0x80000004, descriptor):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            kernel.LocalFree(descriptor)
    finally:
        kernel.CloseHandle(token)


def work_root():
    from .sandbox import in_appcontainer
    if in_appcontainer():
        # Windows redirects TEMP to the package profile. Keep document artifacts
        # in the broker-owned lease so they survive a worker crash for recovery.
        root=Path(os.environ['APE_WORKSPACE']).resolve()
        if root!=Path.cwd().resolve():
            raise OSError('Invalid sandbox workspace')
        root=root/'sessions'
        root.mkdir(mode=0o777,exist_ok=True)
        return root
    identity = hashlib.sha256(private_acl(None).encode()).hexdigest()[:16]
    root = Path(tempfile.gettempdir()) / (ROOT_NAME+"-"+identity)
    if root.is_symlink() or (root.exists() and getattr(root.lstat(), "st_file_attributes", 0) & 0x400):
        raise OSError("Unsafe workspace root")
    root.mkdir(mode=0o700, exist_ok=True)
    private_acl(root)
    return root.resolve()


def tree_bytes(folder):
    total, count = 0, 0
    for base, dirs, files in os.walk(folder, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(base)/name
            try:info = path.lstat()
            except FileNotFoundError:continue  # A completed render may remove its raster concurrently.
            if path.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                raise OSError("Reparse point in a private workspace")
            count += 1
            if count > 50000:
                raise OSError("Too many workspace files")
            if name in files:
                total += info.st_size
    return total


def check_budget(folder, reserve=0):
    if (tree_bytes(folder)+reserve > MAX_DOCUMENT_BYTES or tree_bytes(work_root())+reserve > MAX_SESSION_BYTES or
            shutil.disk_usage(folder).free < MIN_FREE_BYTES+reserve):
        raise OSError("Private PDF workspace disk budget exceeded")


def lease(path):
    if os.name == "nt":
        from ctypes import wintypes as w
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [w.LPCWSTR,w.DWORD,w.DWORD,ctypes.c_void_p,w.DWORD,w.DWORD,w.HANDLE]
        kernel.CreateFileW.restype = w.HANDLE
        kernel.CloseHandle.argtypes = [w.HANDLE]
        handle = kernel.CreateFileW(str(path), 0xC0000000, 0, None, 4, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            raise OSError("Workspace is leased")
        return lambda: kernel.CloseHandle(handle)
    import fcntl
    stream = path.open("a+b")
    try:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        stream.close()
        raise
    return stream.close


def cleanup_profiles(folder):
    """Recover only our random profiles after a forcibly terminated supervisor."""
    if os.name!='nt':return
    from .sandbox import in_appcontainer
    if in_appcontainer():return
    userenv=ctypes.WinDLL('userenv',use_last_error=True)
    userenv.DeleteAppContainerProfile.argtypes=[ctypes.c_wchar_p]
    prefix='.'+Path(folder).name+'.profile-'
    for path in Path(folder).parent.glob(prefix+'*'):
        suffix=path.name.removeprefix(prefix)
        if re.fullmatch('[0-9a-fA-F]{32}',suffix) and not path.is_symlink() and not (getattr(path.lstat(),'st_file_attributes',0)&0x400):
            release=lease(path)
            try:
                result=userenv.DeleteAppContainerProfile('APE.PDF.'+suffix)
                if result<0 and result not in (-2147024894,-2147024893,-2147023728):
                    raise PermissionError('AppContainer profile still in use')
            finally:release()
            path.unlink(missing_ok=True)


class PrivateDirectory:
    def __init__(self, suffix=None, prefix=None, dir=None, **kwargs):
        root = Path(dir).resolve() if dir is not None else work_root()
        if shutil.disk_usage(root).free < MIN_FREE_BYTES:
            raise OSError("Insufficient free space for PDF processing")
        from .sandbox import in_appcontainer
        isolated=in_appcontainer()
        kwargs.setdefault('ignore_cleanup_errors',True)
        self._temp = None if isolated else tempfile.TemporaryDirectory(prefix="ape_", dir=root, **kwargs)
        self.name = str(root/('ape_'+uuid4().hex)) if isolated else self._temp.name
        if isolated:
            Path(self.name).mkdir(mode=0o777)
        self._release = None
        self._finalizer=None
        try:
            if not isolated:
                private_acl(Path(self.name))
            self._release = lease(Path(self.name)/".lease")
            self._finalizer=weakref.finalize(self,self._release)
        except Exception:
            self.cleanup()
            raise

    def cleanup(self):
        if self._release:
            if self._finalizer is not None:self._finalizer()
            else:self._release()
            self._release = None
        for attempt in range(20):
            try:
                if Path(self.name).exists():cleanup_profiles(self.name)
                if self._temp:
                    self._temp.cleanup()
                    if Path(self.name).exists():raise PermissionError('Workspace still in use')
                elif Path(self.name).exists():
                    shutil.rmtree(self.name)
                return
            except PermissionError:
                if attempt==19:
                    # A terminating renderer/AV may briefly retain a handle. The
                    # directory remains private and leased cleanup retries later.
                    import logging
                    logging.getLogger(__name__).warning('Private PDF workspace pending cleanup')
                    return
                time.sleep(.05)

    def __enter__(self):
        return self.name

    def __exit__(self, *_):
        self.cleanup()


def cleanup_stale(max_age=24*3600):
    root = work_root()
    now = time.time()
    for path in root.iterdir():
        if not re.fullmatch(r"ape_[A-Za-z0-9_-]+", path.name):
            continue
        try:
            info = path.lstat()
            if not path.is_dir() or path.is_symlink() or getattr(info,"st_file_attributes",0) & 0x400 or now-info.st_mtime < max_age:
                continue
            if path.resolve().parent != root:
                continue
            release = lease(path/".lease")
            release()
            cleanup_profiles(path)
            # Root is private; only validated immediate children are removed.
            shutil.rmtree(path)
        except OSError:
            continue
