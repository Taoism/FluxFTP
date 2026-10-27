"""Temporary, owned UPnP mappings for active IPv4 FTP listeners."""
import ipaddress
import uuid

import miniupnpc


class PortMapper:
    def __init__(self, trace):
        self.trace = trace
        self.gateway = None
        self.description = "FluxFTP-" + uuid.uuid4().hex
        self.mappings = {}

    def map(self, host, port):
        try:
            if ipaddress.ip_address(host).version != 4:
                raise ValueError("UPnP port mapping requires IPv4.")
            if self.gateway is None:
                gateway = miniupnpc.UPnP(multicastif=host, discoverdelay=1000)
                gateway.discover()
                gateway.selectigd()
                self.gateway = gateway
            gateway = self.gateway
            external = str(ipaddress.IPv4Address(gateway.externalipaddress()))
            if external == "0.0.0.0":
                raise ValueError("The router has no external IPv4 address.")
            existing = gateway.getspecificportmapping(port, "TCP")
            if existing and (port not in self.mappings or tuple(existing[:3]) != (host, port, self.description)):
                raise ValueError(f"Router TCP port {port} already has a mapping. Choose another local port range.")
            if not gateway.addportmapping(port, "TCP", host, port, self.description, "", 3600):
                raise ValueError("The router refused the port mapping.")
            self.mappings[port] = host
            self.trace.event(f"UPnP mapped TCP {external}:{port} to {host}:{port}")
            return external
        except Exception as error:
            raise OSError(f"UPnP mapping failed: {error}") from error

    def close(self):
        for port, host in list(self.mappings.items()):
            try:
                existing = self.gateway.getspecificportmapping(port, "TCP")
                if existing and tuple(existing[:3]) == (host, port, self.description):
                    if not self.gateway.deleteportmapping(port, "TCP"):
                        raise OSError("Router refused mapping removal")
                    self.trace.event(f"UPnP removed TCP mapping {port}")
            except Exception as error:
                self.trace.event(f"UPnP cleanup failed for TCP {port}: {error}; lease expires within one hour")
            finally:
                self.mappings.pop(port, None)
