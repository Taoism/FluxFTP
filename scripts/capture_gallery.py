"""Render the real FluxFTP widgets with isolated, fictional demonstration data.

Run from the repository with a source environment: python scripts/capture_gallery.py
No server connection, transfer, account authorization or user configuration is used.
"""
import os
import sys
import json
import re
import tempfile
from pathlib import Path
from dataclasses import asdict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
capture_profile = tempfile.TemporaryDirectory(prefix='fluxftp-gallery-')
os.environ['LOCALAPPDATA'] = capture_profile.name
os.environ['APPDATA'] = capture_profile.name
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QTreeWidgetItem, QLineEdit, QPlainTextEdit, QTreeWidgetItemIterator, QMenu, QLabel
from PySide6.QtTest import QTest
from fluxftp.app import Window, SiteDialog, STYLE
from fluxftp.core import Site, Entry, PROTOCOLS
from fluxftp.themes import apply_application

OUT = ROOT / 'docs/screenshots'
OUT.mkdir(parents=True, exist_ok=True)
app = QApplication([])
for font in ('tahoma.ttf', 'tahomabd.ttf', 'segoeui.ttf', 'consola.ttf'):
    path = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / font
    if path.exists(): QFontDatabase.addApplicationFont(str(path))
app.setFont(QFont('Tahoma', 10))
w = Window(smoke=True)
for timer in w.findChildren(QTimer): timer.stop()
while w.workers:
    QTest.qWait(10)
w.preferences.setValue('Confirm/exit', False)
w.paused = True
w.queue_file = w.sites_file.with_name('queue.json')
w.deferred_queue = []
apply_application(app, w.preferences, STYLE)
sites = [Site(name='Demo FTPS', host='ftp.example.com', username='demo', root='/public_html'),
         Site(name='Demo SFTP', host='sftp.example.com', username='demo', port=22, protocol=PROTOCOLS[3]),
         Site(name='Demo SCP', host='scp.example.com', username='demo', port=22, protocol='SCP · SSH')]
w.sites = {site.name: asdict(site) for site in sites}
w.log.clear()
w.log.setPlainText('Demonstration workspace — fictional sites and sample files.\nNo network connections or transfers are running.')
panes = w.current_panes()
for index, pane in enumerate(panes):
    pane.mode = 'Local' if index == 0 else 'Remote'
    pane.site = None if index == 0 else sites[0]
    pane.path = 'C:/Demo/Website' if index == 0 else '/public_html'
    pane.address.setText(pane.path)
    pane.show_entries(([Entry('assets', True), Entry('images', True), Entry('index.html', False, 18320, '2026-10-28 10:00'),
                       Entry('README.txt', False, 2048, '2026-10-28 09:30'), Entry('release.zip', False, 734003200, '2026-10-27 18:00')],
                       'Demonstration listing'))
w.tabs.setTabText(w.tabs.currentIndex(), 'Demo website')
w.statusBar().showMessage('Demonstration data · queue held · no connections running')
jobs = []
for name, state, progress in [('release.zip', 'queued', 'Held'), ('index.html', 'queued', 'Queued'), ('assets/app.js', 'failed', 'Failed')]:
    size = {'release.zip': 734003200, 'index.html': 18320, 'assets/app.js': 72410}[name]
    row = QTreeWidgetItem([name, '0%', '—', '—', w.file_size(size), 'C:/Demo/Website/'+name, '/public_html/'+name, 'Upload · '+progress])
    w.queue.addTopLevelItem(row)
    job = w.new_queue_job(dict(row=row, task=lambda _: None, route='Upload', workspace=w.tabs.currentWidget(),
        source_site=None, destination_site=sites[0], state=state, retries=0, held=state=='queued',
        src='C:/Demo/Website/'+name, dst='/public_html/'+name, name=name, directory=False, resume=False, size=size))
    w.queue_jobs.append(job)
    if state=='queued': w.pending.append(job)
    jobs.append(job)

records = []
keep = []
def slug(text): return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')

