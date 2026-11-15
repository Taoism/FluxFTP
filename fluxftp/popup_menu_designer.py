"""Editor for the command order and visibility in FluxFTP popup menus."""
import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QListWidget, QListWidgetItem, QVBoxLayout


def action_key(action, counts):
    if action.isSeparator():
        counts['separator'] = counts.get('separator', 0) + 1
        return f"separator:{counts['separator']}"
    label = action.text().split('\t', 1)[0].replace('&', '').strip()
    counts[label] = counts.get(label, 0) + 1
    return f"{label}:{counts[label]}"


def keys_for(actions):
    counts = {}
    return [action_key(action, counts) for action in actions]


def apply_popup_layout(window, menu, context):
    actions = menu.actions()
    keys = keys_for(actions)
    if not hasattr(menu, '_fluxftp_default_action_order'):
        menu._fluxftp_default_action_order = list(keys)
    raw = window.preferences.value(f'Layout/popup_menus/{context}', '', type=str)
    if raw:
        try:
            order = json.loads(raw)
        except (TypeError, ValueError):
            order = None
        if not isinstance(order, list) or any(not isinstance(key, str) for key in order):
            order = None
    else:
        order = None
    if order is None:
        order = menu._fluxftp_default_action_order
    by_key = dict(zip(keys, actions))
    wanted = [key for key in order if key in by_key]
    for key, action in by_key.items():
        action.setVisible(key in wanted)
    for key in wanted:
        action = by_key[key]
        menu.removeAction(action)
        menu.addAction(action)


class PopupMenuDesigner(QDialog):
    def __init__(self, window, context, title, menu):
        super().__init__(window)
        self.window, self.context = window, context
        self.setWindowTitle(title)
        self.resize(420, 430)
        rows = QVBoxLayout(self)
        rows.addWidget(QLabel('Select the commands to show and arrange them in menu order.'))
        self.commands = QListWidget()
        self.commands.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        actions = menu.actions()
        keys = keys_for(actions)
        current = window.preferences.value(f'Layout/popup_menus/{context}', '', type=str)
        try:
            order = json.loads(current) if current else keys
        except (TypeError, ValueError):
            order = keys
        by_key = dict(zip(keys, actions))
        ordered = [key for key in order if key in by_key]
        ordered.extend(key for key in keys if key not in ordered)
        for key in ordered:
            action = by_key[key]
            label = '────────' if action.isSeparator() else action.text().split('\t', 1)[0].replace('&', '')
            item = QListWidgetItem(label)
            item.setIcon(action.icon())
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
            item.setCheckState(Qt.CheckState.Unchecked if current and key not in order else Qt.CheckState.Checked)
            item.setToolTip(action.statusTip() or action.toolTip())
            self.commands.addItem(item)
        rows.addWidget(self.commands, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                   QDialogButtonBox.StandardButton.Cancel |
                                   QDialogButtonBox.StandardButton.RestoreDefaults)
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        defaults_button = buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults)
        from .toolbar_icons import icon_colors, icon_for, toolbar_icon_set
        ink, disabled = icon_colors(window)
        icon_set = toolbar_icon_set(window)
        save_button.setIcon(icon_for('layout_save', ink, disabled, icon_set))
        defaults_button.setIcon(icon_for('layout_default', ink, disabled, icon_set))
        save_button.clicked.connect(self.save)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).clicked.connect(self.reject)
        defaults_button.clicked.connect(self.restore_defaults)
        rows.addWidget(buttons)
        self.default_keys = keys

    def save(self):
        order = [self.commands.item(i).data(Qt.ItemDataRole.UserRole)
                 for i in range(self.commands.count())
                 if self.commands.item(i).checkState() == Qt.CheckState.Checked]
        self.window.preferences.setValue(f'Layout/popup_menus/{self.context}', json.dumps(order))
        self.accept()

    def restore_defaults(self):
        self.window.preferences.remove(f'Layout/popup_menus/{self.context}')
        self.accept()
