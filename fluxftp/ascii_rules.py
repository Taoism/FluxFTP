"""Validated filename masks for automatic ASCII transfers."""
import fnmatch
import json
import re


DEFAULT_MASKS = ["*." + extension for extension in (
    "asp", "bat", "c", "cfm", "cgi", "cpp", "css", "dhtml", "diz", "h", "htm", "html",
    "ini", "mak", "nfo", "pas", "pl", "sfv", "shtm", "shtml", "tcl", "txt", "csv",
    "js", "json", "xml", "cfg", "md", "py", "sh", "log")]


def validate_rule(pattern, regex=False):
    pattern = pattern.strip()
    if not pattern or len(pattern) > 512 or any(ord(c) < 32 for c in pattern):
        raise ValueError("Enter a mask of 1–512 characters without line breaks or control characters.")
    if regex:
        try:
            re.compile(pattern, re.IGNORECASE)
        except re.error as error:
            raise ValueError(f"Invalid regular expression: {error}") from error
    return {"pattern": pattern, "regex": bool(regex)}


def read_rules(raw="", legacy_extensions=""):
    if not raw:
        masks = ["*" + item.strip() for item in legacy_extensions.split(";") if item.strip()] if legacy_extensions else DEFAULT_MASKS
        return [validate_rule(mask) for mask in masks]
    try:
        values = json.loads(raw)
        if not isinstance(values, list) or len(values) > 1000:
            raise ValueError("ASCII List must contain at most 1000 rules.")
        rules = []
        for value in values:
            if not isinstance(value, dict) or not isinstance(value.get("pattern"), str) or type(value.get("regex")) is not bool:
                raise ValueError("Invalid ASCII List rule.")
            rules.append(validate_rule(value["pattern"], value["regex"]))
        return rules
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("Invalid saved ASCII List.") from error


def matches(path, rules):
    name = str(path).replace("\\", "/").rsplit("/", 1)[-1]
    return any(re.search(rule["pattern"], name, re.IGNORECASE) is not None if rule["regex"] else
               fnmatch.fnmatchcase(name.casefold(), rule["pattern"].casefold()) for rule in rules)


class NormalizedReader:
    """Bounded streaming CRLF-to-LF conversion with exact-size reads."""
    def __init__(self, stream):
        self.stream = stream
        self.buffer = b""
        self.pending = b""
        self.eof = False

    def read(self, size):
        while len(self.buffer) < size and not self.eof:
            block = self.stream.read(16384)
            if not block:
                self.buffer += self.pending
                self.pending = b""
                self.eof = True
                break
            raw = self.pending + block
            self.pending = b"\r" if raw.endswith(b"\r") else b""
            if self.pending:
                raw = raw[:-1]
            self.buffer += raw.replace(b"\r\n", b"\n")
        result, self.buffer = self.buffer[:size], self.buffer[size:]
        return result
