"""Create local Docker access credentials; never overwrites an existing .env."""
import json
from pathlib import Path
import secrets
import os


def main():
    path = Path(__file__).resolve().parent.parent / '.env'
    token = secrets.token_urlsafe(32)
    value = {'user': 'owner', 'role': 'admin', 'projects': ['*']}
    text = 'ARD_IDENTITIES=' + json.dumps({token: value}, separators=(',', ':')) + '\n'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as file:
        file.write(text)
    print('Created .env. Keep it private. Use this access token in the local console:')
    print(token)
    print('For independent approval, add a reviewer with a different user and token as documented in README.md.')


if __name__ == '__main__':
    main()
