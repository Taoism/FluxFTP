"""Build a pinned, statically linked Windows miniupnpc wheel from upstream source."""
import argparse
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
VERSION = '2.3.5'
SOURCE_URL = f'https://miniupnp.tuxfamily.org/files/miniupnpc-{VERSION}.tar.gz'
SOURCE_SHA256 = 'b9a1e57233216c5f62572de6d628e73e910f50cdb71e9c0eb89b169ce154d256'
MAX_SOURCE_BYTES = 1024 * 1024
SOURCES = (
    'addr_is_reserved.c', 'connecthostport.c', 'igd_desc_parse.c', 'minisoap.c',
    'minissdpc.c', 'miniupnpc.c', 'miniwget.c', 'minixml.c', 'portlistingparse.c',
    'receivedata.c', 'upnpcommands.c', 'upnpdev.c', 'upnperrors.c', 'upnpreplyparse.c',
)


def download_source(destination):
    request = Request(SOURCE_URL, headers={'User-Agent': 'FluxFTP pinned dependency build'})
    with urlopen(request, timeout=30) as response:
        archive = response.read(MAX_SOURCE_BYTES + 1)
    if len(archive) > MAX_SOURCE_BYTES:
        raise RuntimeError('The miniupnpc source archive exceeded the size limit.')
    if hashlib.sha256(archive).hexdigest() != SOURCE_SHA256:
        raise RuntimeError('The miniupnpc source archive SHA-256 did not match the pinned release.')
    destination.write_bytes(archive)


def extract_source(archive, destination):
    expected_root = f'miniupnpc-{VERSION}'
    destination.mkdir(parents=True)
    with tarfile.open(archive, 'r:gz') as bundle:
        members = bundle.getmembers()
        if not members or any(Path(item.name).parts[0] != expected_root for item in members):
            raise RuntimeError('The miniupnpc archive contains an unexpected top-level path.')
        bundle.extractall(destination, members=members, filter='data')
    source = destination / expected_root
    if (source / 'VERSION').read_text(encoding='ascii').strip() != VERSION:
        raise RuntimeError('The extracted miniupnpc version did not match the pin.')
    return source


def write_windows_setup(source):
    os_version = platform.version().replace('\\', '').replace('"', '')
    version_header = (source / 'miniupnpcstrings.h.in').read_text(encoding='ascii')
    version_header = version_header.replace('"OS/version"', f'"MSWindows/{os_version}"')
    version_header = version_header.replace('"version"', f'"{VERSION}"')
    (source / 'miniupnpcstrings.h').write_text(version_header, encoding='ascii')
    sources = ['src/miniupnpcmodule.c', *(f'src/{name}' for name in SOURCES)]
    setup_script = '''from setuptools import Extension, setup

setup(
    name="miniupnpc",
    version="2.3.5",
    ext_modules=[Extension(
        "miniupnpc",
        sources={sources!r},
        include_dirs=["include", "."],
        define_macros=[("MINIUPNP_STATICLIB", "1"), ("_WIN32_WINNT", "0x0601")],
        libraries=["ws2_32", "iphlpapi"],
    )],
    zip_safe=False,
)
'''.format(sources=sources)
    (source / 'setup.py').write_text(setup_script, encoding='ascii')


def build_wheel(source, output):
    if (source / 'VERSION').read_text(encoding='ascii').strip() != VERSION:
        raise RuntimeError('The miniupnpc source tree version did not match the pin.')
    write_windows_setup(source)
    environment = compiler_environment(output)
    subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-deps',
        '--no-build-isolation', '--wheel-dir', str(output), str(source)],
        check=True, env=environment)
    run_parser_tests(source, environment)


def run_parser_tests(source, environment):
    build = source / 'build'
    build.mkdir(exist_ok=True)
    path_value = next(value for key, value in environment.items() if key.lower() == 'path')
    compiler = shutil.which('cl.exe', path=path_value)
    if compiler is None:
        raise RuntimeError('MSVC compiler could not be found after initializing its environment.')
    executable = build / 'testupnpreplyparse.exe'
    subprocess.run([compiler, '/nologo', '/O2', '/Iinclude', '/Isrc',
        'src/testupnpreplyparse.c', 'src/minixml.c', 'src/upnpreplyparse.c',
        f'/Fe:{executable}'], cwd=source, env=environment, check=True)
    for fixture in sorted((source / 'testreplyparse').glob('*.xml')):
        expected = fixture.with_suffix('.namevalue')
        subprocess.run([str(executable), str(fixture), str(expected)],
            cwd=source, env=environment, check=True)
    print(f'Passed {len(tuple((source / "testreplyparse").glob("*.xml")))} upstream parser fixtures.')


