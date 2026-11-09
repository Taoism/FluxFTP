"""Construct protocol and cloud connections from FluxFTP's saved settings."""
from __future__ import annotations

from .core import Connection, bypass_proxy
from .site_options import SiteSettings
from .transfer_options import TransferOptions


class ConnectionFactory:
    """Translate app configuration and site policy into backend connections."""
    def __init__(self, window):
        self.window = window

    def create(self, site, password, direction=None):
        from .site_connections import transfer_account, custom_proxy
        window = self.window
        if direction:
            site, password = transfer_account(window, site, password, direction)
        preferences = SiteSettings(window.preferences, site)
        transfer_options = TransferOptions.from_settings(preferences)
        from .cloud import CLOUD_PROTOCOLS
        if site.protocol in CLOUD_PROTOCOLS:
            from .cloud_connection import CloudConnection
            rates = tuple(preferences.value('Transfer/' + route + '_kib', 10, type=int) * 1024
                if preferences.value('Transfer/limit_' + route, False, type=bool) else 0
                for route in ('upload', 'download'))
            connection = CloudConnection(site, window.cloud_service, transfer_options, rates)
            self._attach_trace(connection, site)
            return connection

        window.upload_limiter.configure(preferences.value("Transfer/upload_kib", 10, type=int) * 1024
                                        if preferences.value("Transfer/limit_upload", False, type=bool) else 0)
        window.download_limiter.configure(preferences.value("Transfer/download_kib", 10, type=int) * 1024
                                          if preferences.value("Transfer/limit_download", False, type=bool) else 0)
        bind_sockets = preferences.value("Connection/bind_sockets",
            preferences.value("Connection/bind_local_ip", False, type=bool), type=bool)
        local_bind_ip = ""
        if bind_sockets and preferences.value("Connection/bind_local_ip", False, type=bool):
            local_bind_ip = preferences.value("Connection/local_ip", "", type=str).strip()
            if not local_bind_ip:
                raise ValueError("Enter a local IP address, or uncheck 'Bind Local IP address to:' for automatic binding.")
        proxy_enabled = preferences.value("Connection/proxy_enabled", False, type=bool)
        proxy = custom_proxy(window, site)
        if proxy is None and proxy_enabled and not self.proxy_bypassed(site.host):
            proxy_host = preferences.value("Connection/proxy_host", "", type=str).strip()
            if not proxy_host:
                raise ValueError("Proxy is enabled but no proxy host is configured.")
            proxy = {
                "type": preferences.value("Connection/proxy_type", "SOCKS5", type=str),
                "host": proxy_host,
                "port": preferences.value("Connection/proxy_port", 1080, type=int),
                "username": preferences.value("Connection/proxy_username", "", type=str)
                    if preferences.value("Connection/proxy_auth", False, type=bool) else "",
                "password": window.proxy_password,
            }
        connection = Connection(
            site, password,
            identity_loader=window.key_access.resolve,
            ssh_trust=window.ssh_trust,
            connect_timeout=preferences.value("Connection/connect_timeout", 30, type=int),
            command_timeout=preferences.value("Connection/command_timeout", 200, type=int),
            list_mode=preferences.value("Connection/list_mode", "Default (LIST)", type=str),
            list_command=preferences.value("Connection/list_command", "LIST -al", type=str),
            max_retries=preferences.value("Connection/max_retries", 2, type=int),
            retry_delay=preferences.value("Connection/retry_delay", 3, type=int),
            send_feat=preferences.value("Connection/send_feat", True, type=bool),
            smart_pasv=(preferences.value("Connection/smart_pasv", False, type=bool)
                        if site.use_masq is None else site.use_masq),
            proxy=proxy,
            tcp_keepalive=preferences.value("Connection/tcp_keepalive", True, type=bool),
            keepalive_interval=preferences.value("Connection/keepalive_interval", 30, type=int),
            passive_fallback=preferences.value("Connection/passive_fallback", True, type=bool),
            local_bind_ip=local_bind_ip,
            bind_sockets=bind_sockets,
            upnp_enabled=preferences.value("General/upnp_enabled", False, type=bool),
            proxy_force_passive=preferences.value("Connection/proxy_passive", True, type=bool),
            noop_during_transfer=preferences.value("Connection/transfer_noop", False, type=bool),
            transfer_options=transfer_options,
            upload_limiter=window.upload_limiter,
            download_limiter=window.download_limiter,
            local_port_range=self.configured_local_port_range(),
            use_mlsd=preferences.value("Connection/use_mlsd", True, type=bool),
            filename_encoding=preferences.value('Connection/filename_encoding', 'Current site settings', type=str),
            encoding_fallback=preferences.value('Connection/encoding_fallback', 'cp1252', type=str),
            sftp_read_aheads=preferences.value('Connection/sftp_read_aheads', 50, type=int),
            mlsd_essential=preferences.value('Connection/mlsd_essential', False, type=bool),
            ident=window.ident,
        )
        self._attach_trace(connection, site)
        connection.bandwidth_manager = getattr(window, 'bandwidth_manager', None)
        return connection

    def configured_local_port_range(self):
        preferences = self.window.preferences
        if not preferences.value("Connection/limit_local_ports", False, type=bool):
            return None
        value = preferences.value("Connection/local_port_range", "", type=str)
        try:
            first_text, last_text = value.split("-", 1)
            first, last = int(first_text.strip()), int(last_text.strip())
        except (ValueError, AttributeError):
            raise ValueError("Enter the active local port range as two numbers, such as 1400-1500.")
        if not 1 <= first <= last <= 65535:
            raise ValueError("Active local port range must be within 1-65535 and in ascending order.")
        return first, last

    def proxy_bypassed(self, host):
        preferences = self.window.preferences
        return bypass_proxy(host,
            preferences.value("Connection/proxy_bypass_local", False, type=bool),
            preferences.value("Connection/proxy_exceptions", "", type=str))

    def _attach_trace(self, connection, site):
        window = self.window
        side = getattr(window, '_log_site_sides', {}).get(window._transfer_site_key(site), 1)
        trace = connection.trace
        trace.display_side = side
        trace.listener = lambda message, level: window.ftp_message.emit(
            ('ERROR: ' if level >= 40 else '') + message, trace.display_side)
