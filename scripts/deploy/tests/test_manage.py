"""Exercise deployment decisions without a Docker daemon or production services."""
import base64
import configparser
import fcntl
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sys
from pathlib import Path
import shlex
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch


MODULE = Path(__file__).resolve().parents[1] / 'manage.py'


def load_module():
    spec = importlib.util.spec_from_file_location('deployment_manage', MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DockerFixture:
    """Only the external Docker boundary is replaced; files are real."""
    def __init__(self, fail_dump=False, fail_migration=False, platform_source=None):
        self.calls = []
        self.fail_dump = fail_dump
        self.fail_migration = fail_migration
        self.platform_source = platform_source

    def run(self, args, *, input_path=None, output_path=None, env=None):
        self.calls.append((args, env))
        if 'pg_dump' in args:
            if self.fail_dump:
                raise RuntimeError('simulated interrupted database export')
            Path(output_path).write_bytes(b'PGDMP-test-fixture')
        if 'migrate' in args and self.fail_migration and 'run' in args:
            raise RuntimeError('simulated migration failure')
        if 'ps' in args:
            if '--quiet' in args:
                return {'api': 'a', 'worker': 'b', 'web': 'c', 'ppu-tunnel': 'd'}[args[-1]] * 64 + '\n'
            return 'db\napi\nworker\nweb\ncaddy\n'
        if 'tar -czf' in ' '.join(args):
            bind = args[args.index('--volume') + 1]
            destination = Path(bind[:-len(':/backup:rw')])
            for name in ('storage', 'platform_accounts'):
                if name == 'platform_accounts' and self.platform_source:
                    exclusions = [token for token in shlex.split(args[-1]) if token.startswith('--exclude=')]
                    subprocess.run(['tar', *exclusions, '-czf', str(destination / (name + '.tar.gz')),
                                    '-C', str(self.platform_source), '.'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    continue
                with tarfile.open(destination / (name + '.tar.gz'), 'w:gz') as archive:
                    info = tarfile.TarInfo('fixture.txt')
                    info.size = 7
                    archive.addfile(info, io.BytesIO(b'fixture'))
        if 'SELECT count(*) FROM information_schema.tables' in ' '.join(args):
            return '42\n'
        if 'SELECT version_num FROM alembic_version' in ' '.join(args):
            return 'fixture_revision\n'
        return ''


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(MODULE.is_file(), 'deployment manager has not been implemented')
        self.module = load_module()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.values = {
            'POSTGRES_PASSWORD': '1' * 48,
            'SECRET_KEY': '2' * 64,
            'PII_ENCRYPTION_KEY': base64.urlsafe_b64encode(b'k' * 32).decode(),
            'DEPLOY_ORIGIN': 'https://studio.example.org',
            'BOOTSTRAP_EMAIL': 'owner@example.org',
            'BOOTSTRAP_PASSWORD': 'independent-fixture-password',
        }
        self.write_env()

    def write_env(self):
        path = self.root / '.env'
        path.write_text(''.join(f'{key}={value}\n' for key, value in self.values.items()))
        path.chmod(0o600)

    def deployment(self, runner=None):
        return self.module.Deployment(self.root, runner=runner or DockerFixture())

    def test_public_env_is_rejected_before_docker_sees_secrets(self):
        (self.root / '.env').chmod(0o644)
        runner = DockerFixture()
        with self.assertRaises(self.module.DeployError):
            self.deployment(runner).check()
        self.assertEqual(runner.calls, [])

    def test_plain_http_is_rejected_because_production_sessions_are_secure(self):
        self.values['DEPLOY_ORIGIN'] = 'http://studio.example.org'
        self.write_env()
        with self.assertRaises(self.module.DeployError):
            self.deployment().check()

    def test_url_reserved_database_password_is_rejected(self):
        self.values['POSTGRES_PASSWORD'] = 'unsafe:/' + 'a' * 40
        self.write_env()
        with self.assertRaises(self.module.DeployError):
            self.deployment().check()

    def test_env_is_parsed_without_executing_shell(self):
        marker = self.root / 'must-not-exist'
        with (self.root / '.env').open('a') as stream:
            stream.write(f'UNUSED=$(touch {marker})\n')
        self.deployment().check()
        self.assertFalse(marker.exists())

    def test_compose_execution_uses_validated_env_even_when_shell_exports_conflicting_values(self):
        self.values.update(LOUXIANG_API_IMAGE='review-api:configured', LOUXIANG_WEB_IMAGE='review-web:configured')
        self.write_env()
        runner = DockerFixture()
        deployment = self.deployment(runner)
        with patch.dict(os.environ, {'LOUXIANG_API_IMAGE': 'review-api:wrong-shell',
                                     'LOUXIANG_WEB_IMAGE': 'review-web:wrong-shell',
                                     'POSTGRES_PASSWORD': 'wrong-shell-password',
                                     'DEPLOY_ORIGIN': 'https://wrong-shell.example.org'}):
            deployment.check()
            deployment.require_images(all_services=True)
        environment = next(env for args, env in runner.calls if 'config' in args)
        self.assertIsNotNone(environment, 'Compose must receive the configuration that preflight validated')
        self.assertEqual(environment['LOUXIANG_API_IMAGE'], 'review-api:configured')
        self.assertEqual(environment['LOUXIANG_WEB_IMAGE'], 'review-web:configured')
        self.assertEqual(environment['POSTGRES_PASSWORD'], '1' * 48)
        self.assertEqual(environment['DEPLOY_ORIGIN'], 'https://studio.example.org')

    def test_absent_image_values_keep_preflight_defaults_instead_of_exported_shell_images(self):
        runner = DockerFixture()
        deployment = self.deployment(runner)
        with patch.dict(os.environ, {'LOUXIANG_API_IMAGE': 'review-api:wrong-shell',
                                     'LOUXIANG_WEB_IMAGE': 'review-web:wrong-shell',
                                     'LOUXIANG_TUNNEL_IMAGE': 'review-tunnel:wrong-shell'}):
            deployment.check()
            deployment.require_images(all_services=True)
        environment = next(env for args, env in runner.calls if 'config' in args)
        self.assertIsNotNone(environment, 'Missing .env values must not inherit unrelated shell image tags')
        self.assertEqual(environment.get('LOUXIANG_API_IMAGE', 'louxiang-api:local'), 'louxiang-api:local')
        self.assertEqual(environment.get('LOUXIANG_WEB_IMAGE', 'louxiang-web:local'), 'louxiang-web:local')
        self.assertEqual(environment.get('LOUXIANG_TUNNEL_IMAGE', 'louxiang-tunnel:local'), 'louxiang-tunnel:local')

    def test_dotenv_does_not_change_the_docker_client_context_or_executable_path(self):
        self.values.update(DOCKER_HOST='tcp://wrong-dotenv.invalid:2375', PATH='/wrong-dotenv-bin')
        self.write_env()
        runner = DockerFixture()
        with patch.dict(os.environ, {'DOCKER_HOST': 'unix:///fixture-docker.sock', 'PATH': '/fixture-client-bin'}):
            self.deployment(runner).check()
        environment = next(env for args, env in runner.calls if 'config' in args)
        self.assertEqual(environment['DOCKER_HOST'], 'unix:///fixture-docker.sock')
        self.assertEqual(environment['PATH'], '/fixture-client-bin')

    @unittest.skipUnless(shutil.which('docker'), 'Docker Compose is required for config rendering')
    def test_real_compose_render_matches_checked_images_and_database_config(self):
        repository = MODULE.parents[2]
        (self.root / 'infra/deploy').mkdir(parents=True)
        for relative in ('compose.yaml', 'infra/deploy/compose.production.yaml'):
            shutil.copyfile(repository / relative, self.root / relative)
        for configured in (False, True):
            with self.subTest(configured_images=configured):
                if configured:
                    self.values.update(LOUXIANG_API_IMAGE='review-api:configured', LOUXIANG_WEB_IMAGE='review-web:configured')
                self.write_env()
                deployment = self.module.Deployment(self.root)
                with patch.dict(os.environ, {'LOUXIANG_API_IMAGE': 'review-api:wrong-shell',
                                             'LOUXIANG_WEB_IMAGE': 'review-web:wrong-shell',
                                             'POSTGRES_PASSWORD': 'wrong-shell-password',
                                             'DEPLOY_ORIGIN': 'https://wrong-shell.example.org'}):
                    deployment.check()
                    rendered = json.loads(deployment.run('config', '--format', 'json'))
                services = rendered['services']
                self.assertEqual(services['api']['image'], 'review-api:configured' if configured else 'louxiang-api:local')
                self.assertEqual(services['web']['image'], 'review-web:configured' if configured else 'louxiang-web:local')
                self.assertEqual(services['api']['environment']['DATABASE_URL'],
                                 'postgresql+psycopg://housignal_app:' + '1' * 48 + '@db:5432/housignal')
                self.assertEqual(services['caddy']['environment']['DEPLOY_ORIGIN'], 'https://studio.example.org')

    def test_video_only_ppu_does_not_require_an_unrelated_text_authentication_file(self):
        self.configure_local_ppu_fixture()
        self.values.update(NATIVE_TEXT_BACKEND='dashscope', NATIVE_VIDEO_BACKEND='lightx2v')
        self.write_env()
        original = self.module.validate_private_path
        def local_owner(path, label, **kwargs):
            return original(path, label, **kwargs, owner=os.getuid())
        with patch.object(self.module, 'validate_private_path', local_owner):
            result = self.deployment().check()
        self.assertTrue(result['configuration_valid'])
        self.assertFalse((self.root / 'ppu-runtime/native-qwen.auth').exists())

    def test_resident_text_ppu_still_requires_its_private_authentication_file(self):
        self.configure_local_ppu_fixture()
        self.values.update(NATIVE_TEXT_BACKEND='resident_ppu', NATIVE_VIDEO_BACKEND='dashscope')
        self.write_env()
        original = self.module.validate_private_path
        def local_owner(path, label, **kwargs):
            return original(path, label, **kwargs, owner=os.getuid())
        with patch.object(self.module, 'validate_private_path', local_owner):
            with self.assertRaisesRegex(self.module.DeployError, 'text authentication file'):
                self.deployment().check()

    def configure_local_ppu_fixture(self):
        runtime = self.root / 'ppu-runtime'
        ssh = self.root / 'ppu-ssh'
        runtime.mkdir(mode=0o700)
        ssh.mkdir(mode=0o700)
        for name in ('config', 'id_ed25519', 'known_hosts'):
            path = ssh / name
            path.write_text('nonsecret fixture\n')
            path.chmod(0o600)
        self.values.update(PPU_RUNTIME_DIR=str(runtime), PPU_SSH_DIR=str(ssh))

    def test_systemd_application_start_uses_the_same_deployment_lock_as_backups(self):
        copied_script = self.root / 'scripts/deploy/manage.py'
        copied_script.parent.mkdir(parents=True)
        shutil.copyfile(MODULE, copied_script)
        service = configparser.ConfigParser(interpolation=None)
        service.read(MODULE.parents[2] / 'infra/deploy/louxiang.service')
        shim = self.root / 'bin/docker'
        shim.parent.mkdir()
        log = self.root / 'docker-calls.jsonl'
        shim.write_text('#!' + sys.executable + '\nimport json,os,sys\n'
                        'with open(os.environ["REVIEW_DOCKER_LOG"],"a") as stream: stream.write(json.dumps(sys.argv[1:])+"\\n")\n')
        shim.chmod(0o700)
        command = shlex.split(service['Service']['ExecStart'])
        command = [str(shim) if index == 0 and Path(value).name == 'docker' else
                   sys.executable if index == 0 and Path(value).name == 'python3' else
                   value.replace('/srv/louxiang', str(self.root)) for index, value in enumerate(command)]
        environment = dict(os.environ, PATH=str(shim.parent) + os.pathsep + os.environ['PATH'], REVIEW_DOCKER_LOG=str(log))
        runtime = self.root / '.runtime'
        runtime.mkdir(mode=0o700)
        with (runtime / 'deployment.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = subprocess.run(command, cwd=self.root, env=environment, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0, 'System startup must not restart writers while a backup owns the lock')
        self.assertIn('Another deployment', result.stderr)
        self.assertFalse(log.exists(), 'Blocked application startup must not reach Docker')
        result = subprocess.run(command, cwd=self.root, env=environment, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        starts = [args for args in calls if 'up' in args]
        self.assertEqual(len(starts), 1)
        self.assertIn('--no-build', starts[0])
        self.assertFalse(any('build' in args or 'pg_dump' in args for args in calls))

    def test_failed_migration_never_replaces_application_containers(self):
        runner = DockerFixture(fail_migration=True)
        with self.assertRaises(RuntimeError):
            self.deployment(runner).deploy(verify=False)
        mutations = [args for args, _ in runner.calls if 'up' in args]
        self.assertEqual(len(mutations), 1)
        self.assertEqual(mutations[0][-1], 'db')

    def test_prebuilt_deployment_never_builds_on_small_cloud_host(self):
        runner = DockerFixture()
        try:
            self.deployment(runner).deploy(verify=False, no_build=True)
        except TypeError:
            self.fail('prebuilt deployment mode has not been implemented')
        self.assertFalse(any('build' in args for args, _ in runner.calls))
        migration = next(args for args, _ in runner.calls if 'run' in args and 'migrate' in args)
        self.assertNotIn('--no-build', migration)
        application = next(args for args, _ in runner.calls if 'up' in args and 'api' in args)
        self.assertIn('--no-build', application)
        inspect = next(args for args, _ in runner.calls if args[1:3] == ['image', 'inspect'])
        self.assertIn('louxiang-api:local', inspect)
        self.assertIn('louxiang-web:local', inspect)

    def test_missing_prebuilt_image_fails_before_starting_or_running_containers(self):
        deployment = self.deployment()
        module = self.module
        class MissingImage(DockerFixture):
            def run(self, args, **kwargs):
                if args[1:3] == ['image', 'inspect']:
                    self.calls.append((args, kwargs.get('env')))
                    raise module.DeployError('fixture missing image')
                return super().run(args, **kwargs)
        runner = MissingImage()
        deployment.runner = runner
        with self.assertRaises(self.module.DeployError):
            deployment.deploy(verify=False, no_build=True)
        self.assertFalse(any('up' in args or 'run' in args or 'build' in args for args, _ in runner.calls))

    def test_bootstrap_and_backup_are_compatible_with_compose_227_run_options(self):
        test = self
        class Compose227(DockerFixture):
            def run(self, args, **kwargs):
                if args[:2] == ['docker', 'compose'] and 'run' in args:
                    test.assertNotIn('--no-build', args, 'Compose 2.27 run rejects --no-build')
                return super().run(args, **kwargs)
        runner = Compose227()
        self.deployment(runner).bootstrap()
        self.deployment(runner).backup()
        inspections = [args for args, _ in runner.calls if args[1:3] == ['image', 'inspect']]
        self.assertEqual(len(inspections), 2)
        self.assertTrue(all('louxiang-api:local' in args for args in inspections))

    def test_ppu_configuration_includes_shared_network_overlay(self):
        deployment = self.deployment()
        deployment.values = {**self.values, 'NATIVE_TEXT_BACKEND': 'resident_ppu'}
        self.assertIn(str(self.root.resolve() / 'infra/deploy/compose.ppu.yaml'), deployment.command('config', '--quiet'))

    def test_ppu_missing_private_directories_is_rejected_before_docker(self):
        self.values['NATIVE_VIDEO_BACKEND'] = 'lightx2v'
        self.write_env()
        runner = DockerFixture()
        with self.assertRaises(self.module.DeployError):
            self.deployment(runner).check()
        self.assertEqual(runner.calls, [])

    def test_ssh_private_file_rejects_public_permissions_and_hardlinks(self):
        path = self.root / 'id_ed25519'
        path.write_text('fixture only')
        path.chmod(0o644)
        self.assertTrue(hasattr(self.module, 'validate_private_path'), 'SSH file preflight has not been implemented')
        with self.assertRaises(self.module.DeployError):
            self.module.validate_private_path(path, 'SSH fixture', owner=os.getuid())
        path.chmod(0o600)
        self.module.validate_private_path(path, 'SSH fixture', owner=os.getuid())
        os.link(path, self.root / 'second-link')
        with self.assertRaises(self.module.DeployError):
            self.module.validate_private_path(path, 'SSH fixture', owner=os.getuid())

    def test_backup_is_private_complete_and_resumes_original_services(self):
        runner = DockerFixture()
        bundle = self.deployment(runner).backup()
        manifest = json.loads((bundle / 'manifest.json').read_text())
        self.assertEqual(set(manifest['sha256']), {'database.dump', 'storage.tar.gz', 'platform_accounts.tar.gz'})
        self.assertEqual(manifest['sha256']['database.dump'], hashlib.sha256(b'PGDMP-test-fixture').hexdigest())
        self.assertEqual(bundle.stat().st_mode & 0o777, 0o700)
        self.assertEqual((bundle / 'database.dump').stat().st_mode & 0o777, 0o600)
        self.assertFalse((bundle / '.env').exists())
        stops = [args for args, _ in runner.calls if 'stop' in args]
        starts = [args for args, _ in runner.calls if 'start' in args]
        self.assertEqual(stops[0][-3:], ['worker', 'api', 'web'])
        self.assertEqual([args[-1] for args in starts], ['a' * 64, 'b' * 64, 'c' * 64])
        self.assertTrue(all(args[:2] == ['docker', 'start'] for args in starts))

    def test_recovery_continues_in_order_and_preserves_the_original_backup_exception(self):
        module = self.module
        primary = module.DeployError('fixture database export failure')
        class FailedBackupAndRecovery(DockerFixture):
            def run(self, args, **kwargs):
                if 'pg_dump' in args:
                    self.calls.append((args, kwargs.get('env')))
                    raise primary
                if 'start' in args and (args[-1] == 'a' * 64 or 'api' in args):
                    self.calls.append((args, kwargs.get('env')))
                    raise module.DeployError('fixture recovery failure')
                return super().run(args, **kwargs)
        runner = FailedBackupAndRecovery()
        with patch.object(module.time, 'sleep'), self.assertRaises(self.module.DeployError) as caught:
            self.deployment(runner).backup()
        self.assertIs(caught.exception, primary)
        self.assertEqual([args[-1] for args, _ in runner.calls if 'start' in args],
                         ['a' * 64, 'a' * 64, 'a' * 64, 'b' * 64, 'c' * 64])
        partial = next((self.root / '.runtime/production-backups').glob('.partial-*'))
        recovery = json.loads((partial / 'recovery.json').read_text())
        self.assertEqual(recovery['failed_services'], ['api'])
        self.assertEqual(recovery['backup_error_type'], 'DeployError')

    def test_transient_worker_start_failure_retries_original_container_before_publishing_backup(self):
        module = self.module
        for failures in (1, 2):
            with self.subTest(failures=failures):
                class NamespaceNotReady(DockerFixture):
                    def __init__(self):
                        super().__init__()
                        self.worker_attempts = 0

                    def run(self, args, **kwargs):
                        if 'ps' in args and '--services' in args:
                            self.calls.append((args, kwargs.get('env')))
                            return 'db\napi\nppu-tunnel\nworker\nweb\ncaddy\n'
                        if args[:2] == ['docker', 'start'] and args[-1] == 'b' * 64:
                            self.worker_attempts += 1
                            if self.worker_attempts <= failures:
                                self.calls.append((args, kwargs.get('env')))
                                raise module.DeployError('fixture API namespace is not ready')
                        return super().run(args, **kwargs)

                runner = NamespaceNotReady()
                with patch.object(module.time, 'sleep') as sleep:
                    try:
                        bundle = self.deployment(runner).backup()
                    except module.DeployError:
                        self.fail('A temporary worker namespace start failure must recover and publish the verified backup')
                starts = [args for args, _ in runner.calls if args[:2] == ['docker', 'start']]
                self.assertEqual([args[-1] for args in starts],
                                 ['a' * 64, 'd' * 64] + ['b' * 64] * (failures + 1) + ['c' * 64])
                self.assertTrue(all(len(args) == 3 for args in starts))
                self.assertEqual([call[0] for call in sleep.call_args_list], [(2,)] * failures)
                self.assertTrue(bundle.name.startswith('backup-'))
                self.assertTrue((bundle / 'manifest.json').is_file())
                self.assertFalse((bundle / 'recovery.json').exists())
                self.assertEqual(bundle, self.deployment().validate_backup(bundle))

    def test_successful_backup_with_recovery_failure_keeps_partial_and_attempts_all_services(self):
        module = self.module
        class FailedRecovery(DockerFixture):
            def run(self, args, **kwargs):
                if 'start' in args and (args[-1] == 'b' * 64 or 'worker' in args):
                    self.calls.append((args, kwargs.get('env')))
                    raise module.DeployError('fixture worker recovery failure')
                return super().run(args, **kwargs)
        runner = FailedRecovery()
        with patch.object(module.time, 'sleep') as sleep, self.assertRaises(self.module.DeployError):
            self.deployment(runner).backup()
        self.assertEqual([args[-1] for args, _ in runner.calls if 'start' in args],
                         ['a' * 64, 'b' * 64, 'b' * 64, 'b' * 64, 'c' * 64])
        self.assertEqual([call[0] for call in sleep.call_args_list], [(2,), (2,)])
        self.assertEqual(list((self.root / '.runtime/production-backups').glob('backup-*')), [])
        partial = next((self.root / '.runtime/production-backups').glob('.partial-*'))
        self.assertTrue((partial / 'manifest.json').is_file())
        recovery = json.loads((partial / 'recovery.json').read_text())
        self.assertEqual(recovery, {'failed_services': ['worker'], 'backup_error_type': None,
                                    'backup_artifacts_verified': True})
        self.assertEqual((partial / 'recovery.json').stat().st_mode & 0o777, 0o600)

    def test_backup_archive_helper_can_read_nonroot_volumes_and_write_private_host_directory(self):
        runner = DockerFixture()
        self.deployment(runner).backup()
        archive = next(args for args, _ in runner.calls if 'tar -czf' in ' '.join(args))
        self.assertIn('--user', archive)
        self.assertEqual(archive[archive.index('--user') + 1], '0:0')

    def test_archive_targets_are_host_owned_and_private_before_root_helper_writes(self):
        test = self
        class RootArchiveHelper(DockerFixture):
            def run(self, args, **kwargs):
                if 'tar -czf' in ' '.join(args):
                    bind = args[args.index('--volume') + 1]
                    destination = Path(bind[:-len(':/backup:rw')])
                    for name in ('storage.tar.gz', 'platform_accounts.tar.gz'):
                        path = destination / name
                        test.assertTrue(path.exists(), 'host-owned target must exist before root helper opens it')
                        test.assertEqual(path.stat().st_uid, os.getuid())
                        test.assertEqual(path.stat().st_mode & 0o777, 0o600)
                return super().run(args, **kwargs)
        self.deployment(RootArchiveHelper()).backup()

    def test_chromium_transient_singleton_links_are_excluded_from_restorable_backup(self):
        profile = self.root / 'browser/account/profile'
        profile.mkdir(parents=True)
        (profile / 'Cookies').write_text('private fixture')
        for name in ('SingletonLock', 'SingletonSocket', 'SingletonCookie'):
            (profile / name).symlink_to('/unavailable-transient-runtime')
        bundle = self.deployment(DockerFixture(platform_source=self.root / 'browser')).backup()
        try:
            self.deployment().validate_backup(bundle)
        except self.module.DeployError:
            self.fail('Chromium temporary singleton links must be excluded from the backup')
        with tarfile.open(bundle / 'platform_accounts.tar.gz') as archive:
            names = [member.name for member in archive]
        self.assertIn('./account/profile/Cookies', names)
        self.assertFalse(any(Path(name).name.startswith('Singleton') for name in names))

    def test_unknown_profile_link_prevents_complete_backup_publication(self):
        profile = self.root / 'browser'
        profile.mkdir()
        (profile / 'unknown-link').symlink_to('/not-a-profile-runtime-link')
        runner = DockerFixture(platform_source=profile)
        with self.assertRaises(self.module.DeployError):
            self.deployment(runner).backup()
        self.assertEqual(list((self.root / '.runtime/production-backups').glob('backup-*')), [])
        self.assertTrue(any('start' in args for args, _ in runner.calls))

    def test_restore_failure_still_cleans_only_the_isolated_project(self):
        bundle = self.deployment().backup()
        class InterruptedRestore(DockerFixture):
            def run(self, args, **kwargs):
                if 'pg_restore' in args:
                    self.calls.append((args, kwargs.get('env')))
                    raise RuntimeError('restore interrupted')
                return super().run(args, **kwargs)
        runner = InterruptedRestore()
        with self.assertRaises(RuntimeError):
            self.deployment(runner).restore_drill(bundle)
        self.assertIn('down', runner.calls[-1][0])
        self.assertTrue(runner.calls[-1][0][runner.calls[-1][0].index('--project-name') + 1].startswith('louxiang-restore-'))

    def test_failed_backup_resumes_services_without_publishing_complete_bundle(self):
        runner = DockerFixture(fail_dump=True)
        with self.assertRaises(RuntimeError):
            self.deployment(runner).backup()
        self.assertTrue(any('start' in args for args, _ in runner.calls))
        self.assertEqual(list((self.root / '.runtime/production-backups').glob('backup-*')), [])

    def test_corrupt_backup_is_rejected_before_restore_container_creation(self):
        bundle = self.deployment().backup()
        (bundle / 'database.dump').write_bytes(b'tampered')
        runner = DockerFixture()
        with self.assertRaises(self.module.DeployError):
            self.deployment(runner).restore_drill(bundle)
        self.assertEqual(runner.calls, [])

    def test_archive_traversal_is_rejected_even_with_matching_archive_checksum(self):
        bundle = self.deployment().backup()
        archive_path = bundle / 'storage.tar.gz'
        with tarfile.open(archive_path, 'w:gz') as archive:
            info = tarfile.TarInfo('../../escape')
            info.size = 4
            archive.addfile(info, io.BytesIO(b'evil'))
        manifest_path = bundle / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['sha256']['storage.tar.gz'] = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest))
        runner = DockerFixture()
        with self.assertRaises(self.module.DeployError):
            self.deployment(runner).restore_drill(bundle)
        self.assertEqual(runner.calls, [])

    def test_restore_uses_only_unique_project_and_always_removes_its_volumes(self):
        bundle = self.deployment().backup()
        runner = DockerFixture()
        report = self.deployment(runner).restore_drill(bundle)
        self.assertEqual(report['tables'], 42)
        self.assertEqual(report['schema_version'], 'fixture_revision')
        self.assertFalse(report['production_data_modified'])
        projects = {args[args.index('--project-name') + 1] for args, _ in runner.calls}
        self.assertEqual(len(projects), 1)
        self.assertTrue(next(iter(projects)).startswith('louxiang-restore-'))
        self.assertNotIn('louxiang-prod', projects)
        self.assertIn('down', runner.calls[-1][0])
        self.assertIn('--volumes', runner.calls[-1][0])
        for args, env in runner.calls:
            self.assertIn(os.devnull, args)
            self.assertNotIn(str(self.root / '.env'), args)
            self.assertNotIn('BOOTSTRAP_PASSWORD', env)


if __name__ == '__main__':
    unittest.main()
