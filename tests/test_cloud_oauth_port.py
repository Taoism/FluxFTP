"""Exercise the real bundled OAuth listener without signing in to a real account."""
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

import pytest
from test_cloud import service


def begin(service, parameters):
    result = service.call('config/create', {'name': 'oauth_test', 'type': 'onedrive',
        'parameters': {'config_auth_no_browser': 'true', **parameters},
        'opt': {'nonInteractive': True, 'all': True}})
    for _ in range(60):
        option = result.get('Option') or {}
        value = option.get('Default', '')
        value = str(value).lower() if isinstance(value, bool) else str(value or '')
        args = {'name': 'oauth_test', 'parameters': {}, 'opt': {'nonInteractive': True,
            'continue': True, 'state': result['State'], 'result': value}}
        if option.get('Name') == 'config_is_local':
            args['opt']['result'] = 'true'
            return service.call('config/update', {**args, '_async': True})['jobid']
        result = service.call('config/update', args)
    pytest.fail('OAuth question was not reached')


def wait_for(service, endpoint, arguments, condition):
    for _ in range(100):
        result = service.call(endpoint, arguments)
        if condition(result):
            return result
        time.sleep(.05)
    pytest.fail('Backend did not reach the expected OAuth state')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


@pytest.mark.parametrize('outcome', ['success', 'bad_state', 'cancel'])
def test_onedrive_uses_separate_port_for_listener_and_exchange(service, outcome):
    requests = []
    class TokenEndpoint(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            requests.append(parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode()))
            data = json.dumps({'access_token': 'test-access', 'refresh_token': 'test-refresh',
                               'token_type': 'Bearer', 'expires_in': 3600}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    server = HTTPServer(('127.0.0.1', 0), TokenEndpoint)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        job = begin(service, {'client_id': 'test-client', 'client_secret': 'test-secret',
            'token_url': f'http://127.0.0.1:{server.server_port}/token'})
        auth = wait_for(service, 'config/oauthstatus', {}, lambda r: r['status'] == 'running')
        url = urlparse(auth['authUrl'])
        assert url.hostname == '127.0.0.1' and url.port == 18342
        with pytest.raises(HTTPError) as redirect:
            opener.open(auth['authUrl'], timeout=3)
        assert redirect.value.code in (302, 307)
        target = urlparse(redirect.value.headers['Location'])
        assert target.hostname == 'login.microsoftonline.com'
        query = parse_qs(target.query)
        assert query['redirect_uri'] == ['http://localhost:18342/']
        if outcome == 'cancel':
            service.call('config/oauthstop')
        else:
            state = query['state'][0] if outcome == 'success' else 'wrong-state'
            callback = 'http://127.0.0.1:18342/?' + urlencode({'state': state, 'code': 'test-code'})
            if outcome == 'success':
                with opener.open(callback, timeout=3) as response:
                    assert response.status == 200
            else:
                with pytest.raises(HTTPError) as rejected:
                    opener.open(callback, timeout=3)
                assert rejected.value.code == 400
        status = wait_for(service, 'job/status', {'jobid': job}, lambda r: r.get('finished'))
        assert bool(status['success']) == (outcome == 'success')
        if outcome == 'success':
            assert requests[0]['redirect_uri'] == ['http://localhost:18342/']
            assert requests[0]['code'] == ['test-code']
            details = service.call('config/get', {'name': 'oauth_test'})
            assert json.loads(details['token'])['access_token'] == 'test-access'
        else:
            assert not requests
        assert service.call('config/oauthstatus')['status'] == 'stopped'
    finally:
        server.shutdown()
        thread.join(3)
        server.server_close()
