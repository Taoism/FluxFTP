"""Validated `.fluxftprc` loading and matching helpers."""
from __future__ import annotations

import fnmatch
import re
import tomllib
from pathlib import Path, PurePosixPath


MAX_CONFIG_BYTES = 1_000_000
MAX_RULES = 500
MAX_PATTERN = 512
MAX_MESSAGE = 2048
MAX_RETRIES = 20
MAX_BACKOFF = 24


def _table(value, label):
    if not isinstance(value, dict):
        raise ValueError(f'{label} must be a TOML table.')
    return value


def _known_keys(value, allowed, label):
    unknown = set(value) - set(allowed)
    if unknown:
        names = ', '.join(sorted(str(key) for key in unknown))
        raise ValueError(f'{label} contains unknown key(s): {names}.')


def _string(value, label, *, maximum=512):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f'{label} must be a non-empty string of at most {maximum} characters.')
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f'{label} cannot contain control characters.')
    return value.strip()


def _identifier(value, label):
    result = _string(value, label, maximum=80)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', result):
        raise ValueError(f'{label} must contain only letters, numbers, dot, underscore, or hyphen.')
    return result


def _remote_path(value, label):
    result = _string(value, label, maximum=2048)
    path = PurePosixPath(result)
    if not result.startswith('/') or '//' in result or any(part in ('.', '..') for part in result.split('/')):
        raise ValueError(f'{label} must be a normalized absolute remote path without dot segments.')
    if path.as_posix() != result.rstrip('/') and result != '/':
        raise ValueError(f'{label} must be a normalized absolute remote path.')
    return result.rstrip('/') or '/'


def _patterns(value, label):
    if not isinstance(value, list) or len(value) > 256:
        raise ValueError(f'{label} must be an array of at most 256 glob patterns.')
    result = []
    for pattern in value:
        item = _string(pattern, label + ' item', maximum=256)
        if '/' in item or '\\' in item or item in ('.', '..'):
            raise ValueError(f'{label} globs must match one path component.')
        result.append(item)
    return tuple(result)


def _matches(name, whitelist, blacklist):
    if any(fnmatch.fnmatchcase(name.casefold(), pattern.casefold()) for pattern in blacklist):
        return False
    return not whitelist or any(fnmatch.fnmatchcase(name.casefold(), pattern.casefold()) for pattern in whitelist)


def _safe_regex(pattern):
    """Compile a bounded IRC expression, rejecting constructs prone to ReDoS."""
    pattern = _string(pattern, 'IRC trigger pattern', maximum=MAX_PATTERN)
    if '(?P<folder>' not in pattern or pattern.count('(?P<folder>') != 1:
        raise ValueError('IRC patterns must contain exactly one named folder capture.')
    if any(token in pattern for token in ('(?=', '(?!', '(?<=', '(?<!', '(?:', '(?P<', '(?P=', '(?(', '(?>')):
        # The one required named capture is allowed; all other special groups are not.
        if pattern.count('(?P<folder>') == 1:
            stripped = pattern.replace('(?P<folder>', '', 1)
            if any(token in stripped for token in ('(?=', '(?!', '(?<=', '(?<!', '(?:', '(?P<', '(?P=', '(?(', '(?>')):
                raise ValueError('IRC patterns cannot use lookarounds, backreferences, or nested groups.')
    capture = pattern.split('(?P<folder>', 1)[1].split(')', 1)[0]
    if not capture or '(' in capture or ')' in capture or '\\' in capture:
        raise ValueError('The folder capture must use a simple character class or literal pattern.')
    # Quantifiers may repeat single atoms only. Group repetition and adjacent
    # repeated quantifiers are rejected to keep matching cost linear in message size.
    if re.search(r'\)[*+{]', pattern) or re.search(r'[*+?}]\s*[*+?{]', pattern):
        raise ValueError('IRC patterns cannot repeat groups or stack quantifiers.')
    if re.search(r'(?<!\\)\\[1-9]', pattern):
        raise ValueError('IRC patterns cannot use backreferences.')
    try:
        compiled = re.compile(pattern)
    except re.error as error:
        raise ValueError('Invalid IRC trigger pattern: ' + str(error)) from error
    return compiled


