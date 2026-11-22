"""Auto-trading configuration, preview, and ledger controls."""
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QPlainTextEdit, QTableWidget, QTableWidgetItem,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)


class TradingDialog(QDialog):
    def __init__(self, service):
        super().__init__(service.window)
        self.service = service
        self.setWindowTitle('Auto Trading')
        self.resize(1040, 760)
        root = QVBoxLayout(self)
        heading = QHBoxLayout()
        self.global_enabled = QCheckBox('Enable automatic trading')
        self.global_enabled.toggled.connect(self.toggle_global)
        heading.addWidget(self.global_enabled)
        self.status = QLabel()
        self.status.setWordWrap(True)
        heading.addWidget(self.status, 1)
        root.addLayout(heading)

        tabs = QTableWidget(0, 8)
        tabs.setHorizontalHeaderLabels(['State', 'Route', 'Source', 'Destination', 'Trigger',
                                        'Retries', 'Updated', 'Last error / ID'])
        tabs.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        tabs.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tabs.setAlternatingRowColors(True)
        self.trades = tabs
        root.addWidget(tabs, 2)

        controls = QHBoxLayout()
        self.pause_button = QPushButton('Pause selected')
        self.pause_button.clicked.connect(self.pause_selected)
        self.resume_button = QPushButton('Resume selected')
        self.resume_button.clicked.connect(self.resume_selected)
        self.retry_button = QPushButton('Retry selected')
        self.retry_button.clicked.connect(self.retry_selected)
        self.cancel_button = QPushButton('Cancel selected')
        self.cancel_button.clicked.connect(self.cancel_selected)
        for button in (self.pause_button, self.resume_button, self.retry_button, self.cancel_button):
            controls.addWidget(button)
        controls.addStretch(1)
        controls.addWidget(QLabel('Route'))
        self.manual_route = QComboBox()
        controls.addWidget(self.manual_route)
        self.manual_folder = QLineEdit()
        self.manual_folder.setPlaceholderText('Remote folder name to submit manually')
        controls.addWidget(self.manual_folder, 1)
        self.manual_button = QPushButton('Manual submit')
        self.manual_button.clicked.connect(self.submit_manual)
        controls.addWidget(self.manual_button)
        root.addLayout(controls)

        rule_label = QLabel('Routes. Uncheck a route to pause new automatic trades for it.')
        root.addWidget(rule_label)
        self.routes = QTreeWidget()
        self.routes.setHeaderLabels(['Enabled', 'Route', 'Source rule', 'Destination rule'])
        self.routes.itemChanged.connect(self.route_changed)
        self.routes.setMaximumHeight(130)
        root.addWidget(self.routes)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText('Preview scan results and rejection reasons appear here.')
        self.preview.setMaximumHeight(120)
        root.addWidget(self.preview)

        self.config_editor = QPlainTextEdit()
        self.config_editor.setTabStopDistance(28)
        self.config_editor.setPlaceholderText(
            '# Add [trading], [[sources]], [[destinations]], [[routes]], and optional IRC tables.\n'
            '# Full schema and examples: docs/AUTO_TRADING.md')
        self.config_editor.textChanged.connect(self._editor_changed)
        root.addWidget(QLabel('Configuration file: ' + str(service.config_path)))
        root.addWidget(self.config_editor, 3)

        buttons = QHBoxLayout()
        self.save_button = QPushButton('Save and validate')
        self.save_button.clicked.connect(self.save_config)
        buttons.addWidget(self.save_button)
        self.preview_button = QPushButton('Preview scan')
        self.preview_button.clicked.connect(lambda: self.run_scan(preview=True))
        buttons.addWidget(self.preview_button)
        self.scan_button = QPushButton('Record scan')
        self.scan_button.clicked.connect(lambda: self.run_scan(preview=False))
        buttons.addWidget(self.scan_button)
        self.adapter_button = QPushButton('IRC adapters…')
        self.adapter_button.clicked.connect(self.show_adapter_info)
        buttons.addWidget(self.adapter_button)
        buttons.addStretch(1)
        close = QPushButton('Close')
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        root.addLayout(buttons)

        self._loading_editor = False
        self._dirty = False
        self.service.changed.connect(self.refresh)
        self.service.message.connect(self.show_message)
        self.refresh()

    def _editor_changed(self):
        if not self._loading_editor:
            self._dirty = True

    def refresh(self):
        self.status.setText(self.service.problem or (
            'Trading is enabled. Polling interval: ' + str(self.service.config['poll_seconds']) + ' seconds.'
            if self.service.enabled else 'Trading is disabled. Preview remains available.'))
        self.global_enabled.blockSignals(True)
        self.global_enabled.setChecked(self.service.config.get('enabled', False))
        self.global_enabled.setEnabled(self.service.owner)
        self.global_enabled.blockSignals(False)
        self.pause_button.setEnabled(self.service.owner)
        self.resume_button.setEnabled(self.service.owner)
        self.retry_button.setEnabled(self.service.owner)
        self.cancel_button.setEnabled(self.service.owner)
        self.manual_route.clear()
        for route in self.service.config['routes']:
            self.manual_route.addItem(route['id'], route['id'])
        self.routes.blockSignals(True)
        self.routes.clear()
        source_by_id = {item['id']: item for item in self.service.config['sources']}
        destination_by_id = {item['id']: item for item in self.service.config['destinations']}
        for route in self.service.config['routes']:
            source = source_by_id[route['source']]
            destination = destination_by_id[route['destination']]
            item = QTreeWidgetItem(['', route['id'], source['bookmark'] + ':' + source['path'],
                                    destination['bookmark'] + ':' + destination['path']])
            item.setData(1, Qt.ItemDataRole.UserRole, route['id'])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked if route['enabled'] else Qt.CheckState.Unchecked)
            self.routes.addTopLevelItem(item)
        self.routes.blockSignals(False)
        self.trades.setRowCount(0)
        for record in self.service.ledger.list_trades(limit=1000):
            row = self.trades.rowCount()
            self.trades.insertRow(row)
            values = [record['state'], record['route_id'],
                      record['source_bookmark'] + ':' + record['source_path'],
                      record['destination_bookmark'] + ':' +
                      (record['destination_resolved_path'] or record['destination_path']),
                      record['trigger_source'], f"{record['retries']}/{self.service.config['max_retries']}",
                      time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(record['updated_at'])),
                      (record['last_error'] or record['id'][:12])]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                cell.setData(Qt.ItemDataRole.UserRole, record['id'])
                self.trades.setItem(row, column, cell)
        self.trades.resizeColumnsToContents()
        if not self._dirty:
            self._loading_editor = True
            if self.service.config_path.exists():
                text = self.service.config_path.read_text('utf-8')
            else:
                text = ('# FluxFTP auto-trading configuration\n'
                        '# Add saved-site rules below, save, then preview before enabling.\n\n'
                        '[trading]\n'
                        'enabled = false\n'
                        'poll_seconds = 10\n'
                        'stable_polls = 3\n'
                        'max_depth = 64\n'
                        'max_entries = 100000\n\n'
                        '# See docs/AUTO_TRADING.md for the complete format and examples.\n')
            self.config_editor.setPlainText(text)
            self._loading_editor = False
        if self.service.last_scan:
            self.preview.setPlainText('\n'.join(
                f"{item.get('route') or item.get('source') or 'Candidate'} · "
                f"{item.get('name', '')}: {item.get('reason', '')}"
                for item in self.service.last_scan))

    def show_message(self, message):
        self.status.setText(message)

    def save_config(self):
        try:
            self.service.save_config(self.config_editor.toPlainText())
            self._dirty = False
            self.status.setText('Configuration saved and validated.')
            self.refresh()
        except Exception as error:
            QMessageBox.warning(self, 'Invalid auto-trading configuration', str(error))

    def toggle_global(self, enabled):
        try:
            if self._dirty:
                self.service.save_config(self.config_editor.toPlainText())
                self._dirty = False
            self.service.set_enabled(enabled)
            self.refresh()
        except Exception as error:
            QMessageBox.warning(self, 'Auto Trading', str(error))
            self.refresh()

    def route_changed(self, item, column):
        if column != 0:
            return
        route_id = item.data(1, Qt.ItemDataRole.UserRole)
        enabled = item.checkState(0) == Qt.CheckState.Checked
        try:
            if self._dirty:
                self.service.save_config(self.config_editor.toPlainText())
                self._dirty = False
            self.service.set_route_enabled(route_id, enabled)
        except Exception as error:
            QMessageBox.warning(self, 'Auto Trading route', str(error))
        self.refresh()

    def run_scan(self, *, preview):
        try:
            if self._dirty:
                self.service.save_config(self.config_editor.toPlainText())
                self._dirty = False
            if not self.service.scan_once(preview=preview):
                self.status.setText(self.service.problem or 'A scan is already running.')
            else:
                self.status.setText('Preview scan running…' if preview else 'Recording scan running…')
        except Exception as error:
            QMessageBox.warning(self, 'Auto Trading scan', str(error))

    def _selected_trade_id(self):
        row = self.trades.currentRow()
        cell = self.trades.item(row, 0) if row >= 0 else None
        return cell.data(Qt.ItemDataRole.UserRole) if cell else ''

    def pause_selected(self):
        identity = self._selected_trade_id()
        if identity:
            self.service.pause_trade(identity)

    def resume_selected(self):
        identity = self._selected_trade_id()
        if identity:
            try:
                self.service.resume_trade(identity)
            except Exception as error:
                QMessageBox.warning(self, 'Resume auto trade', str(error))

    def retry_selected(self):
        identity = self._selected_trade_id()
        trade = self.service.ledger.get_trade(identity) if identity else None
        if not trade or trade['state'] != 'failed':
            return
        route = next((item for item in self.service.config['routes'] if item['id'] == trade['route_id']), None)
        destination = next((item for item in self.service.config['destinations']
                            if route and item['id'] == route['destination']), None)
        try:
            self.service.ledger.transition(identity, 'queued', 'Manual retry requested.')
            self.service._enqueue(self.service.ledger.get_trade(identity), destination['conflict'] if destination else 'fail')
            self.refresh()
        except Exception as error:
            QMessageBox.warning(self, 'Retry auto trade', str(error))

    def cancel_selected(self):
        identity = self._selected_trade_id()
        if identity:
            self.service.cancel_trade(identity)

    def submit_manual(self):
        route_id = self.manual_route.currentData()
        folder = self.manual_folder.text().strip()
        if not route_id or not folder:
            QMessageBox.information(self, 'Manual trade', 'Choose a route and enter a remote folder name.')
            return
        accepted, message = self.service.submit_manual(route_id, folder)
        self.status.setText(message)
        if not accepted:
            QMessageBox.warning(self, 'Manual trade', message)

    def show_adapter_info(self):
        try:
            paths = self.service.export_irc_adapters()
        except (OSError, RuntimeError, ValueError) as error:
            QMessageBox.warning(self, 'IRC integrations', f'Could not export the adapters: {error}')
            return
        QMessageBox.information(self, 'IRC integrations',
            'FluxFTP exported ready-to-load adapters to:\n\n'
            f'{paths[0]}\n{paths[1]}\n\n'
            'Load the Python file from HexChat’s Addons manager. In AdiIRC, open Tools → Edit Scripts '
            'and load the .mrc file. Keep these generated files private; they contain the bridge token. '
            'Both clients submit trigger messages to FluxFTP’s local validated intake.')
