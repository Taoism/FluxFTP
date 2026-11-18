"""Safe compatibility layer for RushApp.FTP.Transfer calls."""
from __future__ import annotations

import posixpath
import re
from pathlib import Path

from .site_options import decode as decode_site_options

MAX_SCRIPT = 16_000
MAX_TRANSFERS = 100
ARGUMENT_COUNT = 20
SUPPORTED_FLAGS = {'RS_UP', 'RS_DOWN', 'RS_FILE', 'RS_DIRSRC', 'RS_DIRDES', 'RS_LOGOUT'}


def _split_arguments(source):
    values, start, quoted = [], 0, False
    index = 0
    while index < len(source):
        char = source[index]
        if char == "'":
            if quoted and index + 1 < len(source) and source[index + 1] == "'":
                index += 2
                continue
            quoted = not quoted
        elif char == ',' and not quoted:
            values.append(source[start:index].strip())
            start = index + 1
        index += 1
    if quoted:
        raise ValueError('RushApp transfer has an unterminated quoted string.')
    values.append(source[start:].strip())
    return values


def _split_statements(source):
    values, start, quoted = [], 0, False
    index = 0
    while index < len(source):
        char = source[index]
        if char == "'":
            if quoted and index + 1 < len(source) and source[index + 1] == "'":
                index += 2
                continue
            quoted = not quoted
        elif char == ';' and not quoted:
            values.append(source[start:index].strip())
            start = index + 1
        index += 1
    if quoted:
        raise ValueError('RushApp script has an unterminated quoted string.')
    values.append(source[start:].strip())
    return values


def _strip_comments(source):
    result = []
    index = 0
    quoted = False
    while index < len(source):
        char = source[index]
        if char == "'":
            result.append(char)
            if quoted and index + 1 < len(source) and source[index + 1] == "'":
                result.append("'")
                index += 2
                continue
            quoted = not quoted
            index += 1
            continue
        if not quoted and char == '{':
            end = source.find('}', index + 1)
            if end < 0:
                raise ValueError('RushApp script has an unterminated comment.')
            result.append(' ')
            index = end + 1
            continue
        if not quoted and source.startswith('//', index):
            end = source.find('\n', index + 2)
            index = len(source) if end < 0 else end
            result.append(' ')
            continue
        if not quoted and source.startswith('/*', index):
            end = source.find('*/', index + 2)
            if end < 0:
                raise ValueError('RushApp script has an unterminated comment.')
            result.append(' ')
            index = end + 2
            continue
        result.append(char)
        index += 1
    return ''.join(result)


def _literal(value, label):
    if len(value) >= 2 and value[0] == value[-1] == "'":
        result = value[1:-1].replace("''", "'")
        if any(ord(char) < 32 for char in result):
            raise ValueError(f'{label} cannot contain control characters.')
        return result
    raise ValueError(f'{label} must be a quoted string.')


