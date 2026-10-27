"""Ordered filename highlight masks; the first matching rule supplies the color."""
import json
from functools import lru_cache
from PySide6.QtGui import QColor
from .ascii_rules import validate_rule, matches


@lru_cache(maxsize=128)
def highlight_rules(raw='[]'):
    try:
        values = json.loads(raw)
        if not isinstance(values, list) or len(values) > 1000:
            raise ValueError('Highlights supports up to 1000 rules.')
        result = []
        for value in values:
            if not isinstance(value, dict) or not isinstance(value.get('pattern'), str) or type(value.get('regex')) is not bool:
                raise ValueError('Invalid highlight mask.')
            color = value.get('color')
            if not isinstance(color, str) or not QColor(color).isValid():
                raise ValueError('Choose a valid highlight color.')
            result.append(dict(validate_rule(value['pattern'], value['regex']), color=QColor(color).name()))
        return tuple(result)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError('Invalid saved Highlights list.') from error


def highlight_color(name, rules):
    return next((rule['color'] for rule in rules if matches(name, [rule])), None)
