"""Obtain stable AMD64 DLLs exclusively from Python's official binary repository."""
from __future__ import annotations

import json
from pathlib import Path
import re
import ssl
import tempfile
import time
import urllib.request
from _sha1 import sha1

from .openssl_runtime import (DLLS, BASE_VERSION, version, sha256, read_json,
                              write_json, validate_pair, probe)

API = 'https://api.github.com/repos/python/cpython-bin-deps'
RAW = 'https://raw.githubusercontent.com/python/cpython-bin-deps/'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Unexpected redirect from trusted runtime source')


def fetch(url, limit=8 * 1024 * 1024):
    if not (url.startswith(API + '/') or url.startswith(RAW)):
        raise ValueError('Untrusted runtime download URL')
    request = urllib.request.Request(url, headers={
        'User-Agent': 'FluxFTP-OpenSSL-Updater', 'Accept': 'application/vnd.github+json'})
    opener = urllib.request.build_opener(NoRedirect(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    with opener.open(request, timeout=20) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Runtime download exceeds size limit')
    return data


def api(path):
    return json.loads(fetch(API + path))


def commit_for(obj):
    for _ in range(4):
        digest = obj['sha']
        if not re.fullmatch('[0-9a-f]{40}', digest):
            raise ValueError('Invalid Git object identifier')
        if obj['type'] == 'commit':
            return digest
        if obj['type'] != 'tag':
            break
        obj = api('/git/tags/' + digest)['object']
    raise ValueError('Release does not resolve to a commit')


def newest_release():
    refs = api('/git/matching-refs/tags/openssl-bin-3.')
    candidates = []
    for ref in refs:
        match = re.fullmatch(r'refs/tags/openssl-bin-(3\.(?:0|[1-9][0-9]{0,5})\.(?:0|[1-9][0-9]{0,5}))', ref['ref'])
        if match:
            candidates.append((version(match[1]), match[1], ref['object']))
    if not candidates:
        raise ValueError('No stable Windows builds available')
    _, release, obj = max(candidates, key=lambda item: item[0])
    return release, commit_for(obj)


def stage(location, release, commit):
    """Hash Git blobs against commit metadata; publish only after isolated validation."""
    version(release)
    if not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('Invalid commit')
    destination = location / (release + '-' + commit)
    if destination.exists():
        manifest = validate_pair(destination)
        if manifest['version'] != release or manifest['commit'] != commit:
            raise ValueError('Cached runtime identity mismatch')
        if (destination / 'rejected.json').exists():
            raise ValueError('This runtime previously failed startup')
        probe(destination)
    else:
        tree = api('/git/trees/' + commit + '?recursive=1')
        if tree.get('truncated'):
            raise ValueError('Incomplete release tree')
        entries = {entry['path']: entry for entry in tree['tree']}
        location.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='download-', dir=location) as temporary:
            directory = Path(temporary)
            hashes, blobs = {}, {}
            for name in DLLS:
                path = 'amd64/' + name
                entry = entries[path]
                if entry['type'] != 'blob' or entry.get('mode') not in ('100644', '100755'):
                    raise ValueError('Invalid runtime blob')
                data = fetch(RAW + commit + '/' + path, 32 * 1024 * 1024)
                digest = sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
                if digest != entry['sha'] or len(data) != entry['size']:
                    raise ValueError('Official Git blob verification failed')
                hashes[name], blobs[name] = sha256(data).hexdigest(), digest
                (directory / name).write_bytes(data)
            write_json(directory / 'manifest.json', {
                'version': release, 'commit': commit, 'source': API,
                'sha256': hashes, 'git_blobs': blobs})
            validate_pair(directory)
            probe(directory)
            try:
                directory.rename(destination)
            except FileExistsError:
                # Another running instance may already have published this pair.
                cached = validate_pair(destination)
                if cached['sha256'] != hashes or (destination / 'rejected.json').exists():
                    raise ValueError('Concurrent runtime publication mismatch')
    write_json(location / 'selected.json', {'directory': destination.name})
    return destination


def check(location, current=None, force=False):
    now = time.time()
    try:
        previous = read_json(location / 'checked.json')['time']
        if not force and 0 <= now - previous < 86400:
            return None
    except (OSError, ValueError, KeyError, TypeError):
        pass
    write_json(location / 'checked.json', {'time': now})
    release, commit = newest_release()
    active = current or version(ssl.OPENSSL_VERSION.split()[1])
    if active[0] != 3:
        active = BASE_VERSION
    minimum = max(tuple(active), BASE_VERSION)
    try:
        from .openssl_runtime import selected_directory
        existing = selected_directory(location)
        minimum = max(minimum, version(read_json(existing / 'manifest.json')['version']))
        if version(release) <= minimum and minimum > tuple(active):
            return 'Verified OpenSSL update ready; it will activate on the next launch.'
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if version(release) <= minimum:
        message = 'OpenSSL 3.x is up to date with Python’s trusted Windows builds.'
    else:
        stage(location, release, commit)
        message = f'OpenSSL {release} verified and ready; it will activate on the next launch.'
    write_json(location / 'status.json', {'message': message})
    return message
