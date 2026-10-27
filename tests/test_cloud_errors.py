from fluxftp.cloud import operation_error
import pytest


@pytest.mark.parametrize('details,expected', [
    ('bind: An attempt was made to access a socket in a way forbidden by its access permissions.', 'excluded port range'),
    ('bind: permission denied', 'excluded port range'),
    ('bind: Only one usage of each socket address is normally permitted.', 'another application'),
    ('bind: address already in use', 'another application'),
    ('unknown listener failure', 'port availability'),
])
def test_authorization_listener_errors_are_actionable_and_private(details, expected):
    message = operation_error('config failed to refresh token: failed to start auth webserver: '
                              + details + ' SECRET https://private/?token=SECRET')
    assert expected in message
    assert '53682' in message
    assert 'SECRET' not in message and 'https://private' not in message


def test_expired_authorization_explains_reconnect_without_disclosing_response():
    message = operation_error('oauth: invalid_grant token expired; refresh_token=SECRET https://private/path')
    assert 'Sign in' in message
    assert 'invalid_grant' in message
    assert 'SECRET' not in message
    assert 'private/path' not in message


def test_unknown_backend_errors_stay_private():
    message = operation_error('Authorization: Bearer SECRET; account=user@example.com')
    assert message == 'Cloud operation failed. Check account authorization, permissions and the selected path.'


def test_onedrive_listener_error_names_its_dedicated_port():
    message = operation_error('failed to start auth webserver: listen tcp 127.0.0.1:18342: '
                              'bind: access permissions SECRET')
    assert '18342' in message and '53682' not in message and 'SECRET' not in message


def test_public_s3_root_error_explains_bucket_path():
    message = operation_error('operation error S3: ListBuckets, StatusCode: 403, AccessDenied SECRET')
    assert '/bucket-name/folder' in message and 'Site Manager' in message
    assert 'SECRET' not in message
