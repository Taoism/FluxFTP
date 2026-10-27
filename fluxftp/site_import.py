"""Read exported connection profiles without importing passwords or executing settings."""
from __future__ import annotations

import configparser
import csv
import io
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .core import PROTOCOLS, Site

MAX_BYTES = 8 * 1024 * 1024
MAX_SITES = 5000


@dataclass
class ImportResult:
    source: str
    sites: list[Site] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skipped: int = 0

    def add(self, number, factory):
        try:
            site, notes = factory()
            validate_site(site)
        except (ValueError, TypeError, KeyError):
            self.skipped += 1
            self.warnings.append(f"Entry {number}: skipped; invalid fields or unsupported connection settings.")
            return
        if len(self.sites) >= MAX_SITES:
            raise ValueError("The import exceeds the 5,000-site limit.")
        self.sites.append(site)
        self.warnings.extend(f"Entry {number}: {note}" for note in notes)


def validate_site(site):
    from .site_options import decode
    decode(site.site_options)
    for item in fields(Site):
        value = getattr(site, item.name)
        if item.name in ("port", "tls_pin_port", "passive", "force_utf8", "utf8_username", "single_connection",
                         "max_transfers_total", "max_downloads", "max_uploads", "use_masq"):
            continue
        allowed_controls = '\r\n\t' if item.name == 'description' else ''
        if not isinstance(value, str) or any((ord(c) < 32 and c not in allowed_controls) or ord(c) == 127 for c in value):
            raise ValueError(f"Invalid text in site field: {item.name.replace('_', ' ')}.")
    if not site.name.strip() or not site.host.strip() or any(c.isspace() for c in site.host) or any(c in site.host for c in "/\\@"):
        raise ValueError("A site needs a name and a hostname or IP address (without a URL).")
    if site.protocol not in PROTOCOLS:
        raise ValueError("Unsupported protocol.")
    from .cloud import CLOUD_PROTOCOLS
    if site.protocol in CLOUD_PROTOCOLS:
        if not re.fullmatch(r'[0-9a-f]{32}', site.cloud_account):
            raise ValueError('Select a configured cloud account for this provider.')
        if site.host != 'cloud-' + site.cloud_account:
            raise ValueError('Cloud site account identifier does not match its endpoint.')
        if site.managed_key:
            raise ValueError('Cloud accounts use provider authorization, not SSH/TLS managed keys.')
    if site.managed_key and (len(site.managed_key) != 32 or any(c not in '0123456789abcdef' for c in site.managed_key)):
        raise ValueError('Invalid managed key identifier.')
    if site.managed_key and site.protocol == PROTOCOLS[4]:
        raise ValueError('Plain FTP does not use managed SSH keys or TLS certificates.')
    if (type(site.port) is not int or not 1 <= site.port <= 65535 or type(site.passive) is not bool
            or type(site.tls_pin_port) is not int or not 0 <= site.tls_pin_port <= 65535
            or type(site.force_utf8) is not bool or type(site.utf8_username) is not bool
            or type(site.single_connection) is not bool
            or (site.use_masq is not None and type(site.use_masq) is not bool)
            or any(type(value) is not int or not 1 <= value <= 64 for value in
                   (site.max_transfers_total, site.max_downloads, site.max_uploads))):
        raise ValueError("Invalid port, transfer mode, or transfer limit.")
    if site.tls_cert_sha256 and (len(site.tls_cert_sha256) != 64 or
                                 any(char not in "0123456789abcdefABCDEF" for char in site.tls_cert_sha256)):
        raise ValueError("Invalid trusted TLS certificate fingerprint.")


def port_for(protocol):
    return 22 if protocol == PROTOCOLS[3] else 990 if protocol == PROTOCOLS[2] else 21


def merge_sites(existing, imported):
    """Return a new dictionary; never mutate or overwrite an existing profile."""
    merged = dict(existing)
    names = {n.casefold() for n in merged}
    for site in imported:
        validate_site(site)
        name = site.name
        suffix = 2
        while name.casefold() in names:
            name = f"{site.name} ({suffix})"
            suffix += 1
        names.add(name.casefold())
        merged[name] = asdict(replace(site, name=name))
    return merged


