"""Ordered filename priorities; the first matching rule wins."""
import json
from functools import lru_cache

from .ascii_rules import matches, read_rules

DEFAULT_RULES = json.dumps([{'pattern': mask, 'regex': False}
                           for mask in ('*.sfv', '*.nfo', '*.m3u', '*.jpg', 'Sample')])


@lru_cache(maxsize=128)
def priority_rules(raw=DEFAULT_RULES):
    try:
        return tuple(read_rules(raw or DEFAULT_RULES))
    except ValueError as error:
        raise ValueError(str(error).replace('ASCII List', 'Priority List')) from error


def priority_rank(name, raw=DEFAULT_RULES):
    rules = priority_rules(raw)
    return next((index for index, rule in enumerate(rules) if matches(name, [rule])), len(rules))
