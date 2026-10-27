"""Versioned, credential-free configuration backups with validated restore."""
import json
import math
import os
import uuid
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from PySide6.QtCore import QSettings
from .core import Site
from .site_import import validate_site

FORMAT = 'fluxftp-configuration'
MAX_BYTES = 16 * 1024 * 1024
GROUPS = {'General', 'Connection', 'Transfer', 'Misc', 'Display', 'Colors', 'Layout', 'Sites',
          'Sort', 'Queue', 'Mouse', 'Confirm', 'Associations', 'Ident', 'Bookmarks', 'Workspaces'}


def secret_key(key):
    return any(word in key.casefold() for word in ('password', 'passphrase', 'secret', 'token', 'credential'))


def allowed_key(key):
    return (isinstance(key, str) and len(key) <= 256 and '/' in key and
            key.split('/')[0] in GROUPS and key != 'General/data_path' and not secret_key(key)
            and not any(ord(char) < 32 for char in key))


def clean_value(value, depth=0):
    if depth > 12:
        raise ValueError('Configuration nesting is too deep.')
    if isinstance(value, dict):
        return {key: clean_value(item, depth + 1) for key, item in value.items()
                if isinstance(key, str) and not secret_key(key)}
    if isinstance(value, list):
        return [clean_value(item, depth + 1) for item in value]
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ValueError('Unsupported configuration value type.')


def clean_setting(value):
    if isinstance(value, str) and value.lstrip().startswith(('{', '[')):
        try:
            parsed = json.loads(value)
        except ValueError:
            return value
        return json.dumps(clean_value(parsed), ensure_ascii=False)
    return clean_value(value)


def clean_sites(sites):
    if not isinstance(sites, dict) or len(sites) > 5000:
        raise ValueError('Invalid saved-site collection.')
    result = {}
    names = set()
    allowed = {field.name for field in fields(Site)}
    for name, profile in sites.items():
        if not isinstance(name, str) or not isinstance(profile, dict) or name.casefold() in names:
            raise ValueError('Invalid or duplicate saved-site name.')
        site = Site(**{key: value for key, value in profile.items() if key in allowed})
        if site.name != name:
            raise ValueError('A saved-site name does not match its profile.')
        validate_site(site)
        names.add(name.casefold())
        result[name] = asdict(site)
        for key, kind in (('_folder', str), ('_disabled', bool)):
            if key in profile:
                if type(profile[key]) is not kind:
                    raise ValueError('Invalid site group or disabled flag.')
                result[name][key] = profile[key]
    return result


def configuration(settings, sites):
    values = {key: clean_setting(settings.value(key)) for key in settings.allKeys() if allowed_key(key)}
    return dict(format=FORMAT, version=1, settings=values, sites=clean_sites(sites))


def write_json(path, data):
    path = Path(path)
    payload = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
    if len(payload) > MAX_BYTES:
        raise ValueError('Configuration backup exceeds 16 MB.')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix='.fluxftp-config-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_configuration(path):
    with Path(path).open('rb') as stream:
        payload = stream.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise ValueError('Configuration backup exceeds 16 MB.')
    data = json.loads(payload)
    if not isinstance(data, dict) or data.get('format') != FORMAT or type(data.get('version')) is not int or data['version'] != 1:
        raise ValueError('Choose a FluxFTP configuration backup (version 1).')
    values = data.get('settings')
    if not isinstance(values, dict) or len(values) > 10000 or any(not allowed_key(key) for key in values):
        raise ValueError('Backup contains invalid or unsupported preference keys.')
    values = {key: clean_setting(value) for key, value in values.items()}
    if any(type(value) not in (str, bool, int, float) for value in values.values()):
        raise ValueError('Preferences must contain text, booleans or numbers.')
    if any(type(value) is int and not -(2 ** 31) <= value < 2 ** 31 for value in values.values()):
        raise ValueError('Preference number is out of range.')
    validate_settings(values)
    return dict(format=FORMAT, version=1, settings=values, sites=clean_sites(data.get('sites')))


