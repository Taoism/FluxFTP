import io
import os
import zlib
from dataclasses import replace
from threading import Event
from types import SimpleNamespace

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from fluxftp.compression import ModeZSocket
from fluxftp.core import Connection, Site, fxp
from fluxftp.transfer_options import TransferOptions
from test_integration import servers


class MemorySocket:
    def __init__(self, data=b''):
        self.input = io.BytesIO(data)
        self.output = bytearray()
        self.closed = False
    def recv(self, size): return self.input.read(min(size, 11))
    def sendall(self, data): self.output.extend(data)
    def close(self): self.closed = True


def test_stream_roundtrip_and_bounded_inflation():
    raw = MemorySocket()
    content = b'compressible content\x00\xff' * 100000
    with ModeZSocket(raw, True) as data:
        for index in range(0, len(content), 16384):
            data.sendall(content[index:index + 16384])
    assert zlib.decompress(raw.output) == content
    raw = MemorySocket(bytes(raw.output))
    result = bytearray()
    with ModeZSocket(raw) as data:
        while block := data.recv(777):
            assert len(block) <= 777
            result.extend(block)
    assert result == content and raw.closed


@pytest.mark.parametrize('data', [b'bad', zlib.compress(b'contents')[:-2], zlib.compress(b'contents') + b'extra'])
def test_invalid_or_incomplete_stream_rejected(data):
    with pytest.raises(OSError):
        with ModeZSocket(MemorySocket(data)) as sock:
            while sock.recv(3): pass


@pytest.mark.parametrize('tls', [False, True])
@pytest.mark.parametrize('active', [False, True])
def test_mode_z_listing_upload_download_and_resume(servers, tmp_path, tls, active):
    root, site = servers(tls, mode_z=True)
    site.passive = not active
    payload = b'binary\x00\xff data' * 30000
    source = tmp_path / 'source.bin'
    source.write_bytes(payload)
    options = TransferOptions(compression_bypass_local=False, download_buffer_kib=32, rollback_kib=0)
    with Connection(site, 'password', transfer_options=options) as connection:
        commands = []
        putcmd = connection.ftp.putcmd
        def record(command):
            commands.append(command)
            putcmd(command)
        connection.ftp.putcmd = record
        connection.transfer(source, '/file.bin', True, Event(), lambda *_: None)
        assert (root / 'file.bin').read_bytes() == payload
        assert any(e.name == 'file.bin' for e in connection.list('/'))
        connection.use_mlsd = False
        assert any(e.name == 'file.bin' for e in connection.list('/'))
        target = tmp_path / 'target.bin'
        target.with_name('target.bin.fluxpart').write_bytes(payload[:10000])
        connection.transfer(target, '/file.bin', False, Event(), lambda *_: None, resume=True)
        assert target.read_bytes() == payload
        (root / 'file.bin').write_bytes(payload[:15000])
        connection.transfer(source, '/file.bin', True, Event(), lambda *_: None, resume=True)
        assert (root / 'file.bin').read_bytes() == payload
        assert 'MODE Z' in commands and any(c.startswith('REST ') for c in commands)
        connection.transfer(source, '/file.zip', True, Event(), lambda *_: None)
        assert commands.count('MODE S') >= 1
        assert (root / 'file.zip').read_bytes() == payload


@pytest.mark.parametrize('tls', [False, True])
def test_compressed_ascii_roundtrip(servers, tmp_path, tls):
    root, site = servers(tls, mode_z=True)
    source = tmp_path / 'text.txt'
    source.write_bytes(b'first\r\nsecond\nlast\r\n')
    options = TransferOptions(mode='ASCII', compression_bypass_local=False, download_buffer_kib=8)
    with Connection(site, 'password', transfer_options=options) as connection:
        connection.transfer(source, '/text.txt', True, Event(), lambda *_: None)
        target = tmp_path / 'target.txt'
        connection.transfer(target, '/text.txt', False, Event(), lambda *_: None)
    assert target.read_bytes().splitlines() == [b'first', b'second', b'last']


def test_unsupported_compression_falls_back_once(servers, tmp_path):
    root, site = servers()
    (root / 'file.bin').write_bytes(b'data')
    options = TransferOptions(compression_bypass_local=False)
    with Connection(site, 'password', transfer_options=options) as connection:
        commands = []
        original = connection.ftp.putcmd
        def record(command):
            commands.append(command)
            original(command)
        connection.ftp.putcmd = record
        assert connection.list('/')
        connection.transfer(tmp_path / 'received', '/file.bin', False, Event(), lambda *_: None)
        assert commands.count('MODE Z') == 1
    assert (tmp_path / 'received').read_bytes() == b'data'


