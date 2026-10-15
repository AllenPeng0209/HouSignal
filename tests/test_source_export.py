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
