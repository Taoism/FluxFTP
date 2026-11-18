"""Console entry point compatible with RushApp.FTP.Transfer callers."""
from __future__ import annotations

import json
import sys

from . import cli


def main():
    if len(sys.argv) < 2:
        print('Usage: rush_cmdline "RushApp.FTP.Transfer(...)" [path parameters…]', file=sys.stderr)
        return 2
    try:
        result = cli._request('POST', '/v1/rush-script',
                              {'script': sys.argv[1], 'parameters': sys.argv[2:]})
    except (RuntimeError, ValueError, OSError) as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