@pytest.mark.parametrize('both_support', [True, False])
def test_fxp_negotiates_same_mode_on_both_sides(servers, both_support):
    aroot, asite = servers(mode_z=True)
    broot, bsite = servers(mode_z=both_support)
    payload = b'FXP compressed payload' * 10000
    (aroot / 'source').write_bytes(payload)
    options = TransferOptions(compression_bypass_local=False)
    with Connection(asite, 'password', transfer_options=options) as a, Connection(bsite, 'password', transfer_options=options) as b:
        fxp(a, b, '/source', '/target')
        assert bool(getattr(a.ftp, 'mode_z', False)) == both_support
        assert bool(getattr(b.ftp, 'mode_z', False)) == both_support
    assert (broot / 'target').read_bytes() == payload


def test_address_and_extension_exceptions_and_buffering():
    connection = Connection(Site(host='public.example'), '')
    connection.ftp = SimpleNamespace(sock=SimpleNamespace(getpeername=lambda: ('192.168.1.10', 21)))
    assert connection.bypass_compression_cache()

    connection.transfer_options = TransferOptions(compression_bypass_local=False, download_buffer_kib=64)
    assert connection.compression_for_command('LIST')
    assert not connection.compression_for_command('STOR /FILE.ZIP')
    assert connection.download_buffering() == 65536
    connection.transfer_options = replace(connection.transfer_options, compression_addresses='public.;other')
    assert not connection.compression_for_command('RETR file')
    assert connection.download_buffering() == 0
    connection.transfer_options = replace(connection.transfer_options, compression_addresses='192.168.;other')
    assert connection.bypass_compression_cache()


def test_corrupt_compressed_download_preserves_completed_target(servers, tmp_path, monkeypatch):
    root, site = servers(mode_z=True)
    payload = b'new file contents' * 100
    (root / 'source').write_bytes(payload)
    target = tmp_path / 'target'
    target.write_bytes(b'previous completed file')
    options = TransferOptions(compression_bypass_local=False, stall_resume=False, download_buffer_kib=32)
    with Connection(site, 'password', transfer_options=options) as connection:
        monkeypatch.setattr(connection.ftp, 'ntransfercmd', lambda *_: (ModeZSocket(MemorySocket(zlib.compress(payload)[:-2])), len(payload)))
        with pytest.raises(OSError, match='Incomplete MODE Z'):
            connection.transfer(target, '/source', False, Event(), lambda *_: None)
    assert target.read_bytes() == b'previous completed file'


def test_empty_compressed_file_roundtrip(servers, tmp_path):
    root, site = servers(mode_z=True)
    source = tmp_path / 'empty'
    source.touch()
    options = TransferOptions(compression_bypass_local=False)
    with Connection(site, 'password', transfer_options=options) as connection:
        connection.transfer(source, '/empty', True, Event(), lambda *_: None)
        connection.transfer(tmp_path / 'received', '/empty', False, Event(), lambda *_: None)
    assert (tmp_path / 'received').read_bytes() == b''


@pytest.mark.parametrize('tls', [False, True])
def test_compressed_verified_ascii_resume(servers, tmp_path, tls):
    root, site = servers(tls, mode_z=True)
    source = tmp_path / 'text.txt'
    source.write_bytes(b'first\r\nsecond\r\nthird\r\n')
    (root / 'text.txt').write_bytes(b'first\r\nsec')
    options = TransferOptions(mode='ASCII', ascii_no_resume=False, compression_bypass_local=False, rollback_kib=0, download_buffer_kib=16)
    with Connection(site, 'password', transfer_options=options) as connection:
        connection.transfer(source, '/text.txt', True, Event(), lambda *_: None, resume=True)
        target = tmp_path / 'received.txt'
        target.with_name(target.name + '.fluxpart').write_bytes(b'first' + os.linesep.encode() + b'sec')
        connection.transfer(target, '/text.txt', False, Event(), lambda *_: None, resume=True)
    assert target.read_bytes().splitlines() == [b'first', b'second', b'third']


def test_compression_controls_persist(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from fluxftp import app as module
    from fluxftp.preferences import PreferencesDialog
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / 'compression.ini'), QSettings.Format.IniFormat)
    monkeypatch.setattr(module, 'settings_store', lambda: settings)
    window = module.Window()
    dialog = PreferencesDialog(window)
    assert all(box.isChecked() for box in dialog.compress_checks.values())
    assert dialog.compression_bypass.isChecked() and dialog.download_buffer.value() == 0
    dialog.compress_checks['listing'].setChecked(False)
    dialog.download_buffer.setValue(128)
    dialog.compression_addresses.setPlainText('10.;internal.')
    dialog.compression_masks.setPlainText('*.zip;*.mp4')
    options = TransferOptions.from_settings(settings)
    assert not options.compress_listing and options.download_buffer_kib == 128
    assert options.compression_addresses == '10.;internal.' and options.compression_masks == '*.zip;*.mp4'
    dialog.close()
    window.close()
