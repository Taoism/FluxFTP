import json
from pathlib import Path
from types import SimpleNamespace

from fluxftp.rush_compat import parse_script, queue_script
from fluxftp.connection_model import Site
from fluxftp import rush_cmdline


def transfer(flags="RS_UP or RS_FILE or RS_DIRSRC or RS_DIRDES", *, src="'C:\\\\incoming'",
             src_item="'sample.bin'", src_list="''", dst_list="'site'", dst="'/upload'",
             dst_item="'sample.bin'", handle='0'):
    return (f"Script RushApp.FTP.Transfer({handle}, {src_list}, {src}, {src_item}, "
            f"{dst_list}, {dst}, {dst_item}, {flags}, '', '', '', '', '', 0, 0, 0, 0, 0, 0, 0);")


def test_transfer_parser_accepts_flags_parameters_and_pascal_quotes():
    parsed = parse_script(transfer(src="'C:\\{~0}'"), ["O'Brien"])
    assert parsed[0]['src_folder'] == "C:\\O'Brien"
    assert parsed[0]['flags'] == ['RS_DIRDES', 'RS_DIRSRC', 'RS_FILE', 'RS_UP']
    assert parse_script('begin ' + transfer() + transfer(dst="'/upload/second;item'", dst_item="'second;item'") + 'end.')


def test_transfer_parser_rejects_unknown_flags_options_and_extra_code():
    import pytest
    for source in (
        transfer(flags='RS_UP or RS_UNSAFE'),
        transfer(flags='RS_UP or RS_DOWN'),
        transfer().replace(', 0, 0, 0, 0, 0, 0, 0);', ', 0, 1, 0, 0, 0, 0, 0);'),
        transfer() + 'DeleteFile("C:\\\\important");',
    ):
        with pytest.raises(ValueError):
            parse_script(source)


def test_rush_cmdline_forwards_script_and_path_parameters(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(rush_cmdline.cli, '_request', lambda *args: calls.append(args) or {'queued': True})
    monkeypatch.setattr('sys.argv', ['rush_cmdline.exe', 'RushApp.FTP.Transfer(...)', 'C:\\source'])
    assert rush_cmdline.main() == 0
    assert calls == [('POST', '/v1/rush-script',
                      {'script': 'RushApp.FTP.Transfer(...)', 'parameters': ['C:\\source']})]
    assert '"queued": true' in capsys.readouterr().out


def test_queue_script_resolves_paths_and_uses_transfer_queue(tmp_path):
    local_root = tmp_path / 'incoming'
    local_root.mkdir()
    (local_root / 'sample.bin').write_bytes(b'payload')
    calls = []
    workspace = object()
    class Pane:
        def __init__(self, site, path):
            self.site, self.password = site, ''
            self.mode, self.busy, self.path, self.number = ('Remote' if site else 'Local'), False, str(Path(path).parent), 0
            self.join = lambda _name, target=path: target
    class Api:
        def __init__(self):
            self.window = SimpleNamespace(sites={'site': {}, 'other': {}}, tabs=SimpleNamespace(count=lambda: 1, currentWidget=lambda: workspace, widget=lambda _: workspace))
            self.window.site_from_profile = lambda profile: Site(site_options='{}')
        def _endpoint(self, value, _label):
            path = value['path']
            site = object() if value.get('site') else None
            return Pane(site, path), path
        @staticmethod
        def _basename(path, local):
            return Path(path).name if local else path.rsplit('/', 1)[-1]
        @staticmethod
        def _job(job):
            return {'id': job['id'], 'source': job['src'], 'destination': job['dst']}
    api = Api()
    api.window.tabs.widget = lambda index: workspace if index == 0 else None
    def enqueue(source, destination, entries, **options):
        calls.append((source.join(entries[0].name), destination.join(entries[0].name), entries[0].is_dir, options))
        return [{'id': 'job-1', 'src': calls[-1][0], 'dst': calls[-1][1]}]
    api.window.enqueue_items = enqueue
    request = transfer(src=f"'{local_root}'", dst="'/upload'", src_item="'sample.bin'")
    result = queue_script(api, {'script': request, 'parameters': []})
    assert result['queued'] and result['count'] == 1 and result['logout_after_transfer'] is False
    assert calls == [(str(local_root / 'sample.bin'), '/upload/sample.bin', False,
                      {'start': True, 'confirmed': True})]
    folder_transfer = transfer(flags='RS_UP or RS_DIRSRC or RS_DIRDES', src=f"'{local_root}'",
                               src_item="''", dst_item="''")
    queue_script(api, {'script': folder_transfer})
    assert calls[-1] == (str(local_root), '/upload', True, {'start': True, 'confirmed': True})


def test_queue_script_maps_download_fxp_and_bookmark_paths(tmp_path):
    workspace = object()
    calls = []
    class Pane:
        def __init__(self, remote, path):
            self.site, self.password = (object() if remote else None), ''
            self.mode, self.busy, self.path, self.number = ('Remote' if remote else 'Local'), False, str(Path(path).parent), 0
            self.join = lambda _name, target=path: target
    class Api:
        def __init__(self):
            self.window = SimpleNamespace(sites={'site': {}, 'other': {}},
                tabs=SimpleNamespace(count=lambda: 1, currentWidget=lambda: workspace, widget=lambda _: workspace))
            self.window.site_from_profile = lambda _profile: Site(site_options='{}')
        def _endpoint(self, value, _label): return Pane(bool(value.get('site')), value['path']), value['path']
        @staticmethod
        def _basename(path, _local): return Path(path).name
        @staticmethod
        def _job(job): return job
    api = Api()
    api.window.sites = {'site': {'id': 'site'}, 'other': {'id': 'other'}}
    api.window.site_from_profile = lambda profile: Site(site_options=json.dumps(
        {'Site/bookmarks': [{'name': 'Release', 'path': '/public', 'local': ''}]}
        if profile['id'] == 'site' else {}))
    def enqueue(source, destination, entries, **_options):
        calls.append((source.mode, destination.mode, source.join(entries[0].name), destination.join(entries[0].name)))
        return [{'src': calls[-1][2], 'dst': calls[-1][3]}]
    api.window.enqueue_items = enqueue
    down = transfer(flags='RS_DOWN or RS_FILE or RS_DIRSRC or RS_DIRDES or RS_LOGOUT',
                    src_list="'site'", src="'/public'", dst_list="''", dst=f"'{tmp_path}'")
    result = queue_script(api, {'script': down})
    assert result['logout_after_transfer'] and calls[-1] == (
        'Remote', 'Local', '/public/sample.bin', str(tmp_path / 'sample.bin'))
    fxp = transfer(flags='RS_FILE or RS_DIRDES', src_list="'site'", src="'Release'",
                   dst_list="'other'", dst="'/incoming'")
    queue_script(api, {'script': fxp})
    assert calls[-1] == ('Remote', 'Remote', '/public/sample.bin', '/incoming/sample.bin')