def _unique(items, label):
    seen = set()
    for item in items:
        if item['id'].casefold() in seen:
            raise ValueError(f'Duplicate {label} ID: {item["id"]}')
        seen.add(item['id'].casefold())


def validate_config(raw, site_names):
    raw = _table(raw, 'Root')
    _known_keys(raw, {'trading', 'sources', 'destinations', 'routes', 'irc'}, 'Root')
    trading = _table(raw.get('trading', {}), 'trading')
    _known_keys(trading, {'enabled', 'poll_seconds', 'stable_polls', 'max_depth', 'max_entries',
                          'max_active_trades', 'max_retries', 'retry_backoff_seconds',
                          'terminal_retention_days', 'auto_wipe'}, 'trading')
    enabled = trading.get('enabled', False)
    if type(enabled) is not bool:
        raise ValueError('trading.enabled must be true or false.')
    poll = trading.get('poll_seconds', 10)
    stable = trading.get('stable_polls', 3)
    depth = trading.get('max_depth', 64)
    entries = trading.get('max_entries', 100_000)
    active = trading.get('max_active_trades', 2)
    retries = trading.get('max_retries', 5)
    retention = trading.get('terminal_retention_days', 90)
    wipe = trading.get('auto_wipe', False)
    for value, label, low, high in (
        (poll, 'poll_seconds', 5, 3600), (stable, 'stable_polls', 2, 100),
        (depth, 'max_depth', 1, 128), (entries, 'max_entries', 1, 1_000_000),
        (active, 'max_active_trades', 1, 32), (retries, 'max_retries', 0, MAX_RETRIES),
        (retention, 'terminal_retention_days', 1, 3650),
    ):
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f'trading.{label} must be an integer from {low} through {high}.')
    if type(wipe) is not bool:
        raise ValueError('trading.auto_wipe must be true or false.')
    backoff = trading.get('retry_backoff_seconds', [30, 120, 600, 1800, 3600])
    if (not isinstance(backoff, list) or not backoff or len(backoff) > MAX_BACKOFF or
            any(type(item) is not int or item < 1 or item > 30 * 86400 for item in backoff)):
        raise ValueError('trading.retry_backoff_seconds must contain 1-24 positive values up to 30 days.')

    def records(key, label):
        values = raw.get(key, [])
        if not isinstance(values, list) or len(values) > MAX_RULES:
            raise ValueError(f'{label} must contain at most {MAX_RULES} tables.')
        result = []
        for index, item in enumerate(values):
            item = _table(item, f'{label}[{index}]')
            allowed = {'id', 'bookmark', 'path', 'whitelist', 'blacklist', 'enabled'}
            allowed |= {'completion_marker'} if label == 'sources' else {'conflict', 'verify'}
            _known_keys(item, allowed, f'{label}[{index}]')
            identity = _identifier(item.get('id'), f'{label}[{index}].id')
            bookmark = _string(item.get('bookmark'), f'{label}[{index}].bookmark', maximum=128)
            if bookmark not in site_names:
                raise ValueError(f'{label} {identity} references unknown saved site {bookmark!r}.')
            path = _remote_path(item.get('path'), f'{label} {identity}.path')
            enabled_item = item.get('enabled', True)
            if type(enabled_item) is not bool:
                raise ValueError(f'{label} {identity}.enabled must be true or false.')
            record = dict(id=identity, bookmark=bookmark, path=path,
                               whitelist=_patterns(item.get('whitelist', []), f'{label} {identity}.whitelist'),
                               blacklist=_patterns(item.get('blacklist', []), f'{label} {identity}.blacklist'),
                               enabled=enabled_item)
            if label == 'sources' and 'completion_marker' in item:
                record['completion_marker'] = _string(item['completion_marker'], f'{label} {identity}.completion_marker', maximum=255)
            if label == 'destinations':
                record['conflict'] = item.get('conflict', 'fail')
                record['verify'] = item.get('verify', 'size-and-mtime')
                if record['conflict'] not in ('fail', 'skip', 'rename'):
                    raise ValueError(f'Destination {identity}.conflict must be fail, skip, or rename.')
                if record['verify'] not in ('size', 'size-and-mtime'):
                    raise ValueError(f'Destination {identity}.verify must be size or size-and-mtime.')
            result.append(record)
        _unique(result, label)
        return result

    sources = records('sources', 'sources')
    destinations = records('destinations', 'destinations')
    source_by_id = {item['id']: item for item in sources}
    destination_by_id = {item['id']: item for item in destinations}
    routes_raw = raw.get('routes', [])
    if not isinstance(routes_raw, list) or len(routes_raw) > MAX_RULES:
        raise ValueError(f'routes must contain at most {MAX_RULES} tables.')
    routes = []
    for index, value in enumerate(routes_raw):
        value = _table(value, f'routes[{index}]')
        identity = _identifier(value.get('id'), f'routes[{index}].id')
        source_id = _identifier(value.get('source'), f'route {identity}.source')
        destination_id = _identifier(value.get('destination'), f'route {identity}.destination')
        if source_id not in source_by_id or destination_id not in destination_by_id:
            raise ValueError(f'Route {identity} references an unknown source or destination.')
        enabled_route = value.get('enabled', True)
        if type(enabled_route) is not bool:
            raise ValueError(f'Route {identity}.enabled must be true or false.')
        routes.append(dict(id=identity, source=source_id, destination=destination_id, enabled=enabled_route))
    _unique(routes, 'route')

    irc = _table(raw.get('irc', {}), 'irc')
    _known_keys(irc, {'networks', 'triggers'}, 'irc')
    networks = irc.get('networks', [])
    triggers = irc.get('triggers', [])
    if not isinstance(networks, list) or len(networks) > 64 or not isinstance(triggers, list) or len(triggers) > MAX_RULES:
        raise ValueError('IRC networks/triggers exceed their configured limits.')
    clean_networks = []
    for index, value in enumerate(networks):
        value = _table(value, f'irc.networks[{index}]')
        _known_keys(value, {'id', 'host', 'port', 'tls', 'nickname', 'username', 'channels', 'credential_id'},
                    f'irc.networks[{index}]')
        identity = _identifier(value.get('id'), f'irc.networks[{index}].id')
        host = _string(value.get('host'), f'IRC network {identity}.host', maximum=253)
        port = value.get('port', 6697)
        tls = value.get('tls', True)
        nick = _string(value.get('nickname'), f'IRC network {identity}.nickname', maximum=64)
        username = _string(value.get('username', nick), f'IRC network {identity}.username', maximum=64)
        if not re.fullmatch(r'[A-Za-z0-9_\-\[\]\\`^{|}]{1,30}', nick):
            raise ValueError(f'IRC network {identity}.nickname is not a valid IRC nickname.')
        if any(char.isspace() for char in username) or any(char in username for char in '@!:'):
            raise ValueError(f'IRC network {identity}.username contains invalid characters.')
        channels = value.get('channels', [])
        if type(port) is not int or not 1 <= port <= 65535 or type(tls) is not bool:
            raise ValueError(f'IRC network {identity} has an invalid port or TLS setting.')
        if not isinstance(channels, list) or not channels or len(channels) > 128:
            raise ValueError(f'IRC network {identity} needs 1-128 channels.')
        channels = tuple(_string(channel, 'IRC channel', maximum=200) for channel in channels)
        if any(not channel.startswith(('#', '&', '+', '!')) or any(c in channel for c in ' ,\r\n\x00') for channel in channels):
            raise ValueError(f'IRC network {identity} contains an invalid channel name.')
        clean_networks.append(dict(id=identity, host=host, port=port, tls=tls, nickname=nick,
                                   username=username, channels=channels,
                                   credential_id=_identifier(value['credential_id'], 'IRC credential_id')
                                   if value.get('credential_id') else ''))
    _unique(clean_networks, 'IRC network')
    clean_triggers = []
    for index, value in enumerate(triggers):
        value = _table(value, f'irc.triggers[{index}]')
        _known_keys(value, {'id', 'route', 'network', 'channels', 'senders', 'pattern'},
                    f'irc.triggers[{index}]')
        identity = _identifier(value.get('id'), f'irc.triggers[{index}].id')
        route = _identifier(value.get('route'), f'IRC trigger {identity}.route')
        if route not in {item['id'] for item in routes}:
            raise ValueError(f'IRC trigger {identity} references unknown route {route!r}.')
        network = value.get('network', '')
        if network and network not in {item['id'] for item in clean_networks}:
            raise ValueError(f'IRC trigger {identity} references unknown network {network!r}.')
        channels = value.get('channels', [])
        senders = value.get('senders', [])
        if not isinstance(channels, list) or not channels or not isinstance(senders, list) or not senders:
            raise ValueError(f'IRC trigger {identity} requires channel and sender allowlists.')
        channels = tuple(_string(item, 'IRC trigger channel', maximum=200).casefold() for item in channels)
        senders = tuple(_string(item, 'IRC trigger sender', maximum=128).casefold() for item in senders)
        pattern = _safe_regex(value.get('pattern'))
        clean_triggers.append(dict(id=identity, route=route, network=network,
                                   channels=channels, senders=senders, pattern=pattern))
    _unique(clean_triggers, 'IRC trigger')
    return dict(enabled=enabled, poll_seconds=poll, stable_polls=stable, max_depth=depth,
                max_entries=entries, max_active_trades=active, max_retries=retries,
                retry_backoff_seconds=tuple(backoff), terminal_retention_days=retention,
                auto_wipe=wipe, sources=sources, destinations=destinations, routes=routes,
                networks=clean_networks, triggers=clean_triggers)


