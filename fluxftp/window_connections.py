"""Window-level pane selection, quick-connect, and refresh actions."""
from dataclasses import asdict
from PySide6.QtWidgets import QApplication, QMessageBox

from .cloud import CLOUD_PROTOCOLS
from .core import PROTOCOLS, Site
from .site_dialog import SiteDialog
from .site_import import validate_site


class WindowConnections:
    """Keep pane-level connection workflows outside the window composition root."""
    def current_panes(self):
        split = self.tabs.currentWidget()
        return [split.widget(i) for i in range(split.count())] if split else []

    def _select_all_in_pane(self, index):
        pane = self._pane(index)
        if pane:
            pane.tree.selectAll()

    def _clear_pane_selections(self):
        for pane in self.current_panes():
            pane.tree.clearSelection()

    def _pane(self, index):
        panes = self.current_panes()
        return panes[index] if 0 <= index < len(panes) else None

    def populate_quick_connect(self, site, password="", target=0):
        self.quick_protocol.setCurrentText(site.protocol)
        self.quick_port.setValue(site.port)
        self.quick_host.setText(site.name if site.cloud_account else site.host)
        self.quick_username.setText(site.username)
        self.quick_password.setText(password)
        self.quick_path.setText(site.root)
        self.quick_target.setCurrentIndex(target)

    def edit_saved_site(self, name):
        old_profile = self.sites.get(name)
        if not old_profile:
            return
        editor = SiteDialog(self, {}, editing=True)
        editor.load(old_profile)
        if not editor.exec():
            return
        site = editor.site()
        if any(existing.casefold() == site.name.casefold() and existing != name for existing in self.sites):
            QMessageBox.warning(self, 'Name already exists', 'Choose a different site name.')
            return
        proposed = dict(self.sites)
        proposed.pop(name, None)
        profile = asdict(site)
        for key in ('_credential_id', '_folder', '_disabled'):
            if key in old_profile:
                profile[key] = old_profile[key]
        proposed[site.name] = profile
        if not self.save_sites(proposed):
            return
        try:
            self.set_site_password(site.name, editor.password.text())
        except RuntimeError as error:
            QMessageBox.warning(self, 'Password not remembered', str(error))

    def quick_connect(self):
        if self.quick_protocol.currentText() in CLOUD_PROTOCOLS:
            pane = self._pane(self.quick_target.currentIndex())
            if pane and not pane.busy:
                pane.connect_site(protocol=self.quick_protocol.currentText())
            return
        host = self.quick_host.text().strip()
        if not host:
            self.quick_host.setFocus()
            self.statusBar().showMessage("Enter a server host or IP address", 5000)
            return
        target = self.quick_target.currentIndex()
        pane = self._pane(target)
        if pane is None or pane.busy:
            self.statusBar().showMessage("The selected pane is busy", 5000)
            return
        try:
            site = Site(name=host, host=host, port=self.quick_port.value(),
                        protocol=self.quick_protocol.currentText(), username=self.quick_username.text().strip(),
                        root=self.quick_path.text().strip() or "/",
                        passive=self.preferences.value("Connection/default_passive", True, type=bool))
            validate_site(site)
        except ValueError as error:
            self.error(str(error))
            return
        if site.protocol == PROTOCOLS[4] and self.preferences.value("Confirm/unencrypted", True, type=bool):
            if QMessageBox.question(self, "Unencrypted connection",
                    "Plain FTP sends your password and files without encryption. Continue?") != QMessageBox.StandardButton.Yes:
                return
        if not self.ensure_proxy_credentials(pane, site.host, site):
            return
        if self.preferences.value('Misc/quick_new_tab', False, type=bool):
            self.add_workspace()
            pane = self._pane(target)
        pane.set_remote(site, self.quick_password.text(), site.root)
        self.log.appendPlainText(f"Quick connecting {site.protocol} to {site.host}:{site.port} in {self.quick_target.currentText()}")
        pane.refresh()

    def refresh_focused_pane(self):
        focus = QApplication.focusWidget()
        for pane in self.current_panes():
            if focus is pane or (focus is not None and pane.isAncestorOf(focus)):
                pane.refresh()
                return
        pane = self.mark_commands.pane()
        if pane:
            pane.refresh()

    def refresh_both_panes(self):
        for pane in self.current_panes():
            pane.refresh()

    def swap_remote_servers(self):
        panes = self.current_panes()
        if len(panes) != 2 or any(pane.mode != "Remote" or pane.site is None for pane in panes):
            self.statusBar().showMessage("Connect both panes in Remote mode before switching servers", 5000)
            return
        left, right = panes
        left_state = (left.site, left.password, left.path)
        right_state = (right.site, right.password, right.path)
        left.set_remote(*right_state)
        right.set_remote(*left_state)
        left.refresh()
        right.refresh()
        self.statusBar().showMessage("Remote servers switched between panes", 4000)

    def compare_contents(self):
        self.compare_commands.compare()

    def refresh_pane(self, pane_index):
        split = self.tabs.currentWidget()
        if split and pane_index < split.count():
            split.widget(pane_index).refresh()

