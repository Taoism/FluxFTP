"""Directory listing and remote file operations for native connections."""
import ftplib
import posixpath
import re
import stat
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .connection_model import Entry, remote_path, safe_remote_name


def parse_list_entry(line):
    """Parse common Unix and DOS LIST rows without guessing unknown formats."""
    if not line.strip() or re.fullmatch(r"total\s+\d+\s*", line, re.IGNORECASE):
        return None
    unix = re.fullmatch(
        r"([dl\-][rwxstST\-]{9}[+@.]?)\s+\d+\s+(\S+)\s+(\S+)\s+(\d+)\s+"
        r"(\S+\s+\d{1,2}\s+(?:\d{1,2}:\d{2}(?::\d{2})?|\d{4}))\s(.*)", line)
    dos = re.fullmatch(
        r"(\d{2}[-/]\d{2}[-/]\d{2,4}\s+\d{1,2}:\d{2}\s*[AP]M)\s+"
        r"(<DIR>|\d+)\s+(.*)", line, re.IGNORECASE)
    if unix:
        attributes, user, group, size, modified, name = unix.groups()
        kind = attributes[0]
        if kind == "l":
            name = name.split(" -> ", 1)[0]
        entry = Entry(name, kind == "d", int(size), modified, kind == "l", attributes, user, group)
    elif dos:
        modified, size, name = dos.groups()
        directory = size.upper() == "<DIR>"
        entry = Entry(name, directory, 0 if directory else int(size), modified)
    else:
        raise ValueError("Unsupported LIST response format. Enable 'Use MLSD if available' or use a server with Unix/DOS listings.")
    if entry.name in (".", ".."):
        return None
    safe_remote_name(entry.name)
    return entry


