"""Bounded data snapshots and offline, non-overwriting restore (stdlib only).

Only SQLite metadata and referenced immutable content objects are backed up.
Runtime credentials, connector private files and environment settings are excluded.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import tempfile
import zipfile

from ard.store import encode


FORMAT = 'ai-rd-platform-backup'
VERSION = 1
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_DATABASE_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_FILES = 10002
MAX_COMPRESSION_RATIO = 100
DIGEST = re.compile(r'[0-9a-f]{64}\Z')
TERMINAL_JOBS = {'SUCCEEDED', 'FAILED', 'CANCELLED', 'INTERRUPTED'}
# These are actual top-level object references agreed by feature modules.
# identity_sha256/text_sha256/corpus_sha256/endpoint_sha256 are fingerprints.
OBJECT_FIELDS = {'sha256', 'source_sha256', 'preview_sha256', 'manifest_sha256',
                 'artifact_sha256', 'chunks_sha256', 'vector_sha256', 'vectors_sha256',
                 'dataset_sha256', 'index_sha256'}
RECONFIGURE = ('Private connector credentials and runtime ownership files are excluded. '
               'Reconfigure identities, connectors and runtime credentials before use; '
               'restoration does not reconnect or take control of running containers.')


class BackupBusy(ValueError):
    pass


def _hash_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _json(raw):
    def invalid(value):
        raise ValueError('non-finite JSON is not allowed')
    return json.loads(raw, parse_constant=invalid)


def _digests(value):
    if isinstance(value, str) and DIGEST.fullmatch(value):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _digests(item)
    elif isinstance(value, list):
        for item in value:
            yield from _digests(item)


def _inspect_database(path, available_objects):
    """Validate schema, record project ownership, active jobs and the audit chain."""
    if path.stat().st_size > MAX_DATABASE_BYTES:
        raise ValueError('backup database exceeds 64 MiB')
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA trusted_schema=OFF')
        db.execute('PRAGMA query_only=ON')
        schema = list(db.execute('SELECT type,name FROM sqlite_master'))
        allowed = {('table', 'records'), ('table', 'audit'), ('table', 'sqlite_sequence'),
                   ('index', 'sqlite_autoindex_records_1'), ('index', 'records_project')}
        if any((r['type'], r['name']) not in allowed for r in schema):
            raise ValueError('backup database has unsupported schema objects')
        for table, names in (
            ('records', ['id', 'kind', 'project_id', 'revision', 'created_at', 'updated_at', 'data']),
            ('audit', ['seq', 'at', 'actor', 'action', 'entity_id', 'project_id', 'detail', 'previous_hash', 'hash']),
        ):
            if [r['name'] for r in db.execute('PRAGMA table_info(' + table + ')')] != names:
                raise ValueError('backup database schema is invalid')
        if db.execute('PRAGMA integrity_check').fetchall()[0][0] != 'ok':
            raise ValueError('backup database integrity check failed')
        records = list(db.execute('SELECT * FROM records'))
        projects = {r['id'] for r in records if r['kind'] == 'project'}
        referenced = set()
        for record in records:
            data = _json(record['data'])
            if not isinstance(data, dict) or type(record['revision']) is not int or record['revision'] < 1:
                raise ValueError('invalid backup record')
            if record['kind'] == 'project':
                if record['project_id'] is not None:
                    raise ValueError('project record has an invalid project scope')
            elif record['project_id'] not in projects:
                raise ValueError('backup record refers to an unknown project')
            if record['kind'] == 'job' and data.get('status') not in TERMINAL_JOBS:
                raise BackupBusy('备份需要先结束全部活动任务（包括暂停和等待审批任务）')
            for key in OBJECT_FIELDS:
                if key in data and data[key] is not None:
                    value = data[key]
                    if not isinstance(value, str) or not DIGEST.fullmatch(value) or value not in available_objects:
                        raise ValueError('backup referenced content object is missing or invalid: ' + key)
                    referenced.add(value)
            # Other checksum-like fields are references only if a stored object exists.
            referenced.update(set(_digests(data)) & available_objects)
        previous, checked = '0' * 64, 0
        for row in db.execute('SELECT * FROM audit ORDER BY seq'):
            record = dict(row)
            record.pop('seq')
            digest = record.pop('hash')
            _json(record['detail'])
            if record['previous_hash'] != previous or hashlib.sha256(encode(record)).hexdigest() != digest:
                raise ValueError('backup audit hash chain is invalid')
            if record['project_id'] is not None and record['project_id'] not in projects:
                raise ValueError('backup audit refers to an unknown project')
            previous, checked = digest, checked + 1
        return {'objects': referenced, 'records': len(records),
                'audit': {'valid': True, 'checked': checked, 'head_hash': previous}}
    except sqlite3.Error as exc:
        raise ValueError('backup database is not a valid platform snapshot') from exc
    finally:
        db.close()


def create_backup(store, destination, actor):
    """Serialize in-process Store mutations; SQLite backup provides the read snapshot.

    Cross-process readers/writers cannot tear this SQLite snapshot. Referenced
    objects are immutable and hash-checked while copied. No deletion is supported.
    """
    destination = Path(destination)
    with store.lock, tempfile.TemporaryDirectory(prefix='ard-snapshot-') as temporary:
        if getattr(store._transactions, 'db', None) is not None:
            raise BackupBusy('备份不能在活动写事务内执行')
        with store.connect() as source:
            active = source.execute("SELECT data FROM records WHERE kind='job'").fetchall()
        if any(_json(r[0]).get('status') not in TERMINAL_JOBS for r in active):
            raise BackupBusy('备份需要先结束全部活动任务（包括暂停和等待审批任务）')
        with store.transaction() as db:
            store._audit(db, actor, 'backup:requested', 'platform', None,
                         {'format': FORMAT, 'private_configuration': 'excluded'})
        snapshot = Path(temporary) / 'platform.sqlite3'
        with store.connect() as source, sqlite3.connect(snapshot) as target:
            source.backup(target)
            # The exported database is a self-contained file, without WAL sidecars.
            target.execute('PRAGMA journal_mode=DELETE')
        available = {p.name for p in store.objects.iterdir() if DIGEST.fullmatch(p.name)}
        checked = _inspect_database(snapshot, available)
        files = [('platform.sqlite3', snapshot)] + [('objects/' + d, store.objects / d) for d in sorted(checked['objects'])]
        if len(files) + 1 > MAX_FILES:
            raise ValueError('backup exceeds 10000 content objects')
        entries, total = [], 0
        for name, path in files:
            if path.is_symlink() or not path.is_file():
                raise ValueError('backup referenced content must be a regular file')
            size = path.stat().st_size
            if size > (MAX_DATABASE_BYTES if name == 'platform.sqlite3' else MAX_FILE_BYTES):
                raise ValueError('backup member exceeds its size limit')
            total += size
            if total > MAX_TOTAL_BYTES - MAX_MANIFEST_BYTES:
                raise ValueError('backup exceeds the 512 MiB expanded size limit')
            digest = _hash_file(path)
            if name.startswith('objects/') and digest != path.name:
                raise ValueError('backup content integrity check failed')
            entries.append({'path': name, 'size_bytes': size, 'sha256': digest})
        manifest = {'format': FORMAT, 'version': VERSION, 'created_at': datetime.now(timezone.utc).isoformat(),
                    'files': entries, 'object_count': len(checked['objects']), 'record_count': checked['records'],
                    'audit': checked['audit'], 'private_configuration': RECONFIGURE}
        manifest_bytes = encode(manifest)
        if len(manifest_bytes) > MAX_MANIFEST_BYTES:
            raise ValueError('backup manifest exceeds 4 MiB')
        # Stored members make our own archives immune to decompression amplification.
        with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
            archive.writestr('manifest.json', manifest_bytes)
            for name, path in files:
                archive.write(path, name)
        # Detect unexpected object changes during copying, including non-platform edits.
        with zipfile.ZipFile(destination) as archive:
            for entry in entries:
                digest = hashlib.sha256()
                with archive.open(entry['path']) as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b''):
                        digest.update(chunk)
                if digest.hexdigest() != entry['sha256']:
                    raise ValueError('backup content changed while being copied')
        return {'objects': len(checked['objects']), 'records': checked['records'],
                'audit': checked['audit'], 'sha256': _hash_file(destination)}


def _validate_destination(destination):
    path = Path(os.path.abspath(destination))
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('restore destination must not contain symlinks')
    if not path.parent.is_dir():
        raise ValueError('restore destination parent must already exist')
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError('restore destination must be a NEW empty directory; existing data is never overwritten')
    return path


def _manifest(archive):
    members = archive.infolist()
    names = [m.filename for m in members]
    if len(members) > MAX_FILES or len(set(names)) != len(names):
        raise ValueError('backup has duplicate members or exceeds the file-count limit')
    total = 0
    for member in members:
        name = member.filename
        parts = PurePosixPath(name).parts
        if name.startswith('/') or '\\' in name or any(p in ('..', '.') for p in parts):
            raise ValueError('backup contains an unsafe archive path')
        if name not in ('manifest.json', 'platform.sqlite3') and not (
            len(parts) == 2 and parts[0] == 'objects' and DIGEST.fullmatch(parts[1]) and name == '/'.join(parts)
        ):
            raise ValueError('backup contains an unexpected file')
        mode = member.external_attr >> 16
        if member.is_dir() or (stat.S_IFMT(mode) not in (0, stat.S_IFREG)):
            raise ValueError('backup contains a symlink or non-regular member')
        if member.flag_bits & 1 or member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise ValueError('encrypted or unsupported ZIP members are not allowed')
        limit = MAX_MANIFEST_BYTES if name == 'manifest.json' else (MAX_DATABASE_BYTES if name == 'platform.sqlite3' else MAX_FILE_BYTES)
        total += member.file_size
        if member.file_size > limit or total > MAX_TOTAL_BYTES:
            raise ValueError('backup exceeds the expanded size limit')
        if member.file_size > max(1, member.compress_size) * MAX_COMPRESSION_RATIO:
            raise ValueError('backup compression ratio exceeds the safety limit')
    if 'manifest.json' not in names or 'platform.sqlite3' not in names:
        raise ValueError('backup is missing its manifest or database')
    manifest = _json(archive.read('manifest.json'))
    if not isinstance(manifest, dict) or manifest.get('format') != FORMAT or manifest.get('version') != VERSION:
        raise ValueError('unsupported backup manifest format/version')
    entries = manifest.get('files')
    if not isinstance(entries, list) or len(entries) != len(names) - 1:
        raise ValueError('backup manifest does not enumerate every data member')
    by_name = {}
    for item in entries:
        if not isinstance(item, dict) or set(item) != {'path', 'size_bytes', 'sha256'}:
            raise ValueError('invalid backup manifest entry')
        name, digest, size = item['path'], item['sha256'], item['size_bytes']
        if not isinstance(name, str) or name in by_name or name == 'manifest.json' or name not in names:
            raise ValueError('invalid or duplicate backup manifest path')
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest) or type(size) is not int or size < 0:
            raise ValueError('invalid backup manifest digest/size')
        if archive.getinfo(name).file_size != size:
            raise ValueError('backup manifest size mismatch')
        if name.startswith('objects/') and name.split('/')[1] != digest:
            raise ValueError('backup object filename/hash mismatch')
        by_name[name] = item
    if set(by_name) != set(names) - {'manifest.json'}:
        raise ValueError('backup manifest membership mismatch')
    return manifest, by_name


def restore_backup(archive_path, destination):
    """Verify into a private staging directory, then rename into a new empty target."""
    destination = _validate_destination(destination)
    archive_path = Path(archive_path)
    if not archive_path.is_file() or archive_path.is_symlink():
        raise ValueError('backup archive must be a regular file')
    if archive_path.stat().st_size > MAX_TOTAL_BYTES + 4 * 1024 * 1024:
        raise ValueError('backup archive exceeds its size limit')
    staging = Path(tempfile.mkdtemp(prefix='.ard-restore-', dir=destination.parent))
    try:
        with zipfile.ZipFile(archive_path) as archive:
            manifest, entries = _manifest(archive)
            (staging / 'objects').mkdir(mode=0o700)
            for name, entry in entries.items():
                target = staging / name
                digest, written = hashlib.sha256(), 0
                with archive.open(name) as source, target.open('xb') as output:
                    os.chmod(target, 0o600)
                    for chunk in iter(lambda: source.read(1024 * 1024), b''):
                        written += len(chunk)
                        if written > entry['size_bytes']:
                            raise ValueError('backup member expanded past its declared size')
                        digest.update(chunk); output.write(chunk)
                if written != entry['size_bytes'] or digest.hexdigest() != entry['sha256']:
                    raise ValueError('backup member SHA-256/size verification failed')
        available = {p.name for p in (staging / 'objects').iterdir()}
        checked = _inspect_database(staging / 'platform.sqlite3', available)
        if checked['objects'] != available or checked['records'] != manifest.get('record_count') or \
                len(available) != manifest.get('object_count') or checked['audit'] != manifest.get('audit'):
            raise ValueError('backup metadata/object/audit manifest verification failed')
        _validate_destination(destination)
        # Replacing an empty directory is allowed; the OS rejects nonempty/live data.
        staging.rename(destination)
        return {'destination': str(destination), 'records': checked['records'], 'objects': len(available),
                'audit': checked['audit'], 'archive_sha256': _hash_file(archive_path),
                'private_configuration': RECONFIGURE}
    except (zipfile.BadZipFile, RuntimeError, EOFError) as exc:
        raise ValueError('backup archive is invalid or corrupt') from exc
    finally:
        if staging.exists():
            shutil.rmtree(staging)
