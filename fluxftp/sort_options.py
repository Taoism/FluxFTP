"""Per-pane global sorting and independent file/folder overrides."""
from datetime import datetime, timedelta
import re


def date_key(value):
    text = value.strip()
    if text.isdigit() and len(text) <= 10:
        try: return (1, datetime.fromtimestamp(int(text)))
        except (ValueError, OSError, OverflowError): pass
    for pattern, candidate in (
        ('%Y%m%d%H%M%S', text.split('.')[0]),
        ('%Y-%m-%d %H:%M:%S', text[:19]),
        ('%Y-%m-%d %H:%M', text[:16]),
        ('%m-%d-%y %I:%M%p', text),
        ('%m/%d/%y %I:%M%p', text),
        ('%m-%d-%Y %I:%M%p', text),
        ('%b %d %Y', text),
    ):
        try: return (1, datetime.strptime(candidate, pattern))
        except ValueError: pass
    # Unix LIST omits the year for recent entries. Infer it consistently with ls.
    if re.fullmatch(r'[A-Za-z]{3}\s+\d{1,2}\s+\d{1,2}:\d{2}', text):
        now = datetime.now()
        try:
            stamp = datetime.strptime(f'{text} {now.year}', '%b %d %H:%M %Y')
            if stamp > now + timedelta(days=1): stamp = stamp.replace(year=now.year - 1)
            return (1, stamp)
        except ValueError: pass
    return (0, datetime.min)


def sorted_entries(entries, settings, location, global_key='Name', descending=False):
    def group(items, kind):
        prefix = f'Sort/{location}_{kind}_'
        key = settings.value(prefix + 'key', 'Global', type=str)
        reverse = descending if key == 'Global' else settings.value(prefix + 'descending', False, type=bool)
        key = global_key if key == 'Global' else key
        function = {'Size': lambda e: e.size, 'Date': lambda e: date_key(e.modified)}.get(key, lambda e: e.name.casefold())
        # Names break ties consistently, independent of the requested direction.
        items = sorted(items, key=lambda e: (e.name.casefold(), e.name))
        return sorted(items, key=function, reverse=reverse)
    separate = settings.value('Sort/separate_links', False, type=bool)
    folders = [e for e in entries if e.is_dir and not (separate and e.is_link)]
    links = [e for e in entries if separate and e.is_link]
    files = [e for e in entries if not e.is_dir and not (separate and e.is_link)]
    return group(folders, 'folder') + group(links, 'file') + group(files, 'file')
