#!/usr/bin/env python3
"""Private exact rollback snapshot, verified before any deployment config write."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile

CONFIGS = (
    'finance-cashback/compose.yaml', 'finance-cashback/.env',
    'finance-cashback/cashback-profile.json', 'finance-cashback/actual-bootstrap.json',
    'finance-cashback/static-rules.json', 'finance-cashback/transaction-email-sources.json',
    'finance-runtime/.env', 'finance-runtime/.env.tpl', 'finance-runtime/render-env.sh',
)
DATABASE = 'finance-actual/cashback-data/cashback-events.sqlite3'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def regular(path):
    if path.is_symlink() or any(p.is_symlink() for p in path.parents) or not path.is_file():
        raise ValueError('Snapshot source must be a regular file')


def database_facts(path):
    regular(path)
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('Database integrity check failed')
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        if 'cashback_events' not in tables:
            raise ValueError('Cashback event table missing')
        return {name: db.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0] for name in tables}


def verify(destination):
    regular(destination / 'manifest.json')
    manifest = json.loads((destination / 'manifest.json').read_text())
    if manifest['schema_version'] != 1 or manifest['secrets_included'] is not True:
        raise ValueError('Not an exact private rollback snapshot')
    if set(manifest['files']) != set(CONFIGS) | {DATABASE}:
        raise ValueError('Incomplete configuration manifest')
    receipt = destination / 'verification.json'
    if receipt.exists():
        regular(receipt)
        if json.loads(receipt.read_text()).get('manifest_sha256') != sha(destination / 'manifest.json'):
            raise ValueError('Snapshot manifest receipt mismatch')
    for relative, metadata in manifest['files'].items():
        if relative not in CONFIGS and relative != DATABASE:
            raise ValueError('Unexpected snapshot file')
        path = destination / 'files' / relative
        if metadata is None:
            if path.exists() or path.is_symlink():
                raise ValueError('Unexpected file in snapshot')
            continue
        regular(path)
        if sha(path) != metadata['sha256']:
            raise ValueError('Snapshot hash mismatch')
    database = destination / 'files' / DATABASE
    if database_facts(database) != manifest['database_table_counts']:
        raise ValueError('Snapshot database readback mismatch')
    # Restore the backup through SQLite into a disposable file and open it again.
    with tempfile.TemporaryDirectory(dir=destination) as temporary:
        restored = Path(temporary) / 'restored.sqlite3'
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as source, sqlite3.connect(restored) as target:
            source.backup(target)
        if database_facts(restored) != manifest['database_table_counts']:
            raise ValueError('Snapshot restore-readability failed')
    return manifest


def snapshot(root, destination, image_reference, inspection):
    if not re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', image_reference):
        raise ValueError('Immutable rollback image required')
    if destination.exists() or destination.is_symlink():
        raise ValueError('Snapshot destination already exists')
    if any(p.is_symlink() for p in destination.parents):
        raise ValueError('Snapshot parents must not be symlinks')
    os.umask(0o077)
    destination.mkdir(parents=True, mode=0o700)
    files = {}
    for relative in CONFIGS:
        source = root / relative
        if not source.exists() and not source.is_symlink():
            files[relative] = None
            continue
        regular(source)
        target = destination / 'files' / relative
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(source, target)
        target.chmod(0o600)
        if sha(source) != sha(target):
            raise ValueError('Configuration changed during snapshot')
        stat = source.stat()
        files[relative] = {'sha256': sha(target), 'mode': stat.st_mode & 0o777, 'uid': stat.st_uid, 'gid': stat.st_gid}
    for required in ('finance-cashback/compose.yaml', 'finance-cashback/.env', 'finance-runtime/.env'):
        if files[required] is None:
            raise ValueError('Required existing deployment configuration missing')
    source = root / DATABASE
    before = database_facts(source)
    target = destination / 'files' / DATABASE
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as live, sqlite3.connect(target) as backup:
        live.backup(backup)
    if database_facts(target) != before or database_facts(source) != before:
        raise ValueError('Database changed during snapshot')
    stat = source.stat()
    files[DATABASE] = {'sha256': sha(target), 'mode': stat.st_mode & 0o777, 'uid': stat.st_uid, 'gid': stat.st_gid}
    manifest = {'schema_version': 1, 'secrets_included': True, 'rollback_image': image_reference,
                'files': files, 'database_table_counts': before, 'rollback_performed': False}
    (destination / 'container-inspect.json').write_text(json.dumps(inspection))
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    verify(destination)
    (destination / 'verification.json').write_text(json.dumps({'status': 'VERIFIED', 'manifest_sha256': sha(destination / 'manifest.json'), 'sqlite_restore_readable': True}) + '\n')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--image-reference')
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0 or not args.destination.is_absolute() or args.destination.parent != Path('/opt/backups/finance-cashback'):
        raise ValueError('Use root and the canonical private backup directory')
    if args.verify_only:
        verify(args.destination)
        print(json.dumps({'status': 'VERIFIED', 'path': str(args.destination)}))
        return
    if not args.image_reference:
        raise ValueError('Immutable rollback image required')
    def docker(*arguments):
        result = subprocess.run(['docker', *arguments], text=True, capture_output=True, timeout=120)
        if result.returncode:
            raise RuntimeError('Docker snapshot prerequisite failed')
        return result.stdout
    inspection = json.loads(docker('inspect', 'finance-cashback-control'))[0]
    if inspection['State'].get('Running') or inspection['State'].get('Paused'):
        raise ValueError('Stop Cashback writers before taking deployment snapshot')
    mounts = {m['Destination']: m for m in inspection['Mounts']}
    data = mounts.get('/var/lib/cashback-control', {})
    if data.get('Type') != 'bind' or data.get('Source') != '/opt/stacks/finance-actual/cashback-data':
        raise ValueError('Live Cashback data mount requires reconciliation')
    for target, mount in mounts.items():
        if target.startswith('/etc/cashback/') and mount.get('Source') not in [str(Path('/opt/stacks') / p) for p in CONFIGS]:
            raise ValueError('Live configuration mount requires reconciliation')
    image = json.loads(docker('image', 'inspect', inspection['Image']))[0]
    if args.image_reference not in image.get('RepoDigests', []):
        raise ValueError('Rollback image does not match current image store')
    snapshot(Path('/opt/stacks'), args.destination, args.image_reference, inspection)
    print(json.dumps({'status': 'VERIFIED', 'path': str(args.destination)}))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # No exception detail: inspection and configuration can contain secrets.
        import sys
        try:
            destination = Path(sys.argv[sys.argv.index('--destination') + 1])
            if destination.is_dir() and not destination.is_symlink() and destination.parent == Path('/opt/backups/finance-cashback'):
                receipt = destination / 'failure.json'
                receipt.write_text(json.dumps({'status': 'FAILED', 'writer_restart_attempted': False, 'rollback_performed': False}) + '\n')
                receipt.chmod(0o600)
        except Exception:
            pass
        print('Private Cashback snapshot failed; writer remains stopped and no configuration update is authorized.', file=sys.stderr)
        raise SystemExit(1)
