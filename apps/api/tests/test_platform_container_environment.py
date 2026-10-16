"""Headed platform children use only the container's local X display."""
import json
import os
import subprocess
import sys

import pytest

from app.platform_runtime import PlatformRuntime


def child_environment(tmp_path, monkeypatch, display, authority=None):
    monkeypatch.setenv('DISPLAY', display)
    monkeypatch.setenv('HTTP_PROXY', 'http://untrusted.invalid:8080')
    monkeypatch.setenv('HOUSIGNAL_BROWSER_ENV_UNTRUSTED', 'fixture')
    if authority is None:
        monkeypatch.delenv('XAUTHORITY', raising=False)
    else:
        monkeypatch.setenv('XAUTHORITY', str(authority))
    runtime = PlatformRuntime(root=tmp_path, python=sys.executable)
    scope = runtime._scope('tenant', 'project', 'douyin', create=True)
    command = (
        "import json, os; "
        "print(json.dumps({key: os.environ.get(key) for key in "
        "('DISPLAY', 'XAUTHORITY', 'HTTP_PROXY', 'HOUSIGNAL_BROWSER_ENV_UNTRUSTED')}))"
    )
    result = subprocess.run(
        [sys.executable, '-c', command], cwd=scope, env=runtime._env(scope),
        capture_output=True, text=True, timeout=10, check=True,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize('display', [':0', ':99', ':99.0'])
def test_headed_child_inherits_local_x_display_without_unrelated_environment(tmp_path, monkeypatch, display):
    assert child_environment(tmp_path, monkeypatch, display) == {
        'DISPLAY': display,
        'XAUTHORITY': None,
        'HTTP_PROXY': None,
        'HOUSIGNAL_BROWSER_ENV_UNTRUSTED': None,
    }


@pytest.mark.parametrize('display', [
    '', 'host.example:99', '127.0.0.1:99', ':99;command', ':99\n', ':99/path',
])
def test_child_rejects_remote_or_malformed_x_display(tmp_path, monkeypatch, display):
    assert child_environment(tmp_path, monkeypatch, display)['DISPLAY'] is None


def test_headed_child_inherits_owned_private_xauthority_path(tmp_path, monkeypatch):
    authority = tmp_path / 'Xauthority'
    authority.touch(mode=0o600)
    assert child_environment(tmp_path, monkeypatch, ':99', authority)['XAUTHORITY'] == str(authority)


@pytest.mark.parametrize('kind', ['public_mode', 'symlink', 'linked_parent', 'hardlink', 'directory', 'missing', 'relative'])
def test_child_omits_unsafe_xauthority_paths(tmp_path, monkeypatch, kind):
    authority = tmp_path / 'Xauthority'
    authority.touch(mode=0o600)
    if kind == 'public_mode':
        authority.chmod(0o644)
    elif kind == 'symlink':
        link = tmp_path / 'linked-authority'
        link.symlink_to(authority)
        authority = link
    elif kind == 'linked_parent':
        link = tmp_path / 'linked-parent'
        link.symlink_to(tmp_path, target_is_directory=True)
        authority = link / 'Xauthority'
    elif kind == 'hardlink':
        (tmp_path / 'another-authority-link').hardlink_to(authority)
    elif kind == 'directory':
        authority = tmp_path
    elif kind == 'missing':
        authority = tmp_path / 'missing'
    elif kind == 'relative':
        authority = 'Xauthority'
    assert child_environment(tmp_path, monkeypatch, ':99', authority)['XAUTHORITY'] is None


def test_child_omits_xauthority_owned_by_a_different_uid(tmp_path, monkeypatch):
    authority = tmp_path / 'Xauthority'
    authority.touch(mode=0o600)
    # Keep real filesystem metadata and the real child; only simulate the
    # receiving process identity, without needing root/chown in the test suite.
    monkeypatch.setattr(os, 'geteuid', lambda: os.getuid() + 1)
    assert child_environment(tmp_path, monkeypatch, ':99', authority)['XAUTHORITY'] is None


def test_child_never_passes_xauthority_for_a_remote_display(tmp_path, monkeypatch):
    authority = tmp_path / 'Xauthority'
    authority.touch(mode=0o600)
    assert child_environment(tmp_path, monkeypatch, 'remote.example:99', authority)['XAUTHORITY'] is None