def decode_text(data):
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    return data.decode("utf-8-sig")


def filezilla_path(value):
    if not value:
        return "/"
    if value.startswith("/"):
        return value
    kind, prefix, rest = value.split(" ", 2)
    if kind not in ("0", "1") or prefix != "0":
        raise ValueError("Non-Unix path")
    parts = []
    while rest:
        length, rest = rest.split(" ", 1)
        size = int(length)
        if size < 1 or len(rest) < size:
            raise ValueError("Invalid encoded path")
        parts.append(rest[:size])
        rest = rest[size:]
        if rest:
            if not rest.startswith(" "):
                raise ValueError("Invalid path delimiter")
            rest = rest[1:]
    return "/" + "/".join(parts)


def filezilla(root):
    result = ImportResult("FileZilla XML")
    servers = root.find("Servers")
    if servers is None:
        raise ValueError("The FileZilla export contains no Sites section. Export Site Manager entries.")
    counter = 0
    def walk(parent, groups=()):
        nonlocal counter
        if len(groups) > 50:
            raise ValueError("The export contains too many nested site folders.")
        for node in parent:
            if node.tag == "Folder":
                walk(node, groups + ((node.text or "Folder").strip(),))
            elif node.tag == "Server":
                counter += 1
                def parse():
                    get = lambda k, d="": node.findtext(k, d).strip()
                    code = int(get("Protocol", "0"))
                    protocol = {0: PROTOCOLS[0], 1: PROTOCOLS[3], 3: PROTOCOLS[2], 4: PROTOCOLS[0], 6: PROTOCOLS[4]}[code]
                    if get("Logontype", "0") not in ("0", "1", "2", "5"):
                        raise ValueError("Unsupported authentication")
                    notes = []
                    if code == 0:
                        notes.append("Opportunistic FTP TLS upgraded to required AUTH TLS; no plaintext fallback.")
                    try:
                        path = filezilla_path(get("RemoteDir"))
                    except ValueError:
                        path = "/"
                        notes.append("Remote folder format not supported; initial folder reset to /.")
                    key = get("Keyfile")
                    if key.lower().endswith(".ppk"):
                        key = ""
                        notes.append("PuTTY key omitted; select an OpenSSH private key after import.")
                    return Site(name=" / ".join((*groups, get("Name") or get("Host"))), host=get("Host"),
                                port=int(get("Port") or port_for(protocol)), protocol=protocol,
                                username="anonymous" if get("Logontype", "0") == "0" else get("User"),
                                root=path, ssh_key=key, passive=get("PasvMode") != "MODE_ACTIVE"), notes
                result.add(counter, parse)
    walk(servers)
    return result


def winscp(text):
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error:
        raise ValueError("Invalid WinSCP INI export.") from None
    result = ImportResult("WinSCP INI")
    defaults = dict(parser["Sessions\\Default%20Settings"]) if parser.has_section("Sessions\\Default%20Settings") else {}
    for number, section in enumerate(parser.sections(), 1):
        if not section.startswith("Sessions\\") or unquote(section[9:]) == "Default Settings":
            continue
        def parse():
            values = {**defaults, **dict(parser[section])}
            get = lambda key, default="": unquote(values.get(key.lower(), str(default)))
            code = int(get("FSProtocol", 1))
            if int(get("ProxyMethod", 0)) or int(get("FtpProxyLogonType", 0)) or int(get("Tunnel", 0)) or int(get("IsWorkspace", 0)):
                raise ValueError("Proxy/tunnel/workspace unsupported")
            if code in (1, 2):
                protocol = PROTOCOLS[3]
            elif code == 5:
                protocol = {0: PROTOCOLS[4], 1: PROTOCOLS[2], 2: PROTOCOLS[1], 3: PROTOCOLS[0]}[int(get("Ftps", 0))]
            else:
                raise ValueError("Unsupported protocol")
            notes = []
            key = get("PublicKeyFile")
            if key.lower().endswith(".ppk"):
                key = ""
                notes.append("PuTTY key omitted; select an OpenSSH private key after import.")
            return Site(name=unquote(section[9:]).replace("/", " / "), host=get("HostName"),
                        port=int(get("PortNumber", port_for(protocol))), protocol=protocol,
                        username=get("UserName"), root=get("RemoteDirectory") or "/", ssh_key=key,
                        passive=get("FtpPasvMode", 1) != "0"), notes
        result.add(number, parse)
    return result


