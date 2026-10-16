"""Black-box checks for the public source archive's privacy boundary."""
import hashlib
import json
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import zipfile


SOURCE = Path(__file__).resolve().parents[1] / 'scripts/package_source.py'


def export_project(tmp_path):
    root = tmp_path / 'project'
    (root / 'scripts').mkdir(parents=True)
    shutil.copyfile(SOURCE, root / 'scripts/package_source.py')
    return root


def archive_names(root):
    subprocess.run([sys.executable, str(root / 'scripts/package_source.py')],
                   check=True, capture_output=True, text=True)
    with zipfile.ZipFile(root / 'exports/楼响_HouSignal_正式应用源码.zip') as archive:
        return set(archive.namelist())


def test_source_export_excludes_nested_private_runtime_and_model_files(tmp_path):
    root = export_project(tmp_path)
    private_paths = [
        'apps/api/.private/secret.txt', 'apps/api/runtime/local.db',
        'apps/api/modelweights/model.safetensors', 'apps/api/app/credential.pem',
        'apps/web/build/private.txt', 'docs/.env.production',
        'vendor/example/data/customer.json',
    ]
    for relative in private_paths:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('private-fixture-data')
    (root / '.env.example').write_text('API_KEY=')
    names = archive_names(root)
    assert not set(private_paths) & names
    assert '.env.example' in names


def test_source_export_does_not_follow_an_external_file_symlink(tmp_path):
    root = export_project(tmp_path)
    external = tmp_path / 'private.txt'
    external.write_text('external-private-fixture')
    (root / 'docs').mkdir()
    (root / 'docs/leaked.txt').symlink_to(external)
    assert 'docs/leaked.txt' not in archive_names(root)


def test_source_export_uses_one_snapshot_for_manifest_and_member(tmp_path, monkeypatch):
    root = export_project(tmp_path)
    fixture = root / 'docs/guide.md'
    fixture.parent.mkdir()
    fixture.write_bytes(b'first-source-snapshot')
    original = Path.read_bytes

    def mutate_after_read(path):
        raw = original(path)
        if path == fixture:
            path.write_bytes(b'newer-source-snapshot')
        return raw

    monkeypatch.setattr(Path, 'read_bytes', mutate_after_read)
    runpy.run_path(str(root / 'scripts/package_source.py'), run_name='__main__')
    with zipfile.ZipFile(root / 'exports/楼响_HouSignal_正式应用源码.zip') as archive:
        manifest = json.loads(archive.read('source-manifest.json'))
        row = next(item for item in manifest['files'] if item['path'] == 'docs/guide.md')
        assert row['sha256'] == hashlib.sha256(archive.read('docs/guide.md')).hexdigest()


def test_source_export_rebuilds_external_agent_packages_without_local_dist_or_private_state(tmp_path):
    """Dropping hidden plugin descriptors or integration sources breaks a fresh ZIP installation."""
    root = export_project(tmp_path)
    repository = SOURCE.parents[1]
    shutil.copytree(repository / 'integrations/louxiang-agent', root / 'integrations/louxiang-agent',
                    ignore=shutil.ignore_patterns('dist', '__pycache__'))
    marketplace = root / '.agents/plugins/marketplace.json'
    marketplace.parent.mkdir(parents=True)
    shutil.copyfile(repository / '.agents/plugins/marketplace.json', marketplace)
    private_paths = [
        'integrations/louxiang-agent/dist/local-build.pyz',
        'integrations/louxiang-agent/.runtime/credentials.json',
        'integrations/louxiang-agent/plugin/.codex-plugin/private-credentials.json',
        'integrations/louxiang-agent/plugin/.private/state.json',
        '.agents/private-credentials.json', '.runtime/browser-cookies.json',
        '.env', 'productions/private-project/recording.mp4',
    ]
    for name in private_paths:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('private-fixture-only')
    names = archive_names(root)
    required = {
        'integrations/louxiang-agent/build.py',
        'integrations/louxiang-agent/src/louxiang_agent/cli.py',
        'integrations/louxiang-agent/README.md',
        'integrations/louxiang-agent/plugin/plugin.json',
        'integrations/louxiang-agent/plugin/mcp.json',
        'integrations/louxiang-agent/plugin/.mcp.json',
        'integrations/louxiang-agent/plugin/.codex-plugin/plugin.json',
        'integrations/louxiang-agent/plugin/skills/louxiang-content/SKILL.md',
        '.agents/plugins/marketplace.json',
    }
    assert required <= names
    assert not set(private_paths) & names
    assert not any(name.startswith(('productions/', '.runtime/')) or '/dist/' in name for name in names)
    checkout = tmp_path / 'unzipped'
    with zipfile.ZipFile(root / 'exports/楼响_HouSignal_正式应用源码.zip') as archive:
        archive.extractall(checkout)
    subprocess.run([sys.executable, str(checkout / 'integrations/louxiang-agent/build.py')],
                   cwd=checkout, check=True, capture_output=True, text=True)
    distribution = checkout / 'integrations/louxiang-agent/dist'
    manifest = json.loads((distribution / 'manifest.json').read_text())
    for name, record in manifest.items():
        assert record['sha256'] == hashlib.sha256((distribution / name).read_bytes()).hexdigest()
    portable = subprocess.run([sys.executable, str(distribution / 'louxiang.pyz'), '--help'],
                              check=True, capture_output=True, text=True)
    assert 'login' in portable.stdout and 'call' in portable.stdout
    with zipfile.ZipFile(distribution / 'louxiang-plugin.zip') as plugin:
        assert 'louxiang-tools/plugin/.codex-plugin/plugin.json' in plugin.namelist()
        assert 'louxiang-tools/.agents/plugins/marketplace.json' in plugin.namelist()
