#!/usr/bin/env python3
"""Single-host Compose deployment. Never source .env or print secret values."""
import argparse
import base64
from datetime import datetime, timezone
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import subprocess
import sys
import tarfile
import time
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen
import uuid


ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ('database.dump', 'storage.tar.gz', 'platform_accounts.tar.gz')
COMPOSE_DEFAULTS = {
    'LOUXIANG_API_IMAGE': 'louxiang-api:local',
    'LOUXIANG_WEB_IMAGE': 'louxiang-web:local',
    'LOUXIANG_TUNNEL_IMAGE': 'louxiang-tunnel:local',
    'PPU_SSH_TARGET': 'ppu-runtime',
}
COMPOSE_FIELDS = frozenset(COMPOSE_DEFAULTS) | {'POSTGRES_PASSWORD', 'DEPLOY_ORIGIN', 'PPU_RUNTIME_DIR', 'PPU_SSH_DIR'}


class DeployError(RuntimeError):
    pass


class Runner:
    def run(self, args, *, input_path=None, output_path=None, env=None):
        source = open(input_path, 'rb') if input_path else None
        target = open(output_path, 'wb') if output_path else subprocess.PIPE
        try:
            result = subprocess.run(args, stdin=source, stdout=target,
                                    stderr=subprocess.PIPE, env=env, check=False)
        finally:
            if source:
                source.close()
            if output_path:
                target.close()
        if result.returncode:
            # Docker may include interpolated credentials in errors. Read logs
            # privately on the host rather than forwarding that stderr here.
            raise DeployError(f'Command failed with exit {result.returncode}; inspect Docker/container logs privately.')
        return (result.stdout or b'').decode('utf-8', errors='replace')


def digest(path):
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(block)
    return checksum.hexdigest()


def read_env(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise DeployError('A regular private .env file with mode 0600 is required.')
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, separator, value = (line[7:] if line.startswith('export ') else line).partition('=')
        if not separator or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key.strip()):
            raise DeployError('Invalid .env assignment; use KEY=value entries.')
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def private_directory(path):
    if path.is_symlink():
        raise DeployError('Private deployment paths cannot be symbolic links.')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def validate_private_path(path, label, *, directory=False, owner=10001):
    try:
        metadata = path.lstat()
    except OSError:
        raise DeployError(f'{label} is missing or inaccessible.') from None
    mode = stat.S_IMODE(metadata.st_mode)
    if directory:
        valid = stat.S_ISDIR(metadata.st_mode) and mode == 0o700
    else:
        valid = stat.S_ISREG(metadata.st_mode) and mode in (0o400, 0o600) and metadata.st_nlink == 1
    if not valid or metadata.st_uid != owner:
        raise DeployError(f'{label} must be owned by UID {owner} with private permissions and no file links.')


