"""Workspace navigation, panel captions and optional compositing buffers."""
from PySide6.QtCore import Qt, QSize, QTimer, QPoint, QByteArray
import json
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QTabBar, QToolButton, QComboBox, QGraphicsEffect, QStyle)


DEFAULTS = dict(navigate_tabs=True, multiple_lines=True, tabs_bottom=False, limit_tab_width=False,
    custom_active_font=False, caption_tab_style=False, caption_windows=False, caption_transfer=False,
    caption_ftp=False, caption_system=False, caption_queue=False, caption_tasks=False,
    caption_panes=True, pane_tabs=False, large_icons=False, disable_toolbar_updates=False,
    double_buffer=True)


class WorkspaceTabBar(QTabBar):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def tabSizeHint(self, index):
        size = super().tabSizeHint(index)
        if self.owner.layout_value('limit_tab_width'):
            size.setWidth(min(size.width(), max(60, self.owner.preferences.value('Layout/tab_width', 100, type=int))))
        return size

    def contextMenuEvent(self, event):
        index = self.tabAt(event.pos())
        if index >= 0:
            self.owner.workspace_menu(self.owner.tabs.widget(index), event.globalPos())


class WorkspaceButton(QToolButton):
    def __init__(self, window, workspace):
        super().__init__()
        self.window, self.workspace = window, workspace

    def contextMenuEvent(self, event):
        self.window.workspace_menu(self.workspace, event.globalPos())


class PanelBuffer(QGraphicsEffect):
    def draw(self, painter):
        # An explicit offscreen panel image, on top of Qt's native backing store.
        offset = QPoint()
        pixmap = self.sourcePixmap(Qt.CoordinateSystem.LogicalCoordinates,
            offset, QGraphicsEffect.PixmapPadMode.NoPad)
        painter.drawPixmap(offset, pixmap)


class CaptionPanel(QWidget):
    def __init__(self, content, title):
        super().__init__()
        self.rows = QVBoxLayout(self)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(2)
        self.caption = QLabel(title)
        self.caption.setStyleSheet('font-weight:600; padding:3px 6px;')
        self.caption_tab = QTabBar()
        self.caption_tab.addTab(title)
        self.caption_tab.setExpanding(False)
        self.rows.addWidget(self.caption)
        self.rows.addWidget(self.caption_tab)
        self.rows.addWidget(content, 1)

    def show_caption(self, visible, tab_style):
        self.caption.setVisible(visible and not tab_style)
        self.caption_tab.setVisible(visible and tab_style)


