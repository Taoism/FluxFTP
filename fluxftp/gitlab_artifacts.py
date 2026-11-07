"""GitLab pipeline artifact discovery and safe local import."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

from .core import safe_name

MAX_API_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_EXTRACTED_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_FILES = 20000


def normalize_url(value):
    parsed = urlsplit(str(value).strip())
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or
            parsed.query or parsed.fragment):
        raise ValueError('GitLab URL must be an HTTPS base URL without credentials, query, or fragment.')
    path = parsed.path.rstrip('/')
    return urlunsplit(('https', parsed.netloc, path, '', ''))


def token_id(base_url):
    digest = hashlib.sha256(normalize_url(base_url).casefold().encode('utf-8')).hexdigest()
    return 'gitlab-api-token-v1:' + digest


class _SafeRedirect(HTTPRedirectHandler):
    """Never forward a private token to a redirected host."""
    def redirect_request(self, request, response, code, message, headers, new_url):
        redirected = super().redirect_request(request, response, code, message, headers, new_url)
        if redirected is None:
            return None
        if urlsplit(request.full_url).netloc.casefold() != urlsplit(new_url).netloc.casefold():
            for collection in (redirected.headers, redirected.unredirected_hdrs):
                for key in list(collection):
                    if key.casefold() in ('private-token', 'authorization'):
                        collection.pop(key, None)
        return redirected


class GitLabClient:
    def __init__(self, base_url, token, timeout=30):
        self.base_url = normalize_url(base_url)
        self.token = str(token).strip()
        if not self.token or len(self.token) > 4096 or any(ord(c) < 32 for c in self.token):
            raise ValueError('Enter a valid GitLab access token.')
        self.timeout = timeout
        self.opener = build_opener(_SafeRedirect())

    def _open(self, endpoint, query=None, maximum=MAX_API_BYTES):
        url = self.base_url + '/api/v4/' + endpoint.lstrip('/')
        if query:
            url += '?' + urlencode(query)
        request = Request(url, headers={'PRIVATE-TOKEN': self.token, 'Accept': 'application/json'})
        try:
            response = self.opener.open(request, timeout=self.timeout)
        except HTTPError as error:
            detail = error.read(2048).decode('utf-8', 'replace').replace(self.token, '[redacted]')
            raise RuntimeError(f'GitLab API returned HTTP {error.code}: {detail[:500]}') from error
        except URLError as error:
            raise RuntimeError('Could not connect to the GitLab server: ' + str(error.reason)) from error
        return response, maximum

    def _json(self, endpoint, query=None):
        response, maximum = self._open(endpoint, query)
        with response:
            body = response.read(maximum + 1)
            if len(body) > maximum:
                raise ValueError('GitLab API response exceeded the size limit.')
        value = json.loads(body.decode('utf-8'))
        if not isinstance(value, (dict, list)):
            raise ValueError('GitLab returned an invalid API response.')
        return value

    def pipelines(self, project, ref):
        project_id = quote(str(project).strip(), safe='')
        if not project_id or len(project_id) > 512 or not ref.strip() or len(ref) > 512:
            raise ValueError('Enter a project ID/path and a branch or tag name.')
        result, page = [], 1
        while page <= 10:
            batch = self._json(f'projects/{project_id}/pipelines',
                               {'ref': ref, 'status': 'success', 'order_by': 'id', 'sort': 'desc',
                                'per_page': 100, 'page': page})
            if not isinstance(batch, list):
                raise ValueError('GitLab returned an invalid pipeline list.')
            result.extend(item for item in batch if isinstance(item, dict) and item.get('status') == 'success'
                          and item.get('ref') == ref)
            if len(batch) < 100:
                break
            page += 1
        return project_id, result[:500]

    def project(self, project_id):
        value = self._json(f'projects/{project_id}')
        if not isinstance(value.get('id'), int) or not isinstance(value.get('path_with_namespace'), str):
            raise ValueError('GitLab returned an invalid project record.')
        return value

    def jobs(self, project_id, pipeline_id):
        result, page = [], 1
        while page <= 10:
            batch = self._json(f'projects/{project_id}/pipelines/{int(pipeline_id)}/jobs',
                               {'include_retried': 'false', 'per_page': 100, 'page': page})
            if not isinstance(batch, list):
                raise ValueError('GitLab returned an invalid pipeline job list.')
            result.extend(item for item in batch if isinstance(item, dict) and item.get('status') == 'success'
                          and isinstance(item.get('artifacts_file'), dict)
                          and item['artifacts_file'].get('filename'))
            if len(batch) < 100:
                break
            page += 1
        return result[:500]

    def download_artifact(self, project_id, job_id, cache_root):
        response, _ = self._open(f'projects/{project_id}/jobs/{int(job_id)}/artifacts', maximum=MAX_ARCHIVE_BYTES)
        return import_artifact_response(response, cache_root, f'pipeline-{int(job_id)}')

    @staticmethod
    def _extract(archive, destination):
        seen, total = set(), 0
        with zipfile.ZipFile(archive) as bundle:
            infos = bundle.infolist()
            if not infos or len(infos) > MAX_ARCHIVE_FILES:
                raise ValueError('Artifact must contain between 1 and 20,000 entries.')
            for info in infos:
                name = info.filename
                if not isinstance(name, str) or not name or '\\' in name or '\x00' in name:
                    raise ValueError('Artifact contains an unsafe path.')
                raw_parts = name.split('/')
                if info.is_dir() and raw_parts[-1] == '':
                    raw_parts.pop()
                if any(part in ('', '.', '..') for part in raw_parts):
                    raise ValueError('Artifact contains a path outside its extraction folder.')
                path = PurePosixPath(name)
                if path.is_absolute():
                    raise ValueError('Artifact contains a path outside its extraction folder.')
                for part in path.parts:
                    safe_name(part)
                folded = name.casefold().rstrip('/')
                if folded in seen:
                    raise ValueError('Artifact contains duplicate paths (case-insensitive).')
                seen.add(folded)
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise ValueError('Symbolic links are not allowed in CI artifacts.')
                kind = stat.S_IFMT(mode)
                if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise ValueError('Artifact contains an unsupported special file.')
                total += info.file_size
                if total > MAX_EXTRACTED_BYTES:
                    raise ValueError('Artifact expands beyond the 2 GiB extraction limit.')
                target = destination.joinpath(*path.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                with bundle.open(info) as source, target.open('xb') as output:
                    copied = 0
                    while True:
                        block = source.read(1024 * 1024)
                        if not block:
                            break
                        copied += len(block)
                        if copied > info.file_size or copied > MAX_EXTRACTED_BYTES:
                            raise ValueError('Artifact entry exceeded its declared size.')
                        output.write(block)
                    if copied != info.file_size:
                        raise ValueError('Artifact entry size did not match the archive index.')


def import_artifact_response(response, cache_root, label):
    """Bound, safely extract, and atomically publish a downloaded artifact ZIP."""
    with response:
        size = 0
        root = Path(cache_root)
        root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink():
            raise ValueError('Artifact cache cannot be a symbolic link.')
        staging = Path(tempfile.mkdtemp(prefix='.artifact-import-', dir=root))
        archive = staging / 'artifact.zip'
        extracted = staging / 'content'
        extracted.mkdir()
        try:
            with archive.open('wb') as stream:
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    size += len(block)
                    if size > MAX_ARCHIVE_BYTES:
                        raise ValueError('Artifact archive exceeds the 1 GiB download limit.')
                    stream.write(block)
            if not size:
                raise ValueError('Git provider returned an empty artifact archive.')
            GitLabClient._extract(archive, extracted)
            archive.unlink()
            destination = root / f'{label}-{os.urandom(8).hex()}'
            os.replace(extracted, destination)
            return destination
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        finally:
            if staging.exists() and not any(staging.iterdir()):
                staging.rmdir()
