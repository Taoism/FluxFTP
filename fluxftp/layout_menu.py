"""Save, load, and lock named application layouts."""
import json
from pathlib import Path

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMenu


class LayoutMenu(QObject):
    def __init__(self, window, tools_menu):
        super().__init__(window)
        self.window = window
        self.menu = next((action.menu() for action in tools_menu.actions()
                          if action.menu() and action.text() == 'Layout'), None)
        if self.menu is None:
            self.menu = tools_menu.addMenu('Layout')
        self.menu.addSeparator()
        self.menu.addAction('Save Current Layout…', self.save_layout)
        self.load_menu = self.menu.addMenu('Load Layout')
        self.load_menu.aboutToShow.connect(self.populate_load_menu)
        self.load_menu.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.load_menu.customContextMenuRequested.connect(self.load_menu_context)
        self.default_action = self.menu.addAction('Default layout', window.reset_layout)
        self.menu.addSeparator()
        self.popup_actions = {}
        for context, label in (
                ('window', 'Design Popup Menu of Window…'),
                ('local', 'Design Popup Menu of Local…'),
                ('remote', 'Design Popup Menu of Remote…'),
                ('queue', 'Design Popup Menu of Queue…'),
                ('log', 'Design Popup Menu of Log Window…'),
                ('compare', 'Design Popup Menu of Compare Mode…'),
                ('tray', 'Design Popup Menu of Tray Icon…')):
            self.popup_actions[context] = self.menu.addAction(
                label, lambda checked=False, context=context, label=label: self.design_popup(context, label))

    def popup_template(self, context):
        window = self.window
        if context == 'window':
            menu = QMenu(window)
            for label in ('Rename', 'Duplicate', None, 'Close', 'Close Others'):
                menu.addSeparator() if label is None else menu.addAction(label)
            return menu, True
        if context in ('local', 'remote'):
            pane = next((pane for pane in window.current_panes() if pane.mode == context.title()), None)
            if pane is None:
                menu = QMenu(window)
                return menu, True
            return window.default_file_menu(pane), True
        if context == 'queue':
            return window.queue_commands.context_menu(), True
        if context == 'log':
            return window.ftp_log.createStandardContextMenu(), True
        if context == 'compare':
            return window.compare_commands.menu, False
        if context == 'tray':
            return getattr(window, 'tray_menu', None), False
        return None, False

    def design_popup(self, context, title):
        from .popup_menu_designer import PopupMenuDesigner
        from .toolbar_icons import apply_popup_menu_icons
        menu, temporary = self.popup_template(context)
        if menu is None:
            self.window.statusBar().showMessage('This popup menu is not available yet.', 5000)
            return
        apply_popup_menu_icons(self.window, menu)
        dialog = PopupMenuDesigner(self.window, context, title, menu)
        if dialog.exec():
            self.apply_popup_menu(menu, context)
        if temporary:
            menu.deleteLater()
        dialog.deleteLater()

    def apply_popup_menu(self, menu, context):
        from .popup_menu_designer import apply_popup_layout
        from .toolbar_icons import apply_popup_menu_icons
        apply_popup_menu_icons(self.window, menu)
        apply_popup_layout(self.window, menu, context)

    def initialize(self):
        from PySide6.QtCore import Qt
        window = self.window
        for key, widget in (('ftp', window.ftp_log), ('transfer', window.transfer_log),
                            ('system', window.log), ('tasks', window.tasks_log)):
            widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            widget.customContextMenuRequested.connect(
                lambda point, widget=widget: self.show_log_menu(widget, point))
        window.compare_commands.menu.aboutToShow.connect(
            lambda: self.apply_popup_menu(window.compare_commands.menu, 'compare'))
        tray_menu = getattr(window, 'tray_menu', None)
        if tray_menu:
            tray_menu.aboutToShow.connect(lambda: self.apply_popup_menu(tray_menu, 'tray'))
        self.apply_icons()

    def show_log_menu(self, widget, point):
        menu = widget.createStandardContextMenu()
        self.apply_popup_menu(menu, 'log')
        menu.exec(widget.mapToGlobal(point))
        menu.deleteLater()

    def saved_layouts(self):
        try:
            layouts = json.loads(self.window.preferences.value('Layout/saved_layouts', '{}', type=str))
            return layouts if isinstance(layouts, dict) else {}
        except (TypeError, ValueError):
            return {}

    def populate_load_menu(self):
        self.load_menu.clear()
        layouts = self.saved_layouts()
        self.load_menu.addAction('Default layout', self.window.reset_layout)
        if layouts:
            self.load_menu.addSeparator()
            for name in sorted(layouts, key=str.casefold):
                self.load_menu.addAction(name, lambda checked=False, name=name: self.load_layout(name))
        self.load_menu.addSeparator()
        self.load_menu.addAction('Get more layouts…', self.import_layouts)
        self.apply_icons()

    def apply_icons(self):
        from .toolbar_icons import icon_colors, icon_for, toolbar_icon_set
        ink, disabled = icon_colors(self.window)
        icon_set = toolbar_icon_set(self.window)
        self.menu.menuAction().setIcon(icon_for('layout', ink, disabled, icon_set))
        self.load_menu.menuAction().setIcon(icon_for('layout_load', ink, disabled, icon_set))
        icon_by_label = {
            'Save Current Layout…': 'layout_save', 'Load Layout': 'layout_load',
            'Default layout': 'layout_default', 'Get more layouts…': 'layout_load',
            'Side by side': 'compare', 'Stacked': 'workspace',
            'Lock Toolbar': 'lock_toolbar', 'Lock Dock': 'lock_dock',
            'FTP Rush layout': 'layout', 'Dock panels': 'lock_dock',
        }
        for context, action in self.popup_actions.items():
            icon_by_label[action.text()] = {
                'window': 'workspace', 'local': 'local', 'remote': 'remote',
                'queue': 'queue_window', 'log': 'log', 'compare': 'compare', 'tray': 'tray',
            }[context]
        actions = [*self.menu.actions(), *self.load_menu.actions()]
        layout_lock = getattr(self.window, 'layout_lock', None)
        if layout_lock:
            actions.extend((layout_lock.toolbar_action, layout_lock.dock_action))
        if getattr(self.window, 'docking', None):
            actions.append(self.window.docking.rush_layout_action)
            actions.append(self.window.docking.menu.menuAction())
        for action in actions:
            if action.isSeparator():
                continue
            key = icon_by_label.get(action.text(), 'layout_default')
            action.setIcon(icon_for(key, ink, disabled, icon_set))
        from .toolbar_icons import apply_popup_menu_icons
        for menu in (getattr(getattr(self.window, 'compare_commands', None), 'menu', None),
                     getattr(self.window, 'tray_menu', None)):
            if menu:
                apply_popup_menu_icons(self.window, menu)

    def save_layout(self):
        name, accepted = QInputDialog.getText(self.window, 'Save Current Layout', 'Layout name:')
        name = name.strip()
        if not accepted:
            return
        if not name or len(name) > 80 or any(ord(character) < 32 for character in name):
            self.window.statusBar().showMessage('Enter a layout name of 1–80 characters without control characters.', 6000)
            return
        layouts = self.saved_layouts()
        values = {}
        for key in self.window.preferences.allKeys():
            if key.startswith(('Layout/', 'Display/')) and not key.startswith('Layout/saved_layouts'):
                values[key] = self.window.preferences.value(key)
        layouts[name] = values
        self.window.preferences.setValue('Layout/saved_layouts', json.dumps(layouts, ensure_ascii=False))
        self.window.statusBar().showMessage(f'Layout “{name}” saved.', 5000)

    def import_layouts(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self.window, 'Get more layouts', '', 'FluxFTP layout presets (*.fluxlayout);;JSON files (*.json)')
        if not paths:
            return
        layouts = self.saved_layouts()
        imported = []
        for path in paths:
            try:
                package = json.loads(Path(path).read_text(encoding='utf-8'))
            except (OSError, UnicodeError, ValueError):
                continue
            if not isinstance(package, dict) or package.get('format') != 1:
                continue
            name, values = package.get('name'), package.get('settings')
            if (not isinstance(name, str) or not name.strip() or len(name.strip()) > 80 or
                    any(ord(character) < 32 for character in name) or not isinstance(values, dict) or
                    any(not key.startswith(('Layout/', 'Display/')) or key.startswith('Layout/saved_layouts')
                        for key in values if isinstance(key, str)) or
                    any(not isinstance(key, str) for key in values)):
                continue
            layouts[name.strip()] = values
            imported.append(name.strip())
        if imported:
            self.window.preferences.setValue('Layout/saved_layouts', json.dumps(layouts, ensure_ascii=False))
            self.window.statusBar().showMessage(f'Imported {len(imported)} layout preset(s).', 6000)
        else:
            self.window.statusBar().showMessage('No valid FluxFTP layout presets were found in the selected files.', 6000)

    def export_layout(self, name):
        values = self.saved_layouts().get(name)
        if not isinstance(values, dict):
            return
        path, _ = QFileDialog.getSaveFileName(
            self.window, 'Share Layout', name + '.fluxlayout', 'FluxFTP layout preset (*.fluxlayout)')
        if not path:
            return
        if not path.lower().endswith('.fluxlayout'):
            path += '.fluxlayout'
        try:
            with open(path, 'w', encoding='utf-8') as output:
                json.dump({'format': 1, 'name': name, 'settings': values}, output, ensure_ascii=False, indent=2)
        except OSError as error:
            self.window.error(f'Could not export this layout: {error}')
            return
        self.window.statusBar().showMessage(f'Layout “{name}” exported.', 5000)

    def load_menu_context(self, point):
        action = self.load_menu.actionAt(point)
        name = action.text() if action and action.text() in self.saved_layouts() else None
        if not name:
            return
        menu = QMenu(self.load_menu)
        menu.addAction('Load', lambda: self.load_layout(name))
        menu.addAction('Export…', lambda: self.export_layout(name))
        menu.addAction('Remove', lambda: self.remove_layout(name))
        menu.exec(self.load_menu.mapToGlobal(point))
        menu.deleteLater()

    def remove_layout(self, name):
        layouts = self.saved_layouts()
        if name in layouts:
            del layouts[name]
            self.window.preferences.setValue('Layout/saved_layouts', json.dumps(layouts, ensure_ascii=False))

    def load_layout(self, name):
        values = self.saved_layouts().get(name)
        if not isinstance(values, dict):
            return
        for key, value in values.items():
            if key.startswith(('Layout/', 'Display/')) and not key.startswith('Layout/saved_layouts'):
                self.window.preferences.setValue(key, value)
        self.window.apply_layout_settings()
        self.window.view_commands.options.apply()
        try:
            order = json.loads(self.window.preferences.value('Layout/panel_order', '[]', type=str))
            panels = {'workspace': self.window.workspace_panel, 'logs': self.window.log_tabs,
                      'queue': self.window.queue_panel}
            if isinstance(order, list) and len(order) == 3 and set(order) == set(panels):
                for index, key in enumerate(order):
                    self.window.work_area.insertWidget(index, panels[key])
        except (TypeError, ValueError):
            pass
        self.window._restore_splitter(self.window.work_area, 'Layout/panel_positions')
        for index in range(self.window.tabs.count()):
            split = self.window.tabs.widget(index)
            self.window.initialize_pane_positions(split)
        self.window._manual_panel_layout = self.window.preferences.value(
            'Layout/manual_panel_positions', False, type=bool)
        self.window.log_tabs.setCurrentWidget(self.window.log_panels.get(
            self.window.preferences.value('Layout/active_log', 'ftp', type=str), self.window.log_panels['ftp']))
        self.window.header_rows.rebuild()
        self.window.restore_workspace_layout()
        if self.window.docking.active:
            state = self.window.preferences.value('Layout/dock_state', '', type=str)
            if state:
                from PySide6.QtCore import QByteArray
                try:
                    self.window.docking.host.restoreState(QByteArray.fromHex(state.encode('ascii')), 1)
                except (ValueError, UnicodeError):
                    pass
            self.window.docking.restore_saved_dock_layout()
        self.window.save_panel_layout()
        self.window.statusBar().showMessage(f'Layout “{name}” loaded.', 5000)
