"""Credential-free profiles and explicit comparison baselines."""
import hashlib
import json
import os
import tempfile
from pathlib import Path


def atomic_json(path, data):
    payload = json.dumps(data, ensure_ascii=False)
    if len(payload.encode('utf-8')) > 32_000_000:
        raise ValueError('Settings exceed 32 MB; remove unused profiles or baselines.')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name, suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        Path(name).replace(path)
    finally:
        Path(name).unlink(missing_ok=True)


def pair_key(sites, roots):
    endpoints = [(dict(host=s.host, port=s.port, protocol=s.protocol, username=s.username,
                       cloud_account=s.cloud_account) if s else None, str(root)) for s, root in zip(sites, roots)]
    return hashlib.sha256(json.dumps(endpoints, sort_keys=True).encode()).hexdigest()


class SyncProfiles:
    def __init__(self, path):
        self.path = Path(path)
        self.data = {'profiles': {}, 'baselines': {}}
        if self.path.exists():
            if self.path.stat().st_size > 32_000_000: raise ValueError('Synchronization settings exceed 32 MB.')
            data = json.loads(self.path.read_text('utf-8'))
            if not isinstance(data, dict) or set(data) != {'profiles', 'baselines'} or not all(isinstance(v, dict) for v in data.values()):
                raise ValueError('Invalid synchronization settings; original file preserved.')
            from .folder_sync import validate_spec
            for name, profile in data['profiles'].items():
                if not isinstance(name, str) or not isinstance(profile, dict) or profile.get('mode') not in ('Left → Right', 'Right → Left', 'Two-way') or profile.get('rule') not in ('Size and time', 'Size only', 'Checksum'):
                    raise ValueError('Invalid saved synchronization profile.')
                if type(profile.get('mirror')) is not bool or type(profile.get('tolerance')) is not int or not 0 <= profile['tolerance'] <= 3600:
                    raise ValueError('Invalid saved synchronization options.')
                for field in ('roots', 'sites'):
                    if not isinstance(profile.get(field), list) or len(profile[field]) != 2 or any(not isinstance(v, str) for v in profile[field]):
                        raise ValueError('Invalid saved synchronization endpoints.')
            for baseline in data['baselines'].values():
                if not isinstance(baseline, list) or len(baseline) != 2 or any(not isinstance(v, dict) or len(v) > 10000 for v in baseline):
                    raise ValueError('Invalid synchronization baseline.')
                for listing in baseline:
                    for relative, info in listing.items():
                        if not isinstance(info, dict) or type(info.get('link')) is not bool:
                            raise ValueError('Invalid baseline file metadata.')
                        validate_spec(dict(source_root='/', destination_root='/', relative=relative,
                            source=dict(info, link=False), destination=None))
            self.data = data

    def save(self):
        atomic_json(self.path, self.data)


def enrich_plan(rows, left, right, mode, mirror=False, baseline=None):
    if baseline:
        for key in sorted((set(baseline[0]) | set(baseline[1])) - (set(left) | set(right))):
            rows.append(dict(path=key, left=None, right=None, action='Skip', choices=['Skip'], reason='Deleted on both sides'))
    def equivalent(a, b):
        if a is None or b is None: return a == b
        keys = ('directory', 'link', 'size', 'modified')
        same = all(a.get(k) == b.get(k) for k in keys)
        if a.get('hash_algorithm') and a.get('hash_algorithm') == b.get('hash_algorithm'):
            same = same and a.get('digest') == b.get('digest')
        return same
    for row in rows:
        key = row['path']
        if baseline:
            changes = []
            for side, current, old in zip(('Left', 'Right'), (left, right), baseline):
                if not equivalent(current.get(key), old.get(key)):
                    changes.append(side + (' deleted' if key not in current else ' added' if key not in old else ' changed'))
            row['change'] = ', '.join(changes) or 'Unchanged since baseline'
        else:
            row['change'] = 'No baseline'
        if mirror and mode != 'Two-way' and not row['reason'].startswith('Blocked'):
            source, target = (row['left'], row['right']) if mode == 'Left → Right' else (row['right'], row['left'])
            if source is None and target and not target['directory']:
                action = 'Delete right' if mode == 'Left → Right' else 'Delete left'
                row.update(action='Skip', choices=['Skip', action], reason='Mirror: destination-only file (review deletion)')
    return rows
