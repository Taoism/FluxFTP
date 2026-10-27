"""Nonblocking desktop controls for the trusted Windows runtime updater."""
import queue
import threading

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton, QRadioButton, QButtonGroup, QMessageBox

from . import openssl_runtime as runtime


class RuntimeUpdater(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.busy = False
        self.messages = queue.Queue()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(500)
        self.schedule = QTimer(self)
        self.schedule.timeout.connect(self.automatic)
        self.schedule.start(60 * 60 * 1000)
        QTimer.singleShot(10000, self.automatic)
        try:
            self.report(runtime.read_json(runtime.root() / 'status.json')['message'])
        except (OSError, KeyError, ValueError, TypeError):
            pass

    def report(self, message):
        self.window.log.appendPlainText(message)
        self.window.statusBar().showMessage(message, 15000)

    def automatic(self):
        if self.window.preferences.value('Updates/openssl_enabled', True, type=bool):
            self.start()

    def start(self, force=False):
        if self.busy:
            if force:
                self.report('An OpenSSL update check is already running.')
            return
        self.busy = True
        if force:
            self.report('Checking verified Windows builds for OpenSSL 3.x and 4.x…')
        location = runtime.root()

        def work():
            from .openssl_updates import check as check3
            from .openssl4_updates import check as check4
            results = []
            for major, check in ((3, check3), (4, check4)):
                try:
                    result = check(location, force=force)
                    if result:
                        results.append(result)
                except Exception as error:
                    results.append(f'OpenSSL {major}.x update check could not complete '
                                   f'({type(error).__name__}); current libraries remain available.')
            self.messages.put('\n'.join(results))

        threading.Thread(target=work, name='OpenSSL update check', daemon=True).start()

    def poll(self):
        try:
            result = self.messages.get_nowait()
        except queue.Empty:
            return
        self.busy = False
        if result:
            self.report(result)


def add_controls(form, window):
    import ssl
    location = runtime.root()
    updater = getattr(window, 'runtime_updater', None)
    running = QLabel(f'Currently in use: {ssl.OPENSSL_VERSION}')
    running.setObjectName('ssl_active_version')
    running.setWordWrap(True)
    form.addRow(running)
    group = QButtonGroup(form.parentWidget())
    try:
        selected = runtime.branch(location)
    except (OSError, ValueError, KeyError, TypeError):
        selected = 3
    status = QLabel()
    status.setObjectName('ssl_pending_version')
    status.setWordWrap(True)
    def refresh():
        try:
            selected = runtime.branch(location)
            directory = runtime.available_directory(location, selected)
            available = runtime.validate_pair(directory)['version'] if directory else '.'.join(map(str, runtime.BASE_VERSION))
            active = ssl.OPENSSL_VERSION.split()[1]
            text = f'Next launch: OpenSSL {available} ({selected}.x)'
            if available != active:
                text += ' — restart FluxFTP to apply.'
            status.setText(text)
        except (OSError, ValueError, KeyError, TypeError):
            status.setText('Selected runtime is unavailable. Startup will use the bundled 3.x runtime.')
    def select(major):
        try:
            runtime.choose_branch(location, major)
        except (OSError, ValueError, KeyError, TypeError) as error:
            QMessageBox.warning(form.parentWidget(), 'SSL selection',
                f'The runtime could not be selected ({type(error).__name__}).')
            group.button(runtime.branch(location)).setChecked(True)
        refresh()
    for major in (3, 4):
        radio = QRadioButton(f'OpenSSL {major}.x' + (' (default)' if major == 3 else ''))
        radio.setObjectName(f'ssl_branch_{major}')
        group.addButton(radio, major)
        radio.setChecked(selected == major)
        radio.setEnabled(updater is not None)
        radio.clicked.connect(lambda checked=False, value=major: select(value))
        form.addRow(radio)
    form.addRow(status)
    refresh()
    timer = QTimer(status)
    timer.timeout.connect(refresh)
    timer.start(2000)
    enabled = QCheckBox('Automatically download verified updates for both OpenSSL 3.x and 4.x')
    enabled.setChecked(window.preferences.value('Updates/openssl_enabled', True, type=bool))
    enabled.toggled.connect(lambda value: window.preferences.setValue('Updates/openssl_enabled', value))
    form.addRow(enabled)
    button = QPushButton('Check OpenSSL updates now')
    button.setEnabled(updater is not None)
    enabled.setEnabled(updater is not None)
    button.clicked.connect(lambda: updater.start(force=True))
    form.addRow(button)
    label = QLabel('Both versions stay available. Updates stay within their major version.\n'
                   'Checks run daily in the background. Verified updates activate on the next launch; '
                   'active transfers are not interrupted. A failed startup falls back to bundled 3.x.\n'
                   'This setting controls FTP/FTPS. SFTP and cloud providers use their own libraries.'
                   if updater else 'OpenSSL updates are available in the packaged Windows application.')
    label.setWordWrap(True)
    form.addRow(label)
