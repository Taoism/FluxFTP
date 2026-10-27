"""Explicit FTP filename encodings and bounded directory-listing controls."""
ENCODINGS = ('utf-8', 'cp1252', 'latin-1', 'cp1250', 'cp1251', 'cp1253',
             'cp1254', 'cp1255', 'cp1256', 'cp1257', 'cp1258', 'cp437',
             'cp850', 'cp866', 'koi8-r', 'shift_jis', 'euc_jp', 'gb18030',
             'big5', 'euc_kr')
MODES = ('Current site settings', 'Automatic', *ENCODINGS)


def select_encoding(mode, fallback, site, features=''):
    if mode not in MODES or fallback not in ENCODINGS:
        raise ValueError('Unsupported FTP filename encoding.')
    if site.force_utf8:
        return 'utf-8'
    if mode == 'Current site settings':
        return 'utf-8' if site.utf8_username else 'latin-1'
    if mode == 'Automatic':
        advertised = any(line.strip().upper().split(' ', 1)[0] in ('UTF8', 'UTF-8')
                         for line in features.splitlines())
        return 'utf-8' if advertised else fallback
    return mode


def set_ftp_encoding(ftp, encoding):
    # AUTH may have rebuilt the reply reader using the login encoding while
    # the command encoding was subsequently restored to the filename setting.
    if ftp.encoding == encoding and getattr(ftp.file, 'encoding', None) == encoding:
        return
    ftp.encoding = encoding
    old = ftp.file
    # Preserve non-UTF-8 banner bytes without guessing or changing path codecs.
    # Diagnostics escapes these surrogate bytes; outgoing paths stay strict.
    ftp.file = ftp.sock.makefile('r', encoding=encoding, errors='surrogateescape')
    old.close()
