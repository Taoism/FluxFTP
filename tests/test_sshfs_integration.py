"""Opt-in end-to-end SSHFS-Win drive test; set FLUXFTP_TEST_SSHFS=1 to run."""
import os
from dataclasses import asdict
from pathlib import Path
import subprocess
import time

import paramiko
import pytest
from PySide6.QtCore import QProcess

from fluxftp.sshfs_mounts import sshfs_program, windows_drive_letters_in_use
from test_queue import window
from test_sftp import ssh_server


@pytest.mark.skipif(os.name != 'nt', reason='This integration fixture exercises SSHFS-Win drive mounts.')
@pytest.mark.skipif(os.environ.get('FLUXFTP_TEST_SSHFS') != '1', reason='Set FLUXFTP_TEST_SSHFS=1 to enable the external SSHFS test.')
def test_live_sshfs_mount_and_unmount(window, ssh_server, tmp_path):
    assert sshfs_program(), 'Install WinFsp and SSHFS-Win before enabling this test.'
    remote_root, site = ssh_server
    identity = paramiko.RSAKey.generate(2048)
    identity_path = tmp_path / 'sshfs-test-key'
    identity.write_private_key_file(str(identity_path))
    account = os.environ['USERDOMAIN'] + '\\' + os.environ['USERNAME']
    subprocess.run(['icacls', str(identity_path), '/inheritance:r', '/grant:r', account + ':(R)'], check=True)
    (remote_root / 'authorized_keys').write_text(identity.get_base64(), encoding='ascii')
    (remote_root / 'mount-probe.txt').write_text('FluxFTP SSHFS probe', encoding='utf-8')

    profile = asdict(site)
    profile.update(name='SSHFS local probe', protocol='SFTP · SSH',
                   ssh_key=str(identity_path), managed_key='', root='/')
    window.sites = {'SSHFS local probe': profile}
    window.sites_file = tmp_path / 'sites.json'
    window.ssh_trust.path = tmp_path / 'app-known-hosts'
    free_drives = [letter for letter in 'ZYXWVUTSRQPONMLKJIHGFEDCBA'
                   if letter not in windows_drive_letters_in_use()]
    assert free_drives, 'No free Windows drive letters are available for SSHFS.'
    drive = free_drives[0]
    window.sshfs_mounts.start('SSHFS local probe', '/', drive + ':')
    record = window.sshfs_mounts.mounts[0]
    target = Path(drive + ':/mount-probe.txt')
    deadline = time.monotonic() + 25
    try:
        while time.monotonic() < deadline and not target.exists():
            window.app_instance.processEvents()
            if record['process'].state() == QProcess.ProcessState.NotRunning:
                break
            time.sleep(.1)
        assert target.exists(), record['output']
        assert target.read_text(encoding='utf-8') == 'FluxFTP SSHFS probe'
    finally:
        window.sshfs_mounts.stop(record)

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and drive in windows_drive_letters_in_use():
        window.app_instance.processEvents()
        time.sleep(.1)
    assert drive not in windows_drive_letters_in_use(), 'SSHFS drive did not unmount'