def validate_settings(values):
    class Snapshot:
        def value(self, key, default=None, type=None):
            value = values.get(key, default)
            if type is bool and isinstance(value, str):
                if value.casefold() not in ('true', 'false', '1', '0'):
                    raise ValueError('Invalid boolean preference.')
                return value.casefold() in ('true', '1')
            return type(value) if type else value
    from .transfer_options import TransferOptions
    from .file_associations import read_rules
    settings = Snapshot()
    TransferOptions.from_settings(settings)
    read_rules(settings, 'view')
    read_rules(settings, 'edit')
    raw = values.get('Connection/proxy_profiles', '[]')
    profiles = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(profiles, list):
        raise ValueError('Invalid proxy profiles.')
    for profile in profiles:
        if not isinstance(profile, dict) or type(profile.get('port')) is not int or not 1 <= profile['port'] <= 65535:
            raise ValueError('Invalid proxy profile or port.')
        if any(not isinstance(profile.get(key), str) for key in ('id', 'name', 'host', 'type', 'username')) or type(profile.get('auth')) is not bool:
            raise ValueError('Invalid proxy profile fields.')


def restore_configuration(window, data):
    """Apply to the existing data location; never import credential references."""
    values = dict(data['settings'])
    sites = clean_sites(data['sites'])
    for site in sites.values():
        site['_credential_id'] = uuid.uuid4().hex
    # Imported proxy identifiers must not resolve to unrelated existing secrets.
    raw = values.get('Connection/proxy_profiles', '[]')
    profiles = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(profiles, list) or any(not isinstance(profile, dict) for profile in profiles):
        raise ValueError('Invalid proxy profiles in configuration.')
    old_selected = values.get('Connection/proxy_profile_id', 'proxy')
    selected = None
    proxy_ids = {}
    for profile in profiles:
        previous = profile.get('id')
        profile['id'] = uuid.uuid4().hex
        proxy_ids[previous] = profile['id']
        if previous == old_selected:
            selected = profile['id']
    if not profiles:
        profiles = [dict(id=uuid.uuid4().hex, name='Default',
            type=values.get('Connection/proxy_type', 'SOCKS5'), host=values.get('Connection/proxy_host', ''),
            port=values.get('Connection/proxy_port', 1080), auth=values.get('Connection/proxy_auth', False),
            username=values.get('Connection/proxy_username', ''))]
    values['Connection/proxy_profiles'] = json.dumps(profiles)
    for site in sites.values():
        options = json.loads(site.get('site_options', '{}'))
        if 'Connection/proxy_profile' in options:
            old = options['Connection/proxy_profile']
            options['Connection/proxy_profile'] = proxy_ids.get(old, 'missing-imported-' + uuid.uuid4().hex)
            site['site_options'] = json.dumps(options)
    values['Connection/proxy_profile_id'] = selected or profiles[0]['id']
    settings = window.preferences
    old_values = {key: settings.value(key) for key in settings.allKeys()}
    old_sites = window.sites
    path = window.sites_file
    previous_bytes = path.read_bytes() if path.exists() else None
    recovery = path.parent / 'backups' / f'configuration-before-restore-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.json'
    write_json(recovery, configuration(settings, old_sites))
    try:
        for key in settings.allKeys():
            if allowed_key(key):
                settings.remove(key)
        for key, value in values.items():
            settings.setValue(key, value)
        settings.sync()
        if settings.status() != QSettings.Status.NoError:
            raise OSError('Could not save restored preferences.')
        write_json(path, sites)
        window.sites = sites
    except Exception:
        for key in settings.allKeys():
            if allowed_key(key):
                settings.remove(key)
        for key, value in old_values.items():
            settings.setValue(key, value)
        settings.sync()
        if previous_bytes is not None and (not path.exists() or path.read_bytes() != previous_bytes):
            path.write_bytes(previous_bytes)
        elif previous_bytes is None:
            path.unlink(missing_ok=True)
        window.sites = old_sites
        raise
    return recovery
