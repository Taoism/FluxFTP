"""Expand FTP command templates as data, then run a bounded command sequence."""
from dataclasses import dataclass
import re

from .core import Cancelled
from .diagnostics import redact

TOKEN = re.compile(r'%d\[([^\]]*)\]|%([12fo%])')
SECRET = re.compile(r'password|passwd|pswd|chpass|\bpass\b', re.I)
CREDENTIAL = re.compile(r'^(?:PASS|ACCT|ADAT)\b|^SITE\s+(?:PASSWD|PSWD|CHPASS|AUTHPASS|ADDUSER|GADDUSER)\b', re.I)
CONTROL = re.compile(r'[\x00-\x1f\x7f]')
MAX_COMMANDS = 1000
MAX_LINE = 8192


@dataclass(frozen=True)
class CommandPlan:
    lines: tuple[str, ...]
    secrets: tuple[str, ...]


def validate_lines(lines):
    if not lines or len(lines) > MAX_COMMANDS:
        raise ValueError('Enter between 1 and 1000 command lines.')
    for line in lines:
        if CONTROL.search(line) or len(line) > MAX_LINE or not line.strip():
            raise ValueError('Commands must be nonempty single lines of at most 8192 characters.')
        if not re.match(r'^[A-Za-z]+(?:\s|$)', line) and line.lower() != '&window':
            raise ValueError('Each line must start with an FTP command or &window.')


def compile_commands(lines, entries, prompt):
    """Prompt once per placeholder occurrence, reuse numbered answers, expand per file."""
    validate_lines(lines)
    tokenized = []
    answers = []
    secrets = []
    needs_entry = False
    for line in lines:
        pieces = []
        end = 0
        secret_line = bool(SECRET.search(line) or CREDENTIAL.search(line))
        for token in TOKEN.finditer(line):
            literal = line[end:token.start()]
            if '%' in literal:
                raise ValueError('Unknown placeholder. Use %d[prompt], %1, %2, %f, %o or %%.')
            pieces.append(('text', literal))
            question, code = token.groups()
            if question is not None:
                value = prompt(question, bool(SECRET.search(question)) or (secret_line and line.count('%d[') == 1))
                if value is None:
                    raise Cancelled('Command cancelled; nothing was sent.')
                if CONTROL.search(value) or len(value) > MAX_LINE:
                    raise ValueError('Command arguments cannot contain control characters or exceed 8192 characters.')
                answers.append(value)
                if secret_line or SECRET.search(question):
                    secrets.extend([value, *value.split()])
                pieces.append(('text', value))
            elif code in ('1', '2'):
                if len(answers) < int(code):
                    raise ValueError(f'%{code} refers to a prompt that has not appeared yet.')
                pieces.append(('text', answers[int(code)-1]))
            elif code == '%':
                pieces.append(('text', '%'))
            else:
                needs_entry = True
                pieces.append((code, ''))
            end = token.end()
        tail = line[end:]
        if '%' in tail:
            raise ValueError('Unknown placeholder. Use %d[prompt], %1, %2, %f, %o or %%.')
        pieces.append(('text', tail))
        tokenized.append(pieces)
    if needs_entry and not entries:
        raise ValueError('Select at least one remote item for this command.')
    if len(tokenized) * (len(entries) if needs_entry else 1) > MAX_COMMANDS:
        raise ValueError('The selected items would expand to more than 1000 commands. Select fewer items.')
    result = []
    for entry in entries if needs_entry else [None]:
        for pieces in tokenized:
            values = []
            for code, value in pieces:
                if code == 'f':
                    value = entry.name
                elif code == 'o':
                    value = entry.user
                    if not value:
                        raise ValueError('The selected item has no owner in its directory listing.')
                if CONTROL.search(value):
                    raise ValueError('Command arguments cannot contain control characters.')
                values.append(value)
            result.append(''.join(values).strip())
    validate_lines(result)
    # Raw password-changing commands and literal password arguments are also secret.
    for line in result:
        if SECRET.search(line) or CREDENTIAL.search(line):
            secrets.extend(line.split()[2:] if line.upper().startswith('SITE ') else line.split()[1:])
    return CommandPlan(tuple(result), tuple(s for s in dict.fromkeys(secrets) if s))


def public_text(text, secrets):
    text = str(text)
    for secret in sorted(set(secrets), key=len, reverse=True):
        if secret:
            text = text.replace(secret, '[REDACTED]')
    return '\n'.join(redact(line[:MAX_LINE], secrets) + (' [line truncated]' if len(line) > MAX_LINE else '')
                     for line in text.splitlines())


def execute_commands(connection, path, plan, cancellation, *, raise_on_error=False):
    """No automatic retries after sending commands; stop at the first server error."""
    if connection.sftp or not connection.ftp:
        raise ValueError('These commands require an FTP or FTPS connection.')
    validate_lines(plan.lines)
    connection.trace.secrets = (*connection.trace.secrets, *plan.secrets)
    transcript = []
    completed = 0
    remaining = 256_000

    def append(text):
        nonlocal remaining
        if remaining > 0:
            safe = public_text(text, connection.trace.secrets)
            transcript.append(safe[:remaining])
            remaining -= max(len(str(text)), len(safe))

    try:
        if cancellation.is_set():
            raise Cancelled('Cancelled before sending any commands.')
        connection.ftp.cwd(path)
        for line in plan.lines:
            if cancellation.is_set():
                raise Cancelled('Cancelled. Earlier commands may already have taken effect.')
            if line.lower() == '&window':
                continue  # Responses always have a dedicated window in FluxFTP.
            append('> ' + line)
            verb = line.split(' ', 1)[0].upper()
            if verb in ('LIST', 'NLST', 'MLSD'):
                response = connection.ftp.retrlines(line, append)
            else:
                response = connection.ftp.sendcmd(line)
            append(response)
            if response[:1] not in ('2', '3'):
                raise RuntimeError('Unexpected reply; sequence stopped. Use the transfer controls for data transfers.')
            completed += 1
    except Exception as error:
        append(f'Stopped after {completed} completed command(s): {error}')
        if raise_on_error:
            raise
    else:
        append(f'Completed {completed} command(s).')
    if remaining <= 0:
        transcript.append('[Output truncated at 256000 characters.]')
    return '\n'.join(transcript)
