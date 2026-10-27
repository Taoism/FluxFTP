from __future__ import annotations

import ftplib
import ipaddress
import re
import os
import posixpath
import socket
import ssl
import stat
import time
import base64
import zlib
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from cryptography import x509
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event

from .diagnostics import FTPTraceMixin, Trace
from .transfer_options import TransferOptions
from .ascii_rules import NormalizedReader
from .xdupe import guard as xdupe_guard
from .verification_reports import tracked_file


from .cloud import CLOUD_PROTOCOLS
PROTOCOLS = ["FTPS · AUTH TLS", "FTPS · AUTH SSL", "FTPS · Implicit TLS", "SFTP · SSH", "FTP · Unencrypted", *CLOUD_PROTOCOLS]


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
    safe_name(entry.name)
    return entry


class Cancelled(Exception):
    pass


def safe_name(name):
    if not name or name in (".", "..") or any(c in name for c in "/\\\r\n\x00"):
        raise ValueError(f"Unsafe file name: {name!r}")
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


class PinnedCertificateChanged(ssl.SSLError):
    """A previously accepted server pin no longer matches the peer certificate."""


class CertificateTrustRequired(ssl.SSLCertVerificationError):
    """A verified TLS connection failed; UI may offer explicit per-site pinning."""

    def __init__(self, site, certificate_der, verification_error):
        self.site = site
        self.certificate_der = certificate_der
        self.fingerprint = hashlib.sha256(certificate_der).hexdigest()
        self.previous_fingerprint = getattr(verification_error, "previous_fingerprint", "")
        self.verify_code = getattr(verification_error, "verify_code", None)
        self.verify_message = getattr(verification_error, "verify_message", str(verification_error))
        try:
            self.certificate = x509.load_der_x509_certificate(certificate_der)
        except ValueError:
            self.certificate = None
        super().__init__(self.verify_code or 0, self.verify_message or "certificate is not trusted")


def remote_path(path):
    if any(c in path for c in "\r\n\x00"):
        raise ValueError("Paths cannot contain control characters")
    return path


def open_proxy_socket(host, port, timeout=30, proxy=None, source_address=None):
    """Open a target socket through an optional HTTP CONNECT or SOCKS proxy."""
    host = str(host).strip()
    port = int(port)
    if not host or any(char in host for char in "\r\n\x00") or not 1 <= port <= 65535:
        raise ValueError("Invalid proxy target host or port")
    if not proxy or not proxy.get("host"):
        return socket.create_connection((host, port), timeout, source_address=source_address)
    proxy_host = proxy["host"].strip()
    proxy_port = int(proxy.get("port", 1080))
    sock = socket.create_connection((proxy_host, proxy_port), timeout, source_address=source_address)
    try:
        kind = str(proxy.get("type", "SOCKS5")).casefold()
        username = str(proxy.get("username", ""))
        password = str(proxy.get("password", ""))
        if any(char in proxy_host for char in "\r\n\x00") or any(char in username + password for char in "\r\n\x00"):
            raise ValueError("Proxy host and credentials cannot contain control characters")
        if kind == "http connect":
            authority_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
            authority = f"{authority_host.encode('idna').decode('ascii')}:{port}"
            headers = [f"CONNECT {authority} HTTP/1.1", f"Host: {authority}", "Proxy-Connection: Keep-Alive"]
            if username:
                token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
                headers.append(f"Proxy-Authorization: Basic {token}")
            sock.sendall(("\r\n".join(headers) + "\r\n\r\n").encode("ascii"))
            response = bytearray()
            while b"\r\n\r\n" not in response and len(response) < 16384:
                block = sock.recv(4096)
                if not block:
                    raise OSError("Proxy closed the HTTP CONNECT response")
                response.extend(block)
            status = bytes(response).split(b"\r\n", 1)[0]
            if not re.match(rb"HTTP/1\.[01] 2[0-9][0-9](?: |$)", status):
                raise OSError(f"HTTP proxy rejected CONNECT: {status.decode('latin-1', 'replace')}")
            return sock
        if kind not in ("socks5", "socks4", "socks4a"):
            raise ValueError(f"Unsupported proxy type: {proxy.get('type')}")
        if kind == "socks5":
            methods = b"\x00\x02" if username else b"\x00"
            sock.sendall(b"\x05" + bytes([len(methods)]) + methods)
            greeting = _recv_exact(sock, 2)
            if greeting[0] != 5 or greeting[1] == 255:
                raise OSError("SOCKS5 proxy rejected available authentication methods")
            if greeting[1] == 2:
                user_bytes, pass_bytes = username.encode(), password.encode()
                if not username or len(user_bytes) > 255 or len(pass_bytes) > 255:
                    raise OSError("SOCKS5 proxy requires valid username/password credentials")
                sock.sendall(b"\x01" + bytes([len(user_bytes)]) + user_bytes + bytes([len(pass_bytes)]) + pass_bytes)
                if _recv_exact(sock, 2) != b"\x01\x00":
                    raise OSError("SOCKS5 proxy authentication failed")
            elif greeting[1] != 0:
                raise OSError("SOCKS5 proxy selected an unsupported authentication method")
            try:
                address = socket.inet_pton(socket.AF_INET, host)
                address_type, encoded = b"\x01", address
            except OSError:
                try:
                    address = socket.inet_pton(socket.AF_INET6, host)
                    address_type, encoded = b"\x04", address
                except OSError:
                    domain = host.encode("idna")
                    if len(domain) > 255:
                        raise OSError("Target hostname is too long for SOCKS5")
                    address_type, encoded = b"\x03", bytes([len(domain)]) + domain
            sock.sendall(b"\x05\x01\x00" + address_type + encoded + int(port).to_bytes(2, "big"))
            reply = _recv_exact(sock, 4)
            if reply[0] != 5 or reply[1] != 0:
                reason = {1: 'general failure', 2: 'blocked by proxy rules', 3: 'network unreachable',
                          4: 'host unreachable', 5: 'connection refused', 6: 'TTL expired',
                          7: 'command unsupported', 8: 'address type unsupported'}.get(reply[1], 'invalid reply')
                raise OSError(f"SOCKS5 proxy connection failed to {host}:{port}: {reason} (reply {reply[1]})")
            tail_length = {1: 4, 4: 16}.get(reply[3])
            if reply[3] == 3:
                tail_length = _recv_exact(sock, 1)[0]
            if tail_length is None:
                raise OSError("SOCKS5 proxy returned an invalid address type")
            _recv_exact(sock, tail_length + 2)
            return sock
        # SOCKS4a sends the hostname to the proxy so local DNS is not required.
        try:
            address = socket.inet_aton(host)
            domain = b""
        except OSError:
            address, domain = b"\x00\x00\x00\x01", host.encode("idna") + b"\x00"
        user = username.encode() + b"\x00"
        sock.sendall(b"\x04\x01" + int(port).to_bytes(2, "big") + address + user + domain)
        reply = _recv_exact(sock, 8)
        if reply[0] != 0 or reply[1] != 90:
            raise OSError(f"SOCKS4 proxy connection failed (reply {reply[1]})")
        return sock
    except BaseException:
        sock.close()
        raise


def _configure_tcp_keepalive(sock, interval):
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    idle_option = getattr(socket, "TCP_KEEPIDLE", getattr(socket, "TCP_KEEPALIVE", None))
    if idle_option is not None:
        sock.setsockopt(socket.IPPROTO_TCP, idle_option, interval)
    if hasattr(socket, "TCP_KEEPINTVL"):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, interval)


def _recv_exact(sock, length):
    data = bytearray()
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise OSError("Proxy closed the connection during negotiation")
        data.extend(chunk)
    return bytes(data)


