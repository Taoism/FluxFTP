"""Cloud provider metadata, path validation and safe error presentation."""
import sys
from pathlib import Path

PROVIDERS = {
    'Amazon S3': ('s3', {'provider': 'AWS'}),
    'Backblaze B2': ('b2', {}),
    'Box': ('box', {}),
    'Dropbox': ('dropbox', {}),
    'Google Cloud Storage': ('google cloud storage', {}),
    'Google Drive': ('drive', {}),
    'Microsoft Azure Blob Storage': ('azureblob', {}),
    'Microsoft OneDrive': ('onedrive', {}),
    'Microsoft OneDrive for Business': ('onedrive', {}),
    'Microsoft SharePoint Online': ('onedrive', {}),
    'OpenStack Swift': ('swift', {}),
    'WebDAV': ('webdav', {}),
}
CLOUD_PROTOCOLS = list(PROVIDERS)


def operation_error(details):
    """Return fixed guidance only; backend errors can contain credentials."""
    text = str(details).lower()
    if 'listbuckets' in text:
        return ('S3 could not list buckets. For a public bucket, set the site’s '
                'remote path to /bucket-name/folder in Site Manager instead of /.')
    if 'failed to start auth webserver' in text:
        port = '18342' if '127.0.0.1:18342' in text else '53682'
        if 'access permissions' in text or 'permission denied' in text:
            return ('Cloud sign-in could not start because the operating system blocked its local '
                    f'callback port (127.0.0.1:{port}). On Windows, check whether TCP port {port} '
                    'is in an excluded port range. Resolve the port restriction, then retry setup.')
        if 'address already in use' in text or 'only one usage of each socket address' in text:
            return ('Cloud sign-in could not start because another application or sign-in session '
                    f'is using local port {port}. Finish or cancel that sign-in, then retry setup.')
        return (f'Cloud sign-in could not start its local callback listener on port {port}. '
                'Check local port availability and security software, then retry setup.')
    if 'invalid_grant' in text:
        return ('Cloud authorization expired or was revoked. Sign in to the cloud account again, '
                'then reconnect the site. The provider rejected the saved authorization (invalid_grant).')
    return 'Cloud operation failed. Check account authorization, permissions and the selected path.'


def executable():
    name = 'rclone.exe' if sys.platform == 'win32' else 'rclone'
    path = (Path(sys._MEIPASS) / 'cloud_tools' / name if getattr(sys, 'frozen', False)
            else Path(__file__).resolve().parents[1] / 'runtime' / 'rclone' / name)
    if not path.is_file():
        raise RuntimeError('The bundled cloud backend is missing. Reinstall FluxFTP.')
    return path


def cloud_path(value):
    if not isinstance(value, str) or any(ord(c) < 32 for c in value) or '\\' in value:
        raise ValueError('Invalid cloud path.')
    value = value.lstrip('/')
    if any(part in ('.', '..') for part in value.split('/')):
        raise ValueError('Cloud paths cannot contain . or .. components.')
    return value
