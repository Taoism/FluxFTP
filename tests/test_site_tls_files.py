"""TLS file selections survive saving and reopening a server profile."""
import json
from dataclasses import asdict

from fluxftp.app import SiteDialog
from test_queue import window


def test_tls_file_paths_persist_per_server(window, tmp_path):
    editor = SiteDialog(window, {}, profile_only=True)
    editor.fields['name'].setText('TLS profile')
    editor.fields['host'].setText('example.invalid')
    expected = {}
    for key in ('ca_file', 'cert_file', 'cert_key'):
        path = tmp_path / (key + '.pem')
        path.write_text('placeholder used only for path persistence')
        expected[key] = str(path)
        editor.fields[key].setText(str(path))
    site = editor.site()
    assert window.save_sites({site.name: asdict(site)})
    saved = json.loads(window.sites_file.read_text())
    reopened = SiteDialog(window, {}, editing=True)
    reopened.load(saved[site.name])
    for key, value in expected.items():
        assert saved[site.name][key] == value
        assert reopened.fields[key].text() == value
        assert getattr(reopened.site(), key) == value
    editor.close()
    reopened.close()
