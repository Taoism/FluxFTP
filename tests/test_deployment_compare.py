from fluxftp.deployment_compare import compare_revisions, text_diff
from fluxftp.deployments import _text_snapshot_candidate


def file_record(path, digest):
    return {'relative': path, 'source_sha256': digest}


def revision(files, source_text=None):
    return {'plan': {'files': files}, 'source_text': source_text or {}}


def test_compare_revisions_lists_same_changed_and_plan_only_files():
    first = revision([file_record('index.html', 'a' * 64), file_record('old.ini', 'b' * 64)])
    second = revision([file_record('index.html', 'c' * 64), file_record('new.json', 'd' * 64)])
    rows, _first_text, _second_text = compare_revisions(first, second)
    assert [(row['relative'], row['status']) for row in rows] == [
        ('index.html', 'Changed'), ('new.json', 'Only in second deployment plan'),
        ('old.ini', 'Only in first deployment plan')]


def test_revision_text_diff_is_unified_and_missing_snapshots_are_explained():
    assert text_diff('settings.ini', 'mode=old\n', 'mode=new\n').splitlines() == [
        '--- first/settings.ini', '+++ second/settings.ini', '@@ -1 +1 @@', '-mode=old', '+mode=new']
    assert 'unavailable' in text_diff('settings.ini', None, 'mode=new')


def test_text_snapshot_extensions_and_size_are_bounded():
    assert _text_snapshot_candidate('site/config.yaml', 100)
    assert _text_snapshot_candidate('site/.env.example', 100)
    assert _text_snapshot_candidate('site/Component.vue', 100)
    assert _text_snapshot_candidate('site/app.properties', 100)
    assert not _text_snapshot_candidate('image.png', 100)
    assert not _text_snapshot_candidate('site/config.json', 256 * 1024 + 1)
