"""Automation script schema, connection preparation and step execution."""
import posixpath
import re
from pathlib import Path
from types import SimpleNamespace

from .queue_metadata import remote_path
from .core import Cancelled, PROTOCOLS, safe_name
from .server_commands import compile_commands, execute_commands, SECRET, CREDENTIAL

ACTIONS = ('List folder', 'Download file', 'Upload file', 'Download folder', 'Upload folder', 'Create folder', 'FTP commands')


def command_plan(lines):
    def no_prompt(*_):
        raise ValueError('Automation commands cannot prompt. Replace prompted arguments in Command Designer first.')
    plan = compile_commands(lines, [], no_prompt)
    for line in plan.lines:
        if SECRET.search(line) or CREDENTIAL.search(line):
            raise ValueError('Credential commands cannot be saved in scripts. Use the saved site credential vault.')
        if line.split()[0].upper() in ('USER', 'OPEN', 'CONNECT', 'REIN', 'AUTH', 'QUIT', 'PORT', 'EPRT', 'PASV', 'EPSV', 'RETR', 'STOR', 'APPE'):
            raise ValueError('Use saved sites and transfer steps instead of connection or data-transfer commands.')
    return plan




def validate_script(raw):
    """Return a bounded schema-only copy, excluding credentials/unknown fields."""
    if not isinstance(raw, dict):
        raise ValueError('Invalid script.')
    name = raw.get('name', '')
    if not isinstance(name, str) or not name.strip() or len(name) > 120 or re.search(r'[\x00-\x1f]', name):
        raise ValueError('Enter a script name of 1–120 characters.')
    steps = raw.get('steps')
    if not isinstance(steps, list) or not 1 <= len(steps) <= 100:
        raise ValueError('A script must have 1–100 steps.')
    cleaned = []
    for step in steps:
        if not isinstance(step, dict) or step.get('action') not in ACTIONS:
            raise ValueError('Unknown script action.')
        site = step.get('site')
        if not isinstance(site, str) or not site or len(site) > 500:
            raise ValueError('Each step needs a saved site.')
        action = step['action']
        path = remote_path(step.get('remote', ''))
        item = dict(action=action, site=site, remote=path)
        if action in ('Download file', 'Upload file', 'Create folder'):
            safe_name(posixpath.basename(path))
        if action in ('Download file', 'Upload file', 'Download folder', 'Upload folder'):
            local = step.get('local', '')
            if not isinstance(local, str) or not Path(local).is_absolute() or re.search(r'[\x00-\x1f]', local):
                raise ValueError('Transfer steps need an absolute local file path (including the filename).')
            policy = step.get('exists', 'Fail')
            if policy not in ('Fail', 'Skip', 'Overwrite'):
                raise ValueError('Choose Fail, Skip or Overwrite for existing files.')
            item.update(local=local, exists=policy)
        if action == 'FTP commands':
            lines = step.get('lines', [])
            if not isinstance(lines, list) or not all(isinstance(line, str) for line in lines):
                raise ValueError('Invalid command lines.')
            command_plan(lines)
            item['lines'] = list(lines)
        cleaned.append(item)
    interval = raw.get('interval', 0)
    due = raw.get('next_run', 0)
    if type(interval) is not int or not 0 <= interval <= 525600:
        raise ValueError('Repeat interval must be 0 (once) or 1–525600 minutes.')
    if type(due) not in (int, float) or not 0 <= due <= 253402300799:
        raise ValueError('Invalid schedule time.')
    enabled = raw.get('enabled', False)
    if type(enabled) is not bool or (enabled and not due):
        raise ValueError('An enabled schedule needs a start time.')
    from .calendar_schedule import MODES
    schedule = raw.get('schedule', 'Interval')
    day = raw.get('month_day', 0)
    if schedule not in MODES or type(day) is not int or not 0 <= day <= 31:
        raise ValueError('Invalid calendar schedule.')
    wall_time = raw.get('wall_time', '')
    if not isinstance(wall_time, str) or (wall_time and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d', wall_time)):
        raise ValueError('Invalid calendar wall time.')
    return dict(name=name.strip(), steps=cleaned, interval=interval, next_run=due, enabled=enabled,
                schedule=schedule, month_day=day, wall_time=wall_time)


def next_occurrence(due, interval, now):
    """Coalesce missed occurrences; never enqueue a catch-up burst."""
    if not interval:
        return 0
    seconds = interval * 60
    return due + max(1, int((now - due) // seconds) + 1) * seconds


def saved_connection(window, name, unattended=True, direction=None):
    data = window.sites.get(name)
    if not data or data.get('_disabled'):
        raise ValueError('The saved site is missing or disabled: ' + name)
    site = window.site_from_profile(data)
    password = window.get_site_password(data)
    if direction:
        from .site_connections import transfer_account
        site, password = transfer_account(window, site, password, direction)
    if not password and site.username.casefold() == 'anonymous':
        password = window.preferences.value('General/anonymous_email', 'anonymous@fluxftp.local', type=str)
    if not password and not site.cloud_account and not site.managed_key and site.protocol not in (PROTOCOLS[3], 'SCP · SSH'):
        raise ValueError('Save the site password in Site Manager before testing or scheduling it.')
    connection = window.new_connection(site, password)
    if unattended:
        if connection.proxy and connection.proxy.get('username') and not connection.proxy.get('password'):
            raise ValueError('Save the proxy password before running automation.')
        # Previously verified keys still work, but background runs must never open trust dialogs.
        if connection.ssh_trust:
            connection.ssh_trust = SimpleNamespace(path=connection.ssh_trust.path, verify=lambda *_: False)
        if site.managed_key:
            raise ValueError('Automation currently requires a saved key-file path instead of an interactive managed key.')
    return connection


def execute_script(script, connections, worker, report):
    """Sequential file operations using the application's protocol engines."""
    for index, step in enumerate(script['steps'], 1):
        if worker.cancel.is_set():
            raise Cancelled('Script cancelled. Earlier steps may have completed.')
        report(f'Step {index}/{len(script["steps"])}: {step["action"]}')
        connection = connections[index - 1]
        with connection:
            action, remote = step['action'], step['remote']
            if worker.cancel.is_set():
                raise Cancelled('Script cancelled.')
            if action == 'List folder':
                entries = list_folder(connection, remote, worker.cancel)
                report(f'Step {index}: listed {len(entries)} entries.')
            elif action == 'Create folder':
                connection.mkdir(remote)
            elif action == 'FTP commands':
                execute_commands(connection, remote, command_plan(step['lines']), worker.cancel, raise_on_error=True)
            elif action in ('Upload folder', 'Download folder'):
                from .automation_folders import transfer_folder
                transfer_folder(connection, step, worker, report)
            else:
                upload = action == 'Upload file'
                local = Path(step['local'])
                if local.is_symlink() or local.with_name(local.name + '.fluxpart').is_symlink():
                    raise ValueError('Transfer paths cannot be symbolic links.')
                if upload and not local.is_file():
                    raise ValueError('Upload source must be an existing regular file.')
                if not upload and not local.parent.is_dir():
                    raise ValueError('Download destination folder does not exist.')
                entries = list_folder(connection, posixpath.dirname(remote), worker.cancel)
                entry = next((e for e in entries if e.name == posixpath.basename(remote)), None)
                if entry and (entry.is_dir or entry.is_link):
                    raise ValueError('Transfer steps require regular files, not directories or links.')
                if not upload and entry is None:
                    raise ValueError('Download source file does not exist.')
                exists = entry is not None if upload else local.exists()
                if exists:
                    if step['exists'] == 'Fail':
                        raise ValueError('Destination already exists; change the step policy to Skip or Overwrite.')
                    if step['exists'] == 'Skip':
                        report(f'Step {index}: skipped existing destination.')
                        continue
                    if not upload and not local.is_file():
                        raise ValueError('Download destination is not a regular file.')
                connection.transfer(local, remote, upload, worker.cancel, worker.report, resume=False)
        if worker.cancel.is_set():
            raise Cancelled('Script cancelled. Earlier steps may have completed.')
    return 'Completed all steps.'


def list_folder(connection, path, cancel):
    if cancel.is_set():
        raise Cancelled('Cancelled')
    result = connection.list(path, cancel=cancel) if getattr(connection, 'is_cloud', False) else connection.list(path)
    if cancel.is_set():
        raise Cancelled('Cancelled')
    return result
