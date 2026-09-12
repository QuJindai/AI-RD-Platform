"""`python -m ard` starts a local workspace; remote binding requires identities."""
import argparse
import os

import uvicorn


def main():
    parser = argparse.ArgumentParser(description='AI-RD-Platform')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--data-dir', default=None)
    args = parser.parse_args()
    if args.host not in ('127.0.0.1', '::1', 'localhost'):
        import json
        from ard.security import Security
        identities = json.loads(os.environ.get('ARD_IDENTITIES', '{}'))
        if not identities:
            parser.error('远程绑定需要先配置ARD_IDENTITIES；本机使用默认127.0.0.1')
        Security(identities)
    if args.data_dir:
        os.environ['ARD_DATA_DIR'] = args.data_dir
    uvicorn.run('ard.api:create_app', factory=True, host=args.host, port=args.port, workers=1)


if __name__ == '__main__':
    main()
