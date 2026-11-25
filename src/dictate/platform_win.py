"""Thin ctypes bindings for the Win32 calls we need (clipboard, SendInput, foreground window).

Everything here has explicit argtypes/restype: without them ctypes truncates 64-bit handles.
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes

if sys.platform != "win32":  # pragma: no cover
    raise ImportError("platform_win is Windows-only")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

HANDLE = wintypes.HANDLE
HWND = wintypes.HWND
UINT = wintypes.UINT
BOOL = wintypes.BOOL
DWORD = wintypes.DWORD
SIZE_T = ctypes.c_size_t
LPVOID = ctypes.c_void_p

# -- clipboard ---------------------------------------------------------------------------
user32.OpenClipboard.argtypes = [HWND]
user32.OpenClipboard.restype = BOOL
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = BOOL
user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = BOOL
user32.EnumClipboardFormats.argtypes = [UINT]
user32.EnumClipboardFormats.restype = UINT
user32.GetClipboardData.argtypes = [UINT]
user32.GetClipboardData.restype = HANDLE
user32.SetClipboardData.argtypes = [UINT, HANDLE]
user32.SetClipboardData.restype = HANDLE
user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
user32.RegisterClipboardFormatW.restype = UINT
user32.GetClipboardSequenceNumber.argtypes = []
user32.GetClipboardSequenceNumber.restype = DWORD

kernel32.GlobalAlloc.argtypes = [UINT, SIZE_T]
kernel32.GlobalAlloc.restype = HANDLE
kernel32.GlobalLock.argtypes = [HANDLE]
kernel32.GlobalLock.restype = LPVOID
kernel32.GlobalUnlock.argtypes = [HANDLE]
kernel32.GlobalUnlock.restype = BOOL
kernel32.GlobalSize.argtypes = [HANDLE]
kernel32.GlobalSize.restype = SIZE_T
kernel32.GlobalFree.argtypes = [HANDLE]
kernel32.GlobalFree.restype = HANDLE

GMEM_MOVEABLE = 0x0002
CF_UNICODETEXT = 13

# Formats whose handles are not HGLOBAL memory (GDI objects, metafiles, owner-drawn) or are
# synthesized/private. We cannot copy these byte-for-byte, so they are skipped on save.
# Images still round-trip through CF_DIB / CF_DIBV5, which Windows uses to re-synthesize CF_BITMAP.
UNSAFE_FORMATS = {2, 3, 9, 14, 0x0080, 0x0082, 0x0083, 0x008E}  # BITMAP METAFILEPICT PALETTE ENHMETAFILE DSP*


def is_safe_format(fmt: int) -> bool:
    # 0x0200-0x03FF = CF_PRIVATEFIRST..CF_GDIOBJLAST
    return fmt not in UNSAFE_FORMATS and not 0x0200 <= fmt <= 0x03FF


# -- message-only window (clipboard ownership) -------------------------------------------
user32.CreateWindowExW.argtypes = [
    DWORD,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    HWND,
    wintypes.HMENU,
    wintypes.HINSTANCE,
    LPVOID,
]
user32.CreateWindowExW.restype = HWND
HWND_MESSAGE = HWND(-3)


def create_message_window() -> HWND:
    hwnd = user32.CreateWindowExW(0, "STATIC", "dictate-clipboard", 0, 0, 0, 0, 0, HWND_MESSAGE, None, None, None)
    if not hwnd:
        raise ctypes.WinError(ctypes.get_last_error())
    return hwnd


# -- SendInput ---------------------------------------------------------------------------
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_SHIFT, VK_CONTROL, VK_MENU, VK_RETURN = 0x10, 0x11, 0x12, 0x0D
VK_LWIN, VK_RWIN = 0x5B, 0x5C
VK_V = 0x56

ULONG_PTR = ctypes.c_size_t


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", DWORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = UINT
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short

# Marker so our own hotkey listener can ignore keystrokes we inject.
INJECTED_TAG = 0x64696374  # "dict"


def key_input(vk: int = 0, scan: int = 0, flags: int = 0) -> INPUT:
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.ki = KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=INJECTED_TAG)
    return inp


def send_inputs(inputs: list[INPUT]) -> int:
    if not inputs:
        return 0
    arr = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    return int(sent)


def key_is_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


# -- foreground window / process info -----------------------------------------------------
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = HWND
user32.GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(DWORD)]
user32.GetWindowThreadProcessId.restype = DWORD
kernel32.OpenProcess.argtypes = [DWORD, BOOL, DWORD]
kernel32.OpenProcess.restype = HANDLE
kernel32.CloseHandle.argtypes = [HANDLE]
kernel32.CloseHandle.restype = BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [HANDLE, DWORD, wintypes.LPWSTR, ctypes.POINTER(DWORD)]
kernel32.QueryFullProcessImageNameW.restype = BOOL
kernel32.GetCurrentProcess.argtypes = []
kernel32.GetCurrentProcess.restype = HANDLE
advapi32.OpenProcessToken.argtypes = [HANDLE, DWORD, ctypes.POINTER(HANDLE)]
advapi32.OpenProcessToken.restype = BOOL
advapi32.GetTokenInformation.argtypes = [HANDLE, ctypes.c_int, LPVOID, DWORD, ctypes.POINTER(DWORD)]
advapi32.GetTokenInformation.restype = BOOL

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TOKEN_ELEVATION = 20


def _token_elevated(process: HANDLE) -> bool | None:
    token = HANDLE()
    if not advapi32.OpenProcessToken(process, TOKEN_QUERY, ctypes.byref(token)):
        return None
    try:
        elevated = DWORD()
        size = DWORD()
        ok = advapi32.GetTokenInformation(
            token, TOKEN_ELEVATION, ctypes.byref(elevated), ctypes.sizeof(elevated), ctypes.byref(size)
        )
        return bool(elevated.value) if ok else None
    finally:
        kernel32.CloseHandle(token)


def self_is_elevated() -> bool:
    return bool(_token_elevated(kernel32.GetCurrentProcess()))


class ForegroundInfo:
    __slots__ = ("elevated", "exe", "hwnd", "pid")

    def __init__(self, hwnd: int, pid: int, exe: str, elevated: bool | None) -> None:
        self.hwnd, self.pid, self.exe, self.elevated = hwnd, pid, exe, elevated

    def __repr__(self) -> str:
        return f"ForegroundInfo(exe={self.exe!r}, pid={self.pid}, elevated={self.elevated})"


def foreground_info() -> ForegroundInfo:
    hwnd = user32.GetForegroundWindow()
    pid = DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe, elevated = "", None
    proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if proc:
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = DWORD(len(buf))
            if kernel32.QueryFullProcessImageNameW(proc, 0, buf, ctypes.byref(size)):
                exe = os.path.basename(buf.value).lower()
            elevated = _token_elevated(proc)  # None => we're not allowed to look: likely elevated
        finally:
            kernel32.CloseHandle(proc)
    return ForegroundInfo(int(hwnd or 0), pid.value, exe, elevated)