class Deployment:
    def __init__(self, root=ROOT, *, runner=None):
        self.root = Path(root).resolve()
        self.runner = runner or Runner()
        self.values = None

    def command(self, *args):
        project = (self.values or {}).get('COMPOSE_PROJECT_NAME', 'louxiang-prod')
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,47}', project) or project.startswith('louxiang-restore-'):
            raise DeployError('Choose a safe, stable production COMPOSE_PROJECT_NAME.')
        command = ['docker', 'compose', '--project-name', project, '--project-directory', str(self.root),
                '--env-file', str(self.root / '.env'), '-f', str(self.root / 'compose.yaml'),
                '-f', str(self.root / 'infra/deploy/compose.production.yaml')]
        if self.uses_ppu():
            command.extend(['-f', str(self.root / 'infra/deploy/compose.ppu.yaml')])
        return [*command, *args]

    def uses_ppu(self):
        values = self.values or {}
        return values.get('NATIVE_TEXT_BACKEND') == 'resident_ppu' or values.get('NATIVE_VIDEO_BACKEND') == 'lightx2v'

    def run(self, *args, **kwargs):
        # Compose otherwise lets exported shell values override --env-file.
        # Freeze checked values and optional defaults while retaining Docker
        # client context, PATH and other host runtime settings.
        environment = dict(os.environ)
        environment.update(COMPOSE_DEFAULTS)
        environment.update({key: value for key, value in (self.values or {}).items() if key in COMPOSE_FIELDS})
        kwargs.setdefault('env', environment)
        return self.runner.run(self.command(*args), **kwargs)

    def require_images(self, *, all_services=False):
        references = [self.values.get('LOUXIANG_API_IMAGE', COMPOSE_DEFAULTS['LOUXIANG_API_IMAGE'])]
        if all_services:
            references.append(self.values.get('LOUXIANG_WEB_IMAGE', COMPOSE_DEFAULTS['LOUXIANG_WEB_IMAGE']))
            if self.uses_ppu():
                references.append(self.values.get('LOUXIANG_TUNNEL_IMAGE', COMPOSE_DEFAULTS['LOUXIANG_TUNNEL_IMAGE']))
        if any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./:@-]*', value) for value in references):
            raise DeployError('Business image references must be literal Docker image names or digests.')
        try:
            self.runner.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', *sorted(set(references))])
        except DeployError:
            raise DeployError('A required prebuilt business image is missing. Import it with docker load or docker pull before continuing.') from None

    def check(self):
        self.values = read_env(self.root / '.env')
        for name in ('SECRET_KEY', 'POSTGRES_PASSWORD'):
            value = self.values.get(name, '')
            if len(value) < 32 or any(marker in value.lower() for marker in ('changeme', 'change-me', 'replace', 'example')):
                raise DeployError(f'{name} requires an independent value of at least 32 characters.')
        if not re.fullmatch(r'[A-Za-z0-9_-]+', self.values['POSTGRES_PASSWORD']):
            raise DeployError('POSTGRES_PASSWORD must be URL-safe because Compose embeds it in a database URL.')
        try:
            pii = base64.b64decode(self.values.get('PII_ENCRYPTION_KEY', ''), altchars=b'-_', validate=True)
        except ValueError:
            pii = b''
        if len(pii) != 32:
            raise DeployError('PII_ENCRYPTION_KEY requires a Fernet-compatible 32-byte URL-safe base64 key.')
        origin = urlsplit(self.values.get('DEPLOY_ORIGIN', ''))
        if (origin.scheme != 'https' or not origin.hostname or origin.username or origin.password
                or origin.path or origin.query or origin.fragment):
            raise DeployError('DEPLOY_ORIGIN must be a bare HTTPS origin, for example https://studio.example.org.')
        try:
            ipaddress.ip_address(origin.hostname)
        except ValueError:
            pass
        else:
            raise DeployError('Use a domain with a trusted TLS certificate for production browser sessions.')
        if self.values.get('CADDY_SITE', origin.netloc) != origin.netloc:
            raise DeployError('CADDY_SITE must match the hostname and port of DEPLOY_ORIGIN.')
        if self.uses_ppu():
            for name in ('PPU_RUNTIME_DIR', 'PPU_SSH_DIR'):
                path = Path(self.values.get(name, ''))
                if not path.is_absolute():
                    raise DeployError(f'{name} must be an absolute private directory.')
                validate_private_path(path, name, directory=True)
            for filename in ('config', 'id_ed25519', 'known_hosts'):
                validate_private_path(Path(self.values['PPU_SSH_DIR']) / filename, 'PPU SSH ' + filename)
            if self.values.get('NATIVE_TEXT_BACKEND') == 'resident_ppu':
                validate_private_path(Path(self.values['PPU_RUNTIME_DIR']) / 'native-qwen.auth', 'PPU text authentication file')
        self.run('config', '--quiet')
        return {'configuration_valid': True, 'origin': self.values['DEPLOY_ORIGIN']}

    def deploy(self, *, verify=True, no_build=False):
        self.check()
        if no_build:
            self.require_images(all_services=True)
        else:
            print('Building application images (existing services stay running).', flush=True)
            self.run('build', '--pull', 'api', 'worker', 'migrate', 'web', *(['ppu-tunnel'] if self.uses_ppu() else []))
        self.run('up', '-d', '--wait', '--wait-timeout', '120', 'db')
        print('Applying database migrations.', flush=True)
        self.run('run', '--rm', '--no-deps', '-T', 'migrate')
        print('Starting API, worker, web and HTTPS gateway.', flush=True)
        self.run('up', '-d', '--no-deps', '--no-build', '--force-recreate', '--wait', '--wait-timeout', '180',
                 'api', 'worker', 'web', 'caddy', *(['ppu-tunnel'] if self.uses_ppu() else []))
        return self.verify() if verify else {'containers_started': True, 'public_https_verified': False}

    def start(self):
        # main() holds the same deployment lock used by backup and upgrade.
        self.check()
        self.require_images(all_services=True)
        self.run('up', '-d', '--no-build')
        return {'containers_started': True, 'public_https_verified': False}

    def bootstrap(self):
        self.check()
        if len(self.values.get('BOOTSTRAP_PASSWORD', '')) < 12 or '@' not in self.values.get('BOOTSTRAP_EMAIL', ''):
            raise DeployError('Set BOOTSTRAP_EMAIL and an independent BOOTSTRAP_PASSWORD (at least 12 characters).')
        self.require_images()
        self.run('run', '--rm', '--no-deps', '-T', 'migrate', 'python', 'scripts/bootstrap_owner.py')
        return {'owner_created': True, 'demo_data_loaded': False}

    def verify(self):
        if self.values is None:
            self.check()
        origin = self.values['DEPLOY_ORIGIN']
        for attempt in range(12):
            try:
                with urlopen(origin + '/api/health', timeout=8) as response:
                    health = json.load(response)
                with urlopen(origin + '/', timeout=8) as response:
                    page = response.read(1024 * 1024).decode()
                if (health.get('status') != 'ok' or health.get('database') != 'postgresql'
                        or health.get('environment') != 'production' or '<html' not in page.lower()):
                    raise DeployError('Public response is not the expected production application.')
                return {'public_https_verified': True, 'database': 'postgresql', 'origin': origin}
            except (URLError, TimeoutError, OSError, ValueError):
                if attempt == 11:
                    raise DeployError('Public HTTPS verification failed; check DNS, certificate and security-group ports 80/443.') from None
                time.sleep(5)

    def backup(self):
        self.check()
        self.require_images()
        running = set(self.run('ps', '--status', 'running', '--services').split())
        if 'db' not in running:
            raise DeployError('The production database must be running before backup.')
        recovery_order = [name for name in ('api', 'ppu-tunnel', 'worker', 'web') if name in running]
        containers = {}
        for name in recovery_order:
            identifiers = self.run('ps', '--all', '--quiet', name).split()
            if len(identifiers) != 1 or not re.fullmatch(r'[a-f0-9]{12,64}', identifiers[0]):
                raise DeployError(f'Cannot identify the single running {name} container; backup was not started.')
            containers[name] = identifiers[0]
        parent = private_directory(self.root / '.runtime/production-backups')
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
        partial = private_directory(parent / ('.partial-' + stamp))
        stopped = [name for name in ('worker', 'ppu-tunnel', 'api', 'web') if name in running]
        backup_error = None
        try:
            if stopped:
                print('Entering a short maintenance window for a consistent database and file backup.', flush=True)
                self.run('stop', '--timeout', '60', *stopped)
            self.run('exec', '-T', 'db', 'pg_dump', '-U', 'housignal_owner', '-d', 'housignal',
                     '--format=custom', '--no-password', output_path=partial / 'database.dump')
            # Keep ownership with the host operator. Root tar truncates these
            # existing files instead of creating root-owned bind-mount output.
            for name in ('storage.tar.gz', 'platform_accounts.tar.gz'):
                (partial / name).touch(mode=0o600, exist_ok=False)
            self.run('run', '--rm', '--no-deps', '-T', '--user', '0:0', '--volume', str(partial) + ':/backup:rw',
                     '--entrypoint', 'sh', 'api', '-ceu',
                     'tar -czf /backup/storage.tar.gz -C /data/storage .; '
                     'tar --exclude=SingletonLock --exclude=SingletonSocket --exclude=SingletonCookie '
                     '-czf /backup/platform_accounts.tar.gz -C /app/runtime/platform-accounts .')
            checksums = {}
            for name in ARTIFACTS:
                path = partial / name
                if not path.is_file() or not path.stat().st_size:
                    raise DeployError('Backup artifact is missing or empty; no complete backup was published.')
                path.chmod(0o600)
                checksums[name] = digest(path)
            manifest = {'format': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
                        'sha256': checksums, 'env_included': False,
                        'sensitive_platform_sessions_included': True,
                        'consistency': 'application-writers-stopped'}
            (partial / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
            (partial / 'manifest.json').chmod(0o600)
            self.validate_backup(partial)
        except BaseException as error:
            backup_error = error
            raise
        finally:
            failed = []
            for name in recovery_order:
                # Compose 2.27 may start selected services concurrently or
                # re-evaluate the already-removed one-shot migrate service.
                # Reuse original containers in dependency order. Docker may
                # return before the API's shared namespace is ready for worker.
                for attempt in range(3):
                    try:
                        self.runner.run(['docker', 'start', containers[name]])
                        break
                    except Exception:
                        if attempt == 2:
                            failed.append(name)
                        else:
                            time.sleep(2)
            if failed:
                report = {'failed_services': failed,
                          'backup_error_type': type(backup_error).__name__ if backup_error else None,
                          'backup_artifacts_verified': backup_error is None}
                try:
                    report_path = partial / 'recovery.json'
                    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
                    report_path.chmod(0o600)
                except OSError:
                    pass
                print('Service recovery requires attention: ' + ', '.join(failed) +
                      '. Backup output remains in a private partial directory.', file=sys.stderr)
                if backup_error is None:
                    raise DeployError('Backup artifacts were verified, but service recovery failed: ' + ', '.join(failed))
        destination = parent / ('backup-' + stamp)
        partial.rename(destination)
        return destination

    def validate_backup(self, backup):
        backup = Path(backup).resolve(strict=True)
        try:
            manifest = json.loads((backup / 'manifest.json').read_text())
            if manifest.get('format') != 1 or set(manifest['sha256']) != set(ARTIFACTS):
                raise ValueError()
            for name in ARTIFACTS:
                path = backup / name
                if path.is_symlink() or not path.is_file() or digest(path) != manifest['sha256'][name]:
                    raise ValueError()
            for name in ('storage.tar.gz', 'platform_accounts.tar.gz'):
                with tarfile.open(backup / name, 'r:gz') as archive:
                    for member in archive:
                        path = PurePosixPath(member.name)
                        if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
                            raise ValueError()
        except (OSError, ValueError, KeyError, TypeError, tarfile.TarError):
            raise DeployError('Backup verification failed (manifest, checksum or unsafe archive entry).') from None
        return backup

    def restore_drill(self, backup):
        backup = self.validate_backup(backup)
        project = 'louxiang-restore-' + uuid.uuid4().hex
        # This separate Compose file has no application services, host ports,
        # external volumes or production env_file. Only its unique volumes can
        # be removed in the finally block.
        command = ['docker', 'compose', '--project-name', project, '--project-directory', str(self.root),
                   '--env-file', os.devnull, '-f', str(self.root / 'infra/deploy/compose.restore.yaml')]
        env = {key: value for key, value in os.environ.items() if key in {
            'PATH', 'HOME', 'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_CONFIG', 'XDG_RUNTIME_DIR',
            'LANG', 'LC_ALL', 'TMPDIR', 'SSL_CERT_FILE', 'SSL_CERT_DIR'}}
        env.update(RESTORE_DB_PASSWORD=secrets.token_hex(24), RESTORE_BACKUP_DIR=str(backup))
        try:
            self.runner.run([*command, 'up', '-d', '--wait', '--wait-timeout', '120', 'db'], env=env)
            self.runner.run([*command, 'exec', '-T', 'db', 'psql', '-v', 'ON_ERROR_STOP=1',
                             '-U', 'housignal_owner', '-d', 'housignal', '-c',
                             'CREATE ROLE housignal_app NOLOGIN; CREATE ROLE housignal_worker NOLOGIN;'], env=env)
            self.runner.run([*command, 'exec', '-T', 'db', 'pg_restore', '-U', 'housignal_owner',
                             '--exit-on-error', '--no-owner', '--no-acl', '-d', 'housignal'],
                            input_path=backup / 'database.dump', env=env)
            self.runner.run([*command, 'run', '--rm', '--no-deps', '-T', 'files'], env=env)
            tables = int(self.runner.run([*command, 'exec', '-T', 'db', 'psql', '-At',
                                         '-U', 'housignal_owner', '-d', 'housignal', '-c',
                                         "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"], env=env).strip())
            version = self.runner.run([*command, 'exec', '-T', 'db', 'psql', '-At', '-U', 'housignal_owner',
                                       '-d', 'housignal', '-c', 'SELECT version_num FROM alembic_version'], env=env).strip()
            if tables < 1 or not version:
                raise DeployError('Restored database is missing application tables or its migration version.')
            report = {'restore_project': project, 'tables': tables, 'schema_version': version,
                      'archive_checksums_verified': True, 'production_data_modified': False,
                      'application_login_tested': False}
        finally:
            self.runner.run([*command, 'down', '--volumes', '--remove-orphans'], env=env)
        reports = private_directory(self.root / '.runtime/restore-reports')
        output = reports / (project + '.json')
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        output.chmod(0o600)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'deploy', 'start', 'upgrade', 'bootstrap', 'verify', 'backup', 'restore-drill', 'status'))
    parser.add_argument('backup_directory', nargs='?')
    parser.add_argument('--no-build', action='store_true', help='Use prebuilt images already imported on the target server.')
    args = parser.parse_args()
    private_directory(ROOT / '.runtime')
    lock_path = ROOT / '.runtime/deployment.lock'
    with lock_path.open('a') as lock:
        lock_path.chmod(0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DeployError('Another deployment, backup or restore operation is running.') from None
        deployment = Deployment()
        if args.action == 'restore-drill':
            if not args.backup_directory:
                parser.error('restore-drill requires a backup directory')
            result = deployment.restore_drill(args.backup_directory)
        elif args.action == 'upgrade':
            if args.no_build:
                deployment.check()
                deployment.require_images(all_services=True)
            backup = deployment.backup()
            print('Upgrade backup: ' + str(backup), flush=True)
            result = deployment.deploy(no_build=args.no_build)
        elif args.action == 'status':
            deployment.check()
            print(deployment.run('ps'), end='')
            return
        elif args.action == 'deploy':
            result = deployment.deploy(no_build=args.no_build)
        else:
            result = getattr(deployment, args.action.replace('-', '_'))()
        print(json.dumps(result if not isinstance(result, Path) else {'backup': str(result)}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (DeployError, FileNotFoundError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