def capture(widget, title, group, caption='', size=None):
    keep.append(widget)
    widget.setMaximumSize(2000, 1600)
    if size: widget.resize(*size)
    # Replace any default home/profile paths before a widget is rendered.
    for edit in widget.findChildren(QLineEdit):
        value = edit.text()
        for original in (str(Path.home()), str(Path.home()).replace('\\', '/'), str(ROOT), str(ROOT).replace('\\', '/')):
            value = value.replace(original, 'C:/Demo')
        edit.setText(value)
    widget.show()
    app.processEvents()
    QTest.qWait(40)
    for label in widget.findChildren(QLabel):
        assert str(Path.home()).casefold() not in label.text().casefold(), (title, 'Personal profile path in label')
    filename = slug(group + '-' + title) + '.png'
    assert widget.grab().save(str(OUT / filename)), filename
    records.append(dict(title=title, group=group, file=filename, caption=caption))
    widget.hide()
    print(filename, flush=True)

def navigation(dialog, group, size):
    iterator = QTreeWidgetItemIterator(dialog.navigation)
    while iterator.value():
        item = iterator.value()
        dialog.navigation.setCurrentItem(item)
        dialog.navigation.scrollToItem(item)
        capture(dialog, item.text(0), group, size=size)
        iterator += 1

def modal(callback, title, group):
    errors=[]
    def shoot():
        dialog = app.activeModalWidget()
        try:
            if dialog is None: raise RuntimeError('No modal for '+title)
            capture(dialog, title, group)
        except Exception as error: errors.append(error)
        finally:
            if dialog is not None: dialog.reject()
    QTimer.singleShot(100, shoot)
    callback()
    if errors: raise errors[0]

w.resize(1440, 1100)
w.work_area.setSizes([600, 120, 240])
capture(w, 'Workspace and transfer queue', 'Workspace', 'Sample directory listings and held/failed jobs.', (1440, 1100))
for theme in ('Black', 'Dark Grey', 'Light', 'Midnight'):
    w.preferences.setValue('Display/style', theme)
    apply_application(app, w.preferences, STYLE)
    w.apply_display_settings()
    capture(w, theme + ' theme', 'Workspace', 'Same demonstration workspace with a different application theme.')
w.preferences.setValue('Display/style', 'Classic')
apply_application(app, w.preferences, STYLE)
w.apply_display_settings()
jobs[0]['row'].setSelected(True)
capture(w.queue_commands.context_menu(), 'Queue controls', 'Transfers', 'Hold, pause, resume, stop, retry and queue management commands.')
from fluxftp.app import Worker
demo_worker = Worker(lambda _: None, w)
jobs[0]['state'] = 'active'
jobs[0]['held'] = False
w.active['demonstration'] = dict(job=jobs[0], worker=demo_worker, row=jobs[0]['row'], route='Upload')
jobs[0]['row'].setText(1, '42%')
jobs[0]['row'].setText(7, 'Upload · Transferring (demonstration)')
capture(w.queue_commands.context_menu(), 'Active-transfer controls', 'Transfers', 'Illustrative active state; the worker is not started.')
w.active.clear()
jobs[0]['state'] = 'queued'
jobs[0]['held'] = True
jobs[0]['row'].setText(1, '0%')
jobs[0]['row'].setText(7, 'Upload · Held')
for action in w.menuBar().actions():
    menu = action.menu()
    if menu:
        if action.text().replace('&','') == 'FTP':
            w.tabs.currentWidget().mark_pane = panes[1]
            panes[1].tree.topLevelItem(2).setSelected(True)
        menu.aboutToShow.emit()
        capture(menu, action.text().replace('&',''), 'Menus', 'Disabled items are unavailable or unimplemented; their presence is not a feature claim.')
w.tabs.currentWidget().mark_pane = panes[0]
for title, menu in [('Queue', w.queue_commands.menu), ('Commands', w.command_commands.menu), ('Compare folders', w.compare_commands.menu)]:
    capture(menu, title, 'Menus')
modal(w.queue_commands.schedule, 'Schedule queued transfers', 'Transfers')
panes[0].tree.topLevelItem(2).setSelected(True)
w.active_pane = panes[0]
modal(w.queue_commands.conditional, 'Conditional transfer', 'Transfers')

