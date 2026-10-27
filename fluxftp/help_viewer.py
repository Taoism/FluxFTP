"""Offline searchable help, sourced from the packaged project guide."""
from pathlib import Path
import re
import sys
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLineEdit, QListWidget, QTextBrowser, QSplitter, QLabel


def guide_path():
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
    return root / 'README.md'


def topics(text):
    # Release/development history remains in the document but is not the user index.
    text = text.split('<a id="release-history">')[0]
    blocks = re.split(r'(?m)^(?=## )', text)
    result = []
    for block in blocks:
        if not block.strip():
            continue
        lines = block.splitlines()
        body = re.sub(r'<a id="[^"]+"></a>|---', '', '\n'.join(lines[1:])).strip()
        if body:
            result.append((lines[0].lstrip('# ').strip(), block))
    return result


class HelpDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle('FluxFTP Help')
        self.resize(1040, 740)
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search help topics and instructions…')
        self.search.setAccessibleName('Search help')
        layout.addWidget(self.search)
        split = QSplitter()
        self.index = QListWidget()
        self.index.setAccessibleName('Help index')
        self.content = QTextBrowser()
        self.content.setAccessibleName('Help article')
        self.content.setOpenExternalLinks(True)
        self.content.setOpenLinks(False)
        self.content.anchorClicked.connect(self.follow_link)
        split.addWidget(self.index)
        split.addWidget(self.content)
        split.setSizes([270, 770])
        layout.addWidget(split, 1)
        try:
            text = guide_path().read_text('utf-8')
            self.topics = topics(text)
            self.anchors = dict(re.findall(r'<a id="([^"]+)"></a>\s*## (.+)', text))
        except OSError:
            self.topics = [('Help unavailable', 'The packaged README.md is missing. Reinstall the current FluxFTP release.')]
            self.anchors = {}
        self.index.currentRowChanged.connect(self.select)
        self.search.textChanged.connect(self.filter)
        self.filter()

    def filter(self):
        query = self.search.text().casefold().split()
        self.filtered = [(title, body) for title, body in self.topics if all(word in body.casefold() for word in query)]
        self.index.clear()
        self.index.addItems([title for title, _ in self.filtered])
        if self.filtered:
            self.index.setCurrentRow(0)
        else:
            self.content.setPlainText('No matching help topics.')

    def select(self, row):
        if 0 <= row < len(self.filtered):
            self.content.setMarkdown(self.filtered[row][1])

    def follow_link(self, url):
        if url.scheme() in ('http', 'https'):
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(url)
        elif url.fragment():
            anchor = url.fragment()
            self.search.clear()
            target = self.anchors.get(anchor)
            for index, (title, body) in enumerate(self.filtered):
                slug = re.sub(r'[^a-z0-9 -]', '', title.casefold()).replace(' ', '-')
                if slug == anchor or title == target:
                    self.index.setCurrentRow(index)
                    return
                if f'<a id="{anchor}">' in body:
                    # Empty section headings are omitted from the index.
                    self.index.setCurrentRow(min(index + 1, len(self.filtered) - 1))
                    return


def show_help(window):
    if not getattr(window, 'help_dialog', None):
        window.help_dialog = HelpDialog(window)
    window.help_dialog.show()
    window.help_dialog.raise_()
    window.help_dialog.activateWindow()