def compiler_environment(scratch_dir):
    if shutil.which('cl'):
        return dict(os.environ)
    installer = Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)'))
    vswhere = installer / 'Microsoft Visual Studio/Installer/vswhere.exe'
    if not vswhere.is_file():
        raise RuntimeError('Visual Studio C++ Build Tools are required to build miniupnpc.')
    installation = subprocess.check_output([str(vswhere), '-latest', '-products', '*',
        '-requires', 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64',
        '-property', 'installationPath'], text=True, encoding='mbcs').strip()
    vcvars = Path(installation) / 'VC/Auxiliary/Build/vcvarsall.bat' if installation else None
    if vcvars is None or not vcvars.is_file():
        roots = (Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Microsoft Visual Studio',
            installer / 'Microsoft Visual Studio')
        candidates = sorted((candidate for root in roots
            for candidate in root.glob('*/**/VC/Auxiliary/Build/vcvarsall.bat')), reverse=True)
        vcvars = candidates[0] if candidates else None
    if vcvars is None:
        raise RuntimeError('Visual Studio C++ Build Tools are required to build miniupnpc.')
    batch = scratch_dir / '_miniupnpc-vcvarsall.cmd'
    batch.write_text(f'@echo off\r\ncall "{vcvars}" x64 >nul\r\n'
        'if errorlevel 1 exit /b %ERRORLEVEL%\r\nset\r\n', encoding='mbcs')
    result = subprocess.run(['cmd.exe', '/d', '/c', str(batch)],
        capture_output=True, text=True, encoding='mbcs')
    if result.returncode:
        raise RuntimeError('Visual Studio could not initialize its x64 build environment: ' + result.stderr[-1000:])
    environment = dict(os.environ)
    for line in result.stdout.splitlines():
        if '=' in line:
            name, value = line.split('=', 1)
            if name:
                environment[name] = value
    batch.unlink(missing_ok=True)
    return environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path,
                        help='Use a previously downloaded archive with the pinned checksum.')
    parser.add_argument('--source-tree', type=Path,
                        help='Build from an already extracted miniupnpc source tree.')
    parser.add_argument('--output', type=Path, default=ROOT / 'build/miniupnpc-wheels',
                        help='Directory for the built wheel.')
    parser.add_argument('--install', action='store_true',
                        help='Install the newly built wheel into the active environment.')
    args = parser.parse_args()
    if sys.platform != 'win32' or sys.version_info[:2] != (3, 13) or platform.architecture()[0] != '64bit':
        parser.error('Build this wheel with 64-bit CPython 3.13 on Windows.')
    output = args.output.resolve()
    if output != ROOT and ROOT not in output.parents:
        parser.error('The wheel output must stay inside the FluxFTP workspace.')
    output.mkdir(parents=True, exist_ok=True)

    if args.archive and args.source_tree:
        parser.error('Choose either --archive or --source-tree.')
    if args.source_tree:
        build_wheel(args.source_tree.resolve(), output)
    else:
        with tempfile.TemporaryDirectory(prefix='fluxftp-miniupnpc-') as temporary:
            work = Path(temporary)
            archive = args.archive.resolve() if args.archive else work / f'miniupnpc-{VERSION}.tar.gz'
            if args.archive:
                if archive.stat().st_size > MAX_SOURCE_BYTES:
                    parser.error('The supplied source archive exceeded the size limit.')
                if hashlib.sha256(archive.read_bytes()).hexdigest() != SOURCE_SHA256:
                    parser.error('The supplied source archive SHA-256 did not match the pinned release.')
            else:
                download_source(archive)
            source = extract_source(archive, work / 'source')
            build_wheel(source, output)

    wheels = tuple(output.glob(f'miniupnpc-{VERSION}-cp313-cp313-win_amd64.whl'))
    if len(wheels) != 1:
        raise RuntimeError('Expected exactly one CPython 3.13 x64 miniupnpc wheel.')
    print(f'Built {wheels[0]} (SHA-256 {hashlib.sha256(wheels[0].read_bytes()).hexdigest()}).')
    if args.install:
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--force-reinstall', '--no-index',
            '--no-deps', str(wheels[0])], check=True)


if __name__ == '__main__':
    main()
