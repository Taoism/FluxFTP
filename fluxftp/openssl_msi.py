"""Read official MSI cabinets without installing or executing package actions."""
import ctypes
from ctypes import wintypes
from pathlib import Path
import os
import subprocess


def extract(msi_path, destination, wanted):
    api = ctypes.WinDLL('msi')
    handle = wintypes.UINT
    api.MsiOpenDatabaseW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.POINTER(handle)]
    api.MsiDatabaseOpenViewW.argtypes = [handle, wintypes.LPCWSTR, ctypes.POINTER(handle)]
    api.MsiRecordGetStringW.argtypes = [handle, wintypes.UINT, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    api.MsiRecordReadStream.argtypes = [handle, wintypes.UINT, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    def checked(code):
        if code:
            raise OSError(f'MSI read failed ({code})')
    database = handle()
    checked(api.MsiOpenDatabaseW(str(msi_path), None, ctypes.byref(database)))
    def rows(query):
        view = handle()
        checked(api.MsiDatabaseOpenViewW(database, query, ctypes.byref(view)))
        try:
            checked(api.MsiViewExecute(view, 0))
            while True:
                record = handle()
                code = api.MsiViewFetch(view, ctypes.byref(record))
                if code == 259:
                    break
                checked(code)
                try:
                    yield record
                finally:
                    api.MsiCloseHandle(record)
        finally:
            api.MsiCloseHandle(view)
    def string(record, field):
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        checked(api.MsiRecordGetStringW(record, field, buffer, ctypes.byref(size)))
        return buffer.value
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    found = {}
    try:
        for record in rows('SELECT `File`, `FileName` FROM `File`'):
            key, name = string(record, 1), string(record, 2).split('|')[-1]
            if name in wanted:
                if not key or any(c in key for c in '/\\:*?') or key in ('.', '..'):
                    raise ValueError('Invalid cabinet filename')
                found.setdefault(name, []).append(key)
        if set(found) != set(wanted):
            raise ValueError('Required OpenSSL files absent from MSI')
        cabinets = []
        for record in rows('SELECT `Cabinet` FROM `Media`'):
            name = string(record, 1)
            if not name.startswith('#'):
                raise ValueError('External MSI cabinet refused')
            cabinets.append(name[1:])
        for index, cabinet in enumerate(cabinets):
            if "'" in cabinet:
                raise ValueError('Invalid cabinet name')
            path = destination / f'payload-{index}.cab'
            for record in rows("SELECT `Data` FROM `_Streams` WHERE `Name` = '" + cabinet + "'"):
                with path.open('wb') as output:
                    total = 0
                    while True:
                        buffer = ctypes.create_string_buffer(65536)
                        size = wintypes.DWORD(len(buffer))
                        checked(api.MsiRecordReadStream(record, 1, buffer, ctypes.byref(size)))
                        if not size.value:
                            break
                        total += size.value
                        if total > 256 * 1024 * 1024:
                            raise ValueError('MSI cabinet too large')
                        output.write(buffer.raw[:size.value])
            for name, keys in found.items():
                if (destination / name).exists():
                    continue
                for key in keys:
                    result = subprocess.run([str(Path(os.environ['WINDIR']) / 'System32' / 'expand.exe'),
                        str(path), '-F:' + key, str(destination)], capture_output=True, timeout=60,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    source = destination / key
                    if result.returncode == 0 and source.exists():
                        if source != destination / name:
                            source.replace(destination / name)
                        break
            path.unlink(missing_ok=True)
        if not all((destination / name).is_file() for name in wanted):
            raise ValueError('Required OpenSSL files could not be extracted')
    finally:
        api.MsiCloseHandle(database)
