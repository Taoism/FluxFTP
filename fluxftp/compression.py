"""Streaming MODE Z transport over plain or protected FTP data sockets."""
import io
import ssl
import zlib


class ModeZSocket:
    def __init__(self, sock, uploading=False):
        self.sock = sock
        self.uploading = uploading
        self.codec = zlib.compressobj() if uploading else zlib.decompressobj()
        self.pending = b''

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        try:
            if kind is None:
                if self.uploading:
                    self.sock.sendall(self.codec.flush())
                if isinstance(self.sock, ssl.SSLSocket):
                    self.sock.unwrap()
        finally:
            self.sock.close()

    def close(self):
        self.sock.close()

    def sendall(self, data):
        if not self.uploading:
            raise OSError('Cannot write to a MODE Z download socket')
        compressed = self.codec.compress(data) + self.codec.flush(zlib.Z_SYNC_FLUSH)
        if compressed:
            self.sock.sendall(compressed)

    def recv(self, size):
        if self.uploading:
            raise OSError('Cannot read from a MODE Z upload socket')
        if size <= 0:
            return b''
        while True:
            data = self.pending or self.sock.recv(16384)
            if not data:
                if not self.codec.eof:
                    raise OSError('Incomplete MODE Z stream')
                return b''
            try:
                result = self.codec.decompress(data, size)
            except zlib.error as error:
                raise OSError('Invalid MODE Z stream') from error
            self.pending = self.codec.unconsumed_tail
            if self.codec.unused_data:
                raise OSError('Unexpected data after MODE Z stream')
            if result:
                return result

    def makefile(self, mode='r', buffering=None, *, encoding=None, errors=None, newline=None):
        if mode not in ('r', 'rb'):
            raise ValueError('MODE Z file wrapper supports reading only')
        reader = io.BufferedReader(_ModeZReader(self))
        return reader if mode == 'rb' else io.TextIOWrapper(reader, encoding=encoding, errors=errors, newline=newline)


class _ModeZReader(io.RawIOBase):
    def __init__(self, stream):
        super().__init__()
        self.stream = stream

    def readable(self):
        return True

    def readinto(self, buffer):
        data = self.stream.recv(len(buffer))
        buffer[:len(data)] = data
        return len(data)
