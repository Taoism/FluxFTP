"""Picker for successful GitLab pipeline jobs with downloadable artifacts."""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QHBoxLayout, QLineEdit,
    QComboBox, QPushButton, QLabel, QDialogButtonBox, QMessageBox)

from urllib.parse import quote

from .gitlab_artifacts import GitLabClient, normalize_url, token_id


class _GitLabWorker(QThread):
    result = Signal(object)
    failed = Signal(object)

    def __init__(self, function):
        super().__init__()
        self.function = function

    def run(self):
        try:
            self.result.emit(self.function())
        except Exception as error:
            self.failed.emit(error)


class GitLabArtifactDialog(QDialog):
    def __init__(self, window, parent=None):
        super().__init__(parent or window)
        self.window = window
        self.client = None
        self.project = None
        self.pipelines = []
        self.jobs = []
        self.result_artifact = None
        self.worker = None
        self.pending_accept = False
        self.pending_close = False
        self.setWindowTitle('Import GitLab Pipeline Artifact')
        self.resize(740, 430)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.server = QLineEdit('https://gitlab.com')
        self.project_input = QLineEdit()
        self.project_input.setPlaceholderText('group/project or numeric project ID')
        self.ref = QLineEdit()
        self.ref.setPlaceholderText('branch or tag')
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText('Token with read_api access (saved encrypted after successful connection)')
        form.addRow('GitLab server (HTTPS)', self.server)
        form.addRow('Project', self.project_input)
        form.addRow('Branch or tag', self.ref)
        form.addRow('Access token', self.token)
        layout.addLayout(form)
        actions = QHBoxLayout()
        self.find_button = QPushButton('Find successful pipelines')
        self.jobs_button = QPushButton('Load artifact jobs')
        self.import_button = QPushButton('Import selected artifact')
        actions.addWidget(self.find_button)
        actions.addWidget(self.jobs_button)
        actions.addWidget(self.import_button)
        layout.addLayout(actions)
        self.pipeline = QComboBox()
        self.pipeline.setAccessibleName('Successful GitLab pipeline')
        self.job = QComboBox()
        self.job.setAccessibleName('Successful GitLab job artifact')
        form.addRow('Successful pipeline', self.pipeline)
        form.addRow('Artifact job', self.job)
        self.status = QLabel('Select a project and branch or tag, then load its successful pipelines.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.find_button.clicked.connect(self.find_pipelines)
        self.jobs_button.clicked.connect(self.load_jobs)
        self.import_button.clicked.connect(self.import_artifact)
        self.server.editingFinished.connect(self.load_saved_token)
        self.load_saved_token()
        self.jobs_button.setEnabled(False)
        self.import_button.setEnabled(False)

    def load_saved_token(self):
        if self.token.text():
            return
        try:
            self.token.setText(self.window.credential_vault.get(token_id(self.server.text())))
        except (ValueError, RuntimeError):
            self.token.clear()

    def find_pipelines(self):
        base, project_ref, ref, secret = (self.server.text(), self.project_input.text().strip(),
                                          self.ref.text().strip(), self.token.text())
        if not project_ref or not ref:
            QMessageBox.warning(self, 'GitLab pipeline lookup failed', 'Enter a project ID/path and a branch or tag name.')
            return
        try:
            base = normalize_url(base)
            if not secret:
                secret = self.window.credential_vault.get(token_id(base))
            vault = self.window.credential_vault
        except (ValueError, RuntimeError) as error:
            QMessageBox.warning(self, 'GitLab pipeline lookup failed', str(error))
            return
        def lookup():
            client = GitLabClient(base, secret)
            project = client.project(quote(project_ref, safe=''))
            _, pipelines = client.pipelines(project['id'], ref)
            vault.set(token_id(base), secret)
            return client, project, pipelines
        self.status.setText('Loading successful pipelines…')
        self._begin(lookup, self._pipelines_loaded, 'GitLab pipeline lookup failed')

    def _pipelines_loaded(self, result):
        self.client, self.project, pipelines = result
        self.token.setText(self.client.token)
        try:
            if not pipelines:
                raise ValueError('No successful pipelines were found for that branch or tag.')
            self.pipelines = pipelines
            self.pipeline.clear()
            for item in pipelines:
                sha = str(item.get('sha', ''))
                created = str(item.get('created_at', ''))
                label = f"#{item.get('id')} · {item.get('ref', self.ref.text())} · {sha[:12]} · {created}"
                self.pipeline.addItem(label, item)
            self.jobs.clear()
            self.jobs_button.setEnabled(True)
            self.import_button.setEnabled(False)
            self.status.setText(f"Found {len(pipelines)} successful pipeline(s) in {self.project['path_with_namespace']}.")
        except (ValueError, KeyError) as error:
            self.status.setText(str(error))
            QMessageBox.warning(self, 'GitLab pipeline lookup failed', str(error))

    def load_jobs(self):
        pipeline = self.pipeline.currentData()
        if not pipeline or not self.client or not self.project:
            return
        client, project_id, pipeline_id = self.client, self.project['id'], pipeline['id']
        self.status.setText(f'Loading jobs for pipeline {pipeline_id}…')
        self._begin(lambda: client.jobs(project_id, pipeline_id), self._jobs_loaded, 'GitLab jobs lookup failed')

    def _jobs_loaded(self, jobs):
        try:
            self.jobs = jobs
            self.job.clear()
            for item in self.jobs:
                artifact = item['artifacts_file']
                size = artifact.get('size')
                label = f"{item.get('name', 'job')} · job {item.get('id')} · {artifact.get('filename')}"
                if isinstance(size, int):
                    label += f' · {size:,} bytes'
                self.job.addItem(label, item)
            if not self.jobs:
                self.status.setText('This successful pipeline has no successful jobs with downloadable artifacts.')
            else:
                self.status.setText(f'{len(self.jobs)} successful artifact job(s) are available.')
            self.import_button.setEnabled(bool(self.jobs))
        except (ValueError, KeyError) as error:
            self.status.setText(str(error))
            QMessageBox.warning(self, 'GitLab jobs lookup failed', str(error))

    def import_artifact(self):
        job = self.job.currentData()
        pipeline = self.pipeline.currentData()
        if not job or not pipeline or not self.client or not self.project:
            return
        client = self.client
        project, pipeline, job = self.project, pipeline, job
        cache = self.window.sites_file.parent / 'gitlab-artifacts'
        self.status.setText('Downloading and checking artifact archive…')
        self._begin(lambda: client.download_artifact(project['id'], job['id'], cache),
                    lambda source: self._artifact_loaded(source, project, pipeline, job),
                    'Artifact import failed')

    def _artifact_loaded(self, source, project, pipeline, job):
        try:
            project_path = project['path_with_namespace']
            provenance = {
                'server': self.client.base_url,
                'project_id': project['id'],
                'project_path': project_path,
                'ref': str(pipeline.get('ref', self.ref.text())),
                'pipeline_id': pipeline['id'],
                'pipeline_url': str(pipeline.get('web_url') or
                    project['web_url'].rstrip('/') + '/-/pipelines/' + str(pipeline['id'])),
                'commit_sha': str(pipeline.get('sha', '')),
                'job_id': job['id'],
                'job_name': str(job.get('name', '')),
                'artifact_filename': str(job['artifacts_file']['filename']),
            }
            from .queue_metadata import validate_profile
            validate_profile(dict(name='GitLab import', source=str(source), site='site', destination='/',
                excludes=[], verification='Size only', gitlab=provenance))
            self.result_artifact = (str(source), provenance)
            self.pending_accept = True
        except (KeyError, TypeError, ValueError) as error:
            self.status.setText('GitLab returned incomplete deployment provenance: ' + str(error))
            QMessageBox.warning(self, 'Artifact import failed', self.status.text())

    def _begin(self, function, completed, title):
        if self.worker:
            return
        self.find_button.setEnabled(False)
        self.jobs_button.setEnabled(False)
        self.import_button.setEnabled(False)
        worker = self.worker = _GitLabWorker(function)
        worker.result.connect(completed)
        worker.failed.connect(lambda error: self._operation_failed(error, title))
        worker.finished.connect(self._operation_finished)
        worker.start()

    def _operation_failed(self, error, title):
        self.status.setText(str(error))
        QMessageBox.warning(self, title, str(error))

    def _operation_finished(self):
        worker, self.worker = self.worker, None
        if worker:
            worker.deleteLater()
        self.find_button.setEnabled(True)
        self.jobs_button.setEnabled(bool(self.pipelines))
        self.import_button.setEnabled(bool(self.jobs))
        if self.pending_accept:
            self.pending_accept = False
            self.accept()
        elif self.pending_close:
            self.pending_close = False
            super().reject()

    def reject(self):
        if self.worker:
            self.pending_close = True
            self.status.setText('Finishing the current GitLab request before closing…')
            return
        super().reject()

