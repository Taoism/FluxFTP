"""GitHub Actions workflow artifact discovery and safe local import."""
from __future__ import annotations

import hashlib
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, build_opener

from .gitlab_artifacts import MAX_API_BYTES, MAX_ARCHIVE_BYTES, _SafeRedirect, import_artifact_response

API = 'https://api.github.com'
API_VERSION = '2022-11-28'


def repository_name(value):
    value = str(value).strip().removesuffix('.git')
    match = re.fullmatch(r'(?:https://github\.com/)?([^/\s]+)/([^/\s]+)', value, re.IGNORECASE)
    if not match:
        raise ValueError('Enter a GitHub repository as owner/repository.')
    return match.group(1) + '/' + match.group(2)


def token_id():
    return 'github-actions-api-token-v1:' + hashlib.sha256(API.casefold().encode('utf-8')).hexdigest()


class GitHubActionsClient:
    def __init__(self, token, timeout=30):
        self.token = str(token).strip()
        if not self.token or len(self.token) > 4096 or any(ord(c) < 32 for c in self.token):
            raise ValueError('Enter a valid GitHub token with Actions read permission.')
        self.timeout = timeout
        self.opener = build_opener(_SafeRedirect())

    @staticmethod
    def _repo_path(repository):
        repo = repository_name(repository)
        return '/'.join(quote(part, safe='') for part in repo.split('/'))

    def _open(self, endpoint, query=None, maximum=MAX_API_BYTES):
        url = API + endpoint
        if query:
            url += '?' + urlencode(query)
        request = Request(url, headers={
            'Authorization': 'Bearer ' + self.token,
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': API_VERSION,
        })
        try:
            response = self.opener.open(request, timeout=self.timeout)
        except HTTPError as error:
            detail = error.read(2048).decode('utf-8', 'replace').replace(self.token, '[redacted]')
            raise RuntimeError(f'GitHub API returned HTTP {error.code}: {detail[:500]}') from error
        except URLError as error:
            raise RuntimeError('Could not connect to GitHub: ' + str(error.reason)) from error
        return response, maximum

    def _json(self, endpoint, query=None):
        response, maximum = self._open(endpoint, query)
        with response:
            body = response.read(maximum + 1)
            if len(body) > maximum:
                raise ValueError('GitHub API response exceeded the size limit.')
        try:
            value = json.loads(body.decode('utf-8'))
        except (ValueError, UnicodeError) as error:
            raise ValueError('GitHub returned invalid JSON.') from error
        if not isinstance(value, dict):
            raise ValueError('GitHub returned an invalid API response.')
        return value

    def repository(self, repository):
        path = self._repo_path(repository)
        value = self._json('/repos/' + path)
        if (type(value.get('id')) is not int or not isinstance(value.get('full_name'), str) or
                not isinstance(value.get('html_url'), str)):
            raise ValueError('GitHub returned an invalid repository record.')
        return value

    def workflow_runs(self, repository, branch):
        path = self._repo_path(repository)
        branch = str(branch).strip()
        if not branch or len(branch) > 512:
            raise ValueError('Enter a branch or tag name.')
        result = []
        for page in range(1, 11):
            value = self._json(f'/repos/{path}/actions/runs', {
                'branch': branch, 'status': 'success', 'per_page': 100, 'page': page,
            })
            batch = value.get('workflow_runs')
            if not isinstance(batch, list):
                raise ValueError('GitHub returned an invalid workflow run list.')
            result.extend(item for item in batch if isinstance(item, dict) and
                item.get('conclusion') == 'success' and item.get('head_branch') == branch)
            if len(batch) < 100:
                break
        return result[:500]

    def artifacts(self, repository, run_id):
        path = self._repo_path(repository)
        result = self._json(f'/repos/{path}/actions/runs/{int(run_id)}/artifacts')
        items = result.get('artifacts')
        if not isinstance(items, list):
            raise ValueError('GitHub returned an invalid workflow artifact list.')
        available = []
        for item in items:
            if (not isinstance(item, dict) or item.get('expired') or
                    type(item.get('id')) is not int or not isinstance(item.get('name'), str) or
                    not item.get('name') or not isinstance(item.get('archive_download_url'), str)):
                continue
            available.append({
                **item,
                'artifacts_file': {'filename': item['name'] + '.zip', 'size': item.get('size_in_bytes')},
            })
        return available[:500]

    def download_artifact(self, repository, artifact_id, cache_root):
        path = self._repo_path(repository)
        response, _ = self._open(f'/repos/{path}/actions/artifacts/{int(artifact_id)}/zip',
                                 maximum=MAX_ARCHIVE_BYTES)
        return import_artifact_response(response, cache_root, f'run-artifact-{int(artifact_id)}')
