import pytest

from fluxftp import artifact_oauth


def test_github_device_code_request_and_poll(monkeypatch):
    calls = []
    def fake_post(url, fields):
        calls.append((url, fields))
        if url.endswith('/login/device/code'):
            return {'device_code': 'private-device-code', 'user_code': 'ABCD-EFGH',
                    'verification_uri': 'https://github.com/login/device', 'expires_in': 90, 'interval': 1}
        return {'access_token': 'access-token'}
    monkeypatch.setattr(artifact_oauth, '_post', fake_post)
    flow = artifact_oauth.request_device_code('github', '', 'public-client-id')
    assert flow['user_code'] == 'ABCD-EFGH'
    assert calls[0][1] == {'client_id': 'public-client-id', 'scope': 'offline_access'}
    assert artifact_oauth.poll_device_token(flow, sleep=lambda _seconds: None,
        clock=iter((0, 0, 0)).__next__) == 'access-token'


def test_github_expiring_token_is_refreshed_and_rotated(monkeypatch):
    record = artifact_oauth.github_token_record({
        'access_token': 'old-access', 'expires_in': 10,
        'refresh_token': 'old-refresh', 'refresh_token_expires_in': 100,
    }, 'client-id', now=0)
    calls = []
    saved = []
    def fake_post(url, fields):
        calls.append((url, fields))
        return {'access_token': 'new-access', 'expires_in': 80,
                'refresh_token': 'new-refresh', 'refresh_token_expires_in': 200}
    monkeypatch.setattr(artifact_oauth, '_post', fake_post)

    access = artifact_oauth.resolve_github_access_token(record, 'client-id', saved.append, now=10)

    assert access == 'new-access'
    assert calls == [('https://github.com/login/oauth/access_token', {
        'client_id': 'client-id', 'grant_type': 'refresh_token', 'refresh_token': 'old-refresh',
    })]
    assert len(saved) == 1
    assert artifact_oauth.resolve_github_access_token(saved[0], 'client-id', saved.append, now=11) == 'new-access'


def test_gitlab_device_code_is_bound_to_server_and_requests_read_api(monkeypatch):
    calls = []
    monkeypatch.setattr(artifact_oauth, '_post', lambda url, fields: calls.append((url, fields)) or {
        'device_code': 'device', 'user_code': 'CODE',
        'verification_uri': 'https://gitlab.example/oauth/device', 'expires_in': 300, 'interval': 5,
    })
    flow = artifact_oauth.request_device_code('gitlab', 'https://gitlab.example/', 'application-id')
    assert flow['base_url'] == 'https://gitlab.example'
    assert calls == [('https://gitlab.example/oauth/authorize_device',
                      {'client_id': 'application-id', 'scope': 'read_api'})]


@pytest.mark.parametrize('error', ['authorization_pending', 'slow_down'])
def test_device_poll_retries_pending_and_slow_down(monkeypatch, error):
    responses = iter([{'error': error}, {'access_token': 'ok'}])
    monkeypatch.setattr(artifact_oauth, '_post', lambda *_args: next(responses))
    delays = []
    flow = {'provider': 'gitlab', 'base_url': 'https://gitlab.example', 'client_id': 'id',
            'device_code': 'code', 'interval': 1, 'expires_in': 30}
    assert artifact_oauth.poll_device_token(flow, sleep=delays.append, clock=lambda: 0) == 'ok'
    assert delays == [1, 6 if error == 'slow_down' else 1]


def test_device_poll_reports_denial_and_rejects_untrusted_verification_host(monkeypatch):
    monkeypatch.setattr(artifact_oauth, '_post', lambda *_args: {'error': 'access_denied'})
    flow = {'provider': 'github', 'client_id': 'id', 'device_code': 'code',
            'interval': 1, 'expires_in': 10}
    with pytest.raises(RuntimeError, match='denied'):
        artifact_oauth.poll_device_token(flow, sleep=lambda _seconds: None, clock=lambda: 0)

    monkeypatch.setattr(artifact_oauth, '_post', lambda *_args: {
        'device_code': 'code', 'user_code': 'CODE', 'verification_uri': 'https://attacker.example/device',
        'expires_in': 10, 'interval': 1,
    })
    with pytest.raises(ValueError, match='does not match'):
        artifact_oauth.request_device_code('github', '', 'id')
