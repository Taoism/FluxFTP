import json
from dataclasses import asdict

import pytest

from fluxftp.core import Site, PROTOCOLS
from fluxftp.site_import import read_import, merge_sites, export_sites, MAX_BYTES


def load(tmp_path, text, name="export.xml", encoding="utf-8"):
    path = tmp_path / name
    path.write_text(text, encoding=encoding)
    return read_import(path)


@pytest.mark.parametrize("code,expected", [(0, 0), (1, 3), (3, 2), (4, 0), (6, 4)])
def test_filezilla_protocols_and_folders(tmp_path, code, expected):
    result = load(tmp_path, f'''<FileZilla3><Servers><Folder>Work
      <Server><Name>Media</Name><Host>ftp.example.test</Host><Protocol>{code}</Protocol>
      <User>alice</User><Logontype>1</Logontype><Pass encoding="base64">SECRET</Pass>
      <RemoteDir>1 0 5 media 9 with gaps</RemoteDir><PasvMode>MODE_ACTIVE</PasvMode></Server>
      </Folder></Servers></FileZilla3>''')
    site = result.sites[0]
    assert site.name == "Work / Media"
    assert site.protocol == PROTOCOLS[expected]
    assert site.root == "/media/with gaps"
    assert not site.passive
    assert "SECRET" not in repr(result)


@pytest.mark.parametrize("ftps,expected", [(0, 4), (1, 2), (2, 1), (3, 0)])
def test_winscp_tls_mapping_and_defaults(tmp_path, ftps, expected):
    result = load(tmp_path, f'''[Sessions\\Default%20Settings]
FSProtocol=5
FtpPasvMode=0
[Sessions\\Work%2FMedia%20Server]
HostName=media.example.test
UserName=alice
Password=DO_NOT_IMPORT
Ftps={ftps}
RemoteDirectory=/media
''', "WinSCP.ini", "utf-16")
    site = result.sites[0]
    assert site.name == "Work / Media Server"
    assert site.protocol == PROTOCOLS[expected]
    assert not site.passive
    assert "DO_NOT_IMPORT" not in repr(result)


def test_skip_unsupported_winscp_and_proxy(tmp_path):
    result = load(tmp_path, '''[Sessions\\Good]
HostName=ssh.example.test
PublicKeyFile=key.ppk
[Sessions\\SCP]
HostName=ssh.example.test
FSProtocol=0
[Sessions\\Proxy]
HostName=ssh.example.test
ProxyMethod=2
''', "WinSCP.ini")
    assert len(result.sites) == 1
    assert result.skipped == 2
    assert result.sites[0].protocol == PROTOCOLS[3]
    assert result.sites[0].ssh_key == ""


def test_url_list_ipv6_and_percent_encoding(tmp_path):
    result = load(tmp_path, '''# FTP Rush text export
ftpes://alice:SECRET@[::1]:2121/folder%20name
ftp://anonymous@example.test/
not a url with SECRET
''', "rush.txt")
    assert len(result.sites) == 2
    assert result.skipped == 1
    assert result.sites[0].host == "::1"
    assert result.sites[0].port == 2121
    assert result.sites[0].root == "/folder name"
    assert "SECRET" not in repr(result)


def test_flashfxp_xml(tmp_path):
    result = load(tmp_path, '''<SITES><SITE NAME="Archive"><GROUP>\\Work\\</GROUP>
<PROTOCOL>FTP</PROTOCOL><ADDRESS>archive.example.test</ADDRESS><PORT>2121</PORT>
<USERNAME>alice</USERNAME><PASSWORD>SECRET</PASSWORD><SSL>AUTH SSL</SSL>
<PASSIVE>NO</PASSIVE><REMOTEPATH>/incoming</REMOTEPATH></SITE></SITES>''', "sites.ftp")
    site = result.sites[0]
    assert site.name == "Work / Archive"
    assert site.protocol == PROTOCOLS[1]
    assert site.root == "/incoming"
    assert not site.passive
    assert "SECRET" not in repr(result)


