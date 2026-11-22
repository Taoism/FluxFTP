"""Window-owned auto-trading discovery, ledger and queue integration."""
from __future__ import annotations

import posixpath
import os
import re
import tempfile
import time
import tomllib
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QObject, QLockFile, QTimer, Signal

from .automation_scripts import saved_connection
from .core import Entry, safe_remote_name
from .irc_client import IRCNetworkWorker
from .trade_config import MAX_CONFIG_BYTES, load_config, match_irc_message, match_name, validate_config
from .trade_ledger import TradeLedger
from .trade_manifest import completion_marker_present, folder_manifest
from .worker import Worker


class TradingService(QObject):
    """Poll configured sources and submit eligible trades through TransferQueue."""
    changed = Signal()
    message = Signal(str)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        data_dir = window.sites_file.parent
        self.config_path = data_dir / '.fluxftprc'
        self.ledger_path = data_dir / 'trade.DB'
        self.lock = QLockFile(str(self.ledger_path) + '.lock')
        self.lock.setStaleLockTime(0)
        self.owner = self.lock.tryLock(0)
        self.problem = '' if self.owner else 'Auto trading is open in another FluxFTP instance.'
        self.ledger = TradeLedger(self.ledger_path)
        self.config = load_config(self.config_path, set(window.sites))
        self.last_scan = []
        self.last_scan_at = 0.0
        self.scan_worker = None
        self.scan_generation = 0
        self._scan_preview = False
        self._verification_workers = {}
        self.irc_workers = {}
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self.changed.emit()

    @property
    def enabled(self):
        return bool(self.config.get('enabled')) and not self.problem and self.owner

    def reload(self):
        """Reload a valid config without replacing active state on parse errors."""
        try:
            config = load_config(self.config_path, set(self.window.sites))
        except (OSError, ValueError) as error:
            self.problem = 'Invalid .fluxftprc: ' + str(error)
            self.config = dict(self.config, enabled=False)
            self.message.emit(self.problem)
            self.changed.emit()
            return False
        self.config = config
        self.problem = '' if self.owner else 'Auto trading is open in another FluxFTP instance.'
        if config.get('auto_wipe') and self.owner:
            try:
                self.ledger.terminal_cleanup(config['terminal_retention_days'], enabled=True)
            except (OSError, ValueError, RuntimeError) as error:
                self.problem = 'Trade history cleanup failed: ' + str(error)
        self.changed.emit()
        return not self.problem

    def save_config(self, text):
        """Validate and atomically replace `.fluxftprc`; keep the old file on errors."""
        if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_CONFIG_BYTES:
            raise ValueError('.fluxftprc must be UTF-8 text no larger than 1 MB.')
        config = validate_config(tomllib.loads(text), set(self.window.sites))
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix='.fluxftprc-', dir=self.config_path.parent)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, self.config_path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        self.config = config
        self.problem = '' if self.owner else 'Auto trading is open in another FluxFTP instance.'
        self.changed.emit()
        return config

    def export_irc_adapters(self):
        """Write ready-to-load HexChat and AdiIRC bridge scripts to user data."""
        from .api_server import ControlAPI

        api = getattr(self.window, 'control_api', None)
        if api is None:
            api = ControlAPI(self.window)
            self.window.control_api = api
        source = Path(__file__).parent / 'assets' / 'trading-adapters'
        output = self.config_path.parent / 'irc-adapters'
        output.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(output, 0o700)
        except OSError:
            pass
        values = {
            'hexchat_fluxftp.py': (source / 'hexchat_fluxftp.py').read_text('utf-8')
                .replace('__BRIDGE_TOKEN__', api.bridge_token)
                .replace('__BRIDGE_PORT__', str(api.server.server_address[1])),
            'adiirc_fluxftp.mrc': (source / 'adiirc_fluxftp.mrc').read_text('utf-8')
                .replace('__BRIDGE_TOKEN__', api.bridge_token)
                .replace('__BRIDGE_PORT__', str(api.server.server_address[1])),
        }
        for name, contents in values.items():
            target = output / name
            target.write_text(contents, encoding='utf-8', newline='\n')
            try:
                os.chmod(target, 0o600)
            except OSError:
                pass
        return tuple(output / name for name in values)

    @staticmethod
    def _set_toml_flag(text, table, identity_field, identity, key, value):
        lines = text.splitlines(keepends=True)
        if table == 'trading':
            starts = [index for index, line in enumerate(lines) if line.strip() == '[trading]']
        else:
            starts = [index for index, line in enumerate(lines) if line.strip() == '[[' + table + ']]']
        if not starts and table == 'trading':
            return '[trading]\nenabled = ' + str(bool(value)).lower() + '\n' + text
        if table != 'trading':
            wanted = re.compile(r'^\s*' + re.escape(identity_field) + r'\s*=\s*[\"\']' + re.escape(identity) + r'[\"\']\s*(?:#.*)?$')
            starts = [index for index in starts if any(wanted.match(lines[cursor].rstrip('\r\n'))
                for cursor in range(index + 1, next((n for n in range(index + 1, len(lines))
                    if lines[n].lstrip().startswith('[')), len(lines))))]
        if not starts:
            raise ValueError(f'Could not find {table} entry {identity!r} in .fluxftprc.')
        start = starts[-1]
        end = next((index for index in range(start + 1, len(lines)) if lines[index].lstrip().startswith('[')), len(lines))
        flag = re.compile(r'^\s*' + re.escape(key) + r'\s*=')
        for index in range(start + 1, end):
            if flag.match(lines[index]):
                ending = '\r\n' if lines[index].endswith('\r\n') else '\n' if lines[index].endswith('\n') else ''
                lines[index] = f'{key} = {str(bool(value)).lower()}{ending}'
                return ''.join(lines)
        lines.insert(end, f'{key} = {str(bool(value)).lower()}\n')
        return ''.join(lines)

    def set_enabled(self, value, current_text=None):
        text = (self.config_path.read_text('utf-8') if current_text is None and self.config_path.exists()
                else (current_text or '[trading]\nenabled = false\n'))
        if value and not self.config.get('routes'):
            raise ValueError('Add at least one valid route before enabling auto trading.')
        text = self._set_toml_flag(text, 'trading', '', '', 'enabled', bool(value))
        return self.save_config(text)

    def set_route_enabled(self, route_id, value):
        text = self.config_path.read_text('utf-8')
        text = self._set_toml_flag(text, 'routes', 'id', route_id, 'enabled', bool(value))
        return self.save_config(text)

    def tick(self):
        if self.owner:
            try:
                self._reconcile_queue()
                self._retry_due()
                self._dispatch_eligible()
                self._sync_irc_workers()
            except Exception as error:
                self.message.emit('Auto-trading reconciliation error: ' + str(error)[:300])
        if self.enabled and self.scan_worker is None:
            due = max(5, self.config['poll_seconds'])
            if time.monotonic() - self.last_scan_at >= due:
                self.scan_once()

    def _sync_irc_workers(self):
        desired = {item['id']: item for item in self.config['networks']} if self.enabled else {}
        for identity, worker in tuple(self.irc_workers.items()):
            if identity not in desired:
                worker.stop()
                if not worker.isRunning():
                    self.irc_workers.pop(identity, None)
        for identity, network in desired.items():
            worker = self.irc_workers.get(identity)
            if worker and worker.isRunning():
                continue
            credential_id = network.get('credential_id', '')
            try:
                password = self.window.credential_vault.get(credential_id) if credential_id else ''
                worker = IRCNetworkWorker(network, password, self)
                worker.event_received.connect(self._builtin_irc_event)
                worker.status.connect(self.message.emit)
                worker.finished.connect(lambda key=identity, current=worker:
                    self.irc_workers.pop(key, None) if self.irc_workers.get(key) is current else None)
                self.irc_workers[identity] = worker
                worker.start()
            except Exception as error:
                self.message.emit(f'IRC {identity} could not start: {str(error)[:180]}')

    def _builtin_irc_event(self, event):
        self.handle_irc_message(**event)

    def _connections_for_sources(self, source_ids=None):
        source_specs = self.config['sources']
        if source_ids is not None:
            source_specs = [item for item in source_specs if item['id'] in source_ids]
        connections = {}
        for source in source_specs:
            if not source['enabled'] or source['bookmark'] in connections:
                continue
            connections[source['bookmark']] = saved_connection(self.window, source['bookmark'])
        return connections

    def scan_once(self, *, preview=False):
        """Queue one bounded scan; preview scans never write ledger or queue state."""
        if self.scan_worker is not None:
            return False
        if not self.owner:
            self.problem = 'Another FluxFTP instance owns trade.DB.'
            self.changed.emit()
            return False
        try:
            self.reload()
            if self.problem:
                return False
            generation = self.scan_generation = self.scan_generation + 1
            config_snapshot = self.config
            connections = self._connections_for_sources()
        except Exception as error:
            self.problem = str(error)[:500]
            self.message.emit('Auto-trading scan could not start: ' + self.problem)
            self.changed.emit()
            return False

        def scan(worker):
            result = {'generation': generation, 'scanned_sources': [], 'candidates': [], 'decisions': []}
            source_by_id = {item['id']: item for item in config_snapshot['sources']}
            destination_by_id = {item['id']: item for item in config_snapshot['destinations']}
            for source in config_snapshot['sources']:
                if not source['enabled']:
                    continue
                bookmark = source['bookmark']
                connection = connections.get(bookmark)
                if connection is None:
                    result['decisions'].append(dict(source=source['id'], reason='Source bookmark unavailable.'))
                    continue
                try:
                    with connection:
                        entries = connection.list(source['path'])
                        result['scanned_sources'].append(source['id'])
                        routes = [route for route in config_snapshot['routes']
                                  if route['source'] == source['id'] and route['enabled']]
                        for entry in entries:
                            if not entry.is_dir:
                                continue
                            try:
                                name = safe_remote_name(entry.name)
                            except ValueError:
                                result['decisions'].append(dict(source=source['id'], name=str(entry.name)[:256],
                                                                reason='Rejected unsafe source name.'))
                                continue
                            if entry.is_link:
                                result['decisions'].append(dict(source=source['id'], name=name,
                                                                reason='Rejected symbolic-link folder.'))
                                continue
                            if not match_name(name, source['whitelist'], source['blacklist']):
                                result['decisions'].append(dict(source=source['id'], name=name,
                                                                reason='Rejected by source whitelist/blacklist.'))
                                continue
                            source_path = posixpath.join(source['path'].rstrip('/') or '/', name)
                            try:
                                manifest, signature = folder_manifest(connection, source_path, cancel=worker.cancel,
                                    max_depth=config_snapshot['max_depth'], max_entries=config_snapshot['max_entries'])
                            except Exception as error:
                                result['decisions'].append(dict(source=source['id'], name=name,
                                                                reason='Source scan rejected: ' + str(error)[:256]))
                                continue
                            complete = completion_marker_present(manifest, source.get('completion_marker', ''))
                            if source.get('completion_marker') and not complete:
                                result['decisions'].append(dict(source=source['id'], name=name,
                                    reason='Waiting for completion marker ' + source['completion_marker']))
                            for route in routes:
                                destination = destination_by_id[route['destination']]
                                if not destination['enabled']:
                                    result['decisions'].append(dict(route=route['id'], name=name,
                                                                    reason='Destination rule is disabled.'))
                                    continue
                                if not match_name(name, destination['whitelist'], destination['blacklist']):
                                    result['decisions'].append(dict(route=route['id'], name=name,
                                                                    reason='Rejected by destination whitelist/blacklist.'))
                                    continue
                                result['candidates'].append(dict(source_id=source['id'], route_id=route['id'],
                                    name=name, source_bookmark=source['bookmark'], source_path=source_path,
                                    destination_id=destination['id'], destination_bookmark=destination['bookmark'],
                                    destination_path=posixpath.join(destination['path'].rstrip('/') or '/', name),
                                    manifest=manifest, manifest_digest=signature, marker_ready=complete,
                                    conflict=destination['conflict'], verify=destination['verify']))
                except Exception as error:
                    result['decisions'].append(dict(source=source['id'], reason='Source listing failed: ' + str(error)[:256]))
            return result

        self._scan_preview = preview
        worker = self.scan_worker = Worker(scan, self)
        worker.result.connect(self._scan_complete)
        worker.failed.connect(self._scan_failed)
        worker.finished.connect(lambda current=worker: self._clear_worker(current))
        worker.start()
        self.changed.emit()
        return True

    def _clear_worker(self, worker):
        if self.scan_worker is worker:
            self.scan_worker = None

    def _scan_failed(self, error):
        self.problem = 'Auto-trading scan failed: ' + str(error)[:400]
        self.message.emit(self.problem)
        self.changed.emit()

    def _scan_complete(self, result):
        self.last_scan_at = time.monotonic()
        if result.get('generation') != self.scan_generation:
            return
        self.problem = '' if self.owner else self.problem
        self.last_scan = list(result['decisions'])
        for candidate in result['candidates']:
            self.last_scan.append(dict(route=candidate['route_id'], name=candidate['name'],
                                       reason='Matched; waiting for completion marker or stable listings.'))
        if not self._scan_preview:
            self._record_scan(result)
        self.changed.emit()

    def _record_scan(self, result):
        now = time.time()
        sources = {item['id']: item for item in self.config['sources']}
        routes = {item['id']: item for item in self.config['routes']}
        scanned = set(result['scanned_sources'])
        seen = set()
        for candidate in result['candidates']:
            route = routes.get(candidate['route_id'])
            source = sources.get(candidate['source_id'])
            if not route or not source:
                continue
            trade, _created = self.ledger.record_candidate(rule_id=source['id'], route_id=route['id'],
                source_bookmark=candidate['source_bookmark'], source_path=candidate['source_path'],
                destination_bookmark=candidate['destination_bookmark'], destination_path=candidate['destination_path'],
                manifest_digest=candidate['manifest_digest'], trigger_source='poll')
            seen.add((route['id'], candidate['source_path']))
            if trade['state'] == 'detected':
                self.ledger.transition(trade['id'], 'waiting_stability', 'Candidate discovered by source polling.')
                trade = self.ledger.get_trade(trade['id'])
            if candidate['marker_ready']:
                if trade['state'] == 'waiting_stability':
                    trade = self.ledger.transition(trade['id'], 'eligible', 'Configured completion marker is present.')
            elif trade['state'] in ('waiting_stability', 'detected'):
                trade = self.ledger.observe_stability(trade['id'], candidate['manifest_digest'], self.config['stable_polls'])
        self._dispatch_eligible()

        # A successful listing is authoritative for that source. Pending records
        # that vanished or ceased matching are closed; failed listings are not.
        for trade in self.ledger.list_trades(states=('detected', 'waiting_stability', 'eligible'), limit=10_000):
            source = next((item for item in sources.values() if item['id'] == trade['rule_id']), None)
            route = routes.get(trade['route_id'])
            destination = next((item for item in self.config['destinations']
                                if route and item['id'] == route['destination']), None)
            if (source and route and route['enabled'] and destination and destination['enabled'] and
                    source['id'] in scanned and (route['id'], trade['source_path']) not in seen):
                self.ledger.transition(trade['id'], 'cancelled', 'Source folder disappeared or no longer matches its rules.')
        self.last_scan = self._decision_rows(result)
        self.message.emit(f"Auto-trading scan found {len(result['candidates'])} route candidate(s).")

    def _dispatch_eligible(self):
        """Submit eligible trades without exceeding the configured active limit."""
        if not self.enabled:
            return
        active_states = ('queued', 'transferring', 'verifying')
        active = len(self.ledger.list_trades(states=active_states, limit=10_000))
        available = max(0, self.config['max_active_trades'] - active)
        if not available:
            return
        routes = {item['id']: item for item in self.config['routes'] if item['enabled']}
        sources = {item['id']: item for item in self.config['sources'] if item['enabled']}
        destinations = {item['id']: item for item in self.config['destinations'] if item['enabled']}
        for trade in self.ledger.list_trades(states=('eligible',), limit=10_000):
            if available <= 0:
                break
            route = routes.get(trade['route_id'])
            source = sources.get(trade['rule_id'])
            destination = destinations.get(route['destination']) if route else None
            if not route or not source or not destination:
                continue
            try:
                self._enqueue(trade, destination['conflict'])
            except Exception as error:
                self._trade_failed(trade, 'Could not enqueue trade: ' + str(error)[:256])
            available -= 1

    @staticmethod
    def _decision_rows(result):
        rows = list(result['decisions'])
        for candidate in result['candidates']:
            rows.append(dict(route=candidate['route_id'], name=candidate['name'],
                             reason='Eligible source candidate; stable checks are recorded in trade.DB.'))
        return rows[-2000:]

    def _enqueue(self, trade, conflict):
        from .automation_scripts import saved_connection
        source_profile = self.window.sites.get(trade['source_bookmark'])
        destination_profile = self.window.sites.get(trade['destination_bookmark'])
        if not source_profile or not destination_profile:
            self.ledger.transition(trade['id'], 'failed', 'A configured saved site was removed.',
                                   retries=trade['retries'] + 1, last_error='Saved site was removed.')
            return
        source_data = source_profile
        destination_data = destination_profile
        source = self.window.site_from_profile(source_data)
        destination = self.window.site_from_profile(destination_data)
        source_password = self.window.get_site_password(source_data)
        destination_password = self.window.get_site_password(destination_data)
        source_name = posixpath.basename(trade['source_path'].rstrip('/'))
        destination_name = posixpath.basename(trade['destination_path'].rstrip('/'))
        split = self.window.tabs.widget(0)
        source_parent = posixpath.dirname(trade['source_path'].rstrip('/')) or '/'
        destination_parent = posixpath.dirname(trade['destination_path'].rstrip('/')) or '/'
        source_pane = SimpleNamespace(mode='Remote', site=source, password=source_password,
            busy=False, path=source_parent, number=1, join=lambda _name, path=trade['source_path']: path)
        destination_pane = SimpleNamespace(mode='Remote', site=destination, password=destination_password,
            busy=False, path=destination_parent, number=2, join=lambda _name, path=trade['destination_path']: path,
            parentWidget=lambda: split)
        jobs = self.window.enqueue_items(source_pane, destination_pane,
            [Entry(source_name, True, 0)], start=True, confirmed=True, automatic=True,
            trade_id=trade['id'], trade_conflict=conflict)
        if not jobs:
            raise ValueError('FluxFTP did not create a transfer queue job.')
        queue_id = jobs[0].get('job_id')
        self.ledger.transition(trade['id'], 'queued', 'Submitted to the transfer queue.',
                               queue_job_id=queue_id, last_error='')
        self.message.emit(f"Trade {trade['id'][:12]} queued: {trade['source_path']}")

    def _reconcile_queue(self):
        jobs = {job.get('job_id'): job for job in self.window.queue_jobs if job.get('trade_id')}
        for trade in self.ledger.list_trades(states=('queued', 'transferring', 'verifying'), limit=10_000):
            job = jobs.get(trade['queue_job_id'])
            if job is None:
                if trade['state'] in ('queued', 'transferring', 'verifying'):
                    self.ledger.transition(trade['id'], 'paused',
                        'Queue job is unavailable after restart; review and explicitly resume.',
                        last_error='Queue job unavailable after restart.')
                continue
            queue_state = str(job.get('state', 'queued'))
            if queue_state == 'active' and trade['state'] == 'queued':
                self.ledger.transition(trade['id'], 'transferring', 'Queue worker started.')
            elif queue_state == 'complete' and trade['state'] in ('queued', 'transferring', 'verifying'):
                if trade['state'] != 'verifying':
                    self.ledger.transition(trade['id'], 'verifying', 'Queue reported transfer completion.')
                self._verify_trade(trade, job)
            elif queue_state == 'failed' and trade['state'] in ('queued', 'transferring'):
                self._trade_failed(trade, 'Transfer queue reported failure.')
            elif queue_state == 'cancelled' and trade['state'] in ('queued', 'transferring'):
                self.ledger.transition(trade['id'], 'cancelled', 'Transfer cancelled from the queue.')

    def _verify_trade(self, trade, job):
        if trade['id'] in self._verification_workers:
            return
        source = self.window.sites.get(trade['source_bookmark'])
        destination = self.window.sites.get(trade['destination_bookmark'])
        if not source or not destination:
            self._trade_failed(trade, 'A saved site was removed before verification.')
            return
        try:
            source_connection = saved_connection(self.window, trade['source_bookmark'])
            destination_connection = saved_connection(self.window, trade['destination_bookmark'])
        except Exception as error:
            self._trade_failed(trade, 'Could not prepare verification connections: ' + str(error))
            return
        source_path = trade['source_path']
        destination_path = job.get('actual_dst') or trade['destination_path']
        rule = next((item for item in self.config['destinations'] if item['bookmark'] == trade['destination_bookmark']
                     and (destination_path == item['path'] or destination_path.startswith(item['path'].rstrip('/') + '/'))), None)
        verify = rule['verify'] if rule else 'size'

        def verify_worker(worker):
            with source_connection:
                source_manifest, _ = folder_manifest(source_connection, source_path, cancel=worker.cancel,
                    max_depth=self.config['max_depth'], max_entries=self.config['max_entries'])
            with destination_connection:
                destination_manifest, _ = folder_manifest(destination_connection, destination_path, cancel=worker.cancel,
                    max_depth=self.config['max_depth'], max_entries=self.config['max_entries'])
            if len(source_manifest) != len(destination_manifest):
                raise ValueError(f'Entry count differs: source {len(source_manifest)}, destination {len(destination_manifest)}.')
            for source_entry, dest_entry in zip(source_manifest, destination_manifest):
                if source_entry[:3] != dest_entry[:3]:
                    raise ValueError('Destination path, type or size differs: ' + source_entry[0])
                if verify == 'size-and-mtime' and source_entry[3] != dest_entry[3]:
                    raise ValueError('Destination modification time differs: ' + source_entry[0])
            return destination_path

        worker = Worker(verify_worker, self)
        self._verification_workers[trade['id']] = worker
        worker.result.connect(lambda path, identity=trade['id']: self._verification_complete(identity, path))
        worker.failed.connect(lambda error, identity=trade['id']: self._verification_failed(identity, error))
        worker.finished.connect(lambda identity=trade['id']: self._verification_workers.pop(identity, None))
        worker.start()

    def _verification_complete(self, trade_id, destination_path):
        self.ledger.transition(trade_id, 'complete', 'Source and destination manifests match.',
                               resolved_destination=destination_path)
        self.message.emit(f'Trade {trade_id[:12]} verified complete.')
        self.changed.emit()

    def _verification_failed(self, trade_id, error):
        trade = self.ledger.get_trade(trade_id)
        if trade:
            self._trade_failed(trade, 'Post-transfer verification failed: ' + str(error))

    def _trade_failed(self, trade, reason):
        retries = trade['retries'] + 1
        limit = self.config['max_retries']
        if retries <= limit:
            delays = self.config['retry_backoff_seconds']
            delay = delays[min(retries - 1, len(delays) - 1)]
            next_attempt = time.time() + delay
            self.ledger.transition(trade['id'], 'failed', reason,
                retries=retries, next_attempt=next_attempt, last_error=reason)
            self.message.emit(f'Trade {trade["id"][:12]} failed; retry {retries}/{limit} scheduled.')
        else:
            self.ledger.transition(trade['id'], 'failed', reason,
                retries=retries, next_attempt=0, last_error=reason)
            self.message.emit(f'Trade {trade["id"][:12]} reached its retry limit.')

    def _retry_due(self):
        if not self.enabled:
            return
        routes = {item['id']: item for item in self.config['routes'] if item['enabled']}
        for trade in self.ledger.list_trades(states=('failed',), limit=500):
            if trade['retries'] <= self.config['max_retries'] and trade['next_attempt'] <= time.time() and trade['route_id'] in routes:
                self.ledger.transition(trade['id'], 'queued', 'Retry backoff elapsed; resubmitting trade.')
                updated = self.ledger.get_trade(trade['id'])
                destination = next((item for item in self.config['destinations'] if item['id'] == routes[trade['route_id']]['destination']), None)
                try:
                    self._enqueue(updated, destination['conflict'] if destination else 'fail')
                except Exception as error:
                    current = self.ledger.get_trade(trade['id'])
                    if current and current['state'] == 'queued':
                        self._trade_failed(current, 'Retry submission failed: ' + str(error))

    def handle_irc_message(self, *, network, channel, sender, message, event_id, source_client='irc'):
        """Validate an IRC event and start a bounded source-folder manifest scan."""
        if not self.enabled:
            return False, 'Auto trading is disabled.'
        try:
            matched = match_irc_message(self.config, network, channel, sender, message)
            if not matched:
                return False, 'IRC message did not match an enabled trigger.'
            route = next(item for item in self.config['routes'] if item['id'] == matched['route_id'])
            source = next(item for item in self.config['sources'] if item['id'] == route['source'])
            destination = next(item for item in self.config['destinations'] if item['id'] == route['destination'])
            name = safe_remote_name(matched['folder'])
            if (not source['enabled'] or not destination['enabled'] or not route['enabled'] or
                    not match_name(name, source['whitelist'], source['blacklist']) or
                    not match_name(name, destination['whitelist'], destination['blacklist'])):
                return False, 'IRC folder is rejected by the configured route filters.'
            source_connection = saved_connection(self.window, source['bookmark'])
        except Exception as error:
            return False, str(error)[:256]
        source_path = posixpath.join(source['path'].rstrip('/') or '/', name)
        event_key = str(event_id)[:4096]
        source_client = str(source_client)[:32]
        source_spec, destination_spec = source, destination

        def scan_event(worker):
            with source_connection:
                manifest, signature = folder_manifest(source_connection, source_path, cancel=worker.cancel,
                    max_depth=self.config['max_depth'], max_entries=self.config['max_entries'])
            marker_ready = completion_marker_present(manifest, source_spec.get('completion_marker', ''))
            return signature, marker_ready

        def found(result):
            signature, marker_ready = result
            trade, _ = self.ledger.record_candidate(rule_id=source_spec['id'], route_id=route['id'],
                source_bookmark=source_spec['bookmark'], source_path=source_path,
                destination_bookmark=destination_spec['bookmark'],
                destination_path=posixpath.join(destination_spec['path'].rstrip('/') or '/', name),
                manifest_digest=signature, trigger_source='irc', trigger_id=matched['trigger_id'],
                initial_state='eligible' if marker_ready else 'detected')
            accepted = self.ledger.record_irc_event(event_key, network_id=network, channel=channel,
                sender=sender, trigger_id=matched['trigger_id'], trade_id=trade['id'])
            if not accepted:
                self.message.emit('Duplicate IRC trade event ignored.')
                return
            if trade['state'] == 'detected':
                self.ledger.transition(trade['id'], 'waiting_stability',
                    f'Accepted allowlisted IRC event through {source_client}; awaiting stable listings.')
            elif trade['state'] == 'eligible' and self.enabled:
                self._enqueue(trade, destination_spec['conflict'])
            self.changed.emit()

        try:
            worker = Worker(scan_event, self)
            worker.result.connect(found)
            worker.failed.connect(lambda error: self.message.emit('IRC trade scan failed: ' + str(error)[:256]))
            worker.finished.connect(worker.deleteLater)
            worker.start()
        except Exception as error:
            return False, str(error)[:256]
        return True, 'IRC trade event accepted for validation.'

    def submit_manual(self, route_id, folder):
        """Create an explicit cowboy/manual trade candidate for one configured route."""
        if not self.owner:
            return False, self.problem
        try:
            route = next(item for item in self.config['routes'] if item['id'] == route_id)
            source = next(item for item in self.config['sources'] if item['id'] == route['source'])
            destination = next(item for item in self.config['destinations'] if item['id'] == route['destination'])
            name = safe_remote_name(folder.strip())
            if not match_name(name, source['whitelist'], source['blacklist']) or not match_name(name, destination['whitelist'], destination['blacklist']):
                return False, 'Folder is rejected by the route filters.'
            connection = saved_connection(self.window, source['bookmark'])
        except Exception as error:
            return False, str(error)[:256]
        path = posixpath.join(source['path'].rstrip('/') or '/', name)

        def scan_manual(worker):
            with connection:
                _manifest, signature = folder_manifest(connection, path, cancel=worker.cancel,
                    max_depth=self.config['max_depth'], max_entries=self.config['max_entries'])
            return signature

        def found(signature):
            trade, _ = self.ledger.record_candidate(rule_id=source['id'], route_id=route['id'],
                source_bookmark=source['bookmark'], source_path=path,
                destination_bookmark=destination['bookmark'],
                destination_path=posixpath.join(destination['path'].rstrip('/') or '/', name),
                manifest_digest=signature, trigger_source='manual',
                trigger_id='manual', initial_state='eligible')
            if trade['state'] in ('detected', 'waiting_stability'):
                trade = self.ledger.transition(trade['id'], 'eligible', 'Manual submission explicitly bypassed polling stability.')
            if trade['state'] == 'eligible':
                self._enqueue(trade, destination['conflict'])
            self.changed.emit()

        worker = Worker(scan_manual, self)
        worker.result.connect(found)
        worker.failed.connect(lambda error: self.message.emit('Manual trade scan failed: ' + str(error)[:256]))
        worker.finished.connect(worker.deleteLater)
        worker.start()
        return True, 'Manual trade candidate is being scanned.'

    def show(self):
        from .trading_ui import TradingDialog
        if not getattr(self, 'dialog', None):
            self.dialog = TradingDialog(self)
        self.dialog.refresh()
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def pause_trade(self, trade_id):
        trade = self.ledger.get_trade(trade_id)
        if not trade or trade['state'] in ('complete', 'cancelled', 'rejected', 'paused'):
            return False
        job = next((item for item in self.window.queue_jobs if item.get('job_id') == trade['queue_job_id']), None)
        if job:
            from .transfer_control import pause_jobs
            pause_jobs(self.window, [job])
        self.ledger.transition(trade_id, 'paused', 'Paused by the user.')
        self.changed.emit()
        return True

    def resume_trade(self, trade_id):
        trade = self.ledger.get_trade(trade_id)
        if not trade or trade['state'] != 'paused':
            return False
        job = next((item for item in self.window.queue_jobs if item.get('job_id') == trade['queue_job_id']), None)
        if job:
            if job.get('state') == 'active':
                from .transfer_control import resume_active
                resume_active(self.window, [job])
                target = 'transferring'
            else:
                job['held'] = False
                target = 'queued'
                self.window.save_queue_state()
                self.window.start_next()
            self.ledger.transition(trade_id, target, 'Resumed by the user.')
        else:
            target = trade.get('resume_state') or 'eligible'
            if target in ('queued', 'transferring', 'verifying'):
                target = 'eligible'
            self.ledger.transition(trade_id, target, 'Resumed after review.')
            if target == 'eligible' and self.enabled:
                route = next((item for item in self.config['routes'] if item['id'] == trade['route_id']), None)
                destination = next((item for item in self.config['destinations'] if route and item['id'] == route['destination']), None)
                if route and route['enabled'] and destination and destination['enabled']:
                    self._enqueue(self.ledger.get_trade(trade_id), destination['conflict'])
        self.changed.emit()
        return True

    def cancel_trade(self, trade_id):
        trade = self.ledger.get_trade(trade_id)
        if not trade or trade['state'] in ('complete', 'cancelled', 'rejected'):
            return False
        job = next((item for item in self.window.queue_jobs if item.get('job_id') == trade['queue_job_id']), None)
        if job:
            for record in self.window.active.values():
                if record.get('job') is job:
                    record['worker'].cancel.set()
            self.window.queue_engine.cancel([job])
            self.window.save_queue_state()
        self.ledger.transition(trade_id, 'cancelled', 'Cancelled by the user.')
        self.changed.emit()
        return True

    def shutdown(self):
        self.timer.stop()
        for worker in tuple(self.irc_workers.values()):
            worker.stop()
        for worker in tuple(self.irc_workers.values()):
            worker.wait(3000)
        if self.scan_worker and self.scan_worker.isRunning():
            self.scan_worker.cancel.set()
            self.scan_worker.wait(3000)
        for worker in tuple(self._verification_workers.values()):
            if worker.isRunning():
                worker.cancel.set()
                worker.wait(3000)
        if self.owner:
            self.lock.unlock()
