from pathlib import Path

from PySide6.QtWidgets import QApplication

from fluxftp.app import Window


def test_smoke_window_isolates_settings_initializes_services_and_wires_widgets():
    app = QApplication.instance() or QApplication([])
    window = Window(smoke=True)
    smoke_root = Path(window._smoke_directory.name)
    try:
        assert Path(window.preferences.fileName()) == smoke_root / "settings.ini"
        assert window.sites_file == smoke_root / "data" / "sites.json"
        assert window.credential_vault.path == smoke_root / "data" / "credentials.enc"

        assert window.app_instance is app
        assert window.directory_cache is not None
        assert window.ident is not None
        assert window.queue_engine is not None
        assert window.queue_controller is not None
        assert window.connection_factory is not None
        assert window.cloud_service is not None
        assert window.automation_runtime is not None

        assert window.centralWidget() is not None
        assert window.main_toolbar is not None
        assert window.tabs.count() == 1
        assert len(window.current_panes()) == 2
        assert window.queue is not None
        assert window.browse_timer.isActive()
        assert window.tray_timer.isActive()
    finally:
        window.close()
        app.processEvents()
