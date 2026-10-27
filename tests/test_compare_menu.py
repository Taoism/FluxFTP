from PySide6.QtCore import Qt
from fluxftp.core import Entry
from test_queue import window


def populate(window):
    left, right = window.current_panes()
    left.change_mode('Local')
    right.change_mode('Local')
    left.show_entries(([Entry('same', False, 1), Entry('unique', False, 2),
                        Entry('release', False, 3), Entry('folder', True, 0)], ''))
    right.show_entries(([Entry('same', False, 9), Entry('release.ext', False, 3),
                         Entry('folder', False, 0)], ''))
    return left, right


def rows(pane):
    return {pane.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole).name: pane.tree.topLevelItem(i)
            for i in range(pane.tree.topLevelItemCount())}


def test_compare_options_and_selection(window):
    left, right = populate(window)
    menu = window.compare_commands
    assert [a.text() if not a.isSeparator() else '---' for a in menu.menu.actions()] == [
        'Compare name only', 'Compare name and size', '---', 'Select compare result',
        '---', 'Hide same files/folders', '---', 'Partial Match']
    assert menu.actions['Compare name only'].isChecked()
    assert menu.actions['Hide same files/folders'].isChecked()
    window.compare_contents()
    assert rows(left)['same'].isHidden()
    assert not rows(left)['folder'].isHidden()
    menu.actions['Compare name and size'].trigger()
    assert not rows(left)['same'].isHidden()
    assert not menu.actions['Compare name only'].isChecked()
    menu.actions['Partial Match'].trigger()
    assert rows(left)['release'].isHidden()
    assert rows(right)['release.ext'].isHidden()
    menu.actions['Hide same files/folders'].trigger()
    assert not rows(left)['release'].isHidden()
    left.filter.setText('unique')
    menu.actions['Select compare result'].trigger()
    assert [r.data(0, Qt.ItemDataRole.UserRole).name for r in left.tree.selectedItems()] == ['unique']
    assert window.preferences.value('Compare/partial', type=bool)


def test_listing_change_clears_comparison_and_filter_survives(window):
    left, right = populate(window)
    window.compare_contents()
    left.filter.setText('same')
    assert rows(left)['same'].isHidden()
    right.show_entries(([], ''))
    assert not rows(left)['same'].isHidden()
    assert rows(left)['unique'].isHidden()


def test_busy_comparison_does_not_change_rows(window):
    left, right = populate(window)
    left.busy = True
    window.compare_contents()
    assert not rows(left)['same'].isHidden()
    assert 'finish loading' in window.statusBar().currentMessage()
    left.busy = False
