"""Resolve site-scoped proxy and directional credentials without copying secrets."""
from dataclasses import replace
import json
from .site_options import decode


def transfer_connection(window, site, password, direction):
    site, password = transfer_account(window, site, password, direction)
    return window.new_connection(site, password)


def transfer_account(window, site, password, direction):
    name = decode(site.site_options).get('Account/' + direction + '_site')
    if not name:
        return site, password
    data = window.sites.get(name)
    if not data or data.get('_disabled'):
        raise ValueError('Transfer account profile is missing or disabled: ' + name)
    account = window.site_from_profile(data)
    if (account.host.casefold(), account.port, account.protocol) != (site.host.casefold(), site.port, site.protocol):
        raise ValueError('Transfer account must use the same host, port and protocol as the browsing site.')
    if site.cloud_account:
        raise ValueError('Alternate transfer logins are for FTP/FTPS and SFTP sites.')
    password = window.get_site_password(data)
    if not password and not (account.ssh_key or account.managed_key or account.username.casefold() == 'anonymous' or account.protocol in ('SFTP · SSH', 'SCP · SSH')):
        raise ValueError('Save the alternate account password in Site Manager first.')
    return replace(site, username=account.username, ssh_key=account.ssh_key,
                   managed_key=account.managed_key, cert_file=account.cert_file,
                   cert_key=account.cert_key), password


def custom_proxy(window, site):
    options = decode(site.site_options)
    identity = options.get('Connection/proxy_profile')
    if not identity or options.get('Connection/proxy_enabled') is False or window.proxy_bypassed(site.host):
        return None
    profiles = json.loads(window.preferences.value('Connection/proxy_profiles', '[]', type=str))
    profile = next((p for p in profiles if p.get('id') == identity), None)
    if not profile or not profile.get('host'):
        raise ValueError('Selected site proxy profile is missing or has no host. Edit Site Properties → Proxy Server.')
    secret_id = 'proxy' if identity == 'proxy' else 'proxy:' + identity
    password = window.credential_vault.get(secret_id) if profile.get('auth') else ''
    if profile.get('auth') and profile.get('username') and not password:
        raise ValueError('Save the selected proxy profile password in Options → Connection → Proxy Server.')
    return dict(type=profile['type'], host=profile['host'], port=int(profile['port']),
                username=profile.get('username', '') if profile.get('auth') else '', password=password)
