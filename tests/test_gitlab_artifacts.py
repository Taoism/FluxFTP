import io
import json
import stat
import zipfile

import pytest

from fluxftp.gitlab_artifacts import GitLabClient, normalize_url, token_id
from fluxftp.queue_metadata import validate_profile


class Response(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *_args): self.close()


def archive_bytes(entries):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as bundle:
        for name, content, mode in entries:
            info = zipfile.ZipInfo(name)
            if mode is not None:
                info.external_attr = mode << 16
            bundle.writestr(info, content)
    return data.getvalue()


def test_gitlab_url_and_token_names_are_normalized():
    assert normalize_url("https://gitlab.example/group/") == "https://gitlab.example/group"
    assert token_id("https://gitlab.example/") == token_id("https://GITLAB.example")
    for url in ("http://gitlab.example", "https://user:secret@gitlab.example", "https://gitlab.example/?token=x"):
        with pytest.raises(ValueError, match="HTTPS base URL"):
            normalize_url(url)


def test_pipeline_and_job_lookup_filters_success_and_encodes_project(monkeypatch):
    client = GitLabClient("https://gitlab.example", "test-token")
    calls = []
    responses = {
        "projects/group%2Fproject/pipelines": [
            {"id": 9, "status": "success", "ref": "release"},
            {"id": 8, "status": "failed", "ref": "release"},
            {"id": 7, "status": "success", "ref": "other"},
        ],
        "projects/group%2Fproject/pipelines/9/jobs": [
            {"id": 5, "name": "package", "status": "success", "artifacts_file": {"filename": "artifacts.zip"}},
            {"id": 4, "name": "failed", "status": "failed", "artifacts_file": {"filename": "failed.zip"}},
            {"id": 3, "name": "no artifact", "status": "success", "artifacts_file": None},
        ],
    }
    def fake_json(endpoint, query=None):
        calls.append((endpoint, query))
        return responses[endpoint]
    monkeypatch.setattr(client, "_json", fake_json)

    project_id, pipelines = client.pipelines("group/project", "release")
    jobs = client.jobs(project_id, pipelines[0]["id"])
    assert project_id == "group%2Fproject"
    assert [item["id"] for item in pipelines] == [9]
    assert [item["id"] for item in jobs] == [5]
    assert calls[0][1]["status"] == "success" and calls[0][1]["ref"] == "release"
    assert calls[1][0] == "projects/group%2Fproject/pipelines/9/jobs"


def test_download_artifact_extracts_to_owned_cache_directory(tmp_path, monkeypatch):
    client = GitLabClient("https://gitlab.example", "test-token")
    payload = archive_bytes([("dist/index.html", b"hello", stat.S_IFREG)])
    monkeypatch.setattr(client, "_open", lambda *_args, **_kwargs: (Response(payload), 1024))

    destination = client.download_artifact(42, 17, tmp_path / "cache")

    assert destination.parent == tmp_path / "cache"
    assert destination.name.startswith("pipeline-17-")
    assert (destination / "dist" / "index.html").read_bytes() == b"hello"
    assert list((tmp_path / "cache").iterdir()) == [destination]


@pytest.mark.parametrize("entry", [
    ("../escape.txt", b"bad", stat.S_IFREG),
    ("shortcut", b"target", stat.S_IFLNK),
])
def test_artifact_import_rejects_unsafe_entries(tmp_path, entry):
    archive = tmp_path / "artifact.zip"
    archive.write_bytes(archive_bytes([entry]))
    destination = tmp_path / "extracted"
    destination.mkdir()

    with pytest.raises(ValueError, match="unsafe path|outside its extraction folder|Symbolic links"):
        GitLabClient._extract(archive, destination)
    assert list(destination.iterdir()) == []


def test_artifact_import_rejects_case_insensitive_duplicate_paths(tmp_path, monkeypatch):
    client = GitLabClient("https://gitlab.example", "test-token")
    payload = archive_bytes([("Readme.txt", b"one", stat.S_IFREG),
                             ("README.TXT", b"two", stat.S_IFREG)])
    monkeypatch.setattr(client, "_open", lambda *_args, **_kwargs: (Response(payload), 1024))
    cache = tmp_path / "cache"

    with pytest.raises(ValueError, match="duplicate paths"):
        client.download_artifact(42, 17, cache)
    assert list(cache.iterdir()) == []


def test_deployment_profile_requires_consistent_gitlab_provenance(tmp_path):
    profile = dict(name="CI build", source=str(tmp_path), site="site", destination="/www",
        excludes=[], verification="Size only", gitlab={
            "server": "https://gitlab.example", "project_id": 42, "project_path": "group/project",
            "ref": "release", "pipeline_id": 9,
            "pipeline_url": "https://gitlab.example/group/project/-/pipelines/9",
            "commit_sha": "a" * 40, "job_id": 17, "job_name": "package",
            "artifact_filename": "artifacts.zip",
        })
    assert validate_profile(profile) is profile
    profile["gitlab"]["pipeline_url"] = "https://other.example/group/project/-/pipelines/9"
    with pytest.raises(ValueError, match="does not match"):
        validate_profile(profile)


def test_deployment_editor_exposes_gitlab_pipeline_import():
    from PySide6.QtWidgets import QApplication, QPushButton
    from fluxftp.app import Window
    from fluxftp.deployment_ui import ProfileEditor
    from fluxftp.gitlab_artifact_dialog import GitLabArtifactDialog

    app = QApplication.instance() or QApplication([])
    window = Window(smoke=True)
    editor = None
    dialog = None
    try:
        editor = ProfileEditor(window)
        labels = [button.text() for button in editor.findChildren(QPushButton)]
        assert "Import GitLab pipeline artifact…" in labels
        dialog = GitLabArtifactDialog(window, editor)
        assert dialog.windowTitle() == "Import GitLab Pipeline Artifact"
        assert dialog.server.text() == "https://gitlab.com"
        assert dialog.project_input.placeholderText() == "group/project or numeric project ID"
    finally:
        if dialog:
            dialog.close()
            dialog.deleteLater()
        if editor:
            editor.close()
            editor.deleteLater()
        window.close()
        app.processEvents()
