"""A loopback MODE Z extension to pyftpdlib for protocol integration tests."""
import zlib


def compressed_handler(base):
    class Producer:
        def __init__(self, source):
            self.source = source
            self.codec = zlib.compressobj()
            self.done = False

        def more(self):
            if self.done:
                return b''
            while True:
                block = self.source.more()
                if not block:
                    self.done = True
                    return self.codec.flush()
                output = self.codec.compress(block)
                if output:
                    return output

    class DataChannel(base.dtp_handler):
        def use_sendfile(self):
            return False

        def push(self, data):
            if self.cmd_channel.mode_z:
                data = zlib.compress(data)
            return super().push(data)

        def push_with_producer(self, producer):
            if self.cmd_channel.mode_z:
                producer = Producer(producer)
            return super().push_with_producer(producer)

        def enable_receiving(self, type, cmd):
            super().enable_receiving(type, cmd)
            if self.cmd_channel.mode_z:
                codec = zlib.decompressobj()
                previous = self._data_wrapper
                def decode(data):
                    data = codec.decompress(data)
                    return previous(data) if previous else data
                self._data_wrapper = decode

    class Handler(base):
        mode_z = False
        dtp_handler = DataChannel

        def ftp_MODE(self, line):
            if line.upper() in ('Z', 'S'):
                self.mode_z = line.upper() == 'Z'
                self.respond('200 Mode selected')
            else:
                super().ftp_MODE(line)

    return Handler
