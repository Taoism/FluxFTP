"""Backend contracts and conservative capabilities, with no desktop dependency.

Capabilities describe client implementations, not server promises. FTP HASH,
SFTP check-file and FXP still require successful server negotiation. Existing
connection implementations remain responsible for authentication and I/O.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable


class HashSupport(str, Enum):
    LOCAL = 'local'
    NEGOTIATED = 'server extension required'
    NONE = 'no native checksum exposed'


@dataclass(frozen=True)
class BackendCapabilities:
    backend: str
    payload_pause: bool = False
    resume_download: bool = False
    resume_upload: bool = False
    direct_fxp: bool = False
    native_hash: HashSupport = HashSupport.NONE


LOCAL = BackendCapabilities('local', True, True, True, native_hash=HashSupport.LOCAL)
FTP = BackendCapabilities('ftp', True, True, True, True, HashSupport.NEGOTIATED)
SFTP = BackendCapabilities('sftp', True, True, True, native_hash=HashSupport.NEGOTIATED)
SCP = BackendCapabilities('scp', True, native_hash=HashSupport.NEGOTIATED)
CLOUD = BackendCapabilities('cloud')
UNKNOWN = BackendCapabilities('unknown')
FTP_PROTOCOLS = frozenset(('FTP · Unencrypted', 'FTPS · AUTH TLS', 'FTPS · AUTH SSL', 'FTPS · Implicit TLS'))


def capabilities_for(site):
    if site is None: return LOCAL
    protocol = getattr(site, 'protocol', '')
    if getattr(site, 'cloud_account', ''): return CLOUD
    if protocol in FTP_PROTOCOLS: return FTP
    if protocol == 'SFTP · SSH': return SFTP
    if protocol == 'SCP · SSH': return SCP
    from .cloud import CLOUD_PROTOCOLS
    return CLOUD if protocol in CLOUD_PROTOCOLS else UNKNOWN


def connection_capabilities(connection):
    if connection is None: return LOCAL
    if getattr(connection, 'is_cloud', False): return CLOUD
    site = getattr(connection, 'site', None)
    if site is not None: return capabilities_for(site)
    # Existing protocol test doubles and connection extensions may lack a Site.
    if getattr(connection, 'sftp', None) is not None: return SFTP
    if getattr(connection, 'ftp', None) is not None: return FTP
    return UNKNOWN


def transfer_route(source_site, destination_site):
    if source_site is not None and destination_site is not None:
        return 'FXP' if capabilities_for(source_site).direct_fxp and capabilities_for(destination_site).direct_fxp else 'Relay'
    return 'Upload' if destination_site is not None else 'Download' if source_site is not None else 'Copy'


def supports_resume(source, destination):
    """Byte-offset resume for a complete route, not provider-managed sessions."""
    return connection_capabilities(source).resume_download and connection_capabilities(destination).resume_upload


def supports_payload_pause(job):
    source, destination = job.get('source_site'), job.get('destination_site')
    route = job.get('route') or transfer_route(source, destination)
    return route != 'FXP' and capabilities_for(source).payload_pause and capabilities_for(destination).payload_pause


@runtime_checkable
class ProtocolAdapter(Protocol):
    @property
    def capabilities(self) -> BackendCapabilities: ...
    def list(self, path): ...
    def size(self, path): ...
    def mkdir(self, path): ...
    def rename(self, old, new): ...
    def remove(self, path, is_dir=False): ...
    def transfer(self, local, remote, upload, cancel, progress, resume=False): ...
    def hash_algorithms(self): ...
    def digest(self, path, algorithm, cancel): ...


class ConnectionAdapter:
    """First migration adapter: delegate I/O to a connected existing backend.

The caller owns the connection lifetime. No credentials or connections are
created here, and no unsupported operation is silently converted to overwrite.
"""
    def __init__(self, connection):
        if connection is None: raise ValueError('A remote adapter requires a connection.')
        self.connection = connection

    @property
    def capabilities(self):
        return connection_capabilities(self.connection)

    def list(self, path): return self.connection.list(path)
    def size(self, path): return self.connection.size(path)
    def mkdir(self, path): return self.connection.mkdir(path)
    def rename(self, old, new): return self.connection.rename(old, new)
    def remove(self, path, is_dir=False): return self.connection.remove(path, is_dir=is_dir)

    def transfer(self, local, remote, upload, cancel, progress, resume=False):
        if resume and self.capabilities.backend == 'scp':
            raise ValueError('SCP does not support byte-offset resume. Choose Overwrite, or use SFTP for resumable transfers.')
        # Cloud backends retain their own existing-file/session checks. A true
        # resume flag on a fresh upload is not a promise of byte-offset support.
        return self.connection.transfer(local, remote, upload, cancel, progress, resume)

    def hash_algorithms(self):
        if self.capabilities.native_hash == HashSupport.NONE: return set()
        from .transfer_reliability import _backend_algorithms
        return _backend_algorithms(self.connection)

    def digest(self, path, algorithm, cancel):
        if self.capabilities.native_hash == HashSupport.NONE:
            from .transfer_reliability import UnsupportedHash
            raise UnsupportedHash('This backend does not expose native checksums.')
        from .transfer_reliability import _digest
        return _digest(self.connection, path, algorithm, cancel)


def adapter_for(connection) -> ProtocolAdapter:
    return ConnectionAdapter(connection)
