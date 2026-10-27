"""Populate large directory views in short, pane-owned GUI batches."""
import time
from PySide6.QtCore import QTimer, Qt, QSignalBlocker
from PySide6.QtWidgets import QTreeWidgetItem
from PySide6.QtGui import QBrush, QColor
from .site_options import SiteSettings
from .sort_options import sorted_entries


def populate(pane, entries, finished):
    previous = getattr(pane, '_listing_timer', None)
    if previous:
        previous.stop()
        previous.deleteLater()
    settings = SiteSettings(pane.window.preferences, pane.site if pane.mode == 'Remote' else None)
    server_order = pane.mode == 'Remote' and settings.value('Connection/list_server_order', False, type=bool)
    pane.tree.header().setSortIndicatorShown(not server_order)
    ordered = entries if server_order else sorted_entries(entries, pane.window.preferences,
                                                         pane.mode.lower(), pane.sort_key, pane.sort_descending)
    batch_size = max(100, min(2000, settings.value('Connection/list_batch_size', 300, type=int)))
    iterator, count = iter(ordered), len(entries)
    from .transfer_options import TransferOptions
    from .highlight_rules import highlight_rules, highlight_color
    options = TransferOptions.from_settings(pane.window.preferences)
    try:
        rules = () if pane.window.preferences.value('Display/highlights_disabled', False, type=bool) else highlight_rules(pane.window.preferences.value('Display/highlight_rules', '[]', type=str))
    except ValueError:
        rules = ()
    loaded, generation = 0, pane.generation
    asynchronous = max(count, pane.tree.topLevelItemCount()) > 2000
    clearing = bool(pane.tree.topLevelItemCount())
    pane.tree.setProperty('marksEmpty', True)
    pane.busy = asynchronous
    if asynchronous: pane.tree.setEnabled(False)
    timer = QTimer(pane)
    pane._listing_timer = timer
    def batch():
        nonlocal loaded, clearing
        if generation != pane.generation:
            timer.stop()
            timer.deleteLater()
            pane._listing_timer = None
            pane.tree.setEnabled(True)
            pane.busy = False
            return
        started, rows = time.monotonic(), []
        if clearing:
            with QSignalBlocker(pane.tree):
                while pane.tree.topLevelItemCount():
                    old = pane.tree.takeTopLevelItem(pane.tree.topLevelItemCount() - 1)
                    del old
                    if asynchronous and time.monotonic() - started >= .008: break
            clearing = bool(pane.tree.topLevelItemCount())
            if clearing:
                pane.status.setText('Loading folder… replacing previous listing')
                return
        while loaded < count:
            entry = next(iterator)
            row = QTreeWidgetItem([entry.name, 'Folder' if entry.is_dir else pane.window.file_size(entry.size),
                                   entry.modified, entry.attributes or ('L' if entry.is_link else 'D' if entry.is_dir else 'A'),
                                   entry.user, entry.group])
            row.setData(0, Qt.ItemDataRole.UserRole, entry)
            row.setFlags(row.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            reason = options.skip_reason(entry.name, entry.is_dir, entry.size)
            foreground = options.skip_color if reason else highlight_color(entry.name, rules)
            if foreground:
                brush = QBrush(QColor(foreground))
                for column in range(row.columnCount()): row.setForeground(column, brush)
            if reason: row.setToolTip(0, f'Skip List: {reason}')
            rows.append(row)
            loaded += 1
            if asynchronous and (len(rows) >= batch_size or time.monotonic() - started >= .008): break
        pane.tree.addTopLevelItems(rows)
        needle = pane.filter.text().casefold()
        if needle:
            for row in rows: row.setHidden(needle not in row.text(0).casefold())
        if loaded == count:
            timer.stop()
            timer.deleteLater()
            pane._listing_timer = None
            pane.busy = False
            pane.tree.setEnabled(True)
            finished()
        else:
            pane.status.setText(f'Loading folder… {loaded:,} / {count:,} items')
    timer.timeout.connect(batch)
    if asynchronous: timer.start(1)
    else: batch()
