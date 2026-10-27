import json
from dataclasses import asdict

import pytest
from PySide6.QtWidgets import QApplication

from fluxftp.app import Window, SiteDialog
from fluxftp.core import Site
from fluxftp.site_options import decode, SiteSettings, transfer_settings, mapped_url
from fluxftp.site_import import validate_site
from test_integration import servers


def test_site_overrides_roundtrip_and_connection():
    app = QApplication.instance() or QApplication([])
    window = Window()
    window._smoke_test = True
    values = {'Connection/max_retries': 7, 'Connection/retry_delay': 0,
              'Connection/command_timeout': 42, 'Connection/send_feat': False,
              'Transfer/upload_case': 'Lowercase', 'Transfer/send_kib': 512,
              'Transfer/download_same': 'Auto Overwrite', 'Site/cache': False,
              'Site/bookmarks': [{'name': 'Music', 'path': '/music', 'local': 'C:/Music'}]}
    site = Site(host='example.test', site_options=json.dumps(values))
    editor = SiteDialog(window, {}, editing=True)
    editor.load(asdict(site))
    assert decode(editor.site().site_options) == values
    connection = window.new_connection(editor.site(), '')
    assert connection.max_retries == 7
    assert connection.command_timeout == 42
    assert connection.send_feat is False
    assert connection.transfer_options.send_kib == 512
    assert connection.transfer_options.filename('FILE.TXT', 'Upload') == 'file.txt'
    assert window.preferences.value('Connection/max_retries', 2, type=int) == 2
    editor.navigation.setCurrentItem(editor._nav_items['Connection'])
    connection_page = editor.pages.currentWidget()
    editor.navigation.setCurrentItem(editor._nav_items['Account'])
    assert connection_page is not editor.pages.currentWidget()
    editor.load(None)
    assert decode(editor.site().site_options) == {'Site/bookmarks': []}
    editor.reject()
    window.close()


def test_site_transfer_policy_and_allow_list():
    app = QApplication.instance() or QApplication([])
    window = Window()
    window._smoke_test = True
    source = Site(host='source.test', site_options=json.dumps({'Transfer/download_case': 'Uppercase'}))
    destination = Site(host='dest.test', site_options=json.dumps({
        'Transfer/upload_case': 'Lowercase', 'Transfer/fxp_same': 'Auto Skip',
        'Transfer/skip_enabled': True,
        'Transfer/skip_rules': json.dumps([{'pattern': '*.txt', 'regex': False}]),
        'Transfer/allow_rules': json.dumps([{'pattern': 'keep.txt', 'regex': False}])}))
    assert transfer_settings(window.preferences, source).download_case == 'Uppercase'
    options = transfer_settings(window.preferences, source, destination)
    assert options.upload_case == 'Lowercase'
    assert options.download_case == 'Unchanged'
    assert options.skip_reason('remove.txt', False, 10)
    assert not options.skip_reason('keep.txt', False, 10)
    window.close()


@pytest.mark.parametrize('values', [
    {'Connection/max_retries': -1}, {'Connection/send_feat': 'false'},
    {'Connection/proxy_password': 'secret'}, {'Connection/list_command': 'DELE file'},
    {'Site/http_url': 'https://user:password@example.com'},
    {'Site/bookmarks': [{'name': 'x', 'path': '../x', 'local': ''}]},
    {'Transfer/priority_rules': '[{"pattern":"[", "regex":true}]'},
])
def test_invalid_profile_options_are_rejected(values):
    with pytest.raises(ValueError):
        validate_site(Site(host='example.test', site_options=json.dumps(values)))


def test_web_url_mapping_escapes_names_and_respects_root():
    site = Site(site_options=json.dumps({'Site/http_url': 'https://example.com/media', 'Site/web_root': '/public'}))
    assert mapped_url(site, '/public/a b#c.txt') == 'https://example.com/media/a%20b%23c.txt'
    with pytest.raises(ValueError):
        mapped_url(site, '/publicity/secret')


def test_site_custom_listing_uses_configured_command(servers):
    app = QApplication.instance() or QApplication([])
    window = Window()
    window._smoke_test = True
    root, site = servers(True)
    (root / 'sample.txt').write_text('data')
    site.site_options = json.dumps({'Connection/list_mode': 'Custom', 'Connection/list_command': 'LIST -al'})
    with window.new_connection(site, 'password') as connection:
        commands = []
        original = connection.ftp.retrlines
        def listing(command, callback):
            commands.append(command)
            return original(command, callback)
        connection.ftp.retrlines = listing
        assert 'sample.txt' in [entry.name for entry in connection.list('/')]
        assert commands == ['LIST -al']
    window.close()
