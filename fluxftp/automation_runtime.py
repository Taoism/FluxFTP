"""Own the lifetime of scheduled automation and watched-folder controllers."""


class AutomationRuntime:
    """Bundle automation services while retaining their window-level APIs."""
    def __init__(self, window):
        from .automation import Automation
        from .folder_monitor import FolderMonitor
        from .watch_upload import WatchUploads

        self.window = window
        window.automation = Automation(window)
        window.folder_monitor = FolderMonitor(window)
        window.watch_uploads = WatchUploads(window)

    def shutdown(self):
        window = self.window
        window.automation.shutdown()
        window.folder_monitor.shutdown()
        window.watch_uploads.shutdown()
