"""Bundled cloud-provider brand marks for provider selection surfaces."""
from functools import lru_cache
from pathlib import Path

from PySide6.QtGui import QIcon

from .site_icons import SITE_MARKS, _site_icon


_ASSETS = {
    'Amazon S3': 'amazon-s3',
    'Backblaze B2': 'backblaze-b2',
    'Box': 'box',
    'Dropbox': 'dropbox',
    'Google Cloud Storage': 'google-cloud-storage',
    'Google Drive': 'google-drive.png',
    'Microsoft Azure Blob Storage': 'azure-blob-storage',
    'Microsoft OneDrive': 'onedrive',
    'Microsoft OneDrive for Business': 'onedrive',
    'Microsoft SharePoint Online': 'sharepoint',
    'OpenStack Swift': 'openstack-swift',
}


@lru_cache(maxsize=16)
def provider_icon(provider):
    """Return the bundled provider mark, with a labeled fallback for WebDAV."""
    asset = _ASSETS.get(provider)
    if asset:
        extension = '' if asset.endswith(('.svg', '.png')) else '.svg'
        path = Path(__file__).with_name('assets') / 'cloud-providers' / f'{asset}{extension}'
        icon = QIcon(str(path))
        if not icon.isNull():
            return icon
    mark, color = SITE_MARKS.get(provider, ('WEB', '#62818e'))
    return _site_icon(mark, color)
