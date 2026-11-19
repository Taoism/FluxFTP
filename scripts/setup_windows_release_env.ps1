$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$buildRoot = Join-Path $repoRoot 'build'
$cache = Join-Path $buildRoot 'local-python313'
$runtime = Join-Path $repoRoot 'runtime\windows-python-3.13.18-20261110'
$staging = Join-Path $buildRoot 'windows-python-3.13.18-20261110-staging'
$pythonArchiveName = 'cpython-3.13.18+20261110-x86_64-pc-windows-msvc-install_only.tar.gz'
$pythonArchive = Join-Path $cache $pythonArchiveName
$pythonUrl = "https://github.com/astral-sh/python-build-standalone/releases/download/20261110/$pythonArchiveName"
$pythonSha256 = '7e93813bd362c6413f30788b248590e2f169df2d17dcac81aafbcc7a6eeb542c'
$sqliteArchiveName = 'sqlite-dll-win-x64-3540100.zip'
$sqliteArchive = Join-Path $buildRoot $sqliteArchiveName
$sqliteUrl = "https://sqlite.org/2026/$sqliteArchiveName"
$sqliteSha3 = 'c2d09c8babb9399277055c057c97a53f1ffd7c5f11d0c1f2118ffdfa39f2daa4'

New-Item -ItemType Directory -Force -Path $cache | Out-Null
if (-not (Test-Path -LiteralPath $pythonArchive)) {
    Invoke-WebRequest -Uri $pythonUrl -OutFile $pythonArchive
}
$actualPythonHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $pythonArchive).Hash.ToLowerInvariant()
if ($actualPythonHash -ne $pythonSha256) {
    throw "CPython standalone archive SHA-256 mismatch: $actualPythonHash"
}

if (-not (Test-Path -LiteralPath $runtime)) {
    if (Test-Path -LiteralPath $staging) {
        throw "Remove the incomplete staging directory before retrying: $staging"
    }
    New-Item -ItemType Directory -Path $staging | Out-Null
    tar.exe -xzf $pythonArchive -C $staging --strip-components=1
    if ($LASTEXITCODE -ne 0) { throw 'Could not extract the local CPython runtime.' }

    if (-not (Test-Path -LiteralPath $sqliteArchive)) {
        Invoke-WebRequest -Uri $sqliteUrl -OutFile $sqliteArchive
    }
    $computedSqliteHash = & (Join-Path $staging 'python.exe') -c "import hashlib,sys; print(hashlib.sha3_256(open(sys.argv[1],'rb').read()).hexdigest())" $sqliteArchive
    if ($LASTEXITCODE -ne 0 -or $computedSqliteHash.Trim() -ne $sqliteSha3) {
        throw "SQLite DLL archive SHA3-256 mismatch: $computedSqliteHash"
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($sqliteArchive)
    try {
        $sqliteEntry = $zip.GetEntry('sqlite3.dll')
        if ($null -eq $sqliteEntry) { throw 'SQLite archive does not contain sqlite3.dll.' }
        $sourceStream = $sqliteEntry.Open()
        $targetStream = [System.IO.File]::Create((Join-Path $staging 'DLLs\sqlite3.dll'))
        try { $sourceStream.CopyTo($targetStream) }
        finally { $targetStream.Dispose(); $sourceStream.Dispose() }
    }
    finally { $zip.Dispose() }

    $runtimeInfo = & (Join-Path $staging 'python.exe') -c "import sqlite3,ssl,sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}|{sqlite3.sqlite_version}|{ssl.OPENSSL_VERSION}')"
    if ($runtimeInfo.Trim() -notmatch '^3\.13\.18\|3\.54\.1\|OpenSSL 3\.5\.9 ') {
        throw "Unexpected local Python runtime: $runtimeInfo"
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $runtime) | Out-Null
    Move-Item -LiteralPath $staging -Destination $runtime
}

$python = Join-Path $runtime 'python.exe'
$runtimeCheck = & $python -c "import sqlite3,ssl,sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}|{sqlite3.sqlite_version}|{ssl.OPENSSL_VERSION}')"
if ($LASTEXITCODE -ne 0 -or $runtimeCheck.Trim() -notmatch '^3\.13\.18\|3\.54\.1\|OpenSSL 3\.5\.9 ') {
    throw "Local CPython runtime check failed: $runtimeCheck"
}

$python = Join-Path $runtime 'python.exe'
& $python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'Local pip upgrade failed.' }
& $python -m pip install setuptools==84.2.1 wheel
if ($LASTEXITCODE -ne 0) { throw 'Local native wheel build tools failed to install.' }
if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) {
    $vcvars = 'C:\Program Files\Microsoft Visual Studio\18\Insiders\VC\Auxiliary\Build\vcvarsall.bat'
    if (-not (Test-Path -LiteralPath $vcvars)) {
        throw 'MSVC x64 Build Tools are required to compile the pinned miniupnpc wheel.'
    }
    $environmentLines = & $env:ComSpec /d /s /c "`"$vcvars`" x64 >nul && set"
    if ($LASTEXITCODE -ne 0) { throw 'Could not initialize the local MSVC x64 build environment.' }
    foreach ($line in $environmentLines) {
        $separator = $line.IndexOf('=')
        if ($separator -gt 0) {
            Set-Item -Path "Env:$($line.Substring(0, $separator))" -Value $line.Substring($separator + 1)
        }
    }
}
$miniupnpcArchive = Join-Path $buildRoot 'miniupnpc-2.3.5-source\miniupnpc-2.3.5.tar.gz'
if (Test-Path -LiteralPath $miniupnpcArchive) {
    & $python scripts\build_miniupnpc_windows.py --archive $miniupnpcArchive --install
} else {
    & $python scripts\build_miniupnpc_windows.py --install
}
if ($LASTEXITCODE -ne 0) { throw 'Patched miniupnpc wheel build failed.' }
& $python -m pip install -r requirements-windows.txt --find-links build/miniupnpc-wheels
if ($LASTEXITCODE -ne 0) { throw 'Pinned Windows dependencies failed to install.' }
& $python -m pip install -e . --no-deps
if ($LASTEXITCODE -ne 0) { throw 'FluxFTP local environment install failed.' }

Write-Host "Local build Python: $python"
Write-Host "Base runtime: $runtime"
Write-Host "System Python and PATH were not changed."
