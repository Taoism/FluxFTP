"""Verified official OpenSSL 4 MSI updates, extracted without installation."""
import json
from pathlib import Path
import re
import shutil
import ssl
import tempfile
import time
import urllib.request
from urllib.parse import urlsplit

from . import openssl_runtime as runtime
from .openssl_msi import extract

API = 'https://api.github.com/repos/openssl/installer/releases?per_page=100'


def allowed(url):
    parsed = urlsplit(url)
    return (parsed.scheme == 'https' and not parsed.username and not parsed.password
            and parsed.port in (None, 443) and (
                parsed.hostname == 'api.github.com' and parsed.path == '/repos/openssl/installer/releases'
                or parsed.hostname == 'github.com' and parsed.path.startswith('/openssl/installer/releases/download/')
                or parsed.hostname == 'release-assets.githubusercontent.com'))


class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed(newurl):
            raise ValueError('Unexpected OpenSSL download redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, output, limit):
    if not allowed(url):
        raise ValueError('Untrusted OpenSSL source')
    opener = urllib.request.build_opener(Redirect(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    request = urllib.request.Request(url, headers={'User-Agent': 'FluxFTP-OpenSSL-Updater'})
    total = 0
    with opener.open(request, timeout=60) as response:
        while block := response.read(1024 * 1024):
            total += len(block)
            if total > limit:
                raise ValueError('OpenSSL download exceeds size limit')
            output.write(block)


def newest_release():
    import io
    output = io.BytesIO()
    fetch(API, output, 8 * 1024 * 1024)
    candidates = []
    for release in json.loads(output.getvalue()):
        if release.get('draft') or release.get('prerelease'):
            continue
        match = re.fullmatch(r'publish/(4\.[0-9]+\.[0-9]+)-[0-9]+', release['tag_name'])
        if not match:
            continue
        version = match[1]
        parsed = runtime.version(version)
        for asset in release['assets']:
            if asset['name'] == f'OpenSSL-x64-VS-{version}.msi':
                digest = asset.get('digest', '')
                if not re.fullmatch('sha256:[0-9a-f]{64}', digest):
                    raise ValueError('Official package checksum absent')
                candidates.append((parsed, version, asset['browser_download_url'], digest[7:]))
    if not candidates:
        raise ValueError('No compatible stable OpenSSL 4 package found')
    return max(candidates, key=lambda value: value[0])[1:]


def stage(location, release, url, digest):
    if runtime.version(release)[0] != 4 or not re.fullmatch('[0-9a-f]{64}', digest):
        raise ValueError('Invalid OpenSSL 4 package identity')
    identity = digest[:40]
    destination = location / (release + '-' + identity)
    location.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        with tempfile.TemporaryDirectory(prefix='download-4-', dir=location) as temporary:
            directory = Path(temporary)
            package = directory / 'package.msi'
            with package.open('wb') as output:
                fetch(url, output, 256 * 1024 * 1024)
            if runtime.sha256(package.read_bytes()).hexdigest() != digest:
                raise ValueError('Official OpenSSL package checksum mismatch')
            extract(package, directory, runtime.DLLS4)
            package.unlink()
            adapter = runtime.adapter_directory()
            for name in ('_ssl.pyd', '_hashlib.pyd'):
                shutil.copyfile(adapter / name, directory / name)
            hashes = {name: runtime.sha256((directory / name).read_bytes()).hexdigest()
                      for name in (*runtime.DLLS4, '_ssl.pyd', '_hashlib.pyd')}
            runtime.write_json(directory / 'manifest.json', {'version': release,
                'commit': identity, 'package_sha256': digest, 'source': url, 'sha256': hashes})
            runtime.validate_pair(directory)
            runtime.probe(directory)
            try:
                directory.rename(destination)
            except FileExistsError:
                if runtime.validate_pair(destination)['sha256'] != hashes:
                    raise ValueError('Concurrent OpenSSL package mismatch')
    runtime.validate_pair(destination)
    if (destination / 'rejected.json').exists():
        raise ValueError('This OpenSSL runtime previously failed startup')
    runtime.probe(destination)
    runtime.write_json(runtime.selection_file(location, 4), {'directory': destination.name})
    return destination


def check(location, force=False):
    now = time.time()
    try:
        if not force and 0 <= now - runtime.read_json(location / 'checked-4.json')['time'] < 86400:
            return None
    except (OSError, ValueError, KeyError, TypeError):
        pass
    runtime.write_json(location / 'checked-4.json', {'time': now})
    release, url, digest = newest_release()
    try:
        available = runtime.available_directory(location, 4)
    except (OSError, ValueError, KeyError, TypeError):
        available = runtime.adapter_directory()
    current = runtime.version(runtime.validate_pair(available)['version'])
    if runtime.version(release) > current:
        stage(location, release, url, digest)
        message = f'OpenSSL {release} verified and ready for the next 4.x launch.'
    else:
        message = f'OpenSSL 4.x is up to date ({".".join(map(str, current))}).'
    runtime.write_json(location / 'status-4.json', {'message': message})
    return message