def bypass_proxy(host, local_addresses=False, exceptions=""):
    host = normalize_host(host).rstrip(".").casefold()
    for prefix in re.split(r"[;,\r\n]+", exceptions):
        prefix = prefix.strip().rstrip("*").casefold()
        if prefix and host.startswith(prefix):
            return True
    if not local_addresses:
        return False
    try:
        address = ipaddress.ip_address(host)
        return address.is_loopback or address.is_link_local or any(address in network for network in (
            ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"), ipaddress.ip_network("fc00::/7")))
    except ValueError:
        return "." not in host or host.endswith(".localhost")


class FTPProxyMixin:
    def set_compression(self, enabled):
        enabled = bool(enabled) and not getattr(self, 'mode_z_unsupported', False)
        if enabled == getattr(self, 'mode_z', False):
            return enabled
        try:
            self.voidcmd('MODE Z' if enabled else 'MODE S')
        except ftplib.error_perm as error:
            if enabled and str(error).startswith(('500', '501', '502', '504')):
                self.mode_z_unsupported = True
                self.trace.event('MODE Z unsupported; using stream mode')
                return False
            raise
        self.mode_z = enabled
        return enabled

    def wrap_compression(self, sock, command):
        if getattr(self, 'mode_z', False):
            from .compression import ModeZSocket
            return ModeZSocket(sock, command.split(' ', 1)[0].upper() in ('STOR', 'APPE', 'STOU'))
        return sock

    def configure_data_socket(self, sock):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, getattr(self, "send_buffer", 2048 * 1024))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, getattr(self, "receive_buffer", 2048 * 1024))
        if getattr(self, "data_timeout", None):
            sock.settimeout(self.data_timeout)

    def transfer_noop(self):
        if not getattr(self, "noop_during_transfer", False):
            return
        now = time.monotonic()
        if now - getattr(self, "last_transfer_noop", now) >= 30:
            self.putcmd("NOOP")
            self.pending_transfer_noops = getattr(self, "pending_transfer_noops", 0) + 1
            self.last_transfer_noop = now

    def voidresp(self):
        pending = getattr(self, "pending_transfer_noops", 0)
        if not pending:
            return super().voidresp()
        self.pending_transfer_noops = 0
        completion = None
        for _ in range(pending + 1):
            try:
                response = self.getresp()
            except ftplib.error_perm as error:
                if str(error).startswith(("500", "502", "504")):
                    self.trace.event("Server does not support NOOP during transfer")
                    self.noop_during_transfer = False
                    continue
                raise
            if response.startswith(("226", "250")):
                completion = response
            elif not response.startswith("200"):
                raise ftplib.error_reply(response)
        if completion is None:
            raise ftplib.error_reply("Missing transfer completion reply after NOOP")
        return completion

    def close(self):
        try:
            super().close()
        finally:
            mapper = getattr(self, "port_mapper", None)
            if mapper:
                mapper.close()

    def makepasv(self):
        self.trust_server_pasv_ipv4_address = not getattr(self, "smart_pasv", False)
        if self.af == socket.AF_INET:
            host, port = ftplib.parse227(self.sendcmd('PASV'))
        else:
            host, port = super().makepasv()
        self.advertised_passive = (host, port)
        if getattr(self, "smart_pasv", False):
            # PASV hosts are supplied by the remote server and can be private or forged.
            # Through a proxy, the control socket peer is the proxy, so use the FTP host.
            host = self.host if getattr(self, "proxy", None) else self.sock.getpeername()[0]
        self.trace.event(f'Passive data destination: {host}:{port}; server advertised '
                         f'{self.advertised_passive[0]}:{port}; NAT/Masq override={bool(getattr(self, "smart_pasv", False))}')
        return host, port

    def makeport(self):
        port_range = getattr(self, "local_port_range", None)
        source = getattr(self, "source_address", None)
        mapper = getattr(self, "port_mapper", None)
        if not port_range and not source and not mapper:
            return super().makeport()
        first, last = port_range or (0, 0)
        family = self.af
        wildcard = "::" if family == socket.AF_INET6 else "0.0.0.0"
        bind_host = source[0] if source and source[0] not in ("", "0.0.0.0", "::") else wildcard
        listener = None
        for port in range(first, last + 1):
            candidate = socket.socket(family, socket.SOCK_STREAM)
            try:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    candidate.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                candidate.bind((bind_host, port))
                candidate.listen(1)
                listener = candidate
                break
            except OSError:
                candidate.close()
        if listener is None:
            raise OSError(f"No free active FTP port in configured range {first}-{last}")
        local_host = self.sock.getsockname()[0]
        local_port = listener.getsockname()[1]
        try:
            if mapper:
                local_host = mapper.map(local_host, local_port)
            self.sendport(local_host, local_port) if family == socket.AF_INET else self.sendeprt(local_host, local_port)
        except BaseException:
            listener.close()
            raise
        if self.timeout is not ftplib._GLOBAL_DEFAULT_TIMEOUT:
            listener.settimeout(self.timeout)
        return listener

    def _transfercmd_with_proxy(self, cmd, rest=None):
        policy = getattr(self, 'compression_policy', None)
        if policy:
            self.set_compression(policy(cmd))
        proxy = getattr(self, "proxy", None)
        if not proxy or not self.passiveserver:
            return ftplib.FTP.ntransfercmd(self, cmd, rest)
        size = None
        host, port = self.makepasv()
        try:
            conn = open_proxy_socket(host, port, self.timeout, proxy, getattr(self, "source_address", None))
        except OSError as error:
            advertised = getattr(self, 'advertised_passive', (host, port))
            hint = (f' The server advertised {advertised[0]}:{advertised[1]}, but NAT/Masq selected '
                    f'{host}:{port}. If this server uses a bouncer or separate data servers, disable '
                    '"Server use NAT/Non-routable/Masq IP" in its site settings and retry.'
                    if advertised[0] != host else
                    ' Check the server passive-port forwarding/firewall and proxy access to this destination.')
            raise OSError(f'Passive data connection failed after FTP login: {error}.{hint} '
                          'The data connection did not reach a TLS handshake.') from error
        try:
            if rest is not None:
                self.sendcmd("REST %s" % rest)
            response = self.sendcmd(cmd)
            if response[0] == "2":
                response = self.getresp()
            if response[0] != "1":
                raise ftplib.error_reply(response)
            if response[:3] == "150":
                size = ftplib.parse150(response)
            return conn, size
        except BaseException:
            conn.close()
            raise


class LoggedFTP(FTPProxyMixin, FTPTraceMixin, ftplib.FTP):
    def __init__(self, trace, timeout=30, proxy=None, tcp_keepalive=False,
                 keepalive_interval=30, local_port_range=None):
        self.trace = trace
        self.proxy = proxy
        self.tcp_keepalive = tcp_keepalive
        self.keepalive_interval = keepalive_interval
        self.local_port_range = local_port_range
        self.smart_pasv = False
        super().__init__(timeout=timeout)

    def connect(self, host="", port=0, timeout=-999, source_address=None):
        if host:
            self.host = host
        if port > 0:
            self.port = port
        if timeout != -999:
            self.timeout = timeout
        if source_address is not None:
            self.source_address = source_address
        if self.timeout is not None and not self.timeout:
            raise ValueError("Non-blocking socket is not supported")
        self.sock = open_proxy_socket(self.host, self.port, self.timeout, self.proxy, self.source_address)
        if getattr(self, "ident_register", None):
            self.ident_register(self.sock)
        if self.tcp_keepalive:
            _configure_tcp_keepalive(self.sock, self.keepalive_interval)
        self.af = self.sock.family
        self.file = self.sock.makefile("r", encoding=self.encoding, errors="surrogateescape")
        self.welcome = self.getresp()
        return self.welcome

    def ntransfercmd(self, cmd, rest=None):
        self.trace.event(f"Opening FTP data connection: passive={self.passiveserver}")
        conn, size = self._transfercmd_with_proxy(cmd, rest)
        self.configure_data_socket(conn)
        if self.tcp_keepalive:
            _configure_tcp_keepalive(conn, self.keepalive_interval)
        self.trace.event(f"FTP data connection open: peer={conn.getpeername()}")
        return self.wrap_compression(conn, cmd), size


