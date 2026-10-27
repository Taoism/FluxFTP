from PySide6.QtCore import Qt, QSize
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTabWidget
from fluxftp.layout_options import DEFAULTS, PanelBuffer
from fluxftp.preferences import PreferencesDialog
from test_queue import window


def test_defaults_and_persistence(window):
    dialog = PreferencesDialog(window)
    assert {key: check.isChecked() for key, check in dialog.layout_checks.items()} == DEFAULTS
    assert dialog.tab_width.value() == 100 and not dialog.tab_width.isEnabled()
    assert not dialog.active_tab_color.isEnabled()
    dialog.layout_checks['limit_tab_width'].setChecked(True)
    dialog.tab_width.setValue(120)
    dialog.layout_checks['custom_active_font'].setChecked(True)
    assert window.preferences.value('Layout/tab_width', type=int) == 120
    assert dialog.active_tab_color.isEnabled()
    dialog.close()


def test_wrapping_selection_closing_width_and_bottom(window):
    window.preferences.setValue('Layout/limit_tab_width', True)
    window.preferences.setValue('Layout/tab_width', 100)
    for _ in range(10): window.add_workspace()
    window.show()
    window.resize(900, 750)
    QTest.qWait(50)
    window.apply_layout_settings()
    nav = window.workspace_navigation
    # The compact '+' button may fit on the second row; require actual wrapping.
    assert len(nav.buttons) == 11 and nav.grid.rowCount() > 1
    assert all(button.parentWidget().width() <= 100 for button in nav.buttons)
    nav.buttons[0].click()
    assert window.tabs.currentIndex() == 0
    window.preferences.setValue('Layout/tabs_bottom', True)
    window.apply_layout_settings()
    assert nav.parentWidget() is window.header_rows.rows['tabs']
    window.close_tab(10)
    assert window.tabs.count() == 10 and len(nav.buttons) == 10


def test_native_tabs_and_dropdown_navigation(window):
    window.add_workspace()
    window.preferences.setValue('Layout/multiple_lines', False)
    window.preferences.setValue('Layout/tabs_bottom', True)
    window.preferences.setValue('Layout/custom_active_font', True)
    window.preferences.setValue('Layout/limit_tab_width', True)
    window.preferences.setValue('Layout/tab_width', 100)
    window.preferences.setValue('Layout/active_color', '#123456')
    window.apply_layout_settings()
    assert window.tabs.tabBar().isHidden()
    assert not window.workspace_navigation.isHidden()
    assert window.workspace_navigation.native_bar is not None
    assert window.tabs.tabPosition() == QTabWidget.TabPosition.South
    assert '#123456' in window.tabs.tabBar().styleSheet()
    window.show()
    QTest.qWait(20)
    assert window.workspace_navigation.native_bar.tabRect(0).width() <= 100
    window.preferences.setValue('Layout/navigate_tabs', False)
    window.apply_layout_settings()
    assert window.tabs.tabBar().isHidden()
    window.workspace_navigation.selector.setCurrentIndex(0)
    assert window.tabs.currentIndex() == 0


def test_captions_and_tab_style(window):
    assert window.queue_panel.caption.isHidden()
    assert not window.current_panes()[0].title_label.isHidden()
    for name in ('queue', 'windows', 'ftp', 'system', 'transfer', 'tasks'):
        window.preferences.setValue('Layout/caption_' + name, True)
    window.apply_layout_settings()
    panels = [window.queue_panel, window.workspace_panel, *window.log_panels.values()]
    assert all(not panel.caption.isHidden() and panel.caption_tab.isHidden() for panel in panels)
    window.preferences.setValue('Layout/caption_tab_style', True)
    window.apply_layout_settings()
    assert all(panel.caption.isHidden() and not panel.caption_tab.isHidden() for panel in panels)
    assert not window.current_panes()[0].caption_tab.isHidden()
    window.preferences.setValue('Layout/caption_panes', False)
    window.apply_layout_settings()
    assert window.current_panes()[0].caption_tab.isHidden() and window.current_panes()[0].title_label.isHidden()


def test_pane_tabs_busy_guard_and_large_icons(window):
    window.preferences.setValue('Layout/pane_tabs', True)
    window.preferences.setValue('Layout/large_icons', True)
    window.apply_layout_settings()
    pane = window.current_panes()[0]
    assert pane.mode_tabs.currentIndex() == 1 and pane.mode_button.isHidden()
    pane.mode_tabs.setCurrentIndex(0)
    assert pane.mode == 'Local'
    pane.busy = True
    pane.mode_tabs.setCurrentIndex(1)
    assert pane.mode == 'Local' and pane.mode_tabs.currentIndex() == 0
    pane.busy = False
    pane.mode_tabs.setCurrentIndex(1)
    assert pane.mode == 'Remote'
    assert pane.file_toolbar.iconSize() == QSize(32, 32)
    window.add_workspace()
    assert not window.current_panes()[0].mode_tabs.isHidden()


def test_toolbar_paint_and_panel_buffer_switches(window):
    window.show()
    QApplication.processEvents()
    assert isinstance(window.workspace_panel.graphicsEffect(), PanelBuffer)
    assert not window.grab().isNull()
    window.preferences.setValue('Layout/disable_toolbar_updates', True)
    window.preferences.setValue('Layout/double_buffer', False)
    window.apply_layout_settings()
    assert not window.main_toolbar.updatesEnabled()
    assert window.workspace_panel.graphicsEffect() is None
    assert window.queue_panel.graphicsEffect() is None
    window.preferences.setValue('Layout/disable_toolbar_updates', False)
    window.preferences.setValue('Layout/double_buffer', True)
    window.apply_layout_settings()
    assert window.main_toolbar.updatesEnabled()
    assert not window.grab().isNull()
