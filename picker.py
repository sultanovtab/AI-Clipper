"""Native Windows common file dialog. Only the selected path is returned."""

import ctypes
from ctypes import wintypes
import json


class OPENFILENAMEW(ctypes.Structure):
    _fields_ = [
        ("lStructSize", wintypes.DWORD), ("hwndOwner", wintypes.HWND),
        ("hInstance", wintypes.HINSTANCE), ("lpstrFilter", wintypes.LPCWSTR),
        ("lpstrCustomFilter", wintypes.LPWSTR), ("nMaxCustFilter", wintypes.DWORD),
        ("nFilterIndex", wintypes.DWORD), ("lpstrFile", wintypes.LPWSTR),
        ("nMaxFile", wintypes.DWORD), ("lpstrFileTitle", wintypes.LPWSTR),
        ("nMaxFileTitle", wintypes.DWORD), ("lpstrInitialDir", wintypes.LPCWSTR),
        ("lpstrTitle", wintypes.LPCWSTR), ("Flags", wintypes.DWORD),
        ("nFileOffset", wintypes.WORD), ("nFileExtension", wintypes.WORD),
        ("lpstrDefExt", wintypes.LPCWSTR), ("lCustData", wintypes.LPARAM),
        ("lpfnHook", ctypes.c_void_p), ("lpTemplateName", wintypes.LPCWSTR),
        ("pvReserved", ctypes.c_void_p), ("dwReserved", wintypes.DWORD),
        ("FlagsEx", wintypes.DWORD),
    ]


def pick_file():
    dialog = ctypes.WinDLL("comdlg32", use_last_error=True)
    dialog.GetOpenFileNameW.argtypes = [ctypes.POINTER(OPENFILENAMEW)]
    dialog.GetOpenFileNameW.restype = wintypes.BOOL
    dialog.CommDlgExtendedError.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    options = OPENFILENAMEW()
    options.lStructSize = ctypes.sizeof(options)
    options.lpstrFilter = "Video files (*.mp4;*.mkv;*.mov;*.webm;*.avi)\0*.mp4;*.mkv;*.mov;*.webm;*.avi\0\0"
    options.nFilterIndex = 1
    options.lpstrFile = ctypes.cast(buffer, wintypes.LPWSTR)
    options.nMaxFile = len(buffer)
    options.lpstrTitle = "AI Clipper - Select Local Video"
    # Explorer, existing file/path, preserve working directory, no recent-file entry.
    options.Flags = 0x00080000 | 0x00001000 | 0x00000800 | 0x00000008 | 0x02000000
    if dialog.GetOpenFileNameW(ctypes.byref(options)):
        return {"path": buffer.value}
    error = dialog.CommDlgExtendedError()
    return {"path": None, "error": error or None}


if __name__ == "__main__":
    try:
        print(json.dumps(pick_file()))
    except Exception:
        print(json.dumps({"path": None, "error": "Native picker unavailable"}))
