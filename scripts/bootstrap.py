"""Repair incomplete installs and reinstall when the dependency lock changes."""
import hashlib
from pathlib import Path
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parent.parent
    lock = root / 'requirements.lock'
    marker = Path(sys.prefix) / 'ard-requirements.sha256'
    digest = hashlib.sha256(lock.read_bytes()).hexdigest()
    if not marker.exists() or marker.read_text().strip() != digest:
        subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', str(lock)], check=True)
        temporary = marker.with_suffix('.tmp')
        temporary.write_text(digest + '\n')
        temporary.replace(marker)


if __name__ == '__main__':
    main()