def _flags(value):
    if value == '0':
        return set()
    tokens = value.upper().split()
    if len(tokens) % 2 == 0 or tokens[1::2] != ['OR'] * (len(tokens) // 2):
        raise ValueError(f'Unsupported RushApp transfer flag expression: {value}')
    names = tokens[::2]
    if not names or any(name not in SUPPORTED_FLAGS for name in names):
        unknown = next((name for name in names if name not in SUPPORTED_FLAGS), value)
        raise ValueError(f'Unsupported RushApp transfer flag: {unknown}')
    if len(names) != len(set(names)):
        raise ValueError('RushApp transfer flags cannot be repeated.')
    flags = set(names)
    if {'RS_UP', 'RS_DOWN'} <= flags:
        raise ValueError('RS_UP and RS_DOWN cannot be combined.')
    return flags


def parse_script(source, parameters=()):
    """Parse Transfer calls only; never evaluates Pascal or script expressions."""
    if not isinstance(source, str) or not source.strip() or len(source) > MAX_SCRIPT:
        raise ValueError('Provide a RushApp script of 1–16000 characters.')
    if not isinstance(parameters, (list, tuple)) or len(parameters) > 32 or any(
            not isinstance(item, str) or len(item) > 4096 for item in parameters):
        raise ValueError('RushApp script parameters must be at most 32 strings of up to 4096 characters.')
    expanded = source
    if '{~}' in expanded and not parameters:
        raise ValueError('A RushApp path placeholder has no matching command-line parameter.')
    for index, value in enumerate(parameters):
        if any(ord(char) < 32 for char in value):
            raise ValueError('RushApp path parameters cannot contain control characters.')
        expanded = expanded.replace('{~' + str(index) + '}', value.replace("'", "''"))
    first = parameters[0].replace("'", "''") if parameters else ''
    expanded = expanded.replace('{~}', first)
    if len(expanded) > MAX_SCRIPT:
        raise ValueError('Expanded RushApp script exceeds 16000 characters.')
    if re.search(r'\{~\d+\}', expanded):
        raise ValueError('A RushApp path placeholder has no matching command-line parameter.')

    # Calls are semicolon-delimited. Strip Pascal begin/end wrappers and comments,
    # then insist every remaining statement is the one supported API call.
    cleaned = _strip_comments(expanded)
    cleaned = cleaned.lstrip()
    if cleaned[:5].casefold() == 'begin' and (len(cleaned) == 5 or cleaned[5].isspace()):
        cleaned = cleaned[5:]
    cleaned = cleaned.rstrip()
    if cleaned.endswith('.'):
        cleaned = cleaned[:-1].rstrip()
    if cleaned[-3:].casefold() == 'end' and (len(cleaned) == 3 or cleaned[-4].isspace() or cleaned[-4] == ';'):
        cleaned = cleaned[:-3].rstrip()
    calls = [part for part in _split_statements(cleaned) if part]
    if not calls or len(calls) > MAX_TRANSFERS:
        raise ValueError(f'A RushApp script must contain 1–{MAX_TRANSFERS} transfer calls.')
    transfers = []
    for call in calls:
        match = re.fullmatch(r'(?:Script\s+)?RushApp\.FTP\.Transfer\s*\((.*)\)', call, re.IGNORECASE | re.DOTALL)
        if not match:
            raise ValueError('Only RushApp.FTP.Transfer calls are supported.')
        args = _split_arguments(match.group(1))
        if len(args) != ARGUMENT_COUNT:
            raise ValueError(f'RushApp.FTP.Transfer requires {ARGUMENT_COUNT} arguments; received {len(args)}.')
        if not re.fullmatch(r'\d+', args[0]):
            raise ValueError('Transfer handle must be a non-negative integer.')
        handle = int(args[0])
        strings = [_literal(args[index], f'Transfer argument {index + 1}') for index in range(1, 7)]
        flags = _flags(args[7])
        extras = [_literal(args[index], f'Transfer argument {index + 1}') for index in range(8, 13)]
        if any(extras) or any(value != '0' for value in args[13:]):
            raise ValueError('RushApp folder filters, repeat/retry/sort/depth options are not supported; use empty strings and zeroes.')
        src_list, src_folder, src_item, dst_list, dst_folder, dst_item = strings
        if flags & {'RS_UP', 'RS_DOWN'}:
            if flags & {'RS_UP'} and (src_list or not dst_list):
                raise ValueError('RS_UP requires an empty source site list and a destination site list.')
            if flags & {'RS_DOWN'} and (not src_list or dst_list):
                raise ValueError('RS_DOWN requires a source site list and an empty destination site list.')
        elif not src_list or not dst_list:
            raise ValueError('FXP transfers require source and destination site lists.')
        source_sites = _site_names(src_list)
        destination_sites = _site_names(dst_list)
        transfers.append(dict(handle=handle, flags=sorted(flags), src_list=source_sites,
                              src_folder=src_folder, src_item=src_item, dst_list=destination_sites,
                              dst_folder=dst_folder, dst_item=dst_item))
    return transfers


def _site_names(value):
    names = [name.strip() for name in value.split(';') if name.strip()]
    if len(names) > 20 or len(names) != len(set(names)):
        raise ValueError('A RushApp site list must contain up to 20 distinct site names.')
    return names


def _remote_folder(window, site_name, folder, item, is_directory):
    profile = window.sites.get(site_name)
    if not isinstance(profile, dict) or profile.get('_disabled'):
        raise ValueError(f'The saved site is missing or disabled: {site_name}')
    site = window.site_from_profile(profile)
    if is_directory:
        base = folder or '/'
    else:
        records = decode_site_options(site.site_options).get('Site/bookmarks', [])
        record = next((entry for entry in records if entry.get('name', '').casefold() == folder.casefold()), None)
        if record is None and folder:
            raise ValueError(f'Unknown remote bookmark "{folder}" for saved site "{site_name}".')
        base = record['path'] if record else '/'
    if any(ord(char) < 32 for char in base + item) or '\\' in base or '..' in base.split('/'):
        raise ValueError('Remote RushApp paths must use printable text and forward slashes.')
    if item.startswith('/') or item in ('.', '..') or '..' in item.split('/'):
        raise ValueError('RushApp item paths must be relative and cannot traverse to a parent folder.')
    path = posixpath.normpath(posixpath.join(base or '/', item)) if item else posixpath.normpath(base)
    if not path.startswith('/') or '..' in path.split('/'):
        raise ValueError('Remote RushApp paths must resolve beneath an absolute remote directory.')
    return path


def _local_folder(window, folder, is_directory):
    base = Path(folder).expanduser()
    if not is_directory:
        matches = []
        for profile in window.sites.values():
            if not isinstance(profile, dict) or profile.get('_disabled'):
                continue
            try:
                site = window.site_from_profile(profile)
                records = decode_site_options(site.site_options).get('Site/bookmarks', [])
            except (TypeError, ValueError):
                continue
            matches.extend(record['local'] for record in records
                           if record.get('name', '').casefold() == folder.casefold() and record.get('local'))
        matches = list(dict.fromkeys(matches))
        if len(matches) != 1:
            raise ValueError(f'Unknown or ambiguous local bookmark "{folder}".')
        base = Path(matches[0]).expanduser()
    if not base.is_absolute():
        raise ValueError('Local RushApp folder paths must be absolute or name a saved local bookmark.')
    return base


def _local_path(window, folder, item, is_directory):
    base = _local_folder(window, folder, is_directory)
    if item and (Path(item).is_absolute() or item in ('.', '..') or '..' in item.replace('\\', '/').split('/')):
        raise ValueError('RushApp item paths must be relative and cannot traverse to a parent folder.')
    path = (base / item) if item else base
    path = path.resolve(strict=False)
    if any(ord(char) < 32 for char in str(path)):
        raise ValueError('Local RushApp paths cannot contain control characters.')
    if is_directory and item and Path(item).is_absolute():
        raise ValueError('An item path must be relative to its folder.')
    return str(path)


def queue_script(api, payload):
    """Validate calls, then enqueue them on the GUI thread through FluxFTP's queue."""
    if not isinstance(payload, dict) or set(payload) - {'script', 'parameters'}:
        raise ValueError('RushApp request must contain only script and parameters.')
    window = api.window
    requests = parse_script(payload.get('script'), payload.get('parameters', []))
    prepared = []
    total = 0
    for request in requests:
        handle = request['handle']
        if handle > window.tabs.count():
            raise ValueError(f'Workspace handle {handle} does not exist.')
        workspace = window.tabs.currentWidget() if handle == 0 else window.tabs.widget(handle - 1)
        flags = set(request['flags'])
        is_file = 'RS_FILE' in flags
        upload = 'RS_UP' in flags
        download = 'RS_DOWN' in flags
        src_is_local, dst_is_local = upload, download
        if upload and is_file and not request['src_item']:
            raise ValueError('RS_FILE uploads require a local source item.')
        if download and not request['src_item'] and is_file:
            raise ValueError('RS_FILE downloads require a source file item.')
        if not is_file and request['src_item'] and request['dst_item'] and request['src_item'] != request['dst_item']:
            # Directory items may be renamed, but all contents keep their relative names.
            pass
        source_sites = [None] if src_is_local else request['src_list']
        destination_sites = [None] if dst_is_local else request['dst_list']
        pairs = [(left, right) for left in source_sites for right in destination_sites]
        total += len(pairs)
        if total > MAX_TRANSFERS:
            raise ValueError(f'A RushApp script can queue at most {MAX_TRANSFERS} transfers.')
        for source_site in source_sites:
            for destination_site in destination_sites:
                if src_is_local:
                    src_path = _local_path(window, request['src_folder'], request['src_item'], 'RS_DIRSRC' in flags)
                else:
                    src_path = _remote_folder(window, source_site, request['src_folder'], request['src_item'], 'RS_DIRSRC' in flags)
                if dst_is_local:
                    dst_path = _local_path(window, request['dst_folder'], request['dst_item'] or request['src_item'], 'RS_DIRDES' in flags)
                else:
                    dst_path = _remote_folder(window, destination_site, request['dst_folder'], request['dst_item'] or request['src_item'], 'RS_DIRDES' in flags)
                source, src = api._endpoint({'path': src_path, 'site': source_site}, 'source')
                destination, dst = api._endpoint({'path': dst_path, 'site': destination_site}, 'destination')
                entry_name = api._basename(dst, destination_site is None)
                if src_is_local:
                    local = Path(src)
                    if local.is_symlink() or not local.exists():
                        raise ValueError('Local RushApp source must exist and cannot be a symbolic link.')
                    if is_file and not local.is_file():
                        raise ValueError('RS_FILE source must be a regular file.')
                    if not is_file and not local.is_dir():
                        raise ValueError('Folder transfers require a local directory source.')
                    size = local.stat().st_size if is_file else 0
                else:
                    size = 0
                prepared.append((workspace, source, destination, entry_name, is_file, size))
    jobs = []
    for workspace, source, destination, entry_name, is_file, size in prepared:
        source.number, destination.number = 1, 2
        destination.parentWidget = lambda workspace=workspace: workspace
        from .core import Entry
        created = window.enqueue_items(source, destination, [Entry(entry_name, not is_file, size)],
                                       start=True, confirmed=True)
        jobs.extend(created or [])
    if not jobs:
        raise ValueError('RushApp command did not create any queue items.')
    return {'queued': True, 'count': len(jobs), 'logout_after_transfer': any(
        'RS_LOGOUT' in request['flags'] for request in requests),
        'transfers': [api._job(job) for job in jobs]}
