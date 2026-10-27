"""Conservative removal of empty remote leftovers during folder transfers."""
import posixpath
from pathlib import Path


def remove_missing_zero(source, destination, src, dst, options, cancel):
    from .core import safe_name, Cancelled
    def source_names():
        if source and source.ftp:
            return {name for name, _ in source.ftp.mlsd(src)}
        if source:
            return {entry.name for entry in source.list(src)}
        return {entry.name for entry in Path(src).iterdir()}
    try:
        names = source_names()
    except Exception as error:
        destination.note('Skipped zero-byte cleanup: source listing unavailable: ' + str(error))
        return
    route = 'FXP' if source else 'Upload'
    converted = {options.filename(name, route) for name in names}
    for entry in destination.list(dst):
        if cancel.is_set(): raise Cancelled('Cancelled')
        safe_name(entry.name)
        if entry.is_dir or entry.is_link or entry.name in converted:
            continue
        path = posixpath.join(dst, entry.name)
        if entry.size == 0 and destination.size(path) == 0:
            if entry.name not in {options.filename(name, route) for name in source_names()}:
                if cancel.is_set(): raise Cancelled('Cancelled')
                destination.remove(path)
                destination.note('Removed empty target with no source: ' + path)