class WorkspaceNavigation(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(2)
        self.signature = None
        self.buttons = []
        self.selector = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self.rebuild)

    def rebuild(self):
        w = self.window
        signature = (tuple(w.tabs.tabText(i) for i in range(w.tabs.count())), w.tabs.currentIndex(),
            self.width(), w.layout_value('navigate_tabs'), w.layout_value('multiple_lines'),
            w.layout_value('limit_tab_width'), w.preferences.value('Layout/tab_width', 100, type=int),
            w.layout_value('custom_active_font'), w.preferences.value('Layout/active_color', '#ff0000', type=str),
            tuple(w.tabs.tabToolTip(i) for i in range(w.tabs.count())))
        if signature == self.signature: return
        self.signature = signature
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self.buttons = []
        self.selector = None
        self.native_bar = None
        if getattr(w, 'header_rows', None) is not None and w.layout_value('navigate_tabs') and not w.layout_value('multiple_lines'):
            bar = self.native_bar = WorkspaceTabBar(w)
            bar.setExpanding(False)
            bar.setTabsClosable(True)
            bar.setUsesScrollButtons(True)
            for index, title in enumerate(signature[0]):
                bar.addTab(title)
                bar.setTabToolTip(index, title + '\n' + w.tabs.tabToolTip(index))
            bar.setCurrentIndex(w.tabs.currentIndex())
            bar.currentChanged.connect(w.tabs.setCurrentIndex)
            bar.tabCloseRequested.connect(w.close_tab)
            bar.setStyleSheet(w.active_tab_style('QTabBar::tab:selected'))
            self.grid.addWidget(bar, 0, 0)
            add = QToolButton()
            add.setText('+')
            add.setToolTip('New workspace')
            add.setAccessibleName('New workspace')
            add.clicked.connect(w.add_workspace)
            self.grid.addWidget(add, 0, 1)
            return

        if not w.layout_value('navigate_tabs'):
            self.selector = QComboBox()
            self.selector.addItems(signature[0])
            self.selector.setCurrentIndex(w.tabs.currentIndex())
            self.selector.currentIndexChanged.connect(w.tabs.setCurrentIndex)
            self.selector.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.selector.customContextMenuRequested.connect(
                lambda point: w.workspace_menu(w.tabs.currentWidget(), self.selector.mapToGlobal(point)))
            self.grid.addWidget(self.selector, 0, 0)
            close = QToolButton()
            close.setText('×')
            close.setToolTip('Close current workspace')
            close.clicked.connect(lambda: w.close_tab(w.tabs.currentIndex()))
            self.grid.addWidget(close, 0, 1)
            row, col = 0, 2
        else:
            row, col, used = 0, 0, 0
            available = max(100, self.width())
            row_boxes = {}
            for index, title in enumerate(signature[0]):
                cell = QWidget()
                box = QHBoxLayout(cell)
                box.setContentsMargins(0, 0, 0, 0)
                box.setSpacing(0)
                button = WorkspaceButton(w, w.tabs.widget(index))
                button.setCheckable(True)
                button.setChecked(index == w.tabs.currentIndex())
                button.setToolTip(title + '\n' + w.tabs.tabToolTip(index))
                width = min(available, max(70, button.fontMetrics().horizontalAdvance(title) + 44))
                if w.layout_value('limit_tab_width'):
                    width = min(width, max(60, w.preferences.value('Layout/tab_width', 100, type=int)))
                button.setText(button.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, max(20, width - 35)))
                button.setStyleSheet(w.active_tab_style('QToolButton:checked'))
                button.clicked.connect(lambda checked=False, i=index: w.tabs.setCurrentIndex(i))
                close = QToolButton()
                close.setText('×')
                close.setToolTip('Close ' + title)
                close.setFixedWidth(22)
                close.clicked.connect(lambda checked=False, i=index: w.close_tab(i))
                box.addWidget(button, 1)
                box.addWidget(close)
                cell.setFixedWidth(width)
                if used and used + width > available:
                    row, col, used = row + 1, 0, 0
                if row not in row_boxes:
                    row_widget = QWidget()
                    row_box = QHBoxLayout(row_widget)
                    row_box.setContentsMargins(0, 0, 0, 0)
                    row_box.setSpacing(2)
                    row_box.setAlignment(Qt.AlignmentFlag.AlignLeft)
                    self.grid.addWidget(row_widget, row, 0)
                    row_boxes[row] = row_box
                row_boxes[row].addWidget(cell)
                used += width + 2
                col += 1
                self.buttons.append(button)
        add = QToolButton()
        add.setText('+')
        add.setToolTip('New workspace')
        add.setAccessibleName('New workspace')
        add.clicked.connect(w.add_workspace)
        if self.buttons and used + add.sizeHint().width() <= available:
            row_boxes[row].addWidget(add)
        else:
            self.grid.addWidget(add, row + 1 if self.buttons else row, 0 if self.buttons else col,
                                alignment=Qt.AlignmentFlag.AlignLeft)


