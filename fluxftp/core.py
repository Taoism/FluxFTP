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
from .xdupe import guard as xdupe_guard
from .verification_reports import tracked_file


from .trust_service import PinnedCertificateChanged, CertificateTrustRequired, _site_pin_applies, _verify_tls_pin, tls_context
from .cloud import CLOUD_PROTOCOLS
from .connection_model import (PROTOCOLS, Site, Entry, Cancelled, safe_remote_name, safe_name,
                               normalize_host, remote_path)
from .protocol_connectors import ProtocolConnectionMixin
from .connection_transfer import ConnectionTransferMixin, transfer_tree
from .connection_operations import ConnectionOperationsMixin, parse_list_entry




class AsciiUploadRejected(ftplib.error_perm):
    """The server refused STOR before accepting any ASCII payload."""


def remove_remote_items(connection, items):
    """Preflight selected trees, then delete children before their directories."""
    plan, seen = [], set()

    def visit(path, is_dir, depth=0):
        path = posixpath.normpath(remote_path(path))
        if path in ('/', '.', '..') or not path.startswith('/'):
            raise ValueError('Refusing to delete a remote root or relative path.')
        if path in seen:
            return
        seen.add(path)
        if depth > 100 or len(seen) > 10000:
            raise ValueError('Remote deletion exceeds the directory scan limit.')
        if is_dir:
            if any(part.startswith('+') for part in path.split('/')):
                raise ValueError('Remote directory shortcuts cannot be recursively deleted.')
            for entry in connection.list(path):
                name = entry.name
                if not name or name in ('.', '..') or any(c in name for c in '/\\\r\n\x00'):
                    raise ValueError('Unsafe name in remote deletion listing.')
                if entry.is_link:
                    raise ValueError('Remote folder contains a symbolic link; remove it separately first.')
                visit(posixpath.join(path, name), entry.is_dir, depth + 1)
        plan.append((path, is_dir))

    for path, is_dir in items:
        visit(path, is_dir)
    for path, is_dir in plan:
        connection.remove(path, is_dir)


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
        return address.is_private or address.is_loopback or address.is_link_local
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
        # This credential-free probe retrieves a rejected certificate for explicit user review.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # NOSONAR
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = False  # NOSONAR
        context.verify_mode = ssl.CERT_NONE  # NOSONAR
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


class Connection(ProtocolConnectionMixin, ConnectionTransferMixin, ConnectionOperationsMixin):
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
    from .protocol_adapter import connection_capabilities
    if not all(connection_capabilities(connection).direct_fxp for connection in (source, destination)):
        raise ValueError('FXP requires two FTP/FTPS connections.')
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
    from .fxp_roles import source_passive_candidates
    sscn = False
    if isinstance(a, SecureFTP):
        # SSCN lets the source act as TLS client for the protected data connection.
        try:
            a.voidcmd("SSCN ON")
            sscn = True
            source.trace.event('Protected FXP: source SSCN TLS client')
        except ftplib.error_perm as error:
            if str(error)[:3] not in ('500', '502', '504'):
                raise
    candidates = source_passive_candidates(source.site, destination.site)
    setup_error = None
    for index, reverse in enumerate(candidates):
        active_ftp, passive_ftp = (b, a) if reverse else (a, b)
        active_connection, passive_connection = (destination, source) if reverse else (source, destination)
        try:
            passive = None
            if isinstance(a, SecureFTP) and not sscn:
                passive = ftplib.parse227(passive_ftp.sendcmd('CPSV'))
                passive_ftp.advertised_passive = passive
                if getattr(passive_ftp, 'smart_pasv', False):
                    passive = (passive_ftp.host if getattr(passive_ftp, 'proxy', None) else passive_ftp.sock.getpeername()[0], passive[1])
                passive_connection.trace.event('Protected FXP: passive endpoint CPSV TLS client')
            host, port = passive or passive_ftp.makepasv()
            active_ftp.sendport(host, port)
            break
        except (OSError, EOFError, ftplib.error_reply, ftplib.error_temp,
                ftplib.error_perm, ftplib.error_proto) as error:
            setup_error = error
            if index + 1 >= len(candidates):
                if isinstance(a, SecureFTP) and not sscn:
                    raise ftplib.error_perm('Protected FXP requires active endpoint SSCN or passive endpoint CPSV: ' + str(error)) from error
                raise
            source.trace.event(f'FXP data-route setup failed; trying reversed roles: {type(error).__name__}')
    else:
        raise setup_error or ftplib.error_temp('FXP data route could not be prepared')
    completed = False
    try:
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