class SecureFTP(FTPProxyMixin, FTPTraceMixin, ftplib.FTP_TLS):
    """Explicit/implicit TLS with verified control and data channels."""

    def __init__(self, context, implicit=False, auth_command="AUTH TLS", trace=None, timeout=30,
                 proxy=None, tcp_keepalive=False, keepalive_interval=30, local_port_range=None,
                 cert_pin=""):
        self.trace = trace or Trace()
        self.cert_pin = cert_pin
        self.proxy = proxy
        self.tcp_keepalive = tcp_keepalive
        self.keepalive_interval = keepalive_interval
        self.local_port_range = local_port_range
        self.smart_pasv = False
        super().__init__(context=context, timeout=timeout)
        self.implicit = implicit
        self.auth_command = auth_command

    def connect(self, host="", port=0, timeout=-999, source_address=None):
        if host:
            self.host = host
        if port > 0:
            self.port = port
        if timeout != -999:
            self.timeout = timeout
        if source_address is not None:
            self.source_address = source_address
        if self.timeout is not None and not self.timeout:
            raise ValueError("Non-blocking socket is not supported")
        raw = open_proxy_socket(self.host, self.port, self.timeout, self.proxy, self.source_address)
        self.sock = raw
        if getattr(self, "ident_register", None):
            self.ident_register(raw)
        if self.tcp_keepalive:
            _configure_tcp_keepalive(raw, self.keepalive_interval)
        self.af = raw.family
        if self.implicit:
            self.trace.event(f"TCP connected: peer={raw.getpeername()}; starting implicit TLS handshake")
            self.sock = self.context.wrap_socket(raw, server_hostname=self.host)
            _verify_tls_pin(self.sock, self.cert_pin)
            self.trace.event(f"Implicit TLS established: version={self.sock.version()}; cipher={self.sock.cipher()}")
        else:
            self.sock = raw
        self.file = self.sock.makefile("r", encoding=self.encoding, errors="surrogateescape")
        self.welcome = self.getresp()
        return self.welcome

    def auth(self):
        if isinstance(self.sock, ssl.SSLSocket):
            return
        self.voidcmd(self.auth_command)
        self.trace.event(f"Starting explicit TLS handshake: command={self.auth_command}; hostname={self.host}")
        self.sock = self.context.wrap_socket(self.sock, server_hostname=self.host)
        _verify_tls_pin(self.sock, self.cert_pin)
        self.trace.event(f"Explicit TLS established: version={self.sock.version()}; cipher={self.sock.cipher()}")
        self.file = self.sock.makefile("r", encoding=self.encoding, errors="surrogateescape")

    def ntransfercmd(self, cmd, rest=None):
        self.trace.event(f"Opening FTPS data connection: passive={self.passiveserver}; protected={self._prot_p}")
        conn, size = self._transfercmd_with_proxy(cmd, rest)
        self.configure_data_socket(conn)
        self.trace.event(f"Data TCP connected: peer={conn.getpeername()}")
        if self.tcp_keepalive:
            _configure_tcp_keepalive(conn, self.keepalive_interval)
        if self._prot_p:
            try:
                conn = self.context.wrap_socket(conn, server_hostname=self.host, session=self.sock.session)
                self.trace.event(f"Data TLS established: version={conn.version()}; cipher={conn.cipher()[0]}; session_reused={conn.session_reused}")
            except BaseException:
                conn.close()
                raise
        return self.wrap_compression(conn, cmd), size


def _ftp_reply(fileobj):
    first = fileobj.readline(8192)
    if len(first) < 4 or not first[:3].isdigit():
        raise OSError("FTP server returned an invalid greeting while retrieving its certificate")
    code = first[:3]
    lines = [first]
    if first[3:4] == b"-":
        total = len(first)
        for _ in range(100):
            line = fileobj.readline(8192)
            total += len(line)
            if not line or total > 65536:
                raise OSError("FTP server returned an incomplete greeting while retrieving its certificate")
            lines.append(line)
            if line.startswith(code + b" "):
                break
        else:
            raise OSError("FTP server returned an oversized greeting while retrieving its certificate")
    return code, b"".join(lines).decode("latin-1", "replace").strip()


def _fetch_ftps_certificate(site, host, timeout, proxy, source_address):
    """Fetch the peer certificate without credentials after normal verification fails."""
    raw = open_proxy_socket(host, site.port, timeout, proxy, source_address)
    raw.settimeout(timeout)
    fileobj = None
    try:
        implicit = site.protocol == PROTOCOLS[2]
        auth_command = "AUTH SSL" if site.protocol == PROTOCOLS[1] else "AUTH TLS"
        if not implicit:
            fileobj = raw.makefile("rb")
            code, _ = _ftp_reply(fileobj)
            if not code.startswith(b"2"):
                raise OSError("FTP server did not send a successful greeting while retrieving its certificate")
            raw.sendall((auth_command + "\r\n").encode("ascii"))
            code, reply = _ftp_reply(fileobj)
            if code != b"234":
                raise OSError(f"FTP server rejected {auth_command} while retrieving its certificate: {reply}")
            fileobj.close()
            fileobj = None
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        secure = context.wrap_socket(raw, server_hostname=host)
        try:
            certificate = secure.getpeercert(binary_form=True)
            if not certificate:
                raise OSError("FTPS server did not provide a certificate")
            return certificate
        finally:
            secure.close()
    finally:
        if fileobj is not None:
            fileobj.close()
        try:
            raw.close()
        except OSError:
            pass


def _site_pin_applies(site, host):
    return bool(site.tls_cert_sha256 and
                site.tls_pin_host == host and
                int(site.tls_pin_port or 0) == int(site.port) and
                site.tls_pin_protocol == site.protocol)


def _verify_tls_pin(sock, expected_fingerprint):
    if not expected_fingerprint:
        return
    certificate_der = sock.getpeercert(binary_form=True)
    actual = hashlib.sha256(certificate_der or b"").hexdigest()
    if not hmac.compare_digest(actual.casefold(), expected_fingerprint.replace(":", "").casefold()):
        error = PinnedCertificateChanged("The FTPS server certificate changed after it was trusted for this site")
        error.previous_fingerprint = expected_fingerprint
        raise error
    try:
        cert = x509.load_der_x509_certificate(certificate_der)
        now = datetime.now(timezone.utc)
        if cert.not_valid_before_utc > now or cert.not_valid_after_utc < now:
            raise ssl.SSLError("The trusted FTPS server certificate is outside its validity period")
    except ValueError as error:
        raise ssl.SSLError("The trusted FTPS server certificate could not be parsed") from error


