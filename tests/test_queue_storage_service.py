from pathlib import Path
from threading import Event
import json
import subprocess
import sys
import pytest
from fluxftp.connection_model import Cancelled
from fluxftp.queue_storage import DiskBuffer, StorageSnapshot, read_queue, write_queue, import_file


def descriptor(tmp_path, index=1):
    return dict(job_id=f'{index:032x}', source_site=None, destination_site=None,
        src=str(tmp_path/f'source{index}'), dst=str(tmp_path/f'target{index}'), name=f'file{index}',
        directory=False, resume=False, workspace=0, state='queued', size=3)


def test_full_storage_roundtrip_without_qt(tmp_path):
    data = descriptor(tmp_path)
    path = tmp_path/'input.json'
    write_queue(path, [data])
    code = "from fluxftp.queue_storage import read_queue, write_queue, import_file; from threading import Event; import sys; items=import_file(sys.argv[1],Event()); write_queue(sys.argv[2],items); assert read_queue(sys.argv[2])==items; assert not any(n.startswith('PySide') for n in sys.modules)"
    result = subprocess.run([sys.executable, '-c', code, str(path), str(tmp_path/'output.json')],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_pinned_spool_snapshot_survives_removal_and_owner_close(tmp_path):
    spool = DiskBuffer()
    for i in (1, 2): spool.append(descriptor(tmp_path, i))
    path = Path(spool.path)
    with StorageSnapshot() as snapshot:
        snapshot.add_buffer(spool, dict(workspace=3))
        assert snapshot.count == 2
        spool.take(2)
        spool.close()
        values = list(snapshot)
        assert len(values) == 2 and all(item['workspace'] == 3 for item in values)
        write_queue(tmp_path/'saved.json', snapshot)
        assert {item['job_id'] for item in read_queue(tmp_path/'saved.json')} == {f'{i:032x}' for i in (1, 2)}
    assert not path.exists()


def test_cancelled_atomic_save_preserves_existing_file(tmp_path):
    path = tmp_path/'saved.json'
    write_queue(path, [descriptor(tmp_path)])
    before = path.read_bytes()
    cancel = Event()
    def incoming():
        yield descriptor(tmp_path, 2)
        cancel.set()
        yield descriptor(tmp_path, 3)
    with pytest.raises(Cancelled): write_queue(path, incoming(), cancel)
    assert path.read_bytes() == before and not list(tmp_path.glob('*.tmp'))


def test_import_rejects_overlapping_paths_and_duplicate_identity(tmp_path):
    path = tmp_path/'bad.json'
    item = descriptor(tmp_path)
    item['dst'] = item['src']
    write_queue(path, [item])
    with pytest.raises(ValueError, match='overlap'): import_file(path, Event())
    write_queue(path, [item, item])
    with pytest.raises(ValueError, match='Duplicate'): read_queue(path)


def test_guarded_metadata_validation_does_not_import_desktop(tmp_path):
    fingerprint = dict(directory=False, link=False, size=3, modified='1')
    watch = dict(id='watch', relative='file', fingerprint=fingerprint)
    deployment = dict(id='a'*32, rollback=False,
        profile=dict(name='deploy', source=str(tmp_path), site='remote', destination='/target', excludes=[], verification='Size only'),
        files=[dict(relative='file', source=fingerprint, destination=None, source_sha256='0'*64)])
    path = tmp_path/'metadata.json'
    path.write_text(json.dumps(dict(watch=watch, deployment=deployment)))
    code = "from fluxftp.queue_metadata import validate_tag, validate_job; import json,sys; data=json.load(open(sys.argv[1])); validate_tag(data['watch']); validate_job(data['deployment']); assert not any(n.startswith('PySide') for n in sys.modules)"
    result = subprocess.run([sys.executable, '-c', code, str(path)], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