from fluxftp.site_manager import SitesDialog, ImportDialog
from fluxftp.site_import import ImportResult
site_manager = SitesDialog(w)
for column in range(site_manager.tree.columnCount()): site_manager.tree.resizeColumnToContents(column)
capture(site_manager, 'Site Manager', 'Connections', size=(1250, 720))
import_sites = ImportDialog(w, ImportResult('Example FileZilla export', sites, ['Demonstration import: passwords are not imported.']))
for column in range(import_sites.tree.columnCount()): import_sites.tree.resizeColumnToContents(column)
capture(import_sites, 'Import sites', 'Connections', size=(1120, 600))
site = SiteDialog(w, w.sites)
site.fields['name'].setText('Demo connection')
site.fields['host'].setText('ftp.example.com')
site.fields['username'].setText('demo')
site.fields['local_path'].setText('C:/Demo/Website')
navigation(site, 'Site properties', (1000, 850))
site.navigation.setCurrentItem(site._nav_items['General'])
site.protocol.setCurrentText('SCP · SSH')
capture(site, 'SCP connection', 'Connections', 'Legacy SCP payloads use verified SSH; browsing requires SFTP.', (1000, 850))

from fluxftp.certification import CertificationDialog, report_text
cert = CertificationDialog(w)
cert.output.setPlainText(report_text(dict(status='Passed (demonstration)', scope='Illustrative report; no server was contacted.',
    protocol=PROTOCOLS[0], checks=['Certificate chain, hostname and validity passed the configured TLS trust policy.'],
    trust='Certificate authority and hostname', tls=dict(version='TLSv1.3', cipher='TLS_AES_256_GCM_SHA384'),
    certificate=dict(subject='CN=ftp.example.com', issuer='CN=Example Demonstration CA', current=True,
        valid_until='2027-10-28T00:00:00+00:00', days_remaining=365, sha256='0123456789abcdef'*4))))
capture(cert, 'TLS certification report', 'Trust and diagnostics', 'Illustrative report, not a live certificate check.', (1020, 800))
cert.site.setCurrentText('Demo SFTP')
cert.output.setPlainText(report_text(dict(status='Passed (demonstration)', scope='Illustrative report; no server was contacted.',
    protocol=PROTOCOLS[3], checks=['SSH host key matched configured known-host trust; authentication succeeded.',
    'SSH host keys are not X.509 certificates and have no certificate expiry date.'],
    ssh=dict(algorithm='ssh-ed25519', bits=256, sha256='SHA256:EXAMPLE-FINGERPRINT-FOR-DOCUMENTATION', cipher='aes256-ctr'))))
capture(cert, 'SSH trust report', 'Trust and diagnostics', 'Illustrative SSH report.', (1020, 700))
from fluxftp.connection_diagnostics import DiagnosticsDialog, report_text as diagnostic_text
diag = DiagnosticsDialog(w)
diag.output.setPlainText('DEMONSTRATION REPORT — no server contacted.\n\n'+diagnostic_text(dict(passed=True, protocol=PROTOCOLS[0], data_mode='Passive',
    checks=[dict(stage=stage, status='Passed', seconds=.052, detail=detail) for stage, detail in (
        ('DNS', 'Example server resolved.'), ('TCP / proxy tunnel', 'TCP connection established.'),
        ('Security / authentication', 'TLS verified; FTP login successful.'),
        ('Directory / data connection', 'Listed 5 entries. Read access verified; write access not tested.'))],
    milestones=['TLS handshake passed', 'FTP authentication passed', 'Data TLS handshake passed'])) )
capture(diag, 'Connection diagnostics', 'Trust and diagnostics', 'Illustrative report.', (1000, 720))
from fluxftp.key_manager import KeyManager, KeyForm
manager = KeyManager(w)
manager.tree.addTopLevelItem(QTreeWidgetItem(['Demo SSH identity', 'SSH / Ed25519', 'None', '256']))
capture(manager, 'Key Manager', 'Trust and diagnostics', 'Sample key metadata; no key is saved.', (760, 500))
capture(KeyForm(manager), 'Generate key or certificate', 'Trust and diagnostics', size=(760, 850))
capture(KeyForm(manager, True), 'Import key or certificate', 'Trust and diagnostics', size=(760, 700))
from fluxftp.ssh_trust import TrustDialog
import paramiko
capture(TrustDialog(w, 'sftp.example.com', paramiko.RSAKey.generate(2048)), 'SSH host fingerprint approval', 'Trust and diagnostics', 'Disposable demonstration key; no trust is saved.')

