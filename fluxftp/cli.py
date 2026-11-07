"""Script-friendly command line client for a running FluxFTP desktop session."""
from __future__ import annotations

import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from . import __version__

API = 'http://127.0.0.1:8765'


def _token():
    # The same OS-protected encrypted vault used by the desktop owns the API token.
    from PySide6.QtCore import QCoreApplication, QStandardPaths
    from . import credentials
    from .api_server import TOKEN_ID
    app = QCoreApplication.instance() or QCoreApplication([])
    app.setApplicationName('FluxFTP')
    app.setOrganizationName('FluxFTP')
    from pathlib import Path
    vault_path = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)) / 'credentials.enc'
    token = credentials.CredentialVault(vault_path).get(TOKEN_ID)
    if not token:
        raise RuntimeError('FluxFTP API token is unavailable. Start FluxFTP desktop and enable Tools → JSON API Access.')
    return token


def _request(method, endpoint, payload=None):
    body = None if payload is None else json.dumps(payload).encode('utf-8')
    request = Request(API + endpoint, data=body, method=method,
                      headers={'Authorization': 'Bearer ' + _token(), 'Accept': 'application/json',
                               **({'Content-Type': 'application/json'} if body is not None else {})})
    try:
        with urlopen(request, timeout=130) as response:
            return json.loads(response.read().decode('utf-8'))
    except HTTPError as error:
        try:
            result = json.loads(error.read().decode('utf-8'))
            message = result.get('error', str(error))
        except (ValueError, UnicodeError):
            message = str(error)
        raise RuntimeError(message) from error
    except URLError as error:
        raise RuntimeError('Cannot reach FluxFTP desktop API. Start FluxFTP and enable Tools → JSON API Access.') from error


def build_parser():
    parser = argparse.ArgumentParser(prog='fluxftp-cli', description='Control a running FluxFTP desktop session.')
    parser.add_argument('--version', action='version', version=f'FluxFTP CLI {__version__}')
    commands = parser.add_subparsers(dest='command', required=True)

    sites = commands.add_parser('sites', help='List saved sites')
    sites.add_subparsers(dest='sites_action', required=True).add_parser('list', help='List enabled saved sites')

    listing = commands.add_parser('ls', help='List a remote directory through a saved site')
    listing.add_argument('--site', required=True, help='Saved FluxFTP site name')
    listing.add_argument('path', nargs='?', default='/', help='Remote directory (default: /)')

    queue = commands.add_parser('queue', help='Inspect or control the transfer queue')
    queue_actions = queue.add_subparsers(dest='queue_action', required=True)
    for action in ('status', 'transfers', 'play', 'pause', 'stop'):
        queue_actions.add_parser(action)
    cancel = queue_actions.add_parser('cancel')
    cancel.add_argument('ids', nargs='+', help='Transfer IDs returned by queue transfers')

    transfer = commands.add_parser('transfer', help='Queue a file or folder transfer')
    transfer.add_argument('source')
    transfer.add_argument('destination')
    transfer.add_argument('--source-site', help='Saved site containing the source; omit for local source')
    transfer.add_argument('--destination-site', help='Saved site receiving the destination; omit for local destination')
    transfer.add_argument('--directory', action='store_true', help='Source is a remote directory')
    transfer.add_argument('--start', action='store_true', help='Start the queue after adding this transfer')
    return parser


def run(args):
    if args.command == 'sites':
        return _request('GET', '/v1/sites')
    if args.command == 'ls':
        return _request('GET', '/v1/listing?' + urlencode({'site': args.site, 'path': args.path}))
    if args.command == 'transfer':
        payload = {'source': {'path': args.source, 'site': args.source_site},
                   'destination': {'path': args.destination, 'site': args.destination_site},
                   'start': args.start}
        if args.source_site:
            payload['directory'] = args.directory
        elif args.directory:
            raise ValueError('--directory applies only to a remote source')
        return _request('POST', '/v1/transfers', payload)
    if args.command == 'queue':
        action = args.queue_action
        if action in ('status', 'transfers'):
            return _request('GET', '/v1/status' if action == 'status' else '/v1/transfers')
        payload = {'ids': args.ids} if action == 'cancel' else {}
        return _request('POST', '/v1/queue/' + action, payload)
    raise ValueError('unknown command')


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        result = run(args)
    except (RuntimeError, ValueError, OSError) as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
