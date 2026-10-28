"""Shared connection records and path validation without transport or UI imports."""
from dataclasses import dataclass
import os
import re
import ipaddress
from .cloud import CLOUD_PROTOCOLS

PROTOCOLS = ["FTPS · AUTH TLS", "FTPS · AUTH SSL", "FTPS · Implicit TLS", "SFTP · SSH", "FTP · Unencrypted", *CLOUD_PROTOCOLS, "SCP · SSH"]


@dataclass
class Site:
    name: str = "New site"
    host: str = ""
    port: int = 21
    protocol: str = PROTOCOLS[0]
    username: str = "anonymous"
    root: str = "/"
    ca_file: str = ""
    cert_file: str = ""
    cert_key: str = ""
    ssh_key: str = ""
    managed_key: str = ""
    cloud_account: str = ""
    known_hosts: str = ""
    passive: bool = True
    tls_cert_sha256: str = ""
    tls_pin_host: str = ""
    tls_pin_port: int = 0
    tls_pin_protocol: str = ""
    description: str = ""
    force_utf8: bool = False
    utf8_username: bool = True
    use_masq: bool | None = None
    timezone: str = ""
    single_connection: bool = False
    max_transfers_total: int = 6
    max_downloads: int = 6
    max_uploads: int = 6
    local_path: str = ""
    site_options: str = "{}"


@dataclass
class Entry:
    name: str
    is_dir: bool
    size: int = 0
    modified: str = ""
    is_link: bool = False
    attributes: str = ""
    user: str = ""
    group: str = ""


class Cancelled(Exception):
    pass


def safe_remote_name(name):
    """Validate one remote path component without imposing Windows disk rules."""
    if not name or name in (".", "..") or any(c in name for c in "/\\\r\n\x00"):
        raise ValueError(f"Unsafe file name: {name!r}")
    return name


def safe_name(name):
    safe_remote_name(name)
    if os.name == "nt" and (any(c in name for c in '<>:"|?*') or name.endswith((".", " "))
                            or name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}):
        raise ValueError(f"Unsupported Windows file name: {name!r}")
    return name


def normalize_host(host):
    """Resolve legacy hexadecimal IPv4 host values to standard dotted notation."""
    value = host.strip()
    match = re.fullmatch(r"0[xX]([0-9a-fA-F]{1,8})", value)
    if not match:
        return value
    return str(ipaddress.IPv4Address(int(match.group(1), 16)))
