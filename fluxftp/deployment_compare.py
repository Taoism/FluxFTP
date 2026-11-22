"""Bounded comparison of deployment change sets and captured text snapshots."""
import difflib


MAX_DIFF_BYTES = 1_000_000


def compare_revisions(first, second):
    """Compare changed-file plans without treating absence from a plan as deletion."""
    old_files = {item['relative']: item for item in first['plan']['files']}
    new_files = {item['relative']: item for item in second['plan']['files']}
    old_text = first.get('source_text', {})
    new_text = second.get('source_text', {})
    rows = []
    for relative in sorted(old_files.keys() | new_files.keys(), key=str.casefold):
        old_item, new_item = old_files.get(relative), new_files.get(relative)
        if old_item and new_item:
            old_digest, new_digest = old_item['source_sha256'], new_item['source_sha256']
            status = 'Same content' if old_digest == new_digest else 'Changed'
        elif old_item:
            status, old_digest, new_digest = 'Only in first deployment plan', old_item['source_sha256'], ''
        else:
            status, old_digest, new_digest = 'Only in second deployment plan', '', new_item['source_sha256']
        rows.append(dict(relative=relative, status=status, first_sha256=old_digest,
                         second_sha256=new_digest))
    return rows, old_text, new_text


def text_diff(relative, first_text, second_text):
    """Return a unified UTF-8 text diff, bounded before display."""
    if not isinstance(first_text, str) or not isinstance(second_text, str):
        return 'A text snapshot is unavailable for one or both revisions.'
    lines = difflib.unified_diff(first_text.splitlines(keepends=True), second_text.splitlines(keepends=True),
        fromfile='first/' + relative, tofile='second/' + relative)
    result = ''.join(lines)
    encoded = result.encode('utf-8')
    if len(encoded) > MAX_DIFF_BYTES:
        result = encoded[:MAX_DIFF_BYTES].decode('utf-8', 'ignore')
        return result + '\n… diff truncated at 1 MB …'
    return result or 'No text changes.'