def urls(text):
    result = ImportResult("FTP URL list")
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        def parse():
            parsed = urlsplit(line)
            protocol = {"ftp": PROTOCOLS[4], "ftps": PROTOCOLS[2], "ftpes": PROTOCOLS[0], "sftp": PROTOCOLS[3]}[parsed.scheme.lower()]
            if parsed.query or parsed.fragment or ";" in parsed.netloc:
                raise ValueError("Unsupported URL settings")
            host = parsed.hostname or ""
            notes = ["FTP URL uses plaintext; select FTPS after import if the source site used TLS."] if protocol == PROTOCOLS[4] else []
            return Site(name=host, host=host, port=parsed.port or port_for(protocol), protocol=protocol,
                        username=unquote(parsed.username or "anonymous"), root=unquote(parsed.path) or "/"), notes
        result.add(number, parse)
    return result


def named_protocol(value):
    return {"ftp": PROTOCOLS[4], "ftps": PROTOCOLS[2], "ftpes": PROTOCOLS[0],
            "sftp": PROTOCOLS[3], "auth tls": PROTOCOLS[0], "auth ssl": PROTOCOLS[1]}[value.strip().lower()]


def ftprush(root):
    """FTP Rush 2 XML: GROUP/SITE nodes, SSL selector and sparse default fields."""
    result = ImportResult("FTP Rush XML")
    counter = 0

    def boolean(value, default):
        value = value.strip().lower()
        if not value:
            return default
        return {"true": True, "false": False, "1": True, "0": False}[value]

    def walk(node, groups=(), depth=0):
        nonlocal counter
        if depth > 50:
            raise ValueError("The export contains too many nested site folders.")
        if node.tag == "SITE":
            counter += 1

            def parse():
                get = lambda key, default="": node.findtext(key, default).strip()
                # FTP Rush 2 selector order: normal, AUTH SSL, implicit, AUTH TLS, SSH.
                protocol = {0: PROTOCOLS[4], 1: PROTOCOLS[1], 2: PROTOCOLS[2],
                            3: PROTOCOLS[0], 4: PROTOCOLS[3]}[int(get("SSL") or "0")]
                notes = []
                ssl_node = node.find("SSL")
                if protocol in PROTOCOLS[:3] and ssl_node is not None:
                    if not all(boolean(ssl_node.get(k, ""), True) for k in ("LIST", "ONDATA")):
                        notes.append("Data/listing encryption upgraded to required TLS protection.")
                if get("SSLFXP"):
                    notes.append("Review FXP settings: FluxFTP uses SSCN; FTP Rush's FXP preference is not imported.")
                host = get("HOST")
                name = node.get("NAME", "").strip() or host
                return Site(name=" / ".join((*groups, name)), host=host,
                            port=int(get("PORT") or port_for(protocol)), protocol=protocol,
                            username=get("USER") or "anonymous", root=get("RPATH") or "/",
                            passive=boolean(get("PASV"), True)), notes

            result.add(counter, parse)
            return
        for child in node:
            if child.tag == "GROUP":
                name = child.get("NAME", "").strip()
                walk(child, groups + ((name,) if name else ()), depth + 1)
            elif child.tag == "SITE":
                walk(child, groups, depth + 1)

    # The outer GROUP is the exported container, not an extra folder name.
    walk(root)
    return result