class Connection:
    def __init__(self, site: Site, password="", connect_timeout=30, command_timeout=30,
                 list_mode="Automatic (MLSD, then LIST)", max_retries=0, retry_delay=0,
                 send_feat=False, smart_pasv=True, proxy=None, tcp_keepalive=False,
                 keepalive_interval=30, passive_fallback=False, local_port_range=None,
                 local_bind_ip="", use_mlsd=True, ident=None, list_command="LIST -al", bind_sockets=False,
                 upnp_enabled=False, proxy_force_passive=True, noop_during_transfer=False,
                 transfer_options=None, upload_limiter=None, download_limiter=None, identity_loader=None,
                 filename_encoding='Current site settings', encoding_fallback='cp1252', sftp_read_aheads=50,
                 mlsd_essential=False, ssh_trust=None):
        self.site, self.password = site, password
        self.identity_loader = identity_loader
        self.ssh_trust = ssh_trust
        self.ident = ident
        self.connect_timeout = connect_timeout
        self.command_timeout = command_timeout
        self.list_mode = list_mode
        if list_command not in ("LIST -al", "LIST -a"):
            raise ValueError("Custom listing command must be LIST -al or LIST -a.")
        self.list_command = list_command
        self.use_mlsd = bool(use_mlsd)
        from .listing_options import select_encoding
        select_encoding(filename_encoding, encoding_fallback, site)
        self.filename_encoding, self.encoding_fallback = filename_encoding, encoding_fallback
        if type(sftp_read_aheads) is not int or not 1 <= sftp_read_aheads <= 128:
            raise ValueError('SFTP read-ahead must be between 1 and 128.')
        self.sftp_read_aheads = sftp_read_aheads
        self.mlsd_essential = bool(mlsd_essential)
        self._mlsd_facts_configured = False
        self.max_retries = max(0, int(max_retries))
        self.retry_delay = max(0, int(retry_delay))
        self.send_feat = bool(send_feat)
        self.smart_pasv = bool(smart_pasv)
        self.proxy = proxy if proxy and proxy.get("host") else None
        self.tcp_keepalive = bool(tcp_keepalive)
        self.keepalive_interval = max(1, int(keepalive_interval))
        self.passive_fallback = bool(passive_fallback)
        if local_port_range is not None:
            first, last = local_port_range
            if type(first) is not int or type(last) is not int or not 1 <= first <= last <= 65535:
                raise ValueError("Active FTP port range must be within 1-65535 and in ascending order")
            self.local_port_range = (first, last)
        else:
            self.local_port_range = None
        self.local_bind_ip = str(local_bind_ip or "").strip()
        self.bind_sockets = bool(bind_sockets or self.local_bind_ip)
        self.upnp_enabled = bool(upnp_enabled)
        self.proxy_force_passive = bool(proxy_force_passive)
        self.noop_during_transfer = bool(noop_during_transfer)
        self.transfer_options = transfer_options or TransferOptions()
        self.upload_limiter, self.download_limiter = upload_limiter, download_limiter
        if self.local_bind_ip:
            try:
                ipaddress.ip_address(self.local_bind_ip)
            except ValueError as error:
                raise ValueError("Local bind address must be an IPv4 or IPv6 address") from error
        self.ftp = self.ssh = self.sftp = None
        self.security = ""
        self.trace = Trace(password)
        self.phase = "initialization"

    def __enter__(self):
        try:
            self.connect()
            return self
        except BaseException:
            self.close()
            raise

    def __exit__(self, exc_type, error, tb):
        if error:
            self.trace.error(self.phase, error)
        self.close()

    def connect(self):
        self._mlsd_facts_configured = False
        self.trace.event(f"Connection requested: host={self.site.host}; port={self.site.port}; protocol={self.site.protocol}; passive={self.site.passive}")
        for attempt in range(self.max_retries + 1):
            try:
                self._connect()
                return
            except (ssl.SSLCertVerificationError, PinnedCertificateChanged) as error:
                self.trace.error(self.phase, error)
                self.close()
                self.ftp = self.ssh = self.sftp = None
                if self.site.protocol not in PROTOCOLS[:3]:
                    raise
                try:
                    certificate_der = _fetch_ftps_certificate(
                        self.site, normalize_host(self.site.host), self.connect_timeout,
                        self.proxy, (self.local_bind_ip, 0) if self.local_bind_ip else None,
                    )
                except Exception as fetch_error:
                    self.trace.event(f"Could not retrieve the rejected FTPS certificate for review: {type(fetch_error).__name__}")
                    raise error
                raise CertificateTrustRequired(self.site, certificate_der, error) from error
            except Cancelled:
                self.close()
                raise
            except Exception as error:
                self.trace.error(self.phase, error)
                self.close()
                self.ftp = self.ssh = self.sftp = None
                if attempt >= self.max_retries:
                    raise
                wait = self.retry_delay
                self.trace.event(f"Connection attempt {attempt + 1} failed; retrying in {wait} seconds")
                time.sleep(wait)

    def _connect(self):
        if self.ident and not self.proxy:
            self.ident.begin(self)
        self._connect_protocol()
        if self.ident and not self.proxy:
            self.ident.ready(self)

    def _connect_protocol(self):
        identity = None
        if self.site.managed_key:
            if self.identity_loader is None:
                raise ValueError('This connection requires access to the managed key vault.')
            identity = self.identity_loader(self.site)
            self.trace.event('Managed client identity loaded from Key Manager')
            expected = 'ssh' if self.site.protocol == PROTOCOLS[3] else 'tls'
            if self.site.protocol == PROTOCOLS[4] or identity[0]['kind'] != expected:
                raise ValueError('Managed key does not match the selected protocol.')
        s = self.site
        if self.proxy and s.protocol != PROTOCOLS[3] and not s.passive and not self.proxy_force_passive:
            raise ValueError("Active FTP through a proxy is not supported. Enable 'Use passive mode when using proxy' or select passive mode for this site.")
        host = normalize_host(s.host)
        if s.protocol not in PROTOCOLS:
            raise ValueError("Unsupported protocol")
        if s.protocol == PROTOCOLS[3]:
            import paramiko
            self.phase = "SSH host-key configuration"
            self.trace.event(f"SSH configuration: known_hosts={s.known_hosts or 'system default'}; key_file={s.ssh_key or 'agent/default keys'}")
            self.ssh = paramiko.SSHClient()
            self.ssh.load_system_host_keys()
            self.ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
            if self.ssh_trust is not None:
                from .ssh_trust import VerifiedPolicy
                if self.ssh_trust.path.exists():
                    self.ssh.load_host_keys(str(self.ssh_trust.path))
                self.ssh.set_missing_host_key_policy(VerifiedPolicy(self.ssh_trust.path, self.ssh_trust.verify))
            if s.known_hosts:
                self.ssh.load_host_keys(s.known_hosts)
            self.phase = "SSH handshake / host-key verification / authentication"
            self.trace.event(self.phase)
            socket_to_target = open_proxy_socket(host, s.port, self.connect_timeout, self.proxy,
                                                 (self.local_bind_ip, 0) if self.local_bind_ip else None)
            socket_to_target.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, self.transfer_options.send_kib * 1024)
            socket_to_target.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self.transfer_options.receive_kib * 1024)
            if self.ident and not self.proxy:
                self.ident.register(self, socket_to_target)
            if self.tcp_keepalive:
                _configure_tcp_keepalive(socket_to_target, self.keepalive_interval)
            try:
                from .key_store import ssh_identity
                self.ssh.connect(host, s.port, s.username, password=self.password or None,
                                 pkey=ssh_identity(identity[1]) if identity else None,
                                 key_filename=None if identity else s.ssh_key or None, timeout=self.connect_timeout, auth_timeout=self.command_timeout,
                                 banner_timeout=self.connect_timeout, look_for_keys=not bool(identity), allow_agent=not bool(identity),
                                 sock=socket_to_target)
            except BaseException:
                socket_to_target.close()
                raise
            self.phase = "opening SFTP subsystem"
            self.sftp = self.ssh.open_sftp()
            self.sftp.get_channel().settimeout(self.command_timeout)
            self.security = "SSH · verified host key"
            self.trace.event(f"SFTP connected: cipher={self.ssh.get_transport().remote_cipher}; host key verified")
            self.phase = "connected"
            return
        if s.protocol == PROTOCOLS[4]:
            self.ftp = LoggedFTP(self.trace, timeout=self.connect_timeout, proxy=self.proxy,
                                 tcp_keepalive=self.tcp_keepalive, keepalive_interval=self.keepalive_interval,
                                 local_port_range=self.local_port_range)
            self.security = "Unencrypted FTP"
        else:
            self.phase = "loading TLS trust / local certificate files"
            self.trace.event(f"TLS configuration: minimum=TLS1.2; hostname_check=True; verify=CERT_REQUIRED; ca_file={s.ca_file or 'system default'}; client_certificate={s.cert_file or 'none'}; private_key_file={s.cert_key or 'none/separate key not selected'}")
            pin = s.tls_cert_sha256 if _site_pin_applies(s, host) else ""
            if pin:
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ctx.minimum_version = ssl.TLSVersion.TLSv1_2
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            else:
                ctx = ssl.create_default_context(cafile=s.ca_file or None)
                ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            if identity:
                from .key_store import load_tls_identity
                load_tls_identity(ctx, *identity)
            elif s.cert_file:
                ctx.load_cert_chain(s.cert_file, s.cert_key or None)
            self.trace.event(f"TLS context ready: trust_store={ctx.cert_store_stats()}")
            self.ftp = SecureFTP(ctx, s.protocol == PROTOCOLS[2],
                                 "AUTH SSL" if s.protocol == PROTOCOLS[1] else "AUTH TLS", trace=self.trace,
                                 cert_pin=pin,
                                 timeout=self.connect_timeout, proxy=self.proxy,
                                 tcp_keepalive=self.tcp_keepalive, keepalive_interval=self.keepalive_interval,
                                 local_port_range=self.local_port_range)
        from .listing_options import select_encoding, set_ftp_encoding
        self.ftp.encoding = select_encoding(self.filename_encoding, self.encoding_fallback, s)
        if self.ident and not self.proxy:
            self.ftp.ident_register = lambda sock: self.ident.register(self, sock)
        self.ftp.smart_pasv = self.smart_pasv
        self.ftp.noop_during_transfer = self.noop_during_transfer
        self.ftp.send_buffer = self.transfer_options.send_kib * 1024
        self.ftp.receive_buffer = self.transfer_options.receive_kib * 1024
        self.ftp.compression_policy = self.compression_for_command
        if self.upnp_enabled and not self.proxy:
            from .upnp import PortMapper
            self.ftp.port_mapper = PortMapper(self.trace)
        self.phase = "TCP connection / server greeting / implicit TLS"
        self.trace.event(self.phase)
        source_address = (self.local_bind_ip, 0) if self.local_bind_ip else None
        self.ftp.connect(host, s.port, source_address=source_address)
        if self.bind_sockets:
            self.ftp.source_address = (self.local_bind_ip or self.ftp.sock.getsockname()[0], 0)
        self.ftp.timeout = self.command_timeout
        self.ftp.sock.settimeout(self.command_timeout)
        self.trace.event(f"Control connection open: peer={self.ftp.sock.getpeername()}")
        self.phase = "explicit TLS / FTP login"
        filename_encoding = self.ftp.encoding
        if s.utf8_username: self.ftp.encoding = 'utf-8'
        try:
            self.ftp.login(s.username, self.password)
        finally:
            self.ftp.encoding = filename_encoding
        self.trace.event("FTP authentication successful")
        if s.force_utf8:
            try:
                self.ftp.sendcmd("OPTS UTF8 ON")
                self.trace.event("Server accepted forced UTF-8 mode")
            except ftplib.all_errors as error:
                self.trace.event(f"Server did not accept OPTS UTF8 ON: {error}")
        features = ''
        if self.send_feat or self.filename_encoding == 'Automatic':
            try:
                features = self.ftp.sendcmd("FEAT")
                self.trace.event("FTP FEAT response received")
            except ftplib.all_errors as error:
                self.trace.event(f"FTP FEAT unavailable: {error}")
        encoding = select_encoding(self.filename_encoding, self.encoding_fallback, s, features)
        set_ftp_encoding(self.ftp, encoding)
        if encoding == 'utf-8' and self.filename_encoding == 'Automatic' and not s.force_utf8:
            try:
                self.ftp.sendcmd('OPTS UTF8 ON')
            except ftplib.error_perm:
                pass  # Some servers advertise UTF8 without implementing OPTS.
        self.trace.event(f'FTP filename encoding: {encoding}')
        if isinstance(self.ftp, SecureFTP):
            self.phase = "enabling encrypted data connections"
            self.ftp.prot_p()
            self.security = f"{self.ftp.sock.version()} · {self.ftp.sock.cipher()[0]} · verified"
        effective_passive = bool(s.passive)
        if self.proxy and s.protocol in (*PROTOCOLS[:3], PROTOCOLS[4]) and not effective_passive:
            # Active mode asks the server to connect back to the client. That
            # callback cannot traverse the configured client-side proxy tunnel.
            effective_passive = True
            self.trace.event("Using passive data mode because FTP active callbacks cannot traverse the configured proxy")
        self.ftp.set_pasv(effective_passive)
        from .xdupe import configure
        configure(self)
        self.phase = "connected"
        self.trace.event(f"Connection ready: {self.security}")

    def close(self):
        if self.ident:
            self.ident.release(self)
        for obj in (self.sftp, self.ssh, self.ftp):
            if obj:
                try:
                    obj.close()
                except Exception:
                    pass
        self.trace.event("Connection closed")

    def bypass_compression_cache(self):
        options = self.transfer_options
        hosts = [self.site.host]
        if not self.proxy:
            sock = self.ftp.sock if self.ftp else self.ssh.get_transport().sock if self.ssh else None
            if sock:
                hosts.append(sock.getpeername()[0])
        return any(bypass_proxy(host, options.compression_bypass_local, options.compression_addresses) for host in hosts)

    def compression_for_command(self, command):
        if self.bypass_compression_cache():
            return False
        verb, _, path = command.partition(' ')
        options = self.transfer_options
        if verb.upper() in ('LIST', 'NLST', 'MLSD'):
            return options.compress_listing
        if verb.upper() not in ('STOR', 'APPE', 'STOU', 'RETR'):
            return False
        from .ascii_rules import matches
        rules = [{'pattern': mask.strip(), 'regex': False} for mask in re.split(r'[;\r\n]+', options.compression_masks) if mask.strip()]
        if matches(path, rules):
            return False
        return options.compress_download if verb.upper() == 'RETR' else options.compress_upload

    def download_buffering(self):
        return 0 if self.bypass_compression_cache() else self.transfer_options.download_buffer_kib * 1024

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

    def finish_download(self, partial, local, remote, cancel, ascii_mode):
        from .crc_check import verify_download
        verify_download(self, partial, getattr(self, 'verification_local_target', local), remote, cancel, ascii_mode)
        if self.transfer_options.keep_file_time:
            try:
                if self.sftp:
                    stamp = self.sftp.stat(remote).st_mtime
                else:
                    response = self.ftp.sendcmd('MDTM ' + remote)
                    value = response[4:].strip()
                    stamp = datetime.strptime(value, '%Y%m%d%H%M%S.%f' if '.' in value else '%Y%m%d%H%M%S').replace(tzinfo=timezone.utc).timestamp()
                os.utime(partial, (os.stat(partial).st_atime, stamp))
            except (OSError, ftplib.Error, ValueError) as error:
                self.note('Could not preserve server timestamp: ' + str(error))

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

    from .bandwidth import native_transfer

    @native_transfer
    def transfer(self, local, remote, upload, cancel: Event, progress, resume=False):
        options = self.transfer_options
        original_passive = self.ftp.passiveserver if self.ftp else None
        if options.force_active and self.ftp and self.proxy:
            raise ValueError("Force Active Mode cannot be used through a proxy.")
        try:
            for attempt in range(3):
                if cancel.is_set():
                    raise Cancelled("Cancelled")
                if self.ftp:
                    if options.force_active:
                        self.ftp.set_pasv(False)
                    self.ftp.data_timeout = options.stall_seconds if options.stall_resume else self.command_timeout
                elif self.sftp:
                    self.sftp.get_channel().settimeout(options.stall_seconds if options.stall_resume else self.command_timeout)
                try:
                    return self._transfer_once(local, remote, upload, cancel, progress, resume)
                except TimeoutError:
                    if not options.stall_resume or attempt == 2:
                        raise
                    self.trace.event("No transfer data before timeout; reconnecting to resume (maximum two retries)")
                    self.close()
                    if cancel.is_set():
                        raise Cancelled("Cancelled")
                    self.connect()
                    resume = not (self.ftp and options.ascii(remote) and options.ascii_no_resume)
        finally:
            if self.ftp and original_passive is not None:
                self.ftp.set_pasv(original_passive)
                self.ftp.data_timeout = None
            elif self.sftp:
                self.sftp.get_channel().settimeout(self.command_timeout)

    def _transfer_once(self, local, remote, upload, cancel: Event, progress, resume=False):
        options = self.transfer_options
        ascii_mode = self.ftp is not None and options.ascii(remote)
        if ascii_mode and options.ascii_no_resume:
            resume = False  # Text conversion changes byte offsets; restart safely.
        limiter = self.upload_limiter if upload else self.download_limiter
        verification_limiter = self.download_limiter
        if options.bypass_local:
            peer = self.site.host
            if not self.proxy:
                sock = self.ftp.sock if self.ftp else self.ssh.get_transport().sock
                peer = sock.getpeername()[0]
            if bypass_proxy(peer, True):
                limiter = None
                verification_limiter = None
        if self.ftp:
            self.ftp.last_transfer_noop = time.monotonic()
        if getattr(self, 'bandwidth_manager', None):
            from .bandwidth import ManagedLimiter
            limiter = ManagedLimiter(self.bandwidth_manager, 0 if upload else 1, limiter)
            verification_limiter = ManagedLimiter(self.bandwidth_manager, 1, verification_limiter)
        self.phase = "upload" if upload else "download"
        self.trace.event(f"Transfer start: direction={self.phase}; local={local}; remote={remote}; resume={resume}")
        local, remote = Path(local), remote_path(remote)
        if ascii_mode and resume:
            return self._resume_ascii(local, remote, upload, cancel, progress, limiter, verification_limiter)
        if upload:
            total = local.stat().st_size
            exists = any(e.name == posixpath.basename(remote) for e in self.list(posixpath.dirname(remote) or "/")) if resume else False
            self.phase = "upload"
            offset = (self.size(remote) or 0) if resume and exists else 0
            if offset > total:
                raise ValueError("Remote file exceeds local size; cannot resume")
            def chunks_sent(block):
                nonlocal offset
                if cancel.is_set():
                    raise Cancelled("Cancelled")
                offset += len(block)
                if self.ftp:
                    self.ftp.transfer_noop()
                if limiter:
                    limiter.consume(len(block), cancel)
                progress(offset, total)
            if self.sftp:
                with local.open("rb") as src, self.sftp.open(remote, "r+b" if resume and exists else "wb") as dst:
                    src.seek(offset)
                    dst.seek(offset)
                    while block := src.read(16384):
                        if cancel.is_set():
                            raise Cancelled("Cancelled")
                        dst.write(block)
                        chunks_sent(block)
            else:
                with local.open("rb") as src:
                    src.seek(offset)
                    if ascii_mode:
                        self._ascii_upload(src, remote, chunks_sent)
                    else:
                        self.ftp.storbinary("STOR " + remote, src, 16384, chunks_sent, rest=offset if resume else None)
        else:
            total = self.size(remote) or 0
            partial = local.with_name(local.name + ".fluxpart")
            if partial.is_symlink():
                raise ValueError("Partial download cannot be a symbolic link")
            offset = partial.stat().st_size if resume and partial.exists() else 0
            if offset > total:
                raise ValueError("Partial download exceeds remote size")
            offset = max(0, offset - options.rollback_kib * 1024)
            options.check_space(local, max(0, total - offset))
            with partial.open("r+b" if offset else "wb", buffering=self.download_buffering()) as dst:
                dst.seek(offset)
                dst.truncate(offset)
                def received(block):
                    nonlocal offset
                    if cancel.is_set():
                        raise Cancelled("Cancelled")
                    options.check_space(local, len(block))
                    dst.write(block)
                    offset += len(block)
                    if self.ftp:
                        self.ftp.transfer_noop()
                    if limiter:
                        limiter.consume(len(block), cancel)
                    progress(offset, total)
                if self.sftp:
                    with self.sftp.open(remote, "rb") as src:
                        src.seek(offset)
                        while block := src.read(16384):
                            received(block)
                else:
                    if ascii_mode:
                        self._ascii_download(remote, received)
                    else:
                        self.ftp.retrbinary("RETR " + remote, received, 16384, rest=offset if offset else None)
            if not ascii_mode and offset != total:
                raise IOError(f"Size mismatch: expected {total}, received {offset}")
            if cancel.is_set():
                raise Cancelled("Cancelled")
            self.finish_download(partial, local, remote, cancel, ascii_mode)
            partial.replace(local)
        if not ascii_mode:
            from .verification_reports import evidence
            actual = self.size(remote) if upload else offset
            evidence('size', 'verified' if actual == total else 'mismatch', total, actual)
            if actual != total: raise IOError("Remote size does not match the uploaded file")
        if cancel.is_set():
            raise Cancelled("Cancelled")
        progress(offset if ascii_mode else total, offset if ascii_mode else total)
        self.trace.event(f"Transfer complete: bytes={total}")

    def _resume_ascii(self, local, remote, upload, cancel, progress, limiter, verification_limiter):
        options = self.transfer_options
        done = 0
        if upload:
            total = local.stat().st_size
            existing = [entry for entry in self.list(posixpath.dirname(remote) or "/") if entry.name == posixpath.basename(remote)]
            if any(entry.is_dir or entry.is_link for entry in existing):
                raise ValueError("ASCII resume requires a regular remote file.")
            exists = bool(existing)
            with local.open("rb") as raw:
                source = NormalizedReader(raw)
                if exists:
                    def verify(block):
                        nonlocal done
                        if cancel.is_set():
                            raise Cancelled("Cancelled")
                        if source.read(len(block)) != block:
                            raise ValueError("ASCII resume refused: the existing remote file is not a matching prefix of the local file.")
                        done += len(block)
                        if verification_limiter:
                            verification_limiter.consume(len(block), cancel)
                    self._ascii_download(remote, verify, newline=b"\n")
                def sent(block):
                    nonlocal done
                    if cancel.is_set():
                        raise Cancelled("Cancelled")
                    done += len(block)
                    if limiter:
                        limiter.consume(len(block), cancel)
                    self.ftp.transfer_noop()
                    progress(done, max(done, total))
                self._ascii_upload(source, remote, sent, append=exists)
        else:
            partial = local.with_name(local.name + ".fluxpart")
            if partial.is_symlink():
                raise ValueError("Partial download cannot be a symbolic link")
            retained = max(0, (partial.stat().st_size if partial.exists() else 0) - options.rollback_kib * 1024)
            total = self.size(remote) or 0
            options.check_space(local, max(0, total - retained))
            remaining = retained
            prepared = False
            with partial.open("r+b" if partial.exists() else "w+b", buffering=self.download_buffering()) as target:
                def received(block):
                    nonlocal done, remaining, prepared
                    if cancel.is_set():
                        raise Cancelled("Cancelled")
                    if limiter:
                        limiter.consume(len(block), cancel)
                    take = min(remaining, len(block))
                    if take and target.read(take) != block[:take]:
                        raise ValueError("ASCII resume refused: the partial download does not match the remote file.")
                    remaining -= take
                    if not remaining and not prepared:
                        target.truncate(retained)
                        prepared = True
                    if block[take:]:
                        options.check_space(local, len(block) - take)
                        target.write(block[take:])
                    done += len(block)
                    self.ftp.transfer_noop()
                    progress(done, max(done, total))
                self._ascii_download(remote, received)
                if remaining:
                    raise ValueError("ASCII resume refused: the partial download exceeds the remote file.")
                if not prepared:
                    target.truncate(0)
            if cancel.is_set():
                raise Cancelled("Cancelled")
            self.finish_download(partial, local, remote, cancel, True)
            partial.replace(local)
        if cancel.is_set():
            raise Cancelled("Cancelled")
        progress(done, done)
        self.trace.event(f"ASCII transfer complete after verified-prefix resume: bytes={done}")

    def _ascii_upload(self, stream, remote, callback, append=False):
        self.ftp.voidcmd("TYPE A")
        pending = b""
        with self.ftp.transfercmd(("APPE " if append else "STOR ") + remote) as data:
            while block := stream.read(16384):
                raw = pending + block
                pending = b"\r" if raw.endswith(b"\r") else b""
                if pending:
                    raw = raw[:-1]
                data.sendall(raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
                callback(block)
            if pending:
                data.sendall(pending)
            if isinstance(data, ssl.SSLSocket):
                data.unwrap()
        self.ftp.voidresp()

    def _ascii_download(self, remote, callback, newline=None):
        newline = os.linesep.encode("ascii") if newline is None else newline
        self.ftp.voidcmd("TYPE A")
        pending = b""
        with self.ftp.transfercmd("RETR " + remote) as data:
            while block := data.recv(16384):
                raw = pending + block
                pending = b"\r" if raw.endswith(b"\r") else b""
                if pending:
                    raw = raw[:-1]
                callback(raw.replace(b"\r\n", b"\n").replace(b"\n", newline))
            if pending:
                callback(pending)
            if isinstance(data, ssl.SSLSocket):
                data.unwrap()
        self.ftp.voidresp()


@tracked_file
@xdupe_guard
def transfer_tree(source, destination, src, dst, is_dir, cancel, progress, resume=False, depth=0, options=None, skipped=None, renamed=None, planned=None, listed_size=None, skipped_directories=None, conflict=None, notice=None, file_started=None):
    """Copy between local/remote or two FTP servers without following directory links."""
    if cancel.is_set():
        raise Cancelled("Cancelled")
    if depth > 100:
        raise ValueError("Directory depth limit exceeded")
    options = options or (destination.transfer_options if destination else source.transfer_options if source else TransferOptions())
    name = posixpath.basename(src.rstrip("/")) if source else Path(src).name
    size = listed_size
    if options.skip_enabled and options.skip_zero and not is_dir and size is None:
        try:
            size = source.size(src) if source else Path(src).stat().st_size
        except ftplib.error_perm:
            # Servers without SIZE can still transfer; unknown size is not zero.
            size = None
    reason = options.skip_reason(name, is_dir, size, automatic=depth > 0)
    if reason:
        if is_dir and skipped_directories is not None:
            skipped_directories.add(src)
        if skipped:
            skipped(src, reason)
        return False
    if not is_dir:
        if name.lower().endswith('.sfv') and (options.single_sfv or options.skip_multiple_sfv):
            from .crc_check import sfv_names
            parent = (posixpath.dirname(src) or '/') if source else Path(src).parent
            siblings = source.list(parent) if source else [Entry(p.name, p.is_dir(), is_link=p.is_symlink()) for p in parent.iterdir()]
            if name not in sfv_names(siblings, options):
                if skipped: skipped(src, 'Misc: SFV selection rule')
                return False
        if planned is not None:
            planned.append((src, dst, size))
            return True
        if file_started:
            file_started(src, dst)
        if options.exists_enabled:
            from .file_exists import transfer_existing
            return transfer_existing(source, destination, src, dst, cancel, progress, resume, options, skipped, renamed, conflict, notice)
        if options.atomic_upload or options.hash_verification != "Off":
            from .transfer_reliability import transfer
            return transfer(source, destination, src, dst, cancel, progress, resume, options, notice)
        if source and destination:
            if getattr(source, 'is_cloud', False) or getattr(destination, 'is_cloud', False):
                from .cloud_connection import relay
                # Object stores do not support appending at an arbitrary byte offset.
                if resume and any(e.name == posixpath.basename(dst) for e in destination.list(posixpath.dirname(dst) or '/')):
                    raise ValueError('Cloud relay cannot resume an existing destination; choose overwrite or enable resume-failure overwrite.')
                relay(source, destination, src, dst, cancel, progress, resume=False)
            elif getattr(source, 'sftp', None) or getattr(destination, 'sftp', None):
                from .remote_relay import relay
                relay(source, destination, src, dst, cancel, progress, resume)
            else:
                fxp(source, destination, src, dst, resume=resume, cancel=cancel, progress=progress)
        elif destination:
            destination.transfer(src, dst, True, cancel, progress, resume)
        elif source:
            source.transfer(dst, src, False, cancel, progress, resume)
        else:
            source_path, destination_path = Path(src), Path(dst)
            if source_path.resolve() == destination_path.resolve():
                raise ValueError("Source and destination are the same file")
            if destination_path.is_symlink():
                raise ValueError("Refusing to overwrite a symbolic link")
            partial = destination_path.with_name(destination_path.name + ".fluxpart")
            if partial.is_symlink():
                raise ValueError("Partial copy cannot be a symbolic link")
            total = source_path.stat().st_size
            offset = partial.stat().st_size if resume and partial.exists() else 0
            if offset > total:
                raise ValueError("Partial copy exceeds source size")
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            options.check_space(dst, total - offset)
            with source_path.open("rb") as input_file, partial.open("ab" if offset else "wb") as output_file:
                input_file.seek(offset)
                while block := input_file.read(262144):
                    if cancel.is_set():
                        raise Cancelled("Cancelled")
                    options.check_space(dst, len(block))
                    output_file.write(block)
                    offset += len(block)
                    progress(offset, total)
            if offset != total:
                from .verification_reports import evidence
                evidence('size', 'mismatch', total, offset)
                raise IOError(f"Size mismatch: expected {total}, copied {offset}")
            from .verification_reports import evidence
            evidence('size', 'verified', total, offset)
            if cancel.is_set():
                raise Cancelled("Cancelled")
            partial.replace(destination_path)
            import shutil
            shutil.copystat(source_path, destination_path)
            progress(total, total)
        return True
    if source:
        entries = source.list(src)
    else:
        entries = [Entry(p.name, p.is_dir(), p.stat().st_size, is_link=p.is_symlink()) for p in Path(src).iterdir()]
    if source and not entries and re.search(r'-\s*COMPLETE\s*-', name, re.IGNORECASE):
        if skipped_directories is not None:
            skipped_directories.add(src)
        if skipped:
            skipped(src, 'Empty COMPLETE marker folder')
        return False
    reason = options.skip_reason(name, True, automatic=depth > 0, empty=not entries)
    if reason:
        if skipped_directories is not None:
            skipped_directories.add(src)
        if skipped:
            skipped(src, reason)
        return False
    kept = []
    for entry in entries:
        reason = options.skip_reason(entry.name, entry.is_dir, automatic=True)
        if reason:
            if entry.is_dir and skipped_directories is not None:
                skipped_directories.add(posixpath.join(src, entry.name) if source else str(Path(src) / entry.name))
            if skipped:
                skipped(posixpath.join(src, entry.name) if source else str(Path(src) / entry.name), reason)
        else:
            kept.append(entry)
    entries = kept
    created = False
    if destination:
        parent = posixpath.dirname(dst.rstrip("/")) or "/"
        name = posixpath.basename(dst.rstrip("/"))
        matches = [e for e in destination.list(parent) if e.name == name]
        if matches:
            if not matches[0].is_dir or matches[0].is_link:
                raise ValueError(f"Destination is not a directory: {dst}")
        else:
            destination.mkdir(dst)
            created = True
    else:
        if Path(dst).is_symlink():
            raise ValueError("Refusing to write through a directory symlink")
        created = not Path(dst).exists()
        if created and options.deny_parent_name and Path(dst).name.casefold() == Path(dst).parent.name.casefold():
            raise ValueError('New directory cannot have the same name as its parent')
        Path(dst).mkdir(parents=True, exist_ok=True)
    if destination and options.remove_missing_zero:
        from .misc_transfer import remove_missing_zero
        remove_missing_zero(source, destination, src, dst, options, cancel)
    route = "FXP" if source and destination else "Upload" if destination else "Download" if source else "Copy"
    entries.sort(key=lambda entry: options.priority(entry.name))
    converted_names = [options.filename(e.name, route) for e in entries]
    compared = [name.casefold() for name in converted_names] if not destination and os.name == "nt" else converted_names
    if len(compared) != len(set(compared)):
        raise ValueError("Filename conversion would overwrite another file in this folder.")
    planned_before = len(planned) if planned is not None else 0
    for e, target_name in zip(entries, converted_names):
        safe_name(e.name)
        safe_name(target_name)
        if e.is_link:
            raise ValueError(f"Symbolic links are not transferred: {e.name}")
        s = posixpath.join(src, e.name) if source else str(Path(src) / e.name)
        d = posixpath.join(dst, target_name) if destination else str(Path(dst) / target_name)
        transfer_tree(source, destination, s, d, e.is_dir, cancel, progress, resume, depth + 1, options, skipped, renamed, planned, e.size, skipped_directories, conflict, notice, file_started)
    if created and options.no_empty_dirs and (planned is None or len(planned) == planned_before):
        if destination:
            if not destination.list(dst): destination.remove(dst, is_dir=True)
        elif not any(Path(dst).iterdir()):
            Path(dst).rmdir()
    return True


def verify_sfv(manifest):
    """Verify entries confined to the manifest directory."""
    manifest = Path(manifest)
    results = []
    root = manifest.parent.resolve()
    for line in manifest.read_text("utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        name, expected = line.rsplit(None, 1)
        if len(expected) != 8:
            raise ValueError(f"Invalid SFV checksum: {expected}")
        int(expected, 16)
        file = (root / name).resolve()
        if not file.is_relative_to(root):
            raise ValueError("SFV entry escapes the manifest directory")
        if not file.is_file():
            results.append((name, "Missing"))
            continue
        checksum = 0
        with file.open("rb") as stream:
            while block := stream.read(1048576):
                checksum = zlib.crc32(block, checksum)
        results.append((name, "OK" if f"{checksum:08x}" == expected.lower() else "Mismatch"))
    return results


def fxp(source: Connection, destination: Connection, src, dst, resume=False, cancel=None, progress=None):
    """Cancel blocked control reads by shutting down owned transfer connections."""
    from threading import Thread
    cancel = cancel or Event()
    finished = Event()
    def watch():
        while not finished.wait(.05):
            if cancel.is_set():
                for connection in (source, destination):
                    sock = getattr(connection.ftp, 'sock', None)
                    if sock:
                        try: sock.shutdown(socket.SHUT_RDWR)
                        except OSError: pass
                return
    if cancel.is_set(): raise Cancelled('Cancelled')
    watcher = Thread(target=watch, daemon=True)
    watcher.start()
    try:
        _fxp(source, destination, src, dst, resume)
        if cancel.is_set(): raise Cancelled('Cancelled')
        if progress:
            size = source.size(src) or 0
            progress(size, size)
    except Exception:
        source.close()
        destination.close()
        if cancel.is_set(): raise Cancelled('Cancelled') from None
        raise
    finally:
        finished.set()
        watcher.join()


def _fxp(source: Connection, destination: Connection, src, dst, resume=False):
    """Direct server-to-server transfer; no clear-data fallback for FTPS."""
    if source.ftp is None or destination.ftp is None:
        raise ValueError("Direct FXP requires FTP/FTPS at both ends")
    a, b = source.ftp, destination.ftp
    source.phase = destination.phase = "FXP server-to-server transfer"
    source.trace.event(f"FXP requested: destination_host={destination.site.host}; source_path={src}; destination_path={dst}")
    if isinstance(a, SecureFTP) != isinstance(b, SecureFTP):
        raise ValueError("FXP requires matching data protection on both servers")
    if (source.site.host.casefold(), source.site.port, source.site.username, posixpath.normpath(src)) == (destination.site.host.casefold(), destination.site.port, destination.site.username, posixpath.normpath(dst)):
        raise ValueError("FXP source and destination are the same file")
    expected = source.size(src)
    ascii_mode = source.transfer_options.ascii(src)
    offset = 0
    if resume:
        if ascii_mode:
            if not source.transfer_options.ascii_no_resume:
                raise ValueError("Verified ASCII FXP resume is unavailable; enable overwrite fallback to restart.")
        else:
            existing = [e for e in destination.list(posixpath.dirname(dst) or '/') if e.name == posixpath.basename(dst)]
            if any(e.is_dir or e.is_link for e in existing):
                raise ValueError("FXP resume requires a regular destination file")
            offset = destination.size(dst) if existing else 0
            if offset is None or expected is None or offset > expected:
                raise ValueError("FXP destination exceeds source size or size is unknown")
    a.voidcmd("TYPE A" if ascii_mode else "TYPE I")
    b.voidcmd("TYPE A" if ascii_mode else "TYPE I")
    compressed = source.compression_for_command('RETR ' + src) and destination.compression_for_command('STOR ' + dst)
    if compressed:
        source_mode = a.set_compression(True)
        destination_mode = b.set_compression(True)
        compressed = source_mode and destination_mode
    if not compressed:
        a.set_compression(False)
        b.set_compression(False)
    from .fxp_roles import source_passive
    reverse = source_passive(source.site, destination.site)
    active_ftp, passive_ftp = (b, a) if reverse else (a, b)
    active_connection, passive_connection = (destination, source) if reverse else (source, destination)
    sscn = False
    passive = None
    if isinstance(a, SecureFTP):
        # SSCN lets the source act as TLS client for the protected data connection.
        try:
            a.voidcmd("SSCN ON")
            sscn = True
            source.trace.event('Protected FXP: source SSCN TLS client')
        except ftplib.error_perm as error:
            if str(error)[:3] not in ('500', '502', '504'):
                raise
            try:
                passive = ftplib.parse227(passive_ftp.sendcmd('CPSV'))
                passive_ftp.advertised_passive = passive
                if getattr(passive_ftp, 'smart_pasv', False):
                    passive = (passive_ftp.host if getattr(passive_ftp, 'proxy', None) else passive_ftp.sock.getpeername()[0], passive[1])
                passive_connection.trace.event('Protected FXP: passive endpoint CPSV TLS client')
            except ftplib.error_perm as fallback:
                raise ftplib.error_perm('Protected FXP requires active endpoint SSCN or passive endpoint CPSV: ' + str(fallback)) from error
    completed = False
    try:
        host, port = passive or passive_ftp.makepasv()
        active_ftp.sendport(host, port)
        if offset:
            b.sendcmd(f"REST {offset}")
        if offset:
            a.sendcmd(f"REST {offset}")
        # Some servers wait for the data connection before their preliminary
        # STOR reply. Start both endpoints before reading either reply so the
        # source can connect to the passive destination (including TLS).
        b.putcmd("STOR " + remote_path(dst))
        a.putcmd("RETR " + remote_path(src))
        for endpoint in (b, a):
            response = endpoint.getresp()
            if not response.startswith('1'):
                raise ftplib.error_reply(response)
        a.voidresp()
        b.voidresp()
        completed = True
    finally:
        if sscn and completed:
            try:
                a.voidcmd("SSCN OFF")
            except (OSError, ftplib.Error):
                pass
    if not ascii_mode and destination.size(dst) != expected:
        raise IOError("FXP destination size does not match the source")

