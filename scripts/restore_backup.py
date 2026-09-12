#!/usr/bin/env python3
"""Restore a verified platform backup into a NEW empty directory, never live data."""
import argparse
import json
from pathlib import Path
import sys

# Supports a repository checkout as well as an installed ai-rd-platform package.
repository = Path(__file__).resolve().parents[1]
if (repository / 'ard').is_dir():
    sys.path.insert(0, str(repository))

from ard.features.operation_backup import restore_backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path, help='ai-rd-backup.zip')
    parser.add_argument('destination', type=Path, help='new or empty directory; its parent must exist')
    args = parser.parse_args()
    try:
        result = restore_backup(args.archive, args.destination)
    except (ValueError, OSError) as exc:
        parser.exit(2, f'Restore rejected: {exc}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
