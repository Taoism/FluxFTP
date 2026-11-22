"""OAuth device authorization helpers for CI artifact providers."""
from __future__ import annotations

import json
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

GITHUB_TOKEN_RECORD_PREFIX = 'fluxftp-github-oauth-v1:'


class _SameHostRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        if urlsplit(request.full_url).netloc.casefold() != urlsplit(new_url).netloc.casefold():
            return None
        return super().redirect_request(request, response, code, message, headers, new_url)


def _post(url, fields, timeout=20):
    if urlsplit(url).scheme != 'https':
        raise ValueError('OAuth endpoints must use HTTPS.')
    request = Request(url, data=urlencode(fields).encode('ascii'), headers={
        'Accept': 'application/json', 'Content-Type': 'application/x-www-form-urlencoded',
    })
    try:
        with build_opener(_SameHostRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(64 * 1024 + 1)
    except HTTPError as error:
        detail = error.read(2048).decode('utf-8', 'replace')
        try:
            message = json.loads(detail).get('error_description') or json.loads(detail).get('error')
        except (ValueError, AttributeError):
            message = ''
        raise RuntimeError(f'OAuth provider returned HTTP {error.code}' + (f': {message}' if message else '.')) from error
    if len(raw) > 64 * 1024:
        raise ValueError('OAuth response exceeded the size limit.')
    try:
        value = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeError) as error:
        raise ValueError('OAuth provider returned invalid JSON.') from error
    if not isinstance(value, dict):
        raise ValueError('OAuth provider returned an invalid response.')
    return value


def request_device_code(provider, base_url, client_id):
    """Start a provider device flow; return display data and private polling data."""
    client_id = str(client_id).strip()
    if not client_id or len(client_id) > 512 or any(ord(c) < 33 for c in client_id):
        raise ValueError('Enter the OAuth application client ID.')
    if provider == 'github':
        base_url = 'https://github.com'
        endpoint = base_url + '/login/device/code'
        fields = {'client_id': client_id, 'scope': 'offline_access'}
    elif provider == 'gitlab':
        from .gitlab_artifacts import normalize_url
        base_url = normalize_url(base_url)
        endpoint = base_url + '/oauth/authorize_device'
        fields = {'client_id': client_id, 'scope': 'read_api'}
    else:
        raise ValueError('Unsupported OAuth provider.')
    value = _post(endpoint, fields)
    code = value.get('device_code')
    user_code = value.get('user_code')
    verification_uri = value.get('verification_uri')
    expires_in = value.get('expires_in')
    interval = value.get('interval', 5)
    if (not isinstance(code, str) or not code or not isinstance(user_code, str) or not user_code or
            not isinstance(verification_uri, str) or urlsplit(verification_uri).scheme != 'https' or
            type(expires_in) is not int or not 1 <= expires_in <= 3600 or
            type(interval) not in (int, float) or not 1 <= interval <= 60):
        raise ValueError(str(value.get('error_description') or 'OAuth provider returned incomplete device authorization data.'))
    allowed_host = 'github.com' if provider == 'github' else urlsplit(base_url).hostname
    if urlsplit(verification_uri).hostname != allowed_host:
        raise ValueError('OAuth verification URL does not match the selected provider.')
    return {'device_code': code, 'user_code': user_code, 'verification_uri': verification_uri,
            'expires_in': expires_in, 'interval': interval, 'provider': provider,
            'base_url': base_url, 'client_id': client_id}


def poll_device_token(flow, sleep=time.sleep, clock=time.monotonic, structured=False):
    """Poll until authorization succeeds, expires, or the user denies access."""
    provider = flow['provider']
    endpoint = ('https://github.com/login/oauth/access_token' if provider == 'github' else
                flow['base_url'] + '/oauth/token')
    started = clock()
    interval = flow['interval']
    while clock() - started < flow['expires_in']:
        sleep(interval)
        fields = {'client_id': flow['client_id'], 'device_code': flow['device_code'],
                  'grant_type': 'urn:ietf:params:oauth:grant-type:device_code'}
        value = _post(endpoint, fields)
        token = value.get('access_token')
        if isinstance(token, str) and token:
            return value if structured else token
        error = value.get('error')
        if error == 'authorization_pending':
            continue
        if error == 'slow_down':
            interval = min(interval + 5, 60)
            continue
        if error in ('access_denied', 'authorization_declined'):
            raise RuntimeError('Sign-in was denied.')
        if error in ('expired_token', 'device_code_expired'):
            raise RuntimeError('The sign-in code expired. Start sign-in again.')
        raise RuntimeError(str(value.get('error_description') or 'OAuth sign-in failed.'))
    raise RuntimeError('The sign-in code expired. Start sign-in again.')


def github_token_record(response, client_id, now=None):
    """Serialize GitHub's rotating access/refresh pair for encrypted-vault storage."""
    if not isinstance(response, dict) or not isinstance(response.get('access_token'), str):
        raise ValueError('GitHub did not return an access token.')
    record = {'version': 1, 'client_id': str(client_id), 'access_token': response['access_token']}
    now = time.time() if now is None else now
    if type(response.get('expires_in')) is int:
        record['access_expires_at'] = now + response['expires_in']
    if isinstance(response.get('refresh_token'), str) and response['refresh_token']:
        record['refresh_token'] = response['refresh_token']
        if type(response.get('refresh_token_expires_in')) is int:
            record['refresh_expires_at'] = now + response['refresh_token_expires_in']
    return GITHUB_TOKEN_RECORD_PREFIX + json.dumps(record, separators=(',', ':'))


def is_github_token_record(value):
    return isinstance(value, str) and value.startswith(GITHUB_TOKEN_RECORD_PREFIX)


def resolve_github_access_token(stored, client_id, save, now=None):
    """Return a usable access token, rotating and persisting expiring credentials."""
    if not is_github_token_record(stored):
        return stored
    try:
        record = json.loads(stored[len(GITHUB_TOKEN_RECORD_PREFIX):])
    except (ValueError, TypeError) as error:
        raise RuntimeError('The saved GitHub sign-in record is invalid. Sign in again.') from error
    if (not isinstance(record, dict) or record.get('version') != 1 or
            not isinstance(record.get('client_id'), str) or record['client_id'] != client_id or
            not isinstance(record.get('access_token'), str)):
        raise RuntimeError('The saved GitHub sign-in does not match this app. Sign in again.')
    now = time.time() if now is None else now
    expires_at = record.get('access_expires_at')
    if type(expires_at) is not int and type(expires_at) is not float:
        return record['access_token']
    if now + 60 < expires_at:
        return record['access_token']
    refresh_token = record.get('refresh_token')
    refresh_expires_at = record.get('refresh_expires_at')
    if not isinstance(refresh_token, str) or (refresh_expires_at is not None and now >= refresh_expires_at):
        raise RuntimeError('The saved GitHub sign-in expired. Sign in again.')
    response = _post('https://github.com/login/oauth/access_token', {
        'client_id': client_id, 'grant_type': 'refresh_token', 'refresh_token': refresh_token,
    })
    if not isinstance(response.get('access_token'), str) or not isinstance(response.get('refresh_token'), str):
        raise RuntimeError('GitHub could not refresh the saved sign-in. Sign in again.')
    renewed = github_token_record(response, client_id, now=now)
    save(renewed)
    return response['access_token']
