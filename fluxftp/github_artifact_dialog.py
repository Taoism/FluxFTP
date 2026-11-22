"""Picker for successful GitHub Actions runs and their downloadable artifacts."""
from __future__ import annotations

from PySide6.QtWidgets import QMessageBox

from .gitlab_artifact_dialog import GitLabArtifactDialog
from .github_artifacts import GitHubActionsClient, repository_name, token_id


class GitHubArtifactDialog(GitLabArtifactDialog):
    """Reuse the asynchronous pipeline picker while adapting it to GitHub runs."""
    oauth_provider = 'github'
    oauth_settings_key = 'github_artifact_oauth_client_id'
    provider_name = 'GitHub'
    def __init__(self, window, parent=None):
        super().__init__(window, parent)
        self.setWindowTitle('Import GitHub Actions Artifact')
        self.server.setText('https://github.com')
        self.server.setReadOnly(True)
        self.project_input.setPlaceholderText('owner/repository')
        self.token.setPlaceholderText('Token with Actions read permission (saved encrypted after successful connection)')
        self.oauth_client_id.setPlaceholderText('GitHub App client ID (configure Actions: read permission)')
        self.oauth_client_id.setText(self.window.preferences.value(self.oauth_settings_key, ''))
        form = self.layout().itemAt(0).layout()
        form.labelForField(self.server).setText('GitHub')
        form.labelForField(self.project_input).setText('Repository')
        form.labelForField(self.ref).setText('Branch or tag')
        form.labelForField(self.token).setText('GitHub token')
        form.labelForField(self.oauth_client_id).setText('GitHub App client ID')
        self.sign_in_button.setText('Sign in with GitHub…')
        self.pipeline.setAccessibleName('Successful GitHub Actions workflow run')
        self.job.setAccessibleName('GitHub Actions artifact')
        self.status.setText('Select a repository and branch or tag, then load successful workflow runs.')
        self.load_saved_token()

    def load_saved_token(self):
        if self.token.text():
            return
        try:
            self.token.setText(self.window.credential_vault.get(token_id()))
        except RuntimeError:
            self.token.clear()

    def find_pipelines(self):
        repository, ref, secret = (self.project_input.text().strip(), self.ref.text().strip(), self.token.text())
        if not repository or not ref:
            QMessageBox.warning(self, 'GitHub workflow lookup failed', 'Enter a repository and branch or tag name.')
            return
        try:
            repository = repository_name(repository)
            if not secret:
                secret = self.window.credential_vault.get(token_id())
            vault = self.window.credential_vault
            client_id = self.window.preferences.value(self.oauth_settings_key, '')
        except (ValueError, RuntimeError) as error:
            QMessageBox.warning(self, 'GitHub workflow lookup failed', str(error))
            return

        def lookup():
            from .artifact_oauth import is_github_token_record, resolve_github_access_token
            saved_oauth = is_github_token_record(secret)
            saved_record = [secret if saved_oauth else '']
            def save_record(value):
                vault.set(token_id(), value)
                saved_record[0] = value
            access_token = resolve_github_access_token(secret, client_id,
                save_record)
            client = GitHubActionsClient(access_token)
            project = client.repository(repository)
            runs = client.workflow_runs(project['full_name'], ref)
            pipelines = [{**item, 'ref': item.get('head_branch', ref), 'sha': item.get('head_sha', ''),
                          'web_url': item.get('html_url', ''), 'created_at': item.get('created_at', '')}
                         for item in runs]
            if not saved_oauth:
                vault.set(token_id(), secret)
            return client, {'id': project['id'], 'full_name': project['full_name'],
                            'path_with_namespace': project['full_name'], 'web_url': project['html_url']}, pipelines, saved_record[0]

        self.status.setText('Loading successful workflow runs…')
        self._begin(lookup, self._pipelines_loaded, 'GitHub workflow lookup failed')

    def load_jobs(self):
        run = self.pipeline.currentData()
        if not run or not self.client or not self.project:
            return
        client, repository, run_id = self.client, self.project['full_name'], run['id']
        self.status.setText(f'Loading artifacts for workflow run {run_id}…')
        self._begin(lambda: client.artifacts(repository, run_id), self._jobs_loaded, 'GitHub artifact lookup failed')

    def import_artifact(self):
        artifact = self.job.currentData()
        run = self.pipeline.currentData()
        if not artifact or not run or not self.client or not self.project:
            return
        client, project, run = self.client, self.project, run
        cache = self.window.sites_file.parent / 'github-artifacts'
        self.status.setText('Downloading and checking artifact archive…')
        self._begin(lambda: client.download_artifact(project['full_name'], artifact['id'], cache),
            lambda source: self._artifact_loaded_github(source, project, run, artifact), 'GitHub artifact import failed')

    def _artifact_loaded_github(self, source, project, run, artifact):
        try:
            provenance = {
                'repository': project['full_name'],
                'run_id': run['id'],
                'run_url': str(run['web_url']),
                'commit_sha': str(run['sha']),
                'ref': str(run['ref']),
                'artifact_id': artifact['id'],
                'artifact_name': str(artifact['name']),
            }
            from .queue_metadata import validate_profile
            validate_profile(dict(name='GitHub Actions import', source=str(source), site='site', destination='/',
                excludes=[], verification='Size only', github=provenance))
            self.result_artifact = (str(source), provenance)
            self.pending_accept = True
        except (KeyError, TypeError, ValueError) as error:
            self.status.setText('GitHub returned incomplete artifact provenance: ' + str(error))
            QMessageBox.warning(self, 'GitHub artifact import failed', self.status.text())

    def _oauth_token_ready(self, response):
        try:
            from .artifact_oauth import github_token_record
            client_id = self.window.preferences.value(self.oauth_settings_key, '')
            if self.oauth_remember.isChecked():
                saved = github_token_record(response, client_id)
                self.window.credential_vault.set(token_id(), saved)
                self.token.setText(saved)
            else:
                self.token.setText(response['access_token'])
            self.status.setText('GitHub sign-in succeeded. Load workflow runs to verify repository access.')
        except (KeyError, RuntimeError, ValueError) as error:
            self.token.setText(response.get('access_token', '') if isinstance(response, dict) else '')
            self.status.setText('Signed in for this session; encrypted saving failed: ' + str(error))

    def reject(self):
        if self.worker:
            self.pending_close = True
            self.status.setText('Finishing the current GitHub request before closing…')
            return
        super(GitLabArtifactDialog, self).reject()
