"""Display, appearance, and visual preference page construction."""
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QFormLayout, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton, QRadioButton,
    QVBoxLayout, QWidget,
)


class PreferencesDisplayPages:
    """Build the display and appearance pages used by PreferencesDialog."""

    def _populate_display_page(self, title, form, description):
        if title == "Sort":
            self.sort_controls = {}
            for location in ('Remote', 'Local'):
                section = QGroupBox(location)
                grid = QGridLayout(section)
                for row, kind in enumerate(('folder', 'file')):
                    prefix = f'Sort/{location.lower()}_{kind}_'
                    choices = QGroupBox('Sort ' + kind + ' with')
                    choices_layout = QGridLayout(choices)
                    key_group = QButtonGroup(choices)
                    direction_box = QGroupBox('Sorting')
                    direction_box.setStyleSheet('QRadioButton:disabled { color:#9a9a9a; }')
                    direction_layout = QVBoxLayout(direction_box)
                    direction_group = QButtonGroup(direction_box)
                    controls = {'keys': {}, 'directions': {}, 'direction_box': direction_box}
                    for index, label in enumerate(('Global', 'Name', 'Size', 'Date')):
                        button = QRadioButton(label)
                        button.setChecked(self._value(prefix + 'key', 'Global') == label)
                        key_group.addButton(button)
                        choices_layout.addWidget(button, index % 2, index // 2)
                        button.toggled.connect(lambda checked, value=label, key=prefix, box=direction_box:
                            (self.settings.setValue(key + 'key', value), box.setEnabled(value != 'Global'), self._apply_sort(None)) if checked else None)
                        controls['keys'][label] = button
                    for label, descending in (('Ascending', False), ('Descending', True)):
                        button = QRadioButton(label)
                        button.setChecked(self._value(prefix + 'descending', False, bool) == descending)
                        direction_group.addButton(button)
                        direction_layout.addWidget(button)
                        button.toggled.connect(lambda checked, value=descending, key=prefix:
                            (self.settings.setValue(key + 'descending', value), self._apply_sort(None)) if checked else None)
                        controls['directions'][label] = button
                    direction_box.setEnabled(self._value(prefix + 'key', 'Global') != 'Global')
                    grid.addWidget(choices, row, 0)
                    grid.addWidget(direction_box, row, 1)
                    grid.setColumnStretch(0, 2)
                    grid.setColumnStretch(1, 1)
                    self.sort_controls[(location.lower(), kind)] = controls
                form.addRow(section)
            box = QGroupBox('Miscellaneous')
            rows = QFormLayout(box)
            self.sort_checks = {}
            for key, label in (('separate_links', 'Separately Sort Folders and Links'),
                               ('date_descending', 'Default Descending Sort Order for Date Column'),
                               ('size_descending', 'Default Descending Sort Order for Size Column')):
                self.sort_checks[key] = self._check(rows, 'Sort/' + key, label, False, self._apply_sort)
            form.addRow(box)
            description.setText('Global follows the column and direction selected in each pane. Click a column again to reverse it. Changes save immediately.')

        elif title == "Highlights":
            from .highlight_rules import highlight_rules
            self.highlights_disabled = self._check(form, "Display/highlights_disabled", "Do not use Highlights", False, self._refresh_skip_colors)
            note = QLabel("NOTE:\nThe mask supports wildcards and regular expressions. If an item has an icon next to it, it is a regular expression.")
            note.setWordWrap(True)
            form.addRow(note)
            container = QWidget()
            row = QHBoxLayout(container)
            row.setContentsMargins(0, 0, 0, 0)
            self.highlight_list = QListWidget()
            self.highlight_list.setAccessibleName('Filename highlight rules')
            self.highlight_list.setMinimumHeight(350)
            row.addWidget(self.highlight_list, 1)
            buttons = QVBoxLayout()
            self.highlight_edit = QPushButton("Edit")
            self.highlight_delete = QPushButton("Delete")
            self.highlight_up = QPushButton("Move Up")
            self.highlight_down = QPushButton("Move Down")
            for label, callback, button in (
                ("Add", lambda: self._edit_highlight_rule(False), QPushButton("Add")),
                ("Edit", lambda: self._edit_highlight_rule(True), self.highlight_edit),
                ("Delete", self._delete_highlight_rule, self.highlight_delete),
                ("Move Up", lambda: self._move_highlight_rule(-1), self.highlight_up),
                ("Move Down", lambda: self._move_highlight_rule(1), self.highlight_down)):
                button.clicked.connect(callback)
                buttons.addWidget(button)
            buttons.addStretch()
            row.addLayout(buttons)
            form.addRow(container)
            try:
                rules = highlight_rules(self._value("Display/highlight_rules", "[]", str))
            except ValueError as error:
                rules = []
                form.addRow(QLabel(str(error) + ' Saved rules are retained until you edit the list.'))
            self._load_highlight_rules(rules)
            self.highlight_list.itemSelectionChanged.connect(self._highlight_selection_changed)
            self.highlight_list.itemDoubleClicked.connect(lambda _: self._edit_highlight_rule(True))
            self._highlight_selection_changed()
            description.setText('First matching rule wins. Colors apply to filenames in both panes. Skip List colors take precedence. Changes save immediately.')


        elif title == "Font":
            self.font_previews = {}
            self.font_buttons = {}
            for scope, title in (('application', 'Application'), ('files', 'Remote/Local/Queue Window'), ('logs', 'Log Window')):
                group = QGroupBox(title)
                row = QHBoxLayout(group)
                preview = QLabel()
                preview.setProperty('fontScope', scope)
                preview.setWordWrap(True)
                button = QPushButton('Change Font…')
                button.clicked.connect(lambda checked=False, scope=scope: self._choose_font(scope))
                row.addWidget(preview, 1)
                row.addWidget(button)
                self.font_previews[scope] = preview
                self.font_buttons[scope] = button
                form.addRow(group)
            self.default_font_button = QPushButton('Default Font')
            self.default_font_button.clicked.connect(self._reset_fonts)
            form.addRow('', self.default_font_button)
            self._refresh_font_previews()
            description.setText('Changes save immediately. Default Font restores all three font groups.')

        elif title == "Color":
            from .color_options import ColorControl, color_value
            self.color_controls = {}
            self.color_themes = {}
            for scope, title in (('remote', 'Row Background Color of Remote Window'),
                                 ('local', 'Row Background Color of Local Window'), ('queue', 'Queue Window')):
                box = QGroupBox(title)
                grid = QGridLayout(box)
                theme = QComboBox()
                theme.addItems(['(Default)', 'Classic', 'Light', 'Midnight', 'Dark Grey', 'Black', 'Custom'])
                theme.setCurrentText(self._value('Colors/' + scope + '/theme', '(Default)'))
                grid.addWidget(QLabel('Color theme:'), 0, 0)
                grid.addWidget(theme, 0, 1, 1, 2)
                self.color_themes[scope] = theme
                self._bind_combo(theme, 'Colors/' + scope + '/theme',
                    lambda value, scope=scope: self._set_color_theme(scope, value),
                    repeat_on_activation=True)
                for col, (role, label) in enumerate((('selection', 'Selection'), ('even', 'Even row'), ('odd', 'Odd row'))):
                    control = ColorControl(color_value(self.settings, scope, role),
                        settings=self.settings, setting_key=f'{scope}/{role}')
                    grid.addWidget(QLabel(label), 1, col)
                    grid.addWidget(control, 2, col)
                    self.color_controls[(scope, role)] = control
                    control.changed.connect(lambda value, scope=scope, role=role: self._set_display_color(scope, role, value))
                form.addRow(box)
            gradient = QCheckBox('Use dark-to-light gradient progress bars')
            gradient.setChecked(self._value('Colors/progress_gradient', True, bool))
            gradient.toggled.connect(lambda enabled: (
                self.settings.setValue('Colors/progress_gradient', enabled), self.window.apply_colors()))
            form.addRow('Queue progress:', gradient)
            self.progress_gradient = gradient
            box = QGroupBox('FTP Log')
            grid = QGridLayout(box)
            for index, (role, label) in enumerate((('background', 'Background Color'), ('error', 'Error Message'),
                ('reply1', 'Server (1) Reply Message'), ('reply2', 'Server (2) Reply Message'),
                ('command', 'FTP Command'), ('prompt', 'Prompt Message'))):
                row, col = divmod(index, 2)
                control = ColorControl(color_value(self.settings, 'log', role),
                    settings=self.settings, setting_key=f'log/{role}')
                grid.addWidget(QLabel(label), row * 2, col)
                grid.addWidget(control, row * 2 + 1, col)
                self.color_controls[('log', role)] = control
                control.changed.connect(lambda value, role=role: self._set_display_color('log', role, value))
            form.addRow(box)
            description.setText('Classic, Light, Midnight, Dark Grey and Black in Themes coordinate these colors automatically. Select System to edit custom colors. Low-contrast text is corrected for readability. Server (1) and (2) correspond to the left and right panes.')

        elif title == "Layout":
            from .layout_options import DEFAULTS
            from .color_options import ColorControl
            self.layout_checks = {}
            def group(title):
                box = QGroupBox(title)
                rows = QFormLayout(box)
                form.addRow(box)
                return rows
            def check(rows, key, title):
                control = self._check(rows, 'Layout/' + key, title, DEFAULTS[key], self.window.apply_layout_settings)
                self.layout_checks[key] = control
                return control
            rows = group('Window List')
            check(rows, 'navigate_tabs', 'Navigate via Tabs')
            check(rows, 'multiple_lines', 'Multiple Lines')
            check(rows, 'tabs_bottom', 'Show Tab(s) at Bottom')
            if hasattr(self.window, 'header_rows'):
                self.layout_checks['tabs_bottom'].setEnabled(False)
                self.layout_checks['tabs_bottom'].setToolTip('Workspace tabs stay beneath the Workspaces heading.')
                rows.addRow(QLabel('Drag row handles to arrange the toolbar, connection fields, tabs and window list.'))
            self.layout_checks['limit_tab_width'], self.tab_width = self._checked_spin(rows,
                'Layout/limit_tab_width', 'Limit width of Tabs', 'Layout/tab_width', 100, 60, 1000, ' px')
            self.layout_checks['limit_tab_width'].toggled.connect(self.window.apply_layout_settings)
            self.tab_width.valueChanged.connect(self.window.apply_layout_settings)
            active = check(rows, 'custom_active_font', 'Use customize font color for active tab')
            self.active_tab_color = ColorControl(self._value('Layout/active_color', '#ff0000', str),
                settings=self.settings, setting_key='layout/active_color')
            self.active_tab_color.setEnabled(active.isChecked())
            active.toggled.connect(self.active_tab_color.setEnabled)
            self.active_tab_color.changed.connect(lambda value: (self.settings.setValue('Layout/active_color', value), self.window.apply_layout_settings()))
            rows.addRow('Active tab color', self.active_tab_color)
            rows = group('Display Caption Area of Dock Panel')
            captions = QWidget()
            grid = QGridLayout(captions)
            grid.setContentsMargins(0, 0, 0, 0)
            for index, (key, label) in enumerate((('caption_tab_style', 'Docking as Tab Style'), ('caption_windows', 'Window List'),
                ('caption_transfer', 'Transfer Log'), ('caption_ftp', 'FTP Log'), ('caption_system', 'System Log'),
                ('caption_queue', 'Queue'), ('caption_tasks', 'Task Window'), ('caption_panes', 'Remote and Local'))):
                control = QCheckBox(label)
                control.setChecked(self._value('Layout/' + key, DEFAULTS[key], bool))
                control.toggled.connect(lambda value, key=key: (self.settings.setValue('Layout/' + key, value), self.window.apply_layout_settings()))
                self.layout_checks[key] = control
                grid.addWidget(control, index // 2, index % 2)
            rows.addRow(captions)
            rows = group('Remote and Local Window')
            check(rows, 'pane_tabs', 'Use Tab Switching for Remote and Local Window')
            large_icons = check(rows, 'large_icons', 'Use Large Icons for Remote/Local Window')
            self.toolbar_icon_size = self._spin(rows, 'Layout/toolbar_icon_size', 'Toolbar icon size', self.window.toolbar_icon_size(), 16, 48, ' px')
            self.toolbar_icon_size.valueChanged.connect(self.window.set_toolbar_icon_size)
            large_icons.toggled.connect(lambda value: self.toolbar_icon_size.setValue(32 if value else 16))
            self._combo(rows, 'Display/pane_layout', 'Pane arrangement', ['Side by side', 'Stacked'], 'Side by side', self._apply_layout)
            self._combo(rows, 'Display/pane_order', 'Portrait pane order',
                ['Site then Local', 'Local then Site'], 'Site then Local', self._apply_pane_order)
            rows = group('Performance')
            check(rows, 'disable_toolbar_updates', 'Disable Update Main FTP Toolbar')
            check(rows, 'double_buffer', 'Enable Double Buffer painting for Dock panel')
            description.setText('Changes save immediately. Caption settings control panel titles. Double buffering can reduce painting flicker.')

        elif title == "Icons":
            self._combo(form, 'Display/icon_layout', 'Toolbar icon layout',
                ['Icons only', 'Text beside icons', 'Text below icons'], 'Icons only',
                lambda _value: self.window.apply_layout_settings())
            self._combo(form, 'Display/icon_set', 'Toolbar icon set',
                ['Windows', 'Theme matched', 'Light (for dark theme)', 'Dark (for light theme)', 'Bold', 'Color accents'], 'Windows',
                lambda _value: self.window.apply_layout_settings())
            self._combo(form, 'Display/folder_icon_set', 'Folder icon set',
                ['Soft grayscale', 'Black & white', 'Outline', 'Windows tan'], 'Soft grayscale',
                lambda _value: self.window.apply_display_settings())
            description.setText('Choose a toolbar icon style and a coordinated folder icon set. Folder sets include monochrome styles and classic Windows tan, with theme-aware contrast. Choose a custom folder image in Themes.')

        elif title == "Themes":

            self.theme_combo = self._combo(form, "Display/style", "Theme", ["Classic", "System", "Follow OS", "Light", "Midnight", "Dark Grey", "Black"], "Classic", self._apply_style,
                repeat_on_activation=True)
            self.header_theme_combo = self._combo(form, 'Display/header_theme', 'Remote / Site headers',
                ['Follow theme', 'Classic', 'System', 'Light', 'Midnight', 'Dark Grey', 'Black'], 'Follow theme',
                self.window.apply_display_settings)

            icon_row = QWidget()
            icon_layout = QHBoxLayout(icon_row)
            icon_layout.setContentsMargins(0, 0, 0, 0)
            self.folder_icon_path = QLineEdit(self._value('Display/folder_icon_path', ''))
            self.folder_icon_path.setReadOnly(True)
            self.folder_icon_path.setPlaceholderText('Theme-matched folder icons')
            choose_folder_icon = QPushButton('Choose image…')
            reset_folder_icon = QPushButton('Use theme icons')
            choose_folder_icon.clicked.connect(self._choose_folder_icon)
            reset_folder_icon.clicked.connect(self._reset_folder_icon)
            icon_layout.addWidget(self.folder_icon_path, 1)
            icon_layout.addWidget(choose_folder_icon)
            icon_layout.addWidget(reset_folder_icon)
            form.addRow('Folder icons:', icon_row)

            description.setText("Follow OS automatically switches between Light and Midnight when the operating system appearance changes. Named themes are fixed presets. System allows custom colors. Choose an image to use your own folder icon throughout the app, or restore the theme-matched dark and light icons.")

        elif title == "Status bar":

            self._check(form, "Display/status_bar", "Show the status bar", True, self._apply_status_bar)

            description.setText("Show or hide the connection and transfer status line at the bottom of the main window.")

        elif title == "Display":
            self.display_checks = {}
            def group(title):
                box = QGroupBox(title)
                rows = QFormLayout(box)
                form.addRow(box)
                return rows
            def check(rows, key, label, default=False):
                control = self._check(rows, 'Display/' + key, label, default, self.window.apply_display_settings)
                self.display_checks[key] = control
                return control
            rows = group('System Tray')
            check(rows, 'minimize_tray', 'Minimize to system tray')
            check(rows, 'flash_tray', 'Flash Tray icon when transferring', True)
            check(rows, 'always_tray', 'Always Show Tray Icon', True)
            rows = group('Local Window')
            check(rows, 'local_full_path', 'Display the full path in the local address bar')
            hidden = check(rows, 'local_show_hidden', 'Show hidden files and folders')
            hidden.toggled.connect(lambda _: [pane.refresh() for i in range(self.window.tabs.count())
                for pane in (self.window.tabs.widget(i).widget(0), self.window.tabs.widget(i).widget(1)) if pane.mode == 'Local'])
            rows = group('Display Mode of File Size')
            buttons = QWidget()
            layout = QHBoxLayout(buttons)
            layout.setContentsMargins(0, 0, 0, 0)
            self.size_modes = {}
            self.size_mode_group = QButtonGroup(self)
            for label in ('Bytes', 'KBytes', 'Dynamic'):
                button = QRadioButton(label)
                button.setChecked(self._value('Display/size_mode', 'Dynamic') == label)
                button.toggled.connect(lambda checked, value=label: (self.settings.setValue('Display/size_mode', value), self.window.apply_display_settings()) if checked else None)
                self.size_mode_group.addButton(button)
                self.size_modes[label] = button
                layout.addWidget(button)
            rows.addRow(buttons)
            rows = group('Transfer')
            check(rows, 'flash_completed', 'Flash Window when Transfer Completed', True)
            self.display_checks['update_download_size'], self.download_seconds = self._checked_spin(rows,
                'Display/update_download_size', 'Update Downloading File Size every', 'Display/download_seconds', 15, 1, 3600, ' second(s)')
            rows = group('Sites Menu')
            self.display_checks['split_sites'], self.sites_lines = self._checked_spin(rows,
                'Display/split_sites', 'Split Sites Menu every', 'Display/sites_lines', 25, 1, 1000, ' lines')
            for control in (self.download_seconds, self.sites_lines): control.valueChanged.connect(self.window.apply_display_settings)
            for key in ('update_download_size', 'split_sites'): self.display_checks[key].toggled.connect(self.window.apply_display_settings)
            check(rows, 'hide_quick_connect', 'Hide Quick Connect from Sites Dropdown Menu')
            check(rows, 'show_quick_dialog', 'Show Quick Connect dialog entry', True)
            check(rows, 'show_recent', 'Show "Recent sites" entry', True)
            rows = group('Miscellaneous')
            check(rows, 'gradient_indicator', 'Use Gradient Color for Side Indicator', True)
            check(rows, 'hide_unfocused_selection', 'Hide Selection when ListView Lost Focus')
            check(rows, 'hyphen_separator', 'Always use "-" to split Left/Right site name')
            description.setText('Changes save immediately. Tray options require a system tray. Recent sites store connection details without passwords.')

        else:
            return False
        return True
