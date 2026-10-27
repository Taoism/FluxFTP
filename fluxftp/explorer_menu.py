"""Host the Windows Shell's IContextMenu, including extension submenu messages.

API contracts: https://learn.microsoft.com/windows/win32/api/shobjidl_core/nn-shobjidl_core-icontextmenu
"""
import ctypes as C
import os
import uuid
from pathlib import Path
from ctypes import wintypes as W


class GUID(C.Structure):
    _fields_ = [('data1', W.DWORD), ('data2', W.WORD), ('data3', W.WORD), ('data4', C.c_ubyte * 8)]

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class CommandInfo(C.Structure):
    _fields_ = [('cbSize', W.DWORD), ('fMask', W.DWORD), ('hwnd', W.HWND),
                ('lpVerb', C.c_void_p), ('lpParameters', C.c_void_p), ('lpDirectory', C.c_void_p),
                ('nShow', C.c_int), ('dwHotKey', W.DWORD), ('hIcon', W.HANDLE)]


def check(result):
    if result < 0:
        raise OSError(f'Windows Explorer menu failed (HRESULT 0x{result & 0xffffffff:08X}).')
    return result


def method(pointer, index, restype, *argtypes):
    table = C.cast(pointer, C.POINTER(C.POINTER(C.c_void_p))).contents
    return C.WINFUNCTYPE(restype, C.c_void_p, *argtypes)(table[index])


