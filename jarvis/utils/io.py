"""Secure file writes: owner-only access (chmod 600, or an owner-only ACL on Windows)."""
import functools, os, pathlib, subprocess
from ..constants import CONFIG_DIR, FILE_PERMISSION
from .osinfo import IS_WINDOWS, hidden_subprocess_kwargs


def _sid_from_token() -> str | None:
    """The current user's SID (``S-1-5-21-…``) read from the process token."""
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                             wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]

    TOKEN_QUERY, TOKEN_USER = 0x0008, 1
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
        return None
    try:
        size = wintypes.DWORD()
        advapi32.GetTokenInformation(token, TOKEN_USER, None, 0, ctypes.byref(size))
        if not size.value:
            return None
        buf = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(token, TOKEN_USER, buf, size, ctypes.byref(size)):
            return None
        psid = ctypes.c_void_p.from_buffer(buf).value  # TOKEN_USER.User.Sid comes first
        out = wintypes.LPWSTR()
        if not advapi32.ConvertSidToStringSidW(psid, ctypes.byref(out)):
            return None
        try:
            return out.value
        finally:
            kernel32.LocalFree(out)
    finally:
        kernel32.CloseHandle(token)


def _sid_from_whoami() -> str | None:
    """Fallback: ``whoami /user /fo csv /nh`` → ``"domain\\user","S-1-5-…"``."""
    try:
        out = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
                             timeout=10, check=False, **hidden_subprocess_kwargs()).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    sid = out.strip().rsplit(",", 1)[-1].strip().strip('"')
    return sid if sid.startswith("S-1-") else None


@functools.lru_cache(maxsize=1)
def current_user_sid() -> str | None:
    """Windows: the SID of the user running Jarvis, or None when it can't be read."""
    if not IS_WINDOWS:
        return None
    try:
        sid = _sid_from_token()
    except (OSError, AttributeError, ValueError):
        sid = None
    return sid or _sid_from_whoami()


def _windows_owner_only(path: pathlib.Path) -> bool:
    """Drop inherited ACEs and grant full control to the current user only.

    ``os.chmod`` can only toggle the read-only bit on Windows, so the
    equivalent of mode 600 is an explicit ACL set with ``icacls``. The grant
    names the user by SID (``*S-1-5-…``): a ``DOMAIN\\user`` name can fail to
    resolve (error 1332), which would leave the inherited ACL in place.
    """
    sid = current_user_sid()
    if not sid:
        return False
    try:
        r = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:F"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10, check=False, **hidden_subprocess_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def restrict_to_owner(path: pathlib.Path) -> bool:
    """Best effort: make ``path`` readable and writable by its owner only.

    Returns whether it worked. Never raises.
    """
    if IS_WINDOWS:
        return _windows_owner_only(pathlib.Path(path))
    try:
        os.chmod(path, FILE_PERMISSION)
    except OSError:
        return False
    return True


# Files already locked down by this process. Rewriting a file in place keeps
# its ACL, so on Windows the (process-spawning) icacls call runs once per file.
_restricted: set[str] = set()


def _secure_write(path: pathlib.Path, data: str):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    path.write_text(data, encoding="utf-8")
    key = str(path)
    if IS_WINDOWS and existed and key in _restricted:
        return
    if restrict_to_owner(path):
        _restricted.add(key)