def flashfxp(root):
    result = ImportResult("FlashFXP XML")
    for number, node in enumerate(root.findall(".//SITE"), 1):
        def parse():
            get = lambda k, d="": node.findtext(k, d).strip()
            protocol = named_protocol(get("PROTOCOL", "FTP"))
            encryption = get("SSL", "NONE").upper()
            if protocol == PROTOCOLS[4]:
                protocol = {"NONE": PROTOCOLS[4], "": PROTOCOLS[4], "AUTH TLS": PROTOCOLS[0],
                            "AUTH SSL": PROTOCOLS[1], "IMPLICIT": PROTOCOLS[2], "IMPLICIT SSL": PROTOCOLS[2]}[encryption]
            group = get("GROUP").strip("/\\").replace("\\", " / ")
            name = node.get("NAME") or get("ADDRESS")
            return Site(name=f"{group} / {name}" if group else name, host=get("ADDRESS"),
                        port=int(get("PORT") or port_for(protocol)), protocol=protocol,
                        username=get("USERNAME", "anonymous"), root=get("REMOTEPATH") or "/",
                        passive=get("PASSIVE", "DEFAULT").upper() not in ("NO", "FALSE", "0", "OFF")), []
        result.add(number, parse)
    return result


def csv_sites(text):
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "host" not in reader.fieldnames or "protocol" not in reader.fieldnames:
        raise ValueError("CSV requires host and protocol headers. Optional: name, port, username, root, passive.")
    result = ImportResult("CSV")
    for number, row in enumerate(reader, 2):
        def parse():
            protocol = named_protocol(row.get("protocol") or "")
            mode = (row.get("passive") or "true").strip().lower()
            if mode not in ("true", "false", "1", "0"):
                raise ValueError("Invalid passive mode")
            return Site(name=row.get("name") or row["host"], host=row["host"],
                        port=int(row.get("port") or port_for(protocol)), protocol=protocol,
                        username=row.get("username") or "anonymous", root=row.get("root") or "/",
                        passive=mode in ("true", "1")), []
        result.add(number, parse)
    return result


def read_import(path):
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("Choose an export smaller than 8 MB.")
    probe = data.replace(b"\x00", b"").lstrip(b"\xef\xbb\xbf\xff\xfe \r\n\t")
    if probe.startswith(b"<"):
        if b"<!DOCTYPE" in probe.upper() or b"<!ENTITY" in probe.upper():
            raise ValueError("XML document types and entities are not supported.")
        try:
            root = ET.fromstring(data)
        except (ET.ParseError, ValueError):
            raise ValueError("Invalid XML site export.") from None
        if root.tag == "FileZilla3":
            result = filezilla(root)
        elif root.tag == "GROUP" or (root.tag == "SITE" and root.find("HOST") is not None):
            result = ftprush(root)
        elif root.tag == "SITES" and any(node.find("HOST") is not None for node in root.iter("SITE")):
            result = ftprush(root)
        elif root.tag == "SITES":
            result = flashfxp(root)
        else:
            raise ValueError("Unsupported XML site format. Choose a FileZilla, FTP Rush or FlashFXP site export.")
    else:
        try:
            text = decode_text(data)
        except UnicodeError:
            raise ValueError("Export must use UTF-8 or UTF-16 text encoding.") from None
        if text.lstrip().startswith("{"):
            try:
                doc = json.loads(text)
            except ValueError:
                raise ValueError("Invalid FluxFTP JSON export.") from None
            if doc.get("format") != "fluxftp-sites" or doc.get("version") != 1 or not isinstance(doc.get("sites"), list):
                raise ValueError("Unsupported FluxFTP export format.")
            result = ImportResult("FluxFTP JSON")
            allowed = {f.name for f in fields(Site)}
            for number, item in enumerate(doc["sites"], 1):
                def parse(item=item):
                    if not isinstance(item, dict):
                        raise ValueError("Invalid site object")
                    return Site(**{k: v for k, v in item.items() if k in allowed}), []
                result.add(number, parse)
        elif text.lstrip().startswith("["):
            result = winscp(text)
        elif Path(path).suffix.lower() == ".csv":
            result = csv_sites(text)
        else:
            result = urls(text)
    result.warnings.insert(0, "Passwords, stored trust decisions, proxy settings and automation commands are not imported. Review authentication and network settings before connecting.")
    return result


def export_sites(path, sites):
    clean = [asdict(Site(**{f.name: data[f.name] for f in fields(Site) if f.name in data})) for data in sites.values()]
    target = Path(path)
    target.write_text(json.dumps({"format": "fluxftp-sites", "version": 1, "sites": clean}, indent=2, ensure_ascii=False), "utf-8")