class ConnectionOperationsMixin:
    def list(self, path):
        self.phase = "directory listing / data connection"
        self.trace.event(f"Listing directory: {path}")
        path = remote_path(path)
        if self.sftp:
            entries = [Entry(x.filename, stat.S_ISDIR(x.st_mode), x.st_size, str(x.st_mtime), stat.S_ISLNK(x.st_mode),
                             stat.filemode(x.st_mode), str(x.st_uid) if x.st_uid is not None else '',
                             str(x.st_gid) if x.st_gid is not None else '')
                       for x in self.sftp.listdir_iter(path, read_aheads=self.sftp_read_aheads) if x.filename not in (".", "..")]
        else:
            try:
                entries = self._list_ftp(path)
            except (OSError, ftplib.Error) as error:
                if not self.passive_fallback or not self.site.passive or self.proxy or self.list_mode in ('STAT', 'STAT -l'):
                    raise
                self.trace.event(f"Passive listing failed; retrying in active PORT mode: {type(error).__name__}")
                self.ftp.set_pasv(False)
                try:
                    entries = self._list_ftp(path, allow_mlsd=False)
                finally:
                    self.ftp.set_pasv(True)
        # Some servers expose directory shortcuts as files or symbolic links.
        # Keep the exact alias for navigation; the server resolves its target.
        entries = [replace(entry, is_dir=True) if entry.name.startswith('+') else entry for entry in entries]
        return self._apply_site_timezone(entries)

    def _apply_site_timezone(self, entries):
        zone_name = self.site.timezone
        if not zone_name:
            return entries
        try:
            if zone_name == "Local Time":
                zone = datetime.now().astimezone().tzinfo
            elif zone_name == "UTC":
                zone = timezone.utc
            elif zone_name.startswith("UTC") and len(zone_name) == 9:
                sign = 1 if zone_name[3] == "+" else -1
                hours, minutes = (int(part) for part in zone_name[4:].split(":"))
                zone = timezone(sign * timedelta(hours=hours, minutes=minutes), zone_name)
            else:
                zone = ZoneInfo(zone_name)
        except (ZoneInfoNotFoundError, ValueError):
            self.trace.event(f"Unknown site timezone {zone_name!r}; leaving server timestamps unchanged")
            return entries
        adjusted = []
        for entry in entries:
            value = entry.modified
            stamp = None
            try:
                if value.isdigit() and len(value) <= 10:
                    stamp = datetime.fromtimestamp(float(value), timezone.utc)
                else:
                    normalized = value.rstrip("Z")
                    fmt = "%Y%m%d%H%M%S.%f" if "." in normalized else "%Y%m%d%H%M%S"
                    stamp = datetime.strptime(normalized, fmt).replace(tzinfo=timezone.utc)
            except (ValueError, OverflowError, OSError):
                pass
            adjusted.append(replace(entry, modified=stamp.astimezone(zone).strftime("%Y-%m-%d %H:%M %Z")) if stamp else entry)
        return adjusted

    def _list_ftp(self, path, allow_mlsd=True):
        if self.list_mode in ('STAT', 'STAT -l'):
            response = self.ftp.sendcmd(self.list_mode + ' ' + remote_path(path))
            rows = response.splitlines()
            if len(rows) < 2:
                raise ValueError('Server did not return a multiline STAT listing.')
            entries = []
            for line in rows[1:-1]:
                # Servers may prefix every row with the multiline reply code.
                line = re.sub(r'^21[123][- ]', '', line)
                if line.startswith(' '):
                    line = line.lstrip()
                entry = parse_list_entry(line)
                if entry:
                    entries.append(entry)
            return entries
        if allow_mlsd and self.use_mlsd and self.list_mode not in ("LIST only", "Custom", "Show Hidden Files"):
            try:
                if self.mlsd_essential and not self._mlsd_facts_configured:
                    try:
                        self.ftp.sendcmd('OPTS MLST type;size;modify;')
                    except ftplib.error_perm:
                        self.trace.event('Server declined essential MLSD facts; using server defaults')
                    self._mlsd_facts_configured = True
                return [Entry(n, f.get("type") == "dir", int(f.get("size", 0)), f.get("modify", ""),
                              attributes=f.get('unix.mode', f.get('perm', '')),
                              user=f.get('unix.ownername', f.get('unix.owner', f.get('unix.uid', ''))),
                              group=f.get('unix.groupname', f.get('unix.group', f.get('unix.gid', ''))))
                        for n, f in self.ftp.mlsd(path) if f.get("type") not in ("cdir", "pdir") and n not in (".", "..")]
            except ftplib.error_perm as e:
                if not str(e).startswith(("500", "501", "502", "504")):
                    raise
                self.use_mlsd = False
        command = (self.list_command if self.list_mode == "Custom" else
                   "LIST -a" if self.list_mode == "Show Hidden Files" else "LIST")
        old = self.ftp.pwd()
        try:
            self.ftp.cwd(path)
            entries, errors = [], []
            def received(line):
                # Drain the response even on a bad row so the control channel stays aligned.
                try:
                    entry = parse_list_entry(line)
                    if entry is not None: entries.append(entry)
                except ValueError as error:
                    if not errors: errors.append(error)
            self.ftp.retrlines(command, received)
            if errors: raise errors[0]
        finally:
            self.ftp.cwd(old)
        return entries

    def mkdir(self, path):
        self.phase = "create directory"
        path = remote_path(path)
        if self.transfer_options.deny_parent_name and posixpath.basename(path.rstrip('/')).casefold() == posixpath.basename(posixpath.dirname(path.rstrip('/'))).casefold():
            raise ValueError('New directory cannot have the same name as its parent')
        return self.sftp.mkdir(path) if self.sftp else self.ftp.mkd(path)

    def note(self, message):
        self.trace.event(message)
        if getattr(self, 'transfer_notice', None): self.transfer_notice(message)

    def rename(self, old, new):
        self.phase = "rename"
        old, new = remote_path(old), remote_path(new)
        return self.sftp.rename(old, new) if self.sftp else self.ftp.rename(old, new)

    def chmod(self, path, mode):
        if not 0 <= mode <= 0o7777:
            raise ValueError('Permissions must be between 0000 and 7777.')
        path = remote_path(path)
        return self.sftp.chmod(path, mode) if self.sftp else self.ftp.sendcmd(f'SITE CHMOD {mode:04o} {path}')

    def set_modified(self, path, timestamp):
        path = remote_path(path)
        if self.sftp:
            info = self.sftp.stat(path)
            self.sftp.utime(path, (info.st_atime if info.st_atime is not None else timestamp, timestamp))
        else:
            stamp = datetime.fromtimestamp(timestamp, timezone.utc).strftime('%Y%m%d%H%M%S')
            self.ftp.sendcmd(f'MFMT {stamp} {path}')

    def remove(self, path, is_dir=False):
        self.phase = "remove directory" if is_dir else "remove file"
        path = remote_path(path)
        if self.sftp:
            return self.sftp.rmdir(path) if is_dir else self.sftp.remove(path)
        return self.ftp.rmd(path) if is_dir else self.ftp.delete(path)

    def size(self, path):
        if self.sftp:
            return self.sftp.stat(remote_path(path)).st_size
        self.ftp.voidcmd("TYPE I")
        return self.ftp.size(remote_path(path))




