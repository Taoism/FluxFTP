"""Opt-in text diagnostics. Never enable ftplib's raw wire debug printer."""
from __future__ import annotations

import logging
import json
import logging.handlers
import os
import platform
import re
import ssl
import struct
import sys
import traceback
import uuid
from pathlib import Path


logger = logging.getLogger("fluxftp.diagnostics")
logger.setLevel(logging.DEBUG)
logger.propagate = False
logger.addHandler(logging.NullHandler())
log_path = None


def redact(text, secrets=(), *, preserve_newlines=False):
    text = str(text)
    variants = {variant for secret in secrets if secret for variant in
                (secret, repr(secret)[1:-1], json.dumps(secret)[1:-1])}
    for secret in sorted(variants, key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
                  "[PRIVATE KEY OMITTED]", text, flags=re.DOTALL)
    text = re.sub(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s]*@", r"\1[REDACTED]@", text)
    text = re.sub(r"(?im)\b(PASS|ACCT|ADAT|password|passphrase)(\s+|\s*[:=]\s*)[^\r\n]*", r"\1 [REDACTED]", text)
    # Remote banners cannot inject fake log records or terminal control sequences.
    return "".join(c if c.isprintable() or (preserve_newlines and c == '\n') else f"\\x{ord(c):02x}" for c in text)


def configure_debug(path):
    global log_path
    path = Path(path).expanduser().absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=2 * 1024 * 1024,
                                                   backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s"))
    for previous in logger.handlers[:]:
        logger.removeHandler(previous)
        previous.close()
    logger.addHandler(handler)
    log_path = path
    logger.info("=== FluxFTP debug session started; pid=%s ===", os.getpid())
    logger.info("Runtime: Python %s; %s; architecture=%s-bit; frozen=%s",
                platform.python_version(), platform.platform(), struct.calcsize("P") * 8,
                bool(getattr(sys, "frozen", False)))
    logger.info("SSL runtime: %s; TLS1.2=%s; TLS1.3=%s", ssl.OPENSSL_VERSION, ssl.HAS_TLSv1_2, ssl.HAS_TLSv1_3)
    import _ssl
    logger.info("Executable: %s; SSL module: %s", redact(sys.executable), redact(_ssl.__file__))
    logger.info("Debug file: %s; passwords and credential command arguments are omitted; hosts and file paths are included.", redact(path))
    return path


def close_debug():
    global log_path
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    logger.addHandler(logging.NullHandler())
    log_path = None


class Trace:
    def __init__(self, *secrets):
        self.id = uuid.uuid4().hex[:8]
        self.secrets = secrets

    def event(self, message, level=logging.DEBUG):
        safe = redact(message, self.secrets)
        logger.log(level, "[%s] %s", self.id, safe)
        listener = getattr(self, 'listener', None)
        if listener:
            try: listener(redact(message, self.secrets, preserve_newlines=True), level)
            except RuntimeError: pass  # The desktop may already have closed.

    def error(self, phase, error):
        self.event(f"Failure during {phase}: {type(error).__name__}: {error}", logging.ERROR)
        if isinstance(error, ssl.SSLCertVerificationError):
            self.event(f"Certificate verification: code={getattr(error, 'verify_code', None)}; reason={getattr(error, 'verify_message', None)}", logging.ERROR)
        elif isinstance(error, ssl.SSLError):
            self.event(f"SSL details: library={getattr(error, 'library', None)}; reason={getattr(error, 'reason', None)}", logging.ERROR)
        # Frame locations are useful without logging source lines, locals or key material.
        for frame in traceback.extract_tb(error.__traceback__):
            self.event(f"  at {frame.filename}:{frame.lineno} in {frame.name}")


def worker_failure(error):
    # A connection's Trace already records the sanitized exception. Unknown exceptions
    # may contain credentials, so global handlers record type/locations only.
    logger.error("Background operation failed: %s", type(error).__name__)
    for frame in traceback.extract_tb(error.__traceback__):
        logger.debug("  at %s:%s in %s", redact(frame.filename), frame.lineno, frame.name)


class FTPTraceMixin:
    def putcmd(self, line):
        self._last_command = line if line.upper().startswith('STOR ') else ''
        verb, _, argument = line.partition(" ")
        verb = verb.upper()
        # Only protocol-negotiation arguments are useful here. In particular do not
        # log USER, PASS, ACCT, SITE, arbitrary paths, or authentication tokens.
        safe_args = {"AUTH": {"TLS", "SSL"}, "PBSZ": {"0"}, "PROT": {"P", "C"},
                     "TYPE": {"I", "A"}, "SSCN": {"ON", "OFF"}}
        shown = f" {argument}" if argument in safe_args.get(verb, set()) else " [argument omitted]" if argument else ""
        self.trace.event(f"FTP > {verb}{shown}")
        return super().putcmd(line)

    def getmultiline(self):
        response = super().getmultiline()
        callback = getattr(self, 'xdupe_received', None)
        if callback:
            callback(response, getattr(self, '_last_command', ''))
        self.trace.event(f"FTP < {response}")
        return response
