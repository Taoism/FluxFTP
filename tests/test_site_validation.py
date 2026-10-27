from dataclasses import asdict

import pytest
from PySide6.QtWidgets import QDialog, QMessageBox
from fluxftp.app import SiteDialog
from fluxftp.core import Site, PROTOCOLS
from fluxftp.site_import import validate_site, merge_sites
from test_queue import window


@pytest.mark.parametrize('masq', [None, False, True])
def test_typed_nat_setting_validates_and_merges(masq):
    site = Site(name='test', host='example.test', protocol=PROTOCOLS[1], use_masq=masq,
                description='First line\nSecond line\r\n\tNotes')
    validate_site(site)
    assert merge_sites({}, [site])['test'] == asdict(site)


@pytest.mark.parametrize('value', ['false', 0, 1])
def test_invalid_nat_type_still_rejected(value):
    with pytest.raises(ValueError, match='Invalid port'):
        validate_site(Site(name='test', host='example.test', use_masq=value))


@pytest.mark.parametrize('field,value', [('host', 'host\nNOOP'), ('username', 'user\rPASS'),
                                        ('description', 'notes\x00'), ('root', '/path\x7f')])
def test_invalid_text_reports_field_without_contents(field, value):
    site = Site(name='test', host='example.test')
    setattr(site, field, value)
    with pytest.raises(ValueError) as error:
        validate_site(site)
    assert field in str(error.value)
    assert value not in str(error.value)


@pytest.mark.parametrize('masq', [False, True])
def test_editor_accepts_auth_ssl_and_nat_checkbox(window, monkeypatch, masq):
    dialog = SiteDialog(window, {}, editing=True)
    dialog.fields['name'].setText('Saved site')
    dialog.fields['host'].setText('0x5f83ca7f')
    dialog.protocol.setCurrentText(PROTOCOLS[1])
    dialog.port.setValue(30001)
    dialog.use_masq.setChecked(masq)
    dialog.description.setPlainText('Notes\nMore notes')
    errors = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: errors.append(args))
    dialog.validate()
    assert not errors
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.site().protocol == PROTOCOLS[1]
    assert dialog.site().use_masq is masq
    assert dialog.site().port == 30001
    dialog.close()
