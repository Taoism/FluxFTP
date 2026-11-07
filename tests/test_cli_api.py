import io
import json
import sys
import threading
import time
from contextlib import redirect_stderr, redirect_stdout
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PySide6.QtWidgets import QApplication, QTreeWidgetItem

from fluxftp import cli
from fluxftp.api_server import ControlAPI
from fluxftp.app import Window
from fluxftp.connection_model import Entry, Site


def run_cli(argv):
    output, error, code = io.StringIO(), io.StringIO(), []
    original_argv = sys.argv
    def invoke():
        try:
            with redirect_stdout(output), redirect_stderr(error):
                sys.argv = ["fluxftp-cli", *argv]
                try:
                    code.append(cli.main())
                except SystemExit as exc:
                    code.append(exc.code)
        finally:
            sys.argv = original_argv
    thread = threading.Thread(target=invoke)
    thread.start()
    app = QApplication.instance()
    deadline = time.monotonic() + 10
    while thread.is_alive() and time.monotonic() < deadline:
        app.processEvents()
        thread.join(.005)
    assert not thread.is_alive(), "CLI request did not finish"
    return code[0], output.getvalue(), error.getvalue()


def test_cli_api_auth_listing_transfers_queue_json_and_exit_codes(monkeypatch, tmp_path, capsys):
    app = QApplication.instance() or QApplication([])
    window = Window(smoke=True)
    api = ControlAPI(window, port=0)
    original_api = cli.API
    original_token = cli._token
    cli.API = f"http://127.0.0.1:{api.server.server_address[1]}"
    cli._token = lambda: api.token
    try:
        window.sites = {"alpha": {"host": "example.test", "port": 21, "protocol": "FTP"}}
        window.site_from_profile = lambda _profile: Site(host="example.test")
        window.get_site_password = lambda _profile: "stored-password"

        class ListedConnection:
            def __enter__(self): return self
            def __exit__(self, *_args): pass
            def list(self, path):
                assert path == "/folder"
                return [Entry("report.txt", False, 12, "now")]

        window.new_connection = lambda *_args: ListedConnection()
        jobs = []
        def enqueue(_source, _destination, entries, **_kwargs):
            row = QTreeWidgetItem([entries[0].name, "", "", "", "", str(tmp_path / "report.txt"), "/report.txt", "Upload · Queued"])
            job = {"row": row, "name": entries[0].name, "route": "Upload", "task": None,
                   "workspace": window.tabs.currentWidget(), "source_site": None, "destination_site": None,
                   "src": str(tmp_path / "report.txt"), "dst": "/report.txt", "state": "queued", "retries": 0}
            jobs.append(job)
            window.queue_jobs.append(job)
            return [job]
        window.enqueue_items = enqueue
        window.play_queue = lambda: setattr(window, "paused", False)
        window.toggle_pause = lambda: setattr(window, "paused", True)
        window.stop_queue = lambda: setattr(window, "pending", [])
        window.transfer_history.cancelled = lambda _jobs: None
        window.queue_engine.cancel = lambda _jobs: []
        window.save_queue_state = lambda: True

        status, sites_json, error = run_cli(["sites", "list"])
        assert status == 0 and not error
        assert json.loads(sites_json)["sites"][0]["name"] == "alpha"

        status, listing_json, error = run_cli(["ls", "--site", "alpha", "/folder"])
        assert status == 0 and not error
        assert json.loads(listing_json)["entries"][0]["name"] == "report.txt"

        source = tmp_path / "report.txt"
        source.write_text("payload", encoding="utf-8")
        status, queued_json, error = run_cli(["transfer", str(source), "/report.txt", "--destination-site", "alpha"])
        assert status == 0 and not error
        queued = json.loads(queued_json)
        assert queued["queued"] and queued["held"]
        transfer_id = queued["transfers"][0]["id"]

        status, transfers_json, error = run_cli(["queue", "transfers"])
        assert status == 0 and json.loads(transfers_json)["transfers"][0]["id"] == transfer_id
        for action in ("play", "pause", "stop", "cancel", "status"):
            args = ["queue", action]
            if action == "cancel":
                args.append(transfer_id)
            status, body, error = run_cli(args)
            assert status == 0 and not error
            assert isinstance(json.loads(body), dict)

        request = Request(cli.API + "/v1/sites", headers={"Authorization": "Bearer wrong-token"})
        try:
            urlopen(request, timeout=2)
            raise AssertionError("Unauthenticated API request unexpectedly succeeded")
        except HTTPError as exc:
            assert exc.code == 401
            assert json.loads(exc.read())["error"] == "bearer token required"

        status, _body, _error = run_cli(["ls", "--site", "missing", "/"])
        assert status == 1

        prior_argv = sys.argv
        try:
            sys.argv = ["fluxftp-cli", "ls", "--site", "missing", "/"]
            monkeypatch.setattr(cli, "_request", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("saved FluxFTP site was not found")))
            assert cli.main() == 1
            assert json.loads(capsys.readouterr().err)["error"] == "saved FluxFTP site was not found"
        finally:
            sys.argv = prior_argv
    finally:
        api.close()
        cli.API = original_api
        cli._token = original_token
        window.close()
        app.processEvents()
