"""Export reviewed candidate inputs to a NEW directory, without Git ancestry.

This does not initialize Git, commit, push, release, copy private state, or change
production. Verify and review the manifest before publishing the clean snapshot.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = {'.agents', '.github', 'bin', 'cmd', 'common', 'constant', 'controller',
    'deploy', 'docs', 'dto', 'e2e', 'electron', 'i18n', 'logger', 'middleware', 'model',
    'oauth', 'pkg', 'plugins', 'relay', 'relaykit', 'router', 'service', 'setting', 'types', 'web'}
ROOT_FILES = {'.dockerignore', '.env.example', '.gitattributes', '.gitignore', 'AGENTS.md',
    'CLAUDE.md', 'Dockerfile', 'Dockerfile.dev', 'LICENSE', 'NOTICE', 'THIRD-PARTY-LICENSES.md',
    'HANDOFF-SUB2API.md', 'VERSION', 'docker-compose.dev.yml', 'docker-compose.yml',
    'go.mod', 'go.sum', 'main.go', 'makefile', 'new-api.service'}
LAB_FILES = {'lab/maintenance/sub2api-migration.md', 'lab/realyu-pricing-20260924.json',
    'lab/verify_provider_release.py', 'lab/verify_database_matrix.py'}
FORBIDDEN_PARTS = {'.git', '.lab', '.private', 'node_modules', '__pycache__', 'dist',
    'logs', 'backups', 'coverage', '.tanstack', '_reports', '_extras'}
PRIVATE_SUFFIXES = ('.db', '.sqlite', '.sqlite3', '.db-wal', '.db-shm',
    '.sqlite-wal', '.sqlite-shm', '.sqlite3-wal', '.sqlite3-shm', '.log', '.pyc',
    '.key', '.pem', '.p12', '.pfx', '.crt', '.cer', '.p7b', '.jks', '.keystore')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    destination = args.destination.resolve()
    if destination.exists():
        raise RuntimeError('Export refuses existing destinations')
    if destination.is_relative_to(ROOT) or ROOT.is_relative_to(destination):
        raise RuntimeError('Export must be outside the source tree and its ancestors')
    tracked = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=ROOT).decode().split('\0')
    index_entries = subprocess.check_output(['git', 'ls-files', '--stage', '-z'], cwd=ROOT).decode().split('\0')
    git_modes = {}
    for entry in index_entries:
        if entry:
            metadata, name = entry.split('\t', 1)
            git_modes[name] = metadata.split()[0]
    selected = set()
    for name in tracked:
        if not name:
            continue
        path = Path(name)
        if path.is_absolute() or '..' in path.parts:
            raise RuntimeError('Unsafe source path')
        if any(part.casefold() in FORBIDDEN_PARTS for part in path.parts):
            continue
        lower_name = path.name.casefold()
        if lower_name.startswith('.env') and lower_name != '.env.example':
            continue
        if lower_name in ('credentials.json', 'auth.json') or lower_name.endswith(PRIVATE_SUFFIXES):
            continue
        allowed = (path.parts[0] in SOURCE_ROOTS or name in ROOT_FILES or
                   name.startswith('README') and path.suffix == '.md' or name in LAB_FILES or
                   name.startswith('lab/sub2api_e2e/'))
        if allowed and (ROOT / path).is_file():
            selected.add(name)
    # Public installers include explicitly ignored runtime distributions. Only
    # the reviewed release manifest may add files to the public download tree.
    selected = {name for name in selected if not name.startswith('web/public/downloads/')}
    assets = json.loads((ROOT / 'deploy/sub2api/public-downloads-manifest.json').read_text(encoding='utf-8'))['files']
    for asset in assets:
        name = asset['path']
        path = Path(name)
        if not name.startswith('web/public/downloads/') or path.is_absolute() or '..' in path.parts:
            raise RuntimeError('Unsafe public asset path')
        source = ROOT / path
        if not source.is_file() or source.is_symlink() or not source.resolve().is_relative_to(ROOT):
            raise RuntimeError('Public asset missing or outside source tree')
        if hashlib.sha256(source.read_bytes()).hexdigest() != asset['sha256']:
            raise RuntimeError('Public asset differs from reviewed release manifest: ' + name)
        selected.add(name)
    destination.mkdir(parents=True)
    manifest = []
    for name in sorted(selected):
        source = ROOT / name
        resolved = source.resolve()
        if source.is_symlink() or not resolved.is_relative_to(ROOT) or any(part.casefold() in FORBIDDEN_PARTS for part in resolved.relative_to(ROOT).parts):
            raise RuntimeError('Source path crosses a prohibited boundary: ' + name)
        # Keep all upstream workflow content and attributions, but hand it off
        # as inactive templates. The candidate must not activate publication,
        # and code-only Git authorization does not grant workflow permissions.
        output_name = ('deploy/sub2api/workflow-templates/' + name.removeprefix('.github/workflows/')
                       if name.startswith('.github/workflows/') else name)
        target = destination / output_name
        if target.exists():
            raise RuntimeError('Export path collision: ' + output_name)
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = source.read_bytes()
        data = raw
        # Match the repository's text=auto / eol=lf normalization before hashing.
        # Published installers are explicitly -text and remain byte-for-byte.
        if not name.startswith('web/public/downloads/') and b'\0' not in raw:
            try:
                raw.decode('utf-8')
                data = raw.replace(b'\r\n', b'\n')
            except UnicodeDecodeError:
                pass
        target.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise RuntimeError('Export verification failed: ' + name)
        manifest.append({'path': output_name, 'source_path': name, 'sha256': digest, 'source_sha256': hashlib.sha256(raw).hexdigest(),
                         'bytes': target.stat().st_size, 'git_mode': git_modes.get(name, '100644')})
    (destination / 'SOURCE-MANIFEST.json').write_text(json.dumps({'format':1,
        'baseline_release':'realyu-provider-v3.9.2.22-20261008',
        'git_ancestry_included':False,'private_runtime_paths_excluded':True,
        'requires_content_review':True,
        'files':manifest},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'destination':str(destination),'files':len(manifest),
        'bytes':sum(row['bytes'] for row in manifest),'git_initialized':False}))


if __name__ == '__main__':
    main()
