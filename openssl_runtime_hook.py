"""Select verified TLS libraries before PyInstaller's other hooks import SSL."""
from fluxftp.openssl_runtime import bootstrap

bootstrap()
