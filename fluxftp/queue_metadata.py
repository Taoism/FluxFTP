"""Pure validation shared by queue recovery, watches and deployment plans."""
import re
import posixpath
from pathlib import Path
from .connection_model import safe_name


def remote_path(value):
    if not isinstance(value, str) or not value.startswith('/') or re.search(r'[\x00-\x1f\x7f]', value):
        raise ValueError('Enter an absolute remote path beginning with /.')
    if '..' in value.split('/'):
        raise ValueError('Remote paths cannot contain parent-directory segments.')
    return posixpath.normpath(value)


def validate_tag(tag):
    if not isinstance(tag, dict) or set(tag) != {'id', 'relative', 'fingerprint'}:
        raise ValueError('Invalid watch queue metadata.')
    if not isinstance(tag['id'], str) or not tag['id'] or len(tag['id']) > 100:
        raise ValueError('Invalid watch identity.')
    validate_spec(dict(source_root='/', destination_root='/', relative=tag['relative'],
                       source=tag['fingerprint'], destination=None))
    if tag['fingerprint']['directory']: raise ValueError('Watches queue regular files only.')
    return tag


def validate_profile(value):
    required = {'name', 'source', 'site', 'destination', 'excludes', 'verification'}
    if not isinstance(value, dict) or set(value) != required: raise ValueError('Invalid deployment profile.')
    if any(not isinstance(value[key], str) or not value[key] or len(value[key]) > 32768 or any(ord(c) < 32 for c in value[key])
           for key in ('name', 'source', 'site', 'destination')): raise ValueError('Invalid deployment name or location.')
    if not Path(value['source']).is_absolute(): raise ValueError('Deployment source must be an absolute local folder.')
    remote_path(value['destination'])
    if value['verification'] not in ('Checksum readback', 'Size only'): raise ValueError('Invalid deployment verification policy.')
    if not isinstance(value['excludes'], list) or len(value['excludes']) > 100 or any(not isinstance(p, str) or len(p) > 500 for p in value['excludes']):
        raise ValueError('Invalid deployment exclusions.')
    return value


def validate_job(value):
    if not isinstance(value, dict) or set(value) != {'id', 'profile', 'files', 'rollback'}:
        raise ValueError('Invalid deployment queue metadata.')
    if not isinstance(value['id'], str) or not re.fullmatch('[0-9a-f]{32}', value['id']): raise ValueError('Invalid deployment revision.')
    validate_profile(value['profile'])
    if type(value['rollback']) is not bool or not isinstance(value['files'], list) or not 1 <= len(value['files']) <= 10000:
        raise ValueError('Invalid deployment file plan.')
    seen = set()
    for file in value['files']:
        if not isinstance(file, dict) or set(file) != {'relative', 'source', 'destination', 'source_sha256'}:
            raise ValueError('Invalid deployment file metadata.')
        validate_spec(dict(source_root=value['profile']['source'], destination_root=value['profile']['destination'],
            relative=file['relative'], source=file['source'], destination=file['destination']))
        if file['source']['directory'] or (file['destination'] and file['destination']['directory']):
            raise ValueError('Deployment plans contain files only.')
        if not isinstance(file['source_sha256'], str) or not re.fullmatch('[0-9a-f]{64}', file['source_sha256']):
            raise ValueError('Invalid deployment source checksum.')
        if file['relative'].casefold() in seen: raise ValueError('Duplicate deployment path.')
        seen.add(file['relative'].casefold())
    return value


def validate_spec(spec):
    if not isinstance(spec, dict) or set(spec) - {'source_root', 'destination_root', 'relative', 'source', 'destination', 'operation'} or not {'source_root', 'destination_root', 'relative', 'source', 'destination'} <= set(spec):
        raise ValueError('Invalid synchronization metadata.')
    if spec.get('operation', 'copy') not in ('copy', 'delete'):
        raise ValueError('Invalid synchronization operation.')
    deleting = spec.get('operation') == 'delete'
    if deleting and (spec['source'] is not None or not spec['destination'] or spec['destination'].get('directory')):
        raise ValueError('Mirror deletion must refer to a destination file absent from the source.')
    for key in ('source_root', 'destination_root', 'relative'):
        value = spec[key]
        if not isinstance(value, str) or not value or len(value) > 32768 or any(ord(c) < 32 for c in value):
            raise ValueError('Invalid synchronization path.')
    relative = spec['relative']
    if relative.startswith('/') or '\\' in relative or any(part in ('', '.', '..') for part in relative.split('/')):
        raise ValueError('Invalid relative synchronization path.')
    for part in relative.split('/'): safe_name(part)
    for key in ('source', 'destination'):
        value = spec[key]
        if value is None and (key == 'destination' or deleting and key == 'source'): continue
        if not isinstance(value, dict) or set(value) not in ({'directory', 'link', 'size', 'modified'}, {'directory', 'link', 'size', 'modified', 'hash_algorithm', 'digest'}):
            raise ValueError('Invalid synchronization fingerprint.')
        if 'digest' in value:
            import re
            lengths = {'SHA-256': 64, 'SHA-1': 40, 'MD5': 32}
            length = lengths.get(value['hash_algorithm'], 0)
            if not length or not isinstance(value['digest'], str) or not re.fullmatch('[0-9a-f]{' + str(length) + '}', value['digest']):
                raise ValueError('Invalid synchronization checksum.')
        if type(value['directory']) is not bool or value['link'] is not False or type(value['size']) is not int or not 0 <= value['size'] <= 2**63 - 1:
            raise ValueError('Invalid synchronization file metadata.')
        if not isinstance(value['modified'], str) or len(value['modified']) > 128:
            raise ValueError('Invalid synchronization timestamp.')
    return spec