def load_config(path, site_names):
    """Load and validate a UTF-8 TOML `.fluxftprc` file."""
    path = Path(path)
    if not path.exists():
        return validate_config({}, site_names)
    if path.stat().st_size > MAX_CONFIG_BYTES:
        raise ValueError('.fluxftprc exceeds 1 MB.')
    try:
        raw = tomllib.loads(path.read_text('utf-8'))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError('Cannot read .fluxftprc: ' + str(error)) from error
    return validate_config(raw, site_names)


def match_name(name, whitelist, blacklist):
    """Apply one-component allow/deny globs; blacklist always takes precedence."""
    if not isinstance(name, str) or not name or '/' in name or '\\' in name or name in ('.', '..'):
        return False
    return _matches(name, whitelist, blacklist)


def match_irc_message(config, network, channel, sender, message):
    """Return the first allowlisted trigger and folder captured from an IRC event."""
    if not isinstance(message, str) or len(message) > MAX_MESSAGE or any(ord(char) < 32 and char not in '\t' for char in message):
        return None
    for trigger in config['triggers']:
        if trigger['network'] and trigger['network'].casefold() != str(network).casefold():
            continue
        if channel.casefold() not in trigger['channels'] or sender.casefold() not in trigger['senders']:
            continue
        match = trigger['pattern'].fullmatch(message)
        if match:
            folder = match.group('folder').strip()
            if folder and '/' not in folder and '\\' not in folder and folder not in ('.', '..'):
                return dict(trigger_id=trigger['id'], route_id=trigger['route'], folder=folder)
    return None
