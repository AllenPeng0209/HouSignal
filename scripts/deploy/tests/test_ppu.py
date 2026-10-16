"""Exercise PPU startup validation and the rendered container network contract."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = ROOT / 'scripts/deploy/ppu-entrypoint.sh'
OVERLAY = ROOT / 'infra/deploy/compose.ppu.yaml'


class PpuEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(ENTRYPOINT.is_file(), 'PPU tunnel entrypoint is missing')
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.private = self.root / 'private-ssh'
        self.private.mkdir(mode=0o700)
        for name in ('config', 'id_ed25519', 'known_hosts'):
            path = self.private / name
            path.write_text('nonsecret test fixture\n')
            path.chmod(0o600)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        # SSH is the external boundary: record its command without any network.
        shim = self.bin / 'ssh'
        shim.write_text('#!' + sys.executable + '\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n')
        shim.chmod(0o700)
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                        PPU_SSH_DIR=str(self.private), PPU_SSH_TARGET='owned-runtime')

    def run_entrypoint(self):
        return subprocess.run(['sh', str(ENTRYPOINT)], env=self.env,
                              capture_output=True, text=True, timeout=5)

    def test_private_setup_starts_both_loopback_forwards_with_pinned_host_verification(self):
        result = self.run_entrypoint()
        self.assertEqual(result.returncode, 0, result.stderr)
        args = json.loads(result.stdout)
        forwards = [args[index + 1] for index, value in enumerate(args) if value == '-L']
        self.assertEqual(forwards, ['127.0.0.1:18891:127.0.0.1:8891',
                                    '127.0.0.1:18889:127.0.0.1:8879'])
        for option in ('StrictHostKeyChecking=yes', 'BatchMode=yes',
                       'ExitOnForwardFailure=yes', 'IdentitiesOnly=yes',
                       'ForwardAgent=no', 'GlobalKnownHostsFile=/dev/null',
                       'UserKnownHostsFile=' + str(self.private / 'known_hosts')):
            self.assertIn(option, args)
        self.assertEqual(args[args.index('-F') + 1], str(self.private / 'config'))
        self.assertEqual(args[args.index('-i') + 1], str(self.private / 'id_ed25519'))
        self.assertEqual(args[-1], 'owned-runtime')

    def test_world_readable_private_key_is_rejected_before_ssh(self):
        (self.private / 'id_ed25519').chmod(0o644)
        result = self.run_entrypoint()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertNotIn(str(self.private), result.stderr)

    def test_symlink_and_hardlink_credentials_are_rejected(self):
        key = self.private / 'id_ed25519'
        moved = self.root / 'identity'
        key.rename(moved)
        key.symlink_to(moved)
        self.assertNotEqual(self.run_entrypoint().returncode, 0)
        key.unlink()
        os.link(moved, key)
        self.assertNotEqual(self.run_entrypoint().returncode, 0)

    def test_public_directory_and_missing_hostkeys_are_rejected(self):
        self.private.chmod(0o755)
        self.assertNotEqual(self.run_entrypoint().returncode, 0)
        self.private.chmod(0o700)
        (self.private / 'known_hosts').unlink()
        self.assertNotEqual(self.run_entrypoint().returncode, 0)

    def test_target_cannot_add_ssh_options_or_shell_syntax(self):
        for target in ('-Fmalicious', 'runtime extra', 'runtime;touch marker', ''):
            with self.subTest(target=target):
                self.env['PPU_SSH_TARGET'] = target
                result = self.run_entrypoint()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')


@unittest.skipUnless(shutil.which('docker'), 'Docker Compose is required for config rendering')
class PpuComposeTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(OVERLAY.is_file(), 'PPU deployment overlay is missing')
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'infra/deploy').mkdir(parents=True)
        for relative in ('compose.yaml', 'infra/deploy/compose.production.yaml',
                         'infra/deploy/compose.ppu.yaml'):
            shutil.copyfile(ROOT / relative, self.root / relative)
        self.values = {
            'POSTGRES_PASSWORD': 'fixture-only-password',
            'DEPLOY_ORIGIN': 'https://studio.example.org',
            'PPU_RUNTIME_DIR': '/srv/louxiang/private-runtime',
            'PPU_SSH_DIR': '/srv/louxiang/private-ssh',
            'NATIVE_TEXT_BACKEND': 'resident_ppu',
            'NATIVE_VIDEO_BACKEND': 'lightx2v',
        }

    def render(self):
        (self.root / '.env').write_text(''.join(f'{key}={value}\n' for key, value in self.values.items()))
        env = {key: os.environ[key] for key in ('PATH', 'HOME') if key in os.environ}
        return subprocess.run(['docker', 'compose', '--env-file', '.env',
                               '-f', 'compose.yaml', '-f', 'infra/deploy/compose.production.yaml',
                               '-f', 'infra/deploy/compose.ppu.yaml', 'config', '--format', 'json'],
                              cwd=self.root, env=env, capture_output=True, text=True, timeout=15)

    def test_api_worker_and_tunnel_share_loopback_and_private_runtime(self):
        result = self.render()
        self.assertEqual(result.returncode, 0, result.stderr)
        services = json.loads(result.stdout)['services']
        self.assertEqual(services['worker']['network_mode'], 'service:api')
        self.assertEqual(services['ppu-tunnel']['network_mode'], 'service:api')
        for name in ('api', 'worker'):
            mount = next(item for item in services[name]['volumes'] if item['target'] == '/app/.runtime')
            self.assertEqual(mount['source'], '/srv/louxiang/private-runtime')
            self.assertFalse(mount.get('read_only', False))
            self.assertFalse(mount['bind'].get('create_host_path', False))
            self.assertEqual(services[name]['environment']['NATIVE_RESIDENT_TEXT_TOKEN_PATH'],
                             '/app/.runtime/native-qwen.auth')
            self.assertEqual(services[name]['environment']['NATIVE_TEXT_BACKEND'], 'resident_ppu')
            self.assertEqual(services[name]['environment']['NATIVE_VIDEO_BACKEND'], 'lightx2v')
        tunnel = services['ppu-tunnel']
        self.assertEqual(tunnel['image'], 'louxiang-tunnel:local')
        self.assertEqual(tunnel['user'], '10001:10001')
        self.assertTrue(tunnel['read_only'])
        self.assertNotIn('ports', tunnel)
        self.assertEqual(tunnel['volumes'][0]['target'], '/run/ppu-ssh')
        self.assertTrue(tunnel['volumes'][0]['read_only'])
        self.assertFalse(tunnel['volumes'][0]['bind'].get('create_host_path', False))

    def test_missing_private_host_paths_fail_before_startup(self):
        del self.values['PPU_SSH_DIR']
        self.assertNotEqual(self.render().returncode, 0)


if __name__ == '__main__':
    unittest.main()
