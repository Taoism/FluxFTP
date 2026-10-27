"""Local filesystem destinations for the address dropdown."""
import os
from pathlib import Path

from PySide6.QtCore import QDir, QStandardPaths


def local_folders():
    locations = [
        ('Desktop', QStandardPaths.StandardLocation.DesktopLocation, 'desktop'),
        ('Documents', QStandardPaths.StandardLocation.DocumentsLocation, 'folder'),
        ('Downloads', QStandardPaths.StandardLocation.DownloadLocation, 'folder'),
        ('Music', QStandardPaths.StandardLocation.MusicLocation, 'folder'),
        ('Pictures', QStandardPaths.StandardLocation.PicturesLocation, 'folder'),
        ('Videos', QStandardPaths.StandardLocation.MoviesLocation, 'folder'),
        (Path.home().name or 'Home', QStandardPaths.StandardLocation.HomeLocation, 'home'),
    ]
    return [(name, QDir.toNativeSeparators(path), icon) for name, kind, icon in locations
            if (path := QStandardPaths.writableLocation(kind))]


def local_drives():
    if os.name != 'nt':
        return [(drive.absoluteFilePath(), drive.absoluteFilePath(), 'disk') for drive in QDir.drives()]
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetLogicalDrives.argtypes = []
    kernel.GetLogicalDrives.restype = wintypes.DWORD
    kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    kernel.GetDriveTypeW.restype = wintypes.UINT
    kernel.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR, wintypes.DWORD]
    kernel.GetVolumeInformationW.restype = wintypes.BOOL
    kernel.SetThreadErrorMode.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    kernel.SetThreadErrorMode.restype = wintypes.BOOL
    mask = kernel.GetLogicalDrives()
    if not mask:
        raise ctypes.WinError(ctypes.get_last_error())
    kinds = {2: ('Removable drive', 'removable'), 3: ('Local disk', 'disk'),
             4: ('Network drive', 'network'), 5: ('CD/DVD drive', 'optical'),
             6: ('RAM disk', 'disk')}
    result = []
    for index in range(26):
        if mask & (1 << index):
            letter = chr(ord('A') + index)
            root = letter + ':\\'
            drive_type = kernel.GetDriveTypeW(root)
            label, icon = kinds.get(drive_type, ('Drive', 'disk'))
            # Do not probe mapped shares or removable media just to open a menu.
            # Suppress OS error dialogs if a local volume becomes unavailable.
            if drive_type in (3, 6):
                old_mode = wintypes.DWORD()
                if kernel.SetThreadErrorMode(1, ctypes.byref(old_mode)):
                    try:
                        name = ctypes.create_unicode_buffer(261)
                        if kernel.GetVolumeInformationW(root, name, len(name), None, None, None, None, 0):
                            label = name.value or label
                    finally:
                        kernel.SetThreadErrorMode(old_mode.value, None)
            result.append((f'{label} ({letter}:)', root, icon))
    return result
