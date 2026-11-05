"""Offline searchable viewer for the shared FluxFTP Help Center."""
from html.parser import HTMLParser
import json
from pathlib import Path
import sys

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLineEdit, QListWidget, QTextBrowser, QSplitter


def help_root():
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
    return root / 'docs' / 'help'


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


class HelpDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle('FluxFTP Help')
        self.resize(1100, 760)
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search help topics and instructions…')
        self.search.setAccessibleName('Search help')
        layout.addWidget(self.search)
        split = QSplitter()
        self.index = QListWidget()
        self.index.setAccessibleName('Help topics')
        self.content = QTextBrowser()
        self.content.setAccessibleName('Help article')
        self.content.setOpenExternalLinks(False)
        self.content.setOpenLinks(False)
        self.content.anchorClicked.connect(self.follow_link)
        split.addWidget(self.index)
        split.addWidget(self.content)
        split.setSizes([280, 820])
        layout.addWidget(split, 1)
        self.root = help_root()
        try:
            self.topics = json.loads((self.root / 'manifest.json').read_text(encoding='utf-8'))['topics']
            for topic in self.topics:
                article = (self.root / topic['file']).read_text(encoding='utf-8')
                parser = _Text()
                parser.feed(article)
                topic['_html'] = article
                topic['_text'] = ' '.join(parser.parts).casefold()
        except (OSError, ValueError, KeyError):
            self.topics = [dict(title='Help unavailable', summary='The packaged Help Center is missing. Reinstall FluxFTP.', keywords='', _html='<h1>Help unavailable</h1><p>The packaged Help Center is missing. Reinstall FluxFTP.</p>', _text='')]
        self.index.currentRowChanged.connect(self.select)
        self.search.textChanged.connect(self.filter)
        self.filter()

    def filter(self):
        query = self.search.text().casefold().split()
        self.filtered = [topic for topic in self.topics if all(word in (topic['title'] + ' ' + topic.get('summary', '') + ' ' + topic.get('keywords', '') + ' ' + topic['_text']).casefold() for word in query)]
        self.index.clear()
        self.index.addItems([topic['title'] for topic in self.filtered])
        if self.filtered:
            self.index.setCurrentRow(0)
        else:
            self.content.setPlainText('No matching help topics.')

    def select(self, row):
        if 0 <= row < len(self.filtered):
            topic = self.filtered[row]
            self.content.setHtml(topic['_html'])
            self.content.document().setBaseUrl(QUrl.fromLocalFile(str((self.root / topic['file']).resolve())))

    def follow_link(self, url):
        if url.scheme() in ('http', 'https', 'mailto'):
            QDesktopServices.openUrl(url)
        elif url.fragment():
            self.search.clear()
            wanted = url.fragment()
            for row, topic in enumerate(self.filtered):
                if topic['id'] == wanted:
                    self.index.setCurrentRow(row)
                    return


def show_help(window):
    if not getattr(window, 'help_dialog', None):
        window.help_dialog = HelpDialog(window)
    window.help_dialog.show()
    window.help_dialog.raise_()
    window.help_dialog.activateWindow()
