from fluxftp import display_options
from fluxftp.core import Site
from fluxftp.local_locations import local_drives
from test_queue import window


def test_local_dropdown_drives_folders_and_navigation(window, monkeypatch):
    pane = window.current_panes()[0]
    pane.change_mode('Local')
    monkeypatch.setattr(display_options, 'local_folders', lambda: [('Downloads', 'C:\\Users\\Test\\Downloads', 'folder')])
    monkeypatch.setattr(display_options, 'local_drives', lambda: [
        ('System (C:)', 'C:\\', 'disk'), ('Network drive (Z:)', 'Z:\\', 'network'),
        ('CD/DVD drive (S:)', 'S:\\', 'optical'), ('Removable drive (T:)', 'T:\\', 'removable')])
    pane.address.populate_paths()
    actions = {a.data(): a for a in pane.address.path_menu.actions() if a.data()}
    assert {'C:\\', 'Z:\\', 'S:\\', 'T:\\', 'C:\\Users\\Test\\Downloads'} <= set(actions)
    assert all(not actions[path].icon().isNull() for path in ('C:\\', 'Z:\\', 'S:\\', 'T:\\'))
    calls = []
    monkeypatch.setattr(pane, 'navigate', calls.append)
    actions['Z:\\'].trigger()
    assert calls == ['Z:\\']
    pane.busy = True
    actions['C:\\'].trigger()
    assert calls == ['Z:\\']
    pane.busy = False


def test_drive_list_refreshes_and_does_not_leak_into_remote_menu(window, monkeypatch):
    pane = window.current_panes()[0]
    pane.change_mode('Local')
    drives = [('Local disk (C:)', 'C:\\', 'disk')]
    monkeypatch.setattr(display_options, 'local_drives', lambda: list(drives))
    pane.address.populate_paths()
    assert not any(a.data() == 'E:\\' for a in pane.address.path_menu.actions())
    drives.append(('USB (E:)', 'E:\\', 'removable'))
    pane.address.populate_paths()
    assert any(a.data() == 'E:\\' for a in pane.address.path_menu.actions())
    pane.set_remote(Site(name='remote', host='example.test'), '', '/')
    pane.address.populate_paths()
    assert not any(a.data() in ('C:\\', 'E:\\') for a in pane.address.path_menu.actions())


def test_drive_failure_leaves_recent_paths_usable(window, monkeypatch):
    pane = window.current_panes()[0]
    pane.change_mode('Local')
    path = pane.path
    def fail():
        raise OSError('Enumeration unavailable')
    monkeypatch.setattr(display_options, 'local_drives', fail)
    pane.address.populate_paths()
    assert any(a.data() == path and a.isEnabled() for a in pane.address.path_menu.actions())
    assert any(a.text() == 'Unable to enumerate drives' for a in pane.address.path_menu.actions())
