"""Protocol-specific connection setup shared by the native connection engine."""
import ftplib
import socket

from .connection_model import PROTOCOLS, normalize_host
from .trust_service import tls_context


class ProtocolConnectionMixin:
    """Implement native FTP/FTPS, SFTP, and SCP connection handshakes."""
    def _connect_protocol(self):
        from .core import LoggedFTP, SecureFTP, _configure_tcp_keepalive, open_proxy_socket
        identity = None
        if self.site.managed_key:
            if self.identity_loader is None:
                raise ValueError('This connection requires access to the managed key vault.')
            identity = self.identity_loader(self.site)
            self.trace.event('Managed client identity loaded from Key Manager')
            expected = 'ssh' if self.site.protocol in (PROTOCOLS[3], 'SCP · SSH') else 'tls'
            if self.site.protocol == PROTOCOLS[4] or identity[0]['kind'] != expected:
                raise ValueError('Managed key does not match the selected protocol.')
        s = self.site
        if self.proxy and s.protocol not in (PROTOCOLS[3], 'SCP · SSH') and not s.passive and not self.proxy_force_passive:
            raise ValueError("Active FTP through a proxy is not supported. Enable 'Use passive mode when using proxy' or select passive mode for this site.")
        host = normalize_host(s.host)
        if s.protocol not in PROTOCOLS:
            raise ValueError("Unsupported protocol")
        if s.protocol in (PROTOCOLS[3], 'SCP · SSH'):
            import paramiko
            self.phase = "SSH host-key configuration"
            self.trace.event(f"SSH configuration: known_hosts={s.known_hosts or 'system default'}; key_file={s.ssh_key or 'agent/default keys'}")
            self.ssh = paramiko.SSHClient()
            self.ssh.load_system_host_keys()
            self.ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
            if self.ssh_trust is not None:
                from .trust_service import VerifiedPolicy
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
            try:
                self.sftp = self.ssh.open_sftp()
            except Exception as error:
                if s.protocol == 'SCP · SSH':
                    raise ValueError('SCP mode requires SFTP for browsing and metadata, plus the remote scp command for payloads.') from error
                raise
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
            ctx, pin = tls_context(s, host)
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


