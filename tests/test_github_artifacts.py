import io
import stat
import zipfile

import pytest

from fluxftp.github_artifacts import GitHubActionsClient, repository_name, token_id
from fluxftp.queue_metadata import validate_profile


class Response(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *_args): self.close()


def zip_bytes():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        info = zipfile.ZipInfo("dist/app.txt")
        info.external_attr = stat.S_IFREG << 16
        archive.writestr(info, b"github artifact")
    return output.getvalue()


def test_repository_name_and_github_token_key():
    assert repository_name("https://github.com/FluxFTP/FluxFTP.git") == "FluxFTP/FluxFTP"
    assert repository_name("owner/repo") == "owner/repo"
    assert token_id().startswith("github-actions-api-token-v1:")
    with pytest.raises(ValueError, match="owner/repository"):
        repository_name("https://example.test/owner/repo")


def test_github_workflow_run_and_artifact_lookup(monkeypatch):
    client = GitHubActionsClient("token")
    calls = []
    def fake_json(endpoint, query=None):
        calls.append((endpoint, query))
        if endpoint == "/repos/owner/repo":
            return {"id": 7, "full_name": "owner/repo", "html_url": "https://github.com/owner/repo"}
        if endpoint == "/repos/owner/repo/actions/runs":
            return {"workflow_runs": [
                {"id": 11, "head_branch": "release", "conclusion": "success"},
                {"id": 10, "head_branch": "release", "conclusion": "failure"},
                {"id": 9, "head_branch": "other", "conclusion": "success"},
            ]}
        if endpoint.endswith("/runs/11/artifacts"):
            return {"artifacts": [
                {"id": 25, "name": "package", "expired": False, "size_in_bytes": 44,
                 "archive_download_url": "https://api.github.com/artifacts/25/zip"},
                {"id": 24, "name": "expired", "expired": True,
                 "archive_download_url": "https://api.github.com/artifacts/24/zip"},
            ]}
        raise AssertionError(endpoint)
    monkeypatch.setattr(client, "_json", fake_json)

    repo = client.repository("owner/repo")
    runs = client.workflow_runs(repo["full_name"], "release")
    artifacts = client.artifacts(repo["full_name"], runs[0]["id"])

    assert repo["id"] == 7
    assert [run["id"] for run in runs] == [11]
    assert calls[1][1] == {"branch": "release", "status": "success", "per_page": 100, "page": 1}
    assert len(artifacts) == 1
    assert artifacts[0]["artifacts_file"] == {"filename": "package.zip", "size": 44}


def test_github_artifact_download_uses_safe_shared_extractor(tmp_path, monkeypatch):
    client = GitHubActionsClient("token")
    monkeypatch.setattr(client, "_open", lambda *_args, **_kwargs: (Response(zip_bytes()), 1024))

    imported = client.download_artifact("owner/repo", 25, tmp_path / "cache")

    assert imported.name.startswith("run-artifact-25-")
    assert (imported / "dist" / "app.txt").read_bytes() == b"github artifact"


def test_github_provenance_is_validated_and_bound_to_run_url(tmp_path):
    profile = dict(name="Actions build", source=str(tmp_path), site="site", destination="/www",
        excludes=[], verification="Size only", github={
            "repository": "owner/repo", "run_id": 11,
            "run_url": "https://github.com/owner/repo/actions/runs/11",
            "commit_sha": "b" * 40, "ref": "release", "artifact_id": 25,
            "artifact_name": "package",
        })
    assert validate_profile(profile) is profile
    profile["github"]["run_url"] = "https://github.com/owner/else/actions/runs/11"
    with pytest.raises(ValueError, match="does not match"):
        validate_profile(profile)

def test_deployment_editor_exposes_github_actions_import(monkeypatch):
    from PySide6.QtWidgets import QApplication, QPushButton
    from fluxftp.app import Window
    from fluxftp.deployment_ui import ProfileEditor
    from fluxftp.github_artifact_dialog import GitHubArtifactDialog

    app = QApplication.instance() or QApplication([])
    window = Window(smoke=True)
    editor = None
    dialog = None
    try:
        editor = ProfileEditor(window)
        labels = [button.text() for button in editor.findChildren(QPushButton)]
        assert "Import GitHub Actions artifact…" in labels
        dialog = GitHubArtifactDialog(window, editor)
        assert dialog.windowTitle() == "Import GitHub Actions Artifact"
        assert dialog.server.text() == "https://github.com"
        assert dialog.server.isReadOnly()
        assert dialog.project_input.placeholderText() == "owner/repository"
    finally:
        if dialog:
            dialog.close()
            dialog.deleteLater()
        if editor:
            editor.close()
            editor.deleteLater()
        window.close()
        app.processEvents()
