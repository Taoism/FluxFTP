"""Mouse dispatch uses the same transfer and navigation paths as toolbar actions."""
import os
import posixpath
from pathlib import Path

from PySide6.QtCore import QObject, QEvent, Qt, QUrl, QTimer
from PySide6.QtGui import QDesktopServices, QKeySequence
from PySide6.QtWidgets import (QApplication, QWidget, QAbstractButton, QComboBox,
    QDialog, QVBoxLayout, QPlainTextEdit, QDialogButtonBox, QFormLayout, QLineEdit,
    QCheckBox, QMessageBox, QMenu, QInputDialog)

from .mouse_options import mouse_action


class MouseFilter(QObject):
    def eventFilter(self, watched, event):
        kind = event.type()
        if kind not in (QEvent.Type.MouseButtonDblClick, QEvent.Type.KeyPress, QEvent.Type.ContextMenu):
            return False
        window = self.parent()
        if not isinstance(watched, QWidget) or QWidget.window(watched) is not window:
            return False
        if kind == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
            for log in (window.log, window.ftp_log, window.transfer_log, window.tasks_log):
                if watched is log.viewport():
                    window.mouse_log(log)
                    return True
            bar = window.tabs.tabBar()
            empty = watched is bar and bar.tabAt(event.position().toPoint()) < 0
            nav = window.workspace_navigation
            if watched is nav or nav.isAncestorOf(watched):
                node = watched
                empty = True
                while node is not nav:
                    if isinstance(node, (QAbstractButton, QComboBox)):
                        empty = False
                        break
                    node = node.parentWidget()
            if empty and window.mouse_action('empty_tabs') == 'Create New Window':
                QTimer.singleShot(0, window.add_workspace)
                return True
        if kind == QEvent.Type.KeyPress and event.matches(QKeySequence.StandardKey.Paste):
            for pane in window.current_panes():
                if watched is pane.tree or watched is pane.tree.viewport():
                    return window.paste_local_files(pane)
        if kind == QEvent.Type.ContextMenu:
            for pane in window.current_panes():
                if watched is pane.tree.viewport():
                    window.mouse_context(pane, event.pos(), event.globalPos())
                    return True
        return False


