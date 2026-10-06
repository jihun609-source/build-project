"""Windows 기본 API로 유니코드 텍스트를 복사한다. 추가 패키지는 필요 없다."""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time


def _clipboard_api():
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = [
        (user32.GetDesktopWindow, [], wintypes.HWND),
        (user32.OpenClipboard, [wintypes.HWND], wintypes.BOOL),
        (user32.EmptyClipboard, [], wintypes.BOOL),
        (user32.SetClipboardData, [wintypes.UINT, wintypes.HANDLE], wintypes.HANDLE),
        (user32.CloseClipboard, [], wintypes.BOOL),
        (kernel32.GlobalAlloc, [wintypes.UINT, ctypes.c_size_t], wintypes.HGLOBAL),
        (kernel32.GlobalLock, [wintypes.HGLOBAL], ctypes.c_void_p),
        (kernel32.GlobalUnlock, [wintypes.HGLOBAL], wintypes.BOOL),
        (kernel32.GlobalFree, [wintypes.HGLOBAL], wintypes.HGLOBAL),
    ]
    for function, args, result in signatures:
        function.argtypes, function.restype = args, result
    return user32, kernel32


def copy_text(text: str):
    if sys.platform == "darwin":
        subprocess.run(["/usr/bin/pbcopy"], input=text, text=True, encoding="utf-8", check=True, timeout=10,
                       env={**os.environ, "LANG": "en_US.UTF-8", "LC_CTYPE": "en_US.UTF-8"})
        return
    if os.name != "nt":
        raise RuntimeError("D안 설명 붙여넣기는 Windows 또는 macOS가 필요합니다.")
    user32, kernel32 = _clipboard_api()
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
    data = (normalized + "\0").encode("utf-16-le")
    handle = kernel32.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
    if not handle:
        raise RuntimeError("설명 복사용 메모리를 할당하지 못했습니다.")
    try:
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            raise RuntimeError("설명 복사용 메모리에 접근하지 못했습니다.")
        try:
            ctypes.memmove(pointer, data, len(data))
        finally:
            kernel32.GlobalUnlock(handle)
        for _ in range(20):
            if user32.OpenClipboard(user32.GetDesktopWindow()):
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("클립보드가 사용 중이라 설명을 복사하지 못했습니다. 다시 시도하세요.")
        try:
            if not user32.EmptyClipboard() or not user32.SetClipboardData(13, handle):  # CF_UNICODETEXT
                raise RuntimeError("설명을 클립보드에 복사하지 못했습니다.")
            handle = None  # 성공하면 메모리의 소유권은 Windows로 넘어간다.
        finally:
            user32.CloseClipboard()
    finally:
        if handle:
            kernel32.GlobalFree(handle)