class ExplorerMenu:
    def __init__(self, paths, hwnd):
        if os.name != 'nt':
            raise OSError('Explorer Menu is available on Windows. Select Default popup menu on this platform.')
        if not paths:
            raise ValueError('Select a local file or folder.')
        paths = [str(Path(path).absolute()) for path in paths]
        if len({os.path.normcase(str(Path(path).parent)) for path in paths}) != 1:
            raise ValueError('Explorer menu items must share a parent folder.')
        self.hwnd = hwnd
        self.pidls, self.folder, self.context, self.context3, self.context2 = [], C.c_void_p(), C.c_void_p(), C.c_void_p(), C.c_void_p()
        self.menu, self.initialized = None, False
        self.shell, self.ole, self.user = C.WinDLL('shell32'), C.WinDLL('ole32'), C.WinDLL('user32')
        self.ole.CoInitializeEx.argtypes = [C.c_void_p, W.DWORD]
        self.ole.CoInitializeEx.restype = C.c_long
        self.ole.CoTaskMemFree.argtypes = [C.c_void_p]
        self.shell.SHParseDisplayName.argtypes = [W.LPCWSTR, C.c_void_p, C.POINTER(C.c_void_p), W.DWORD, C.POINTER(W.DWORD)]
        self.shell.SHParseDisplayName.restype = C.c_long
        self.shell.SHBindToParent.argtypes = [C.c_void_p, C.POINTER(GUID), C.POINTER(C.c_void_p), C.POINTER(C.c_void_p)]
        self.shell.SHBindToParent.restype = C.c_long
        self.user.CreatePopupMenu.restype = W.HMENU
        self.user.DestroyMenu.argtypes = [W.HMENU]
        self.user.GetMenuItemCount.argtypes = [W.HMENU]
        self.user.GetMenuStringW.argtypes = [W.HMENU, W.UINT, W.LPWSTR, C.c_int, W.UINT]
        self.user.TrackPopupMenuEx.argtypes = [W.HMENU, W.UINT, C.c_int, C.c_int, W.HWND, C.c_void_p]
        self.user.TrackPopupMenuEx.restype = W.UINT
        self.user.ClientToScreen.argtypes = [W.HWND, C.POINTER(W.POINT)]
        try:
            check(self.ole.CoInitializeEx(None, 2))
            self.initialized = True
            children = []
            folder_id = GUID.parse('000214E6-0000-0000-C000-000000000046')
            for index, path in enumerate(paths):
                pidl, child, parent = C.c_void_p(), C.c_void_p(), C.c_void_p()
                check(self.shell.SHParseDisplayName(path, None, C.byref(pidl), 0, None))
                self.pidls.append(pidl)
                check(self.shell.SHBindToParent(pidl, C.byref(folder_id), C.byref(parent), C.byref(child)))
                if index == 0:
                    self.folder = parent
                else:
                    method(parent, 2, W.ULONG)(parent)
                children.append(child.value)
            context_id = GUID.parse('000214E4-0000-0000-C000-000000000046')
            array = (C.c_void_p * len(children))(*children)
            check(method(self.folder, 10, C.c_long, W.HWND, W.UINT, C.POINTER(C.c_void_p),
                         C.POINTER(GUID), C.c_void_p, C.POINTER(C.c_void_p))(
                             self.folder, hwnd, len(children), array, C.byref(context_id), None, C.byref(self.context)))
            for iid, target in (('BCFCE0A0-EC17-11D0-8D10-00A0C90F2719', self.context3),
                                ('000214F4-0000-0000-C000-000000000046', self.context2)):
                ident = GUID.parse(iid)
                method(self.context, 0, C.c_long, C.POINTER(GUID), C.POINTER(C.c_void_p))(
                    self.context, C.byref(ident), C.byref(target))
            self.menu = self.user.CreatePopupMenu()
            if not self.menu:
                raise OSError('Windows could not create the Explorer menu.')
            check(method(self.context, 3, C.c_long, W.HMENU, W.UINT, W.UINT, W.UINT, W.UINT)(
                self.context, self.menu, 0, 1, 0x7fff, 0))
        except Exception:
            self.close()
            raise

    def labels(self):
        labels = []
        for index in range(self.user.GetMenuItemCount(self.menu)):
            value = C.create_unicode_buffer(1024)
            self.user.GetMenuStringW(self.menu, index, value, len(value), 0x400)
            labels.append(value.value)
        return labels

    def handle_message(self, message):
        if message.hwnd != self.hwnd or message.message not in (0x117, 0x2b, 0x2c, 0x120):
            return False, 0
        if self.context3:
            result = C.c_ssize_t()
            status = method(self.context3, 7, C.c_long, W.UINT, C.c_size_t, C.c_ssize_t, C.POINTER(C.c_ssize_t))(
                self.context3, message.message, message.wParam, message.lParam, C.byref(result))
            return status == 0, result.value
        if self.context2 and message.message != 0x120:
            status = method(self.context2, 6, C.c_long, W.UINT, C.c_size_t, C.c_ssize_t)(
                self.context2, message.message, message.wParam, message.lParam)
            return status == 0, 0
        return False, 0

    def show(self, x, y):
        from PySide6.QtCore import QAbstractNativeEventFilter
        from PySide6.QtWidgets import QApplication
        owner = self
        class Messages(QAbstractNativeEventFilter):
            def nativeEventFilter(self, kind, address):
                if bytes(kind) not in (b'windows_generic_MSG', b'windows_dispatcher_MSG'):
                    return False, 0
                return owner.handle_message(W.MSG.from_address(int(address)))
        handler = Messages()
        app = QApplication.instance()
        app.installNativeEventFilter(handler)
        try:
            command = self.user.TrackPopupMenuEx(self.menu, 0x100 | 0x2, x, y, self.hwnd, None)
            if command:
                info = CommandInfo()
                info.cbSize, info.hwnd, info.lpVerb, info.nShow = C.sizeof(info), self.hwnd, command - 1, 1
                info.fMask = 0x100  # CMIC_MASK_NOASYNC: keep the host alive until invocation returns.
                check(method(self.context, 4, C.c_long, C.POINTER(CommandInfo))(self.context, C.byref(info)))
        finally:
            app.removeNativeEventFilter(handler)

    def close(self):
        if self.menu:
            self.user.DestroyMenu(self.menu)
            self.menu = None
        for pointer in (self.context3, self.context2, self.context, self.folder):
            if pointer:
                method(pointer, 2, W.ULONG)(pointer)
                pointer.value = None
        for pidl in self.pidls:
            self.ole.CoTaskMemFree(pidl)
        self.pidls.clear()
        if self.initialized:
            self.ole.CoUninitialize()
            self.initialized = False


def show_explorer_menu(paths, widget, position):
    from PySide6.QtWidgets import QWidget
    top = QWidget.window(widget)
    menu = ExplorerMenu(paths, int(top.winId()))
    try:
        local = top.mapFromGlobal(position)
        scale = top.devicePixelRatioF()
        point = W.POINT(round(local.x() * scale), round(local.y() * scale))
        menu.user.ClientToScreen(menu.hwnd, C.byref(point))
        menu.show(point.x, point.y)
    finally:
        menu.close()