@pytest.mark.parametrize("code,expected", [(0, 4), (1, 1), (2, 2), (3, 0), (4, 3)])
def test_ftprush_native_xml_protocols(tmp_path, code, expected):
    result = load(tmp_path, f'''<?xml version="1.0" encoding="utf-8"?>
<GROUP NAME="Sites"><GROUP NAME="Work"><GROUP NAME="Archive">
<SITE NAME="Main" UID="dummy"><HOST>ftp.example.test</HOST><PORT>2121</PORT>
<USER>alice</USER><PASS>PRIVATE_PASSWORD</PASS><SSL LIST="True" ONDATA="True">{code}</SSL>
<PASV>False</PASV><RPATH>/incoming</RPATH><SSLFXP>1</SSLFXP><PROXYD>False</PROXYD>
<MAXTRANSFER LOGIN="2"/><STATIC DN="0"/></SITE></GROUP></GROUP></GROUP>''', "sites.xml")
    assert result.source == "FTP Rush XML"
    assert result.skipped == 0
    site = result.sites[0]
    assert site.name == "Work / Archive / Main"
    assert site.host == "ftp.example.test"
    assert site.port == 2121
    assert site.protocol == PROTOCOLS[expected]
    assert site.username == "alice"
    assert site.root == "/incoming"
    assert not site.passive
    assert "PRIVATE_PASSWORD" not in repr(result)


def test_ftprush_sparse_defaults_unknown_modes_and_secure_upgrade(tmp_path):
    result = load(tmp_path, '''<GROUP NAME="Sites">
<SITE NAME="Anonymous"><HOST>public.test</HOST></SITE>
<GROUP NAME="Empty"/>
<SITE NAME="TLS"><HOST>tls.test</HOST><SSL ONDATA="False" LIST="False">3</SSL></SITE>
<SITE NAME="Unknown"><HOST>other.test</HOST><SSL>99</SSL></SITE>
</GROUP>''', "sites.xml")
    assert len(result.sites) == 2
    assert result.skipped == 1
    assert result.sites[0] == Site(name="Anonymous", host="public.test", protocol=PROTOCOLS[4])
    assert result.sites[1].protocol == PROTOCOLS[0]
    assert any("encryption upgraded" in note for note in result.warnings)


def test_ftprush_utf16_and_container_detection(tmp_path):
    xml = '<SITES><SITE NAME="Archive"><HOST>archive.test</HOST><SSL>2</SSL></SITE></SITES>'
    result = load(tmp_path, xml, "sites.xml", encoding="utf-16")
    assert result.source == "FTP Rush XML"
    assert result.sites[0].port == 990


def test_csv(tmp_path):
    result = load(tmp_path, 'name,host,protocol,port,username,root,passive\n"Media, UK",media.test,ftpes,2121,alice,/files,true\n', "sites.csv")
    assert result.sites[0].name == "Media, UK"
    assert result.sites[0].port == 2121
    assert result.sites[0].protocol == PROTOCOLS[0]


def test_duplicates_and_export_roundtrip(tmp_path):
    existing = {"Work": asdict(Site(name="Work", host="old.test"))}
    merged = merge_sites(existing, [Site(name="Work", host="new.test"), Site(name="work", host="third.test")])
    assert set(merged) == {"Work", "Work (2)", "work (3)"}
    assert existing["Work"]["host"] == "old.test"
    assert len(existing) == 1
    path = tmp_path / "flux.json"
    merged["Work"]["password"] = "SECRET"
    export_sites(path, merged)
    assert "SECRET" not in path.read_text("utf-8")
    imported = read_import(path)
    assert len(imported.sites) == 3


def test_invalid_and_unknown_entries_skipped(tmp_path):
    result = load(tmp_path, '''<FileZilla3><Servers>
<Server><Name>Cloud</Name><Host>cloud.test</Host><Protocol>7</Protocol></Server>
<Server><Host>bad.test</Host><Port>70000</Port></Server>
<Server><Host>valid.test</Host><Protocol>4</Protocol></Server>
</Servers></FileZilla3>''')
    assert len(result.sites) == 1
    assert result.skipped == 2
    result = load(tmp_path, json.dumps({"format":"fluxftp-sites", "version":1, "sites":[None, {"host":"bad\nhost"}]}), "sites.json")
    assert result.skipped == 2


@pytest.mark.parametrize("content", ['<!DOCTYPE sites [<!ENTITY s "secret">]><FileZilla3/>', '<FileZilla3><SECRET>', '<Unknown/>'])
def test_bad_xml_safe_errors(tmp_path, content):
    with pytest.raises(ValueError) as error:
        load(tmp_path, content)
    assert "SECRET" not in str(error.value)


def test_size_limit(tmp_path):
    with pytest.raises(ValueError, match="8 MB"):
        load(tmp_path, "x" * (MAX_BYTES + 1), "oversize.txt")
