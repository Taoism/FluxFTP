"""Transfer settings and shared, cancellation-aware bandwidth budgets."""
from dataclasses import dataclass
import shutil
import threading
import time
from pathlib import Path
from .ascii_rules import read_rules, matches
from .priority_rules import DEFAULT_RULES, priority_rules, priority_rank

MISC_DEFAULTS = dict(crc_auto=True, crc_delete_log=True, crc_delete_tags=True, crc_xcrc=False,
                     crc_retries=0, crc_keep_bad=False, no_empty_dirs=False, deny_parent_name=False,
                     single_sfv=True, skip_multiple_sfv=True, keep_file_time=True, remove_missing_zero=False)


class RateLimiter:
    def __init__(self):
        self.lock = threading.Lock()
        self.rate = 0
        self.due = 0.0

    def configure(self, rate):
        with self.lock:
            if self.rate != rate:
                self.rate = rate
                self.due = 0.0

    def consume(self, count, cancel):
        with self.lock:
            if not self.rate:
                return
            now = time.monotonic()
            self.due = max(now, self.due) + count / self.rate
            delay = self.due - now
        if cancel.wait(delay):
            from .core import Cancelled
            raise Cancelled("Cancelled")


@dataclass(frozen=True)
class TransferOptions:
    force_active: bool = False
    stall_resume: bool = True
    stall_seconds: int = 120
    rollback_kib: int = 20
    free_mib: int = 0
    send_kib: int = 2048
    receive_kib: int = 2048
    mode: str = "Binary"
    bypass_local: bool = True
    upload_case: str = "Unchanged"
    download_case: str = "Unchanged"
    fxp_case: str = "Unchanged"
    ascii_extensions: str = ""
    ascii_rules: str = ""
    ascii_no_resume: bool = True
    skip_enabled: bool = False
    skip_zero: bool = True
    skip_empty: bool = True
    skip_folders: bool = False
    skip_mode: str = "Both"
    skip_rules: str = '[{"pattern":"*.diz","regex":false},{"pattern":"*.imdb","regex":false},{"pattern":"*.log","regex":false},{"pattern":"*.message","regex":false},{"pattern":"*.old","regex":false}]'
    skip_color: str = "#ff0000"
    allow_rules: str = '[]'
    atomic_upload: bool = False
    hash_verification: str = "Off"
    conflict_prompt: bool = False
    exists_enabled: bool = False
    upload_smaller: str = "Auto Resume"
    upload_same: str = "Auto Skip"
    upload_larger: str = "Auto Rename"
    download_smaller: str = "Auto Resume"
    download_same: str = "Auto Skip"
    download_larger: str = "Auto Rename"
    fxp_smaller: str = "Auto Resume"
    fxp_same: str = "Auto Skip"
    fxp_larger: str = "Auto Rename"
    overwrite_resume_failed: bool = True
    priority_rules: str = DEFAULT_RULES
    compress_listing: bool = True
    compress_upload: bool = True
    compress_download: bool = True
    download_buffer_kib: int = 0
    compression_bypass_local: bool = True
    compression_addresses: str = ""
    compression_masks: str = "*.zip;*.rar;*.cab;*.tar;*.r0?;*.r1?;*.r2?;*.r3?;*.r4?;*.r5?"
    crc_auto: bool = False
    crc_delete_log: bool = True
    crc_delete_tags: bool = True
    crc_xcrc: bool = False
    crc_retries: int = 0
    crc_keep_bad: bool = False
    no_empty_dirs: bool = False
    deny_parent_name: bool = False
    single_sfv: bool = False
    skip_multiple_sfv: bool = False
    keep_file_time: bool = False
    remove_missing_zero: bool = False

    @classmethod
    def from_settings(cls, settings):
        defaults = cls()
        values = {name: settings.value("Transfer/" + name, getattr(defaults, name), type=type(getattr(defaults, name)))
                  for name in cls.__dataclass_fields__}
        for name, default in MISC_DEFAULTS.items():
            values[name] = settings.value('Misc/' + name, default, type=type(default))
        if not 0 <= values['crc_retries'] <= 999:
            raise ValueError('CRC retries must be between 0 and 999.')
        if values["mode"] not in ("Binary", "ASCII", "Auto"):
            raise ValueError("Transfer mode must be Binary, ASCII or Auto.")
        if values["hash_verification"] not in ("Off", "When available", "Required"):
            raise ValueError("Invalid hash verification policy.")
        read_rules(values["ascii_rules"], values["ascii_extensions"])
        priority_rules(values["priority_rules"])
        if not 0 <= values['download_buffer_kib'] <= 65536:
            raise ValueError('Download disk buffer must be between 0 and 65536 KB.')
        for name in ('compression_addresses', 'compression_masks'):
            if len(values[name]) > 16384:
                raise ValueError('Compression exceptions must be at most 16384 characters.')
        # New UI defaults match the reference; direct library calls remain opt-in.
        values["skip_enabled"] = settings.value("Transfer/skip_enabled", True, type=bool)
        values["exists_enabled"] = True
        for route in ("upload", "download", "fxp"):
            for relation in ("smaller", "same", "larger"):
                if values[f"{route}_{relation}"] not in ("Auto Resume", "Auto Skip", "Auto Rename", "Auto Overwrite", "Ask"):
                    raise ValueError("Invalid File Exists action.")
        read_rules(values["skip_rules"])
        read_rules(values["allow_rules"])
        if values["skip_mode"] not in ("Auto Transfer", "Manual Transfer", "Both"):
            raise ValueError("Invalid skip mode.")
        for name in ("upload_case", "download_case", "fxp_case"):
            if values[name] not in ("Unchanged", "Lowercase", "Uppercase"):
                raise ValueError("Invalid filename conversion setting.")
        for name in ("stall_seconds", "send_kib", "receive_kib"):
            if not 1 <= values[name] <= 65536:
                raise ValueError(f"Invalid transfer setting: {name}")
        if min(values["rollback_kib"], values["free_mib"]) < 0:
            raise ValueError("Rollback and free-space values cannot be negative.")
        return cls(**values)

    def filename(self, name, route):
        choice = {"Upload": self.upload_case, "Download": self.download_case, "FXP": self.fxp_case, "Relay": self.fxp_case}.get(route, "Unchanged")
        return name.lower() if choice == "Lowercase" else name.upper() if choice == "Uppercase" else name

    def priority(self, name):
        return priority_rank(name, self.priority_rules)

    def ascii(self, path):
        return self.mode == "ASCII" or (self.mode == "Auto" and matches(path, read_rules(self.ascii_rules, self.ascii_extensions)))

    def skip_reason(self, name, is_dir, size=None, automatic=False, empty=False):
        if not self.skip_enabled or (self.skip_mode == "Auto Transfer" and not automatic) or (self.skip_mode == "Manual Transfer" and automatic):
            return ""
        if matches(name, read_rules(self.allow_rules)):
            return ""
        if is_dir and empty and self.skip_empty:
            return "Empty directory"
        if not is_dir and size == 0 and self.skip_zero:
            return "Zero byte file"
        if (not is_dir or self.skip_folders) and matches(name, read_rules(self.skip_rules)):
            return "Skip List mask"
        return ""

    def check_space(self, path, incoming=0):
        if not self.free_mib:
            return
        folder = Path(path).parent
        while not folder.exists() and folder != folder.parent:
            folder = folder.parent
        if shutil.disk_usage(folder).free - incoming < self.free_mib * 1024 * 1024:
            raise OSError(f"Transfer would leave less than {self.free_mib} MB free on the destination drive.")