from fluxftp.cloud_accounts import CloudAccounts, CloudAccountWizard
cloud = CloudAccounts(w)
for name, provider in [('Demo archive', 'Amazon S3'), ('Demo documents', 'Google Drive'), ('Demo team', 'Microsoft OneDrive')]:
    cloud.tree.addTopLevelItem(QTreeWidgetItem([name, provider]))
cloud.tree.setColumnWidth(0, 350)
capture(cloud, 'Cloud accounts', 'Cloud', 'Illustrative account rows; no linked accounts.', (850, 500))
capture(CloudAccountWizard(w), 'Add cloud account', 'Cloud', 'Provider setup before authorization.', (850, 620))
from fluxftp.cloud_accounts import CloudAccountTest
original_accounts = w.cloud_service.accounts
w.cloud_service.accounts = lambda: {'demo': dict(name='Demo archive', provider='Amazon S3')}
capture(CloudAccountTest(w, 'demo'), 'Test cloud access', 'Cloud', 'Read-only connection test setup; no test is run.', (850, 400))
w.cloud_service.accounts = original_accounts

from fluxftp.sync_dialog import SyncDialog
sync = SyncDialog(w, panes, [None, sites[0]])
def fingerprint(size, time): return dict(directory=False, link=False, size=size, modified=time)
sync.listings = [dict({'index.html': fingerprint(18320, '2026-10-28T10:00:00+00:00'), 'assets/app.js': fingerprint(72410, '2026-10-28T10:00:00+00:00')}),
                 dict({'index.html': fingerprint(17962, '2026-10-27T10:00:00+00:00'), 'old.html': fingerprint(1000, '2026-10-27T10:00:00+00:00')})]
sync.recompare()
capture(sync, 'Synchronize folders', 'Sync and deployment', size=(1200, 900))
from fluxftp.deployment_ui import ProfileEditor, DeploymentDialog
profile = ProfileEditor(w)
profile.name.setText('Demo website')
profile.source.setText('C:/Demo/Website')
profile.destination.setText('/public_html')
capture(profile, 'Deployment profile', 'Sync and deployment', size=(920, 650))
deploy = DeploymentDialog(w)
deploy.description.setText('C:/Demo/Website → Demo FTPS: /public_html\nVerification: Checksum readback')
for row in [('index.html', 'Overwrite (backup first)', '18320', '17962'), ('assets/app.js', 'Upload new file', '72410', '—')]:
    deploy.tree.addTopLevelItem(QTreeWidgetItem(row))
deploy.status.setText('Demonstration plan: review changes, then queue a held deployment.')
capture(deploy, 'Deployment preview and rollback controls', 'Sync and deployment', 'Illustrative plan; no deployment has run.', (1200, 800))
from fluxftp.watch_upload import WatchEditor, WatchDialog
capture(WatchDialog(w.watch_uploads), 'Watch-and-upload folders', 'Sync and deployment', size=(1100, 650))
watch = WatchEditor(w.watch_uploads, w)
watch.name.setText('Demo website uploads')
watch.local.setText('C:/Demo/Website')
capture(watch, 'Watch rule', 'Sync and deployment', size=(920, 750))

from fluxftp.automation_ui import AutomationDialog, ScriptDialog, StepDialog
capture(AutomationDialog(w.automation), 'Scheduled automation', 'Automation', size=(1100, 730))
script = ScriptDialog(w.automation)
script.name.setText('Demo nightly upload')
script.tree.addTopLevelItem(QTreeWidgetItem(['Upload file', 'Demo FTPS', '/public_html/index.html', 'C:/Demo/Website/index.html']))
for column in range(script.tree.columnCount()): script.tree.resizeColumnToContents(column)
capture(script, 'Script Designer', 'Automation', 'Illustrative script step.', (1100, 820))
capture(StepDialog(w, w), 'Script step', 'Automation', size=(900, 650))
modal(w.command_commands.designer, 'Command Designer', 'Automation')
modal(w.bandwidth_control.show, 'Unified bandwidth management', 'Transfers')

