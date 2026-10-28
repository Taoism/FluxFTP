"""Build linked Markdown gallery pages and local visual-review contact sheets."""
import json
import re
from collections import defaultdict
from pathlib import Path
from html import escape

ROOT = Path(__file__).resolve().parents[1]
manifest = json.loads((ROOT / 'docs/screenshots/manifest.json').read_text(encoding='utf-8'))
records = manifest['screenshots']
groups = defaultdict(list)
for record in records:
    assert (ROOT / 'docs/screenshots' / record['file']).is_file()
    groups[record['group']].append(record)
gallery = ROOT / 'docs/gallery'
gallery.mkdir(exist_ok=True)
def slug(text): return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')
index = ['# FluxFTP screenshot gallery', '',
    f"{len(records)} screenshots of FluxFTP {manifest['version']}, grouped by feature.", '',
    'These are captures of the actual application widgets, rendered with isolated settings and fictional demonstration data. '
    'Sample listings, queue states, accounts, reports and plans are illustrative; no servers were contacted and no transfers or authorizations were performed. '
    'Screenshots exclude personal sites, passwords and account data. Click an image to open the full-resolution PNG.', '',
    '[Back to README](../README.md) · [User guide](USER_GUIDE.md) · [Screenshot files](screenshots)', '',
    '![Workspace and transfer queue](screenshots/workspace-workspace-and-transfer-queue.png)', '',
    '## Browse by feature', '', '| Feature area | Screenshots |', '| --- | ---: |']
for group, items in groups.items():
    filename = slug(group)+'.md'
    index.append(f'| [{group}](gallery/{filename}) | {len(items)} |')
    page = [f'# {group}', '', '[All screenshots](../SCREENSHOTS.md) · [User guide](../USER_GUIDE.md)', '',
            'Actual FluxFTP widgets with fictional demonstration data. Click any image for full resolution.', '']
    for item in items:
        page += ['## '+item['title'], '']
        if item['caption']: page += [item['caption'], '']
        url = '../screenshots/'+item['file']
        page += [f'<a href="{url}"><img src="{url}" alt="{escape(item["title"], quote=True)}" width="720"></a>', '']
    (gallery/filename).write_text('\n'.join(page), encoding='utf-8')
index += ['', '## Coverage notes', '',
    '- **Protocols:** FTP/FTPS, SFTP, SCP and cloud share the workspace and site properties. SCP setup and SSH/TLS reports have dedicated captures. '
    'Direct FXP and remote relay use the shared queue; protocol support does not imply every server supports every operation.',
    '- **Transfers:** queue controls, active-transfer controls, scheduling, conditional transfer, recovery, search, conflicts, bandwidth, history, verification exports, manual downloads and TFTP.',
    '- **Files and automation:** remote editing, external-editor review, associations, comparison, sync, deployment/rollback controls, watched uploads, scripts and command templates.',
    '- **Configuration:** every Options category and every site-property category is pictured, including SSL runtime management, proxy/data connections, retry/priority rules, notifications, themes and layout.',
    '- **Menus:** the menu captures document additional commands such as file search, remote attributes, selection, checksums and configuration backup. '
    'Disabled placeholder commands are not presented as implemented features.',
    '- Internal services (job model, queue engine, protocol adapters, trust service and storage) have no separate UI; the screenshots show the controls they support.', '',
    '## Reproduce', '',
    'In a Windows source environment with the project dependencies installed:', '', '```powershell',
    'python scripts/capture_gallery.py', 'python scripts/build_screenshot_gallery.py', '```', '',
    'The capture script uses Qt offscreen rendering and temporary application settings. It neither changes user settings nor starts network operations. '
    'The build script generates these pages and contact sheets under the ignored `build/gallery-review` directory.', '']
(ROOT/'docs/SCREENSHOTS.md').write_text('\n'.join(index), encoding='utf-8')

# Contact sheets are review artifacts, not screenshots of a new product screen.
from PySide6.QtGui import QImage, QPainter, QColor, QFont, QFontDatabase
from PySide6.QtCore import Qt, QRect
from PySide6.QtWidgets import QApplication
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
app = QApplication.instance() or QApplication([])
font_path = Path(os.environ.get('WINDIR', 'C:/Windows'))/'Fonts/tahoma.ttf'
if font_path.exists(): QFontDatabase.addApplicationFont(str(font_path))
review = ROOT/'build/gallery-review'
review.mkdir(parents=True, exist_ok=True)
for start in range(0, len(records), 12):
    image = QImage(1500, 1440, QImage.Format.Format_RGB32)
    image.fill(QColor('white'))
    painter = QPainter(image)
    painter.setFont(QFont('Tahoma', 11))
    for offset, record in enumerate(records[start:start+12]):
        x, y = (offset % 3)*500, (offset//3)*360
        shot = QImage(str(ROOT/'docs/screenshots'/record['file']))
        assert not shot.isNull(), record['file']
        shot = shot.scaled(490, 320, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        painter.drawImage(x+(490-shot.width())//2, y, shot)
        painter.drawText(QRect(x+5, y+322, 490, 36), Qt.TextFlag.TextWordWrap, f'{start+offset+1}. {record["group"]}: {record["title"]}')
    painter.end()
    image.save(str(review/f'contact-{start//12+1:02}.png'))
print(f'Built {len(groups)} gallery pages and {(len(records)+11)//12} review sheets for {len(records)} screenshots.')