class LayoutOptions:
    def initialize_header_rows(self):
        from .header_rows import HeaderRows
        self.removeToolBar(self.main_toolbar)
        root = self.centralWidget().layout()
        root.removeWidget(self.quick_connect_row)
        self.workspace_panel.rows.removeWidget(self.workspace_navigation)
        self.workspace_panel.rows.removeWidget(self.window_list)
        self.header_rows = HeaderRows(self, [
            ('tools', 'Toolbar', self.main_toolbar),
            ('connection', 'Connection fields', self.quick_connect_row),
            ('tabs', 'Workspace tabs', self.workspace_navigation),
            ('overview', 'Window / FTP / Action list', self.window_list),
        ])
        root.insertWidget(0, self.header_rows)
        self.main_toolbar.show()
        self.workspace_navigation.signature = None
        self.apply_layout_settings()

    def _restore_splitter(self, splitter, key):
        value = self.preferences.value(key, '', type=str)
        if not value or len(value) > 16384:
            return
        try:
            splitter.restoreState(QByteArray(bytes.fromhex(value)))
        except (ValueError, TypeError):
            pass

    def pane_layout_key(self, splitter):
        orientation = 'horizontal' if splitter.orientation() == Qt.Orientation.Horizontal else 'vertical'
        return f'Layout/pane_positions/{self.tabs.indexOf(splitter)}/{orientation}'

    def initialize_pane_positions(self, splitter):
        self._restore_splitter(splitter, self.pane_layout_key(splitter))
        if not getattr(splitter, '_positions_connected', False):
            splitter._positions_connected = True
            splitter.splitterMoved.connect(lambda *_: self.save_panel_layout())

    def initialize_panel_positions(self):
        panels = dict(workspace=self.workspace_panel, logs=self.log_tabs, queue=self.queue_panel)
        try:
            order = json.loads(self.preferences.value('Layout/panel_order', '[]', type=str))
            if isinstance(order, list) and len(order) == 3 and set(order) == set(panels):
                for index, name in enumerate(order):
                    self.work_area.insertWidget(index, panels[name])
        except (ValueError, TypeError):
            pass
        self._restore_splitter(self.work_area, 'Layout/panel_positions')
        self._manual_panel_layout = self.preferences.value('Layout/manual_panel_positions', False, type=bool)
        self._panel_positions_ready = True
        self.work_area.splitterMoved.connect(self.panel_position_moved)
        for i in range(self.tabs.count()):
            self.initialize_pane_positions(self.tabs.widget(i))
        key = self.preferences.value('Layout/active_log', 'ftp', type=str)
        if key in self.log_panels:
            self.log_tabs.setCurrentWidget(self.log_panels[key])

    def panel_position_moved(self, *_):
        self._manual_panel_layout = True
        self.save_panel_layout()

    def save_panel_layout(self):
        if not getattr(self, '_panel_positions_ready', False) or getattr(self, '_smoke_test', False) or getattr(self, '_configuration_restored', False):
            return
        docked = getattr(self, 'docking', None) and self.docking.active
        if docked:
            self.docking.save()
        else:
            panels = {self.workspace_panel: 'workspace', self.log_tabs: 'logs', self.queue_panel: 'queue'}
            self.preferences.setValue('Layout/panel_order', json.dumps([panels[self.work_area.widget(i)] for i in range(3)]))
            self.preferences.setValue('Layout/panel_positions', bytes(self.work_area.saveState()).hex())
        self.preferences.setValue('Layout/manual_panel_positions', self._manual_panel_layout)
        for i in range(self.tabs.count()):
            split = self.tabs.widget(i)
            if sum(split.sizes()):
                self.preferences.setValue(self.pane_layout_key(split), bytes(split.saveState()).hex())
        for key, panel in self.log_panels.items():
            if self.log_tabs.currentWidget() is panel:
                self.preferences.setValue('Layout/active_log', key)
                break
        self.preferences.sync()
        self.save_workspaces()

    def layout_value(self, name):
        return self.preferences.value('Layout/' + name, DEFAULTS[name], type=bool)

    def active_tab_style(self, selector):
        if not self.layout_value('custom_active_font'): return ''
        color = QColor(self.preferences.value('Layout/active_color', '#ff0000', type=str))
        if not color.isValid(): color = QColor('#ff0000')
        return f'{selector} {{ color:{color.name()}; font-weight:bold; }}'

    def initialize_layout(self):
        self.workspace_navigation = WorkspaceNavigation(self)
        self.workspace_panel.rows.insertWidget(2, self.workspace_navigation)
        self.tabs.currentChanged.connect(lambda _: self.workspace_navigation.rebuild())
        self.apply_layout_settings()

    def toolbar_icon_size(self):
        default = 32 if self.layout_value('large_icons') else 24
        return max(16, min(48, self.preferences.value('Layout/toolbar_icon_size', default, type=int)))

    def set_toolbar_icon_size(self, size):
        self.preferences.setValue('Layout/toolbar_icon_size', max(16, min(48, int(size))))
        self.apply_layout_settings()

    def apply_layout_settings(self, *_):
        if not hasattr(self, 'workspace_navigation'): return
        from .font_zoom import install_icon_wheel
        style = self.layout_value('caption_tab_style')
        self.workspace_panel.show_caption(self.layout_value('caption_windows'), style)
        self.queue_panel.show_caption(self.layout_value('caption_queue'), style)
        for key, panel in self.log_panels.items(): panel.show_caption(self.layout_value('caption_' + key), style)
        native = self.layout_value('navigate_tabs') and not self.layout_value('multiple_lines')
        detached = hasattr(self, 'header_rows')
        self.tabs.tabBar().setVisible(native and not detached)
        self.tabs.cornerWidget().setVisible(native and not detached)
        self.workspace_navigation.setVisible(not native or detached)
        from PySide6.QtWidgets import QTabWidget
        bottom = self.layout_value('tabs_bottom')
        self.tabs.setTabPosition(QTabWidget.TabPosition.South if bottom else QTabWidget.TabPosition.North)
        rows = self.workspace_panel.rows
        if not detached:
            rows.removeWidget(self.workspace_navigation)
            rows.insertWidget(rows.count() if bottom else 2, self.workspace_navigation)
        self.tabs.tabBar().setElideMode(Qt.TextElideMode.ElideRight)
        self.tabs.tabBar().setExpanding(False)
        css = self.active_tab_style('QTabBar::tab:selected')
        if self.layout_value('limit_tab_width'):
            width = max(60, self.preferences.value('Layout/tab_width', 100, type=int))
            css += f'QTabBar::tab {{ max-width:{width}px; }}'
        self.tabs.tabBar().setStyleSheet(css)
        self.workspace_navigation.rebuild()
        for i in range(self.tabs.count()):
            split = self.tabs.widget(i)
            for pane in (split.widget(0), split.widget(1)):
                if not hasattr(pane, 'mode_tabs'):
                    pane.caption_tab = QTabBar()
                    pane.caption_tab.setExpanding(False)
                    pane.caption_tab.addTab(pane.title_label.text())
                    pane.layout().insertWidget(1, pane.caption_tab)
                    pane.mode_tabs = QTabBar()
                    pane.mode_tabs.addTab('Local')
                    pane.mode_tabs.addTab('Remote')
                    pane.layout().insertWidget(1, pane.mode_tabs)
                    pane.mode_tabs.currentChanged.connect(lambda index, pane=pane: self._switch_pane_tab(pane, index))
                pane.mode_tabs.blockSignals(True)
                pane.mode_tabs.setCurrentIndex(0 if pane.mode == 'Local' else 1)
                pane.mode_tabs.blockSignals(False)
                pane.mode_tabs.setVisible(self.layout_value('pane_tabs'))
                pane.mode_button.setText(pane.mode)
                pane.mode_button.setVisible(not self.layout_value('pane_tabs'))
                pane.title_label.setVisible(self.layout_value('caption_panes') and not style)
                pane.caption_tab.setTabText(0, pane.title_label.text())
                pane.caption_tab.setVisible(self.layout_value('caption_panes') and style)
                pane.file_toolbar.setIconSize(QSize(self.toolbar_icon_size(), self.toolbar_icon_size()))
                install_icon_wheel(self, pane.file_toolbar)
                for button in pane.file_toolbar.findChildren(QToolButton):
                    button.setIconSize(pane.file_toolbar.iconSize())
        self.main_toolbar.setIconSize(QSize(self.toolbar_icon_size(), self.toolbar_icon_size()))
        install_icon_wheel(self, self.main_toolbar)
        for button in self.main_toolbar.findChildren(QToolButton):
            button.setIconSize(self.main_toolbar.iconSize())
        enabled = not self.layout_value('disable_toolbar_updates')
        if self.main_toolbar.updatesEnabled() != enabled:
            self.main_toolbar.setUpdatesEnabled(enabled)
            if enabled: self.main_toolbar.update()
        buffered = self.layout_value('double_buffer')
        for panel in (self.workspace_panel, self.log_tabs, self.queue_panel):
            if buffered and panel.graphicsEffect() is None: panel.setGraphicsEffect(PanelBuffer(panel))
            elif not buffered and panel.graphicsEffect() is not None: panel.setGraphicsEffect(None)
        if hasattr(self, 'toolbar_visibility'): self.toolbar_visibility.apply()
        from .toolbar_icons import apply_toolbar_icons
        apply_toolbar_icons(self)

    def _switch_pane_tab(self, pane, index):
        pane.change_mode('Local' if index == 0 else 'Remote')
        pane.mode_tabs.blockSignals(True)
        pane.mode_tabs.setCurrentIndex(0 if pane.mode == 'Local' else 1)
        pane.mode_tabs.blockSignals(False)