from fluxftp.conflict_dialog import ConflictDialog
capture(ConflictDialog(w, dict(src='C:/Demo/Website/release.zip', dst='/public_html/release.zip', source_size=734003200, target_size=104857600, can_resume=True)),
        'File conflict choices', 'Transfers', size=(820, 350))
from fluxftp.transfer_history import HistoryDialog
history = HistoryDialog(w.transfer_history)
history.tree.addTopLevelItem(QTreeWidgetItem(['2026-10-28 10:00', 'Complete', 'Upload', '18,320', '0.8', 'C:/Demo/index.html', '/public_html/index.html', 'SHA-256 matched (sample)']))
history.summary.setText('Illustrative history row — verification exports are available below.')
capture(history, 'History and verification exports', 'Transfers', 'Illustrative history; no transfer has run.', (1450, 750))
from fluxftp.queue_recovery import LoadQueueDialog
from fluxftp.queue_job import snapshot
w.deferred_queue = [snapshot(w, job) for job in jobs]
capture(LoadQueueDialog(w), 'Queue recovery', 'Transfers', size=(1150, 650))
from fluxftp.queue_filter import QueueSearch
capture(QueueSearch(w.queue_filter), 'Search all queued files', 'Transfers', size=(1200, 650))
from fluxftp.tray_menu import ManualDownload
manual = ManualDownload(w)
manual.local.setText('C:/Demo/Downloads')
manual.remote.setText('/releases/release.zip')
capture(manual, 'Manual download', 'Transfers', size=(850, 350))
from fluxftp.tftp_dialog import TftpDialog
capture(TftpDialog(w), 'TFTP transfer', 'Transfers', size=(850, 650))
from fluxftp.remote_editor import RemoteTextDialog
from fluxftp.transfer_options import TransferOptions
capture(RemoteTextDialog(w, sites[0], '', '/public_html/index.html', b'<!doctype html>\n<html lang="en">\n  <head><title>Demo website</title></head>\n  <body>\n    <h1>Welcome to the demonstration</h1>\n  </body>\n</html>\n', TransferOptions(), 2, True),
        'Remote text editor', 'Files', 'Sample text loaded locally; Save is not used.', (1100, 720))
from fluxftp.file_associations import AssociationEditor
capture(AssociationEditor(w), 'File association editor', 'Files', size=(900, 650))
from fluxftp.view_menu import EditFilesDialog
capture(EditFilesDialog(w), 'Active edit sessions', 'Files', size=(1050, 650))
from fluxftp.association_actions import ExternalFileSession
capture(ExternalFileSession(w, sites[0], '', '/public_html/index.html', TransferOptions(), 2,
    dict(program='Example editor'), True, None, 'C:/Demo/Edit/index.html', b''),
    'External editor upload review', 'Files', 'No external application is launched and no file is uploaded.', (1000, 350))
from fluxftp.setup_wizard import SetupWizard
capture(SetupWizard(w), 'Setup wizard', 'Getting started', size=(1000, 700))
from fluxftp.help_viewer import HelpDialog
capture(HelpDialog(w), 'Built-in help', 'Getting started', size=(1150, 800))

from fluxftp.preferences import PreferencesDialog
preferences = PreferencesDialog(w)
navigation(preferences, 'Options', (1150, 1050))

(OUT / 'manifest.json').write_text(json.dumps(dict(version='0.122.0', sample_data=True, screenshots=records), indent=2)+'\n', encoding='utf-8')
# A normal close may ask about sample queued jobs. Nothing is persisted or started.
for timer in w.findChildren(QTimer): timer.stop()
for worker in list(w.workers):
    worker.cancel.set()
while w.workers: QTest.qWait(10)
w.pending.clear()
w.queue_jobs.clear()
w.deferred_queue.clear()
w.close()
print(f'Captured {len(records)} screenshots.', flush=True)
