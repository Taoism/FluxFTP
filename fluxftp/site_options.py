"""Validated, non-secret site overrides layered over global preferences."""
import json
from urllib.parse import urlsplit, quote

from .ascii_rules import read_rules
from .listing_options import ENCODINGS, MODES

# An explicit allowlist prevents imported profiles from overriding unrelated settings.
SCHEMA = {
    'Account/upload_site': str, 'Account/download_site': str,
    'Connection/ident_name': str, 'Connection/ident_system': str,
    'Connection/proxy_profile': str,
    'Transfer/xdupe': bool,
    'Transfer/fxp_role': ('Auto', 'Passive', 'Active'),
    'Monitor/enabled': bool, 'Monitor/path': str,
    'Monitor/interval': (int, 30, 86400),
    'Connection/filename_encoding': MODES,
    'Connection/encoding_fallback': ENCODINGS,
    'Connection/sftp_read_aheads': (int, 1, 128),
    'Connection/list_batch_size': (int, 100, 2000),
    'Connection/list_server_order': bool,
    'Connection/mlsd_essential': bool,
    'Connection/connect_timeout': (int, 1, 3600),
    'Connection/command_timeout': (int, 1, 3600),
    'Connection/max_retries': (int, 0, 100),
    'Connection/retry_delay': (int, 0, 3600),
    'Connection/send_feat': bool, 'Connection/tcp_keepalive': bool,
    'Connection/keepalive_interval': (int, 1, 3600),
    'Connection/persistent_browsing': bool, 'Connection/ftp_keepalive': bool,
    'Connection/ftp_keepalive_interval': (int, 5, 3600),
    'Connection/transfer_noop': bool, 'Connection/use_mlsd': bool,
    'Connection/proxy_enabled': bool,
    'Connection/list_mode': ('Default (LIST)', 'LIST only', 'Show Hidden Files', 'Custom', 'STAT', 'STAT -l'),
    'Connection/list_command': str, 'General/upnp_enabled': bool,
    'Transfer/upload_case': ('Unchanged', 'Lowercase', 'Uppercase'),
    'Transfer/download_case': ('Unchanged', 'Lowercase', 'Uppercase'),
    'Transfer/fxp_case': ('Unchanged', 'Lowercase', 'Uppercase'),
    'Transfer/force_active': bool, 'Transfer/send_kib': (int, 1, 65536),
    'Transfer/receive_kib': (int, 1, 65536),
    'Transfer/skip_enabled': bool, 'Transfer/skip_empty': bool,
    'Transfer/skip_rules': str, 'Transfer/allow_rules': str,
    'Transfer/priority_rules': str,
    'Transfer/atomic_upload': bool, 'Transfer/conflict_prompt': bool,
    'Transfer/hash_verification': ('Off', 'When available', 'Required'),
    'Site/show_name': bool, 'Site/cache': bool,
    'Site/http_url': str, 'Site/web_root': str, 'Site/bookmarks': list,
}
for route in ('upload', 'download', 'fxp'):
    for relation in ('smaller', 'same', 'larger'):
        SCHEMA[f'Transfer/{route}_{relation}'] = ('Auto Resume', 'Auto Skip', 'Auto Rename', 'Auto Overwrite', 'Ask')


def decode(raw):
    try:
        values = json.loads(raw or '{}')
    except (ValueError, TypeError) as error:
        raise ValueError('Invalid site options.') from error
    if not isinstance(values, dict) or len(raw or '') > 262144:
        raise ValueError('Invalid site options.')
    for key, value in values.items():
        spec = SCHEMA.get(key)
        if spec is None:
            raise ValueError(f'Unsupported site option: {key}')
        if isinstance(spec, tuple):
            valid = (type(value) is int and spec[1] <= value <= spec[2]) if spec[0] is int else value in spec
        else:
            valid = type(value) is spec
        if not valid:
            raise ValueError(f'Invalid site option: {key}')
        if isinstance(value, str) and (len(value) > 65536 or any(ord(c) < 32 for c in value)):
            raise ValueError(f'Invalid text in site option: {key}')
        if key.endswith('_rules'):
            read_rules(value)
        if key == 'Connection/list_command' and not value.upper().startswith('LIST'):
            raise ValueError('Custom listing commands must start with LIST.')
    url = values.get('Site/http_url', '')
    import re
    name = values.get('Connection/ident_name', '')
    system = values.get('Connection/ident_system', '')
    if name and (len(name) > 128 or any(not 32 <= ord(c) <= 126 for c in name)):
        raise ValueError('Ident name must contain 1–128 printable ASCII characters.')
    if system and not re.fullmatch(r'[A-Za-z0-9-]{1,64}', system):
        raise ValueError('Invalid Ident system.')
    path = values.get('Monitor/path', '/')
    if not path.startswith('/') or '..' in path.split('/'):
        raise ValueError('Monitor path must be an absolute remote folder without parent segments.')
    if values.get('Site/web_root') and not values['Site/web_root'].startswith('/'):
        raise ValueError('FTP web root must be an absolute remote path.')
    if url:
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('HTTP URL must be an http/https address without credentials, query or fragment.')
    bookmarks = values.get('Site/bookmarks', [])
    if len(bookmarks) > 1000:
        raise ValueError('At most 1000 bookmarks are allowed.')
    for record in bookmarks:
        if not isinstance(record, dict) or set(record) != {'name', 'path', 'local'} or any(
                not isinstance(v, str) or len(v) > 4096 or any(ord(c) < 32 for c in v) for v in record.values()):
            raise ValueError('Invalid bookmark.')
        if not record['name'].strip() or not record['path'].startswith('/'):
            raise ValueError('Bookmarks require a name and absolute remote path.')
    return values


class SiteSettings:
    def __init__(self, base, site=None):
        self.base = base
        self.overrides = decode(getattr(site, 'site_options', '{}'))

    def value(self, key, default=None, type=None):
        if key in self.overrides:
            return self.overrides[key]
        return self.base.value(key, default, type=type) if type else self.base.value(key, default)


def transfer_settings(base, source=None, destination=None):
    """Destination owns upload/FXP policy; source owns download policy."""
    from .transfer_options import TransferOptions
    return TransferOptions.from_settings(SiteSettings(base, destination or source))


def mapped_url(site, path):
    settings = decode(site.site_options)
    base = settings.get('Site/http_url', '').rstrip('/')
    root = settings.get('Site/web_root', '/').rstrip('/')
    if not base or (path != root and not path.startswith(root + '/')):
        raise ValueError('Configure an HTTP URL and matching web root in Site Properties → URL.')
    return base + '/' + quote(path[len(root):].lstrip('/'), safe='/')
