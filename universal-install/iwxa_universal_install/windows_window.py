"""Windows native I3 linked-window observation, with no embedding or process ownership.

This optional OS adapter is not tied to any IWXA GUI/Core. It only observes
an existing process window and optionally requests OS focus with readback.
"""
from __future__ import annotations
import os

SCHEMA = "IWXA_UI_LINKED_WINDOW/1.0.0"

def observe_windows(process_id: int) -> dict:
    if os.name != "nt":
        return {"schema": SCHEMA, "status": "PLATFORM_UNSUPPORTED", "observed": False}
    if not isinstance(process_id, int) or process_id <= 0:
        raise ValueError("WINDOW_PROCESS_ID_INVALID")
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    enum_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    entries = []

    @enum_type
    def visit(hwnd, lparam):
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == process_id and user32.IsWindowVisible(hwnd):
            count = user32.GetWindowTextLengthW(hwnd)
            if count:
                buffer = ctypes.create_unicode_buffer(count + 1)
                user32.GetWindowTextW(hwnd, buffer, count + 1)
                entries.append({"handle": int(hwnd), "title": buffer.value})
        return True

    user32.EnumWindows.argtypes = [enum_type, wintypes.LPARAM]
    user32.EnumWindows(visit, 0)
    return {"schema": SCHEMA,
            "status": "WINDOW_OBSERVED" if entries else "WINDOW_NOT_FOUND",
            "observed": bool(entries), "native": True,
            "process_id": process_id, "windows": entries}

def focus_existing_window(process_id: int, window_handle: int) -> dict:
    observed = observe_windows(process_id)
    if not observed["observed"] or not any(
        entry["handle"] == window_handle for entry in observed["windows"]
    ):
        return {"schema": SCHEMA, "status": "WINDOW_NOT_OWNED_BY_PROCESS",
                "focused": False}
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow(window_handle)
    focused = int(user32.GetForegroundWindow() or 0) == window_handle
    return {"schema": SCHEMA,
            "status": "WINDOW_FOCUSED" if focused else "WINDOW_FOCUS_DENIED",
            "focused": focused, "native": True, "process_id": process_id}