class MouseActions:
    def mouse_action(self, key):
        return mouse_action(self.preferences, key)

    def initialize_mouse(self):
        self.mouse_filter = MouseFilter(self)
        for widget in (self.tabs.tabBar(), self.workspace_navigation, self.log.viewport(),
                       self.ftp_log.viewport(), self.transfer_log.viewport(), self.tasks_log.viewport()):
            widget.installEventFilter(self.mouse_filter)
        for pane in self.current_panes():
            self.install_pane_mouse(pane)
        self.queue.itemDoubleClicked.connect(self.mouse_queue)
        self.mouse_windows = []

    def install_pane_mouse(self, pane):
        pane.tree.installEventFilter(self.mouse_filter)
        pane.tree.viewport().installEventFilter(self.mouse_filter)

    def retain_mouse_window(self, dialog):
        self.mouse_windows.append(dialog)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.destroyed.connect(lambda: self.mouse_windows.remove(dialog) if dialog in self.mouse_windows else None)
        dialog.show()

    def mouse_file(self, pane, entry, action=None):
        if pane.busy:
            return
        action = action or self.mouse_action('local_file' if pane.mode == 'Local' else 'ftp_file')
        if action in ('Begin Transfer', 'Add to Queue'):
            self.mouse_transfer(pane, [entry], action == 'Begin Transfer')
        elif action == 'Execute File' and pane.mode == 'Local':
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(pane.join(entry.name))):
                self.error('No application could open this file.')
        elif action in ('View File', 'Edit File'):
            from .association_actions import open_associated_file
            if not open_associated_file(self, pane, entry, editable=action == 'Edit File'):
                if pane.mode == 'Remote':
                    from .remote_editor import open_remote_text
                    open_remote_text(self, pane, entry, editable=action == 'Edit File')
                elif not QDesktopServices.openUrl(QUrl.fromLocalFile(pane.join(entry.name))):
                    self.error('No application could open this file.')

    def mouse_transfer(self, pane, entries, start):
        if pane.busy:
            return
        split = pane.parentWidget()
        destination = split.widget(1 if split.widget(0) is pane else 0)
        self.enqueue_items(pane, destination, entries, start=start)

    def paste_local_files(self, pane):
        if pane.mode != 'Remote':
            return False
        if not self.preferences.value('Mouse/allow_paste', True, type=bool):
            return True
        if pane.busy or pane.site is None:
            self.statusBar().showMessage('Connect an idle remote pane before pasting files.', 5000)
            return True
        mime = QApplication.clipboard().mimeData()
        if not mime or not mime.hasUrls() or not mime.urls() or not all(url.isLocalFile() for url in mime.urls()):
            self.statusBar().showMessage('Copy local files or folders before pasting.', 5000)
            return True
        self.enqueue_local_paths(pane, [url.toLocalFile() for url in mime.urls()], start=True)
        return True

    def mouse_log(self, log):
        if getattr(self, 'docking', None) and self.docking.active and self.mouse_action('log') == 'Max/Restore Window':
            key = next((key for key, widget in dict(ftp=self.ftp_log, transfer=self.transfer_log, system=self.log, tasks=self.tasks_log).items() if widget is log), 'system')
            self.docking.show_panel(key)
            return
        if self.mouse_action('log') == 'Max/Restore Window':
            previous = getattr(self, '_mouse_log_sizes', None)
            if previous is None:
                self._mouse_log_sizes = self.work_area.sizes()
                self.work_area.setSizes([0, max(1, sum(self._mouse_log_sizes)), 0])
            else:
                self.work_area.setSizes(previous)
                self._mouse_log_sizes = None
        else:
            dialog = QDialog(self)
            dialog.setWindowTitle('Log snapshot')
            dialog.resize(900, 600)
            rows = QVBoxLayout(dialog)
            text = QPlainTextEdit()
            text.setReadOnly(True)
            text.setFont(log.font())
            text.setPlainText(log.toPlainText())
            rows.addWidget(text)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            buttons.rejected.connect(dialog.close)
            rows.addWidget(buttons)
            self.retain_mouse_window(dialog)

    def mouse_context(self, pane, position, global_position):
        row = pane.tree.itemAt(position)
        if row is not None and not row.isSelected():
            pane.tree.clearSelection()
            pane.tree.setCurrentItem(row)
            row.setSelected(True)
        elif row is None:
            pane.tree.clearSelection()
        if pane.busy:
            return
        if pane.mode == 'Local' and self.mouse_action('local_context') == 'Explorer Menu':
            try:
                from .explorer_menu import show_explorer_menu
                paths = [pane.join(entry.name) for entry in pane.selected()] or [pane.path]
                show_explorer_menu(paths, pane, global_position)
                pane.refresh()
            except (OSError, ValueError) as error:
                self.error(str(error))
            return
        menu = self.default_file_menu(pane)
        menu.exec(global_position)

    def default_file_menu(self, pane):
        menu = QMenu(pane)
        entries = pane.selected()
        if len(entries) == 1:
            entry = entries[0]
            if entry.is_dir:
                menu.addAction('Open folder', lambda: pane.navigate(pane.join(entry.name)))
            elif pane.mode == 'Local':
                menu.addAction('Execute File', lambda: self.mouse_file(pane, entry, 'Execute File'))
            if not entry.is_dir:
                for action in ('View File', 'Edit File'):
                    menu.addAction(action, lambda checked=False, action=action: self.mouse_file(pane, entry, action))
        if entries:
            menu.addAction('Begin Transfer', lambda: self.mouse_transfer(pane, entries, True))
            menu.addAction('Add to Queue', lambda: self.mouse_transfer(pane, entries, False))
            menu.addSeparator()
            if len(entries) == 1:
                menu.addAction('Rename', pane.rename)
            menu.addAction('Delete', pane.delete)
        if pane.mode == 'Remote' and self.preferences.value('Mouse/allow_paste', True, type=bool):
            menu.addAction('Paste local files', lambda: self.paste_local_files(pane))
        menu.addAction('Refresh', pane.refresh)
        copy_names = menu.addAction('Copy Names', lambda: QApplication.clipboard().setText(
            '\n'.join(entry.name for entry in entries)))
        copy_names.setEnabled(bool(entries))
        if pane.mode == 'Remote' and pane.site:
            from .site_options import mapped_url
            try:
                paths = [pane.join(entry.name) for entry in entries] if entries else [pane.path]
                urls = [mapped_url(pane.site, path) for path in paths]
            except ValueError:
                urls = []
            action = menu.addAction('Copy Web URL', lambda: QApplication.clipboard().setText('\n'.join(urls)))
            action.setEnabled(bool(urls))
        menu.addAction('New folder', pane.mkdir)
        return menu

    def mouse_queue(self, row, _column=0):
        job = next((job for job in self.queue_jobs if job['row'] is row), None)
        if not job or 'src' not in job:
            return
        action = self.mouse_action('queue_item')
        if action == 'Enter Source Folder':
            self.enter_queue_source(job)
        elif action == 'Edit Queue':
            self.edit_mouse_queue(job)

    def enter_queue_source(self, job):
        from shiboken6 import isValid
        split = job['workspace']
        if not isValid(split) or self.tabs.indexOf(split) < 0:
            self.add_workspace()
            split = self.tabs.currentWidget()
            pane = split.widget(0)
        else:
            pane = split.widget(job.get('source_side', 1) - 1)
        if pane.busy:
            self.statusBar().showMessage('The source pane is busy.', 5000)
            return
        site = job['source_site']
        password = job.get('source_password', '')
        if site and job.get('needs_credentials'):
            if not self.ensure_proxy_credentials(self, site.host):
                return
            if site.username.casefold() == 'anonymous':
                password = self.preferences.value('General/anonymous_email', 'anonymous@fluxftp.local', type=str)
            else:
                password, accepted = QInputDialog.getText(self, 'Enter Source Folder',
                    f'Password or key passphrase for {site.username}@{site.host}:', QLineEdit.EchoMode.Password)
                if not accepted:
                    return
        # A queue item can outlive its original pane connection. Preserve that pane.
        if self._transfer_site_key(pane.site) != self._transfer_site_key(site):
            self.add_workspace()
            split = self.tabs.currentWidget()
            pane = split.widget(0)
        self.tabs.setCurrentWidget(split)
        if site:
            pane.set_remote(site, password, posixpath.dirname(job['src']) or '/')
            pane.refresh()
        else:
            parent = str(Path(job['src']).parent)
            pane.local_path = parent
            if pane.mode != 'Local':
                pane.change_mode('Local')
            else:
                pane.navigate(parent)

    def edit_mouse_queue(self, job):
        if job.get('sync'):
            self.statusBar().showMessage('Build a new synchronization preview to change these transfer paths.', 5000)
            return
        if job.get('buffered'):
            self.statusBar().showMessage('Select a loaded file to edit its transfer paths.', 5000)
            return
        if job.get('state') not in ('queued', 'failed') or job.get('crc_job'):
            self.statusBar().showMessage('Only waiting or failed ordinary transfers can be edited.', 5000)
            return
        job['editing'] = True
        dialog = QDialog(self)
        dialog.setWindowTitle('Edit Queue')
        dialog.resize(650, 180)
        form = QFormLayout(dialog)
        source, destination = QLineEdit(job['src']), QLineEdit(job['dst'])
        resume = QCheckBox('Resume partial transfer')
        resume.setChecked(job['resume'])
        form.addRow('Source path', source)
        form.addRow('Target path', destination)
        form.addRow(resume)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        def accept():
            try:
                self.update_mouse_queue(job, source.text(), destination.text(), resume.isChecked())
            except ValueError as error:
                QMessageBox.warning(dialog, 'Invalid queue paths', str(error))
                return
            dialog.accept()
        buttons.accepted.connect(accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        try:
            dialog.exec()
        finally:
            job.pop('editing', None)
            self.start_next()

    def update_mouse_queue(self, job, src, dst, resume):
        if job.get('sync'):
            raise ValueError('Build a new synchronization preview to change these paths.')
        if job.get('state') not in ('queued', 'failed') or job.get('crc_job'):
            raise ValueError('This transfer cannot be edited while running or after completion.')
        for value, site in ((src, job['source_site']), (dst, job['destination_site'])):
            if not value or any(ord(char) < 32 for char in value):
                raise ValueError('Paths cannot be empty or contain control characters.')
            if not (value.startswith('/') if site else Path(value).is_absolute()):
                raise ValueError('Use absolute source and target paths.')
            if site and '..' in value.split('/'):
                raise ValueError('Remote paths cannot contain parent-directory segments.')
        same = self._transfer_site_key(job['source_site']) == self._transfer_site_key(job['destination_site'])
        if same:
            a, b = (posixpath.normpath(src), posixpath.normpath(dst)) if job['source_site'] else (str(Path(src).resolve()), str(Path(dst).resolve()))
            if not job['source_site']:
                a, b = os.path.normcase(a), os.path.normcase(b)
            separator = '/' if job['source_site'] else os.sep
            if a == b or (job['directory'] and b.startswith(a.rstrip(separator) + separator)):
                raise ValueError('Source and target cannot overlap.')
        if not job['source_site']:
            path = Path(src)
            if path.is_symlink() or not path.exists() or path.is_dir() != job['directory']:
                raise ValueError('Source must exist and retain its original file/folder type; links are unsupported.')
        name = posixpath.basename(src.rstrip('/')) if job['source_site'] else Path(src).name
        from .core import safe_name
        safe_name(name)
        job.update(src=src, dst=dst, name=name, resume=resume, retries=0)
        job['task'] = self.make_queue_task(job)
        for column, value in ((0, name), (5, src), (6, dst)):
            job['row'].setText(column, value)
        self.save_queue_state()
