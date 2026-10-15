"""Research login is scoped separately from publication and returns no secrets."""
from dataclasses import replace
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_discovery import discovery_client

BASE = '/api/projects/p/operations/discovery/douyin-account'


def runtime_class():
    return importlib.import_module('app.douyin_research_runtime').DouyinResearchRuntime


def test_research_scope_is_distinct_project_private_and_env_has_no_credentials(tmp_path, monkeypatch):
    runtime = runtime_class()(root=tmp_path)
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'never-export-secret')
    monkeypatch.setenv('HTTP_PROXY', 'never-export-proxy')
    scope = runtime.scope('t', 'p', create=True)
    assert scope.name == 'research' and scope.parent.name == 'douyin'
    assert scope != runtime.scope('t', 'other')
    assert scope != runtime.scope('other', 'p')
    assert scope.stat().st_mode & 0o777 == 0o700
    assert runtime._env(scope)['EASEL_ROOT'] == str(scope)
    assert 'DASHSCOPE_API_KEY' not in runtime._env(scope)
    assert 'HTTP_PROXY' not in runtime._env(scope)
    command = runtime.command(scope, 'login')
    assert '--headed' in command and 'login' in command
    assert str(scope / 'profiles' / 'DouyinResearchProfile') in command
    assert not any('publish' in part or 'creator' in part or 'sms' in part for part in command)
    with pytest.raises(ValueError):
        runtime.command(scope, 'publish')


def test_scope_and_qr_reject_symlinks(tmp_path):
    runtime = runtime_class()(root=tmp_path)
    scope = runtime.scope('t', 'p', create=True)
    (scope / 'qr.png').symlink_to(tmp_path / 'foreign.png')
    assert runtime.qr_path('t', 'p') is None
    other = runtime.scope('t', 'other')
    other.parent.mkdir(parents=True)
    other.symlink_to(scope, target_is_directory=True)
    with pytest.raises(ValueError):
        runtime.scope('t', 'other')


def test_publisher_success_and_profile_never_authorize_research(tmp_path):
    runtime = runtime_class()(root=tmp_path)
    scope = runtime.scope('t', 'p', create=True)
    publisher = scope.parent
    (publisher / 'status.json').write_text(json.dumps({'state':'success', 'verified':True, 'loggedIn':True}))
    (publisher / 'profiles' / 'DouyinProfile').mkdir(parents=True)
    assert runtime.login_status('t', 'p')['loggedIn'] is False
    assert runtime.login_status('t', 'p')['verified'] is False


def test_success_requires_recent_verified_timestamp_and_qr_only_during_login(tmp_path, monkeypatch):
    runtime = runtime_class()(root=tmp_path)
    scope = runtime.scope('t', 'p', create=True)
    import app.douyin_research_runtime as module
    monkeypatch.setattr(module.time, 'time', lambda: 2000)
    runtime._write(scope / 'status.json', {'state':'success', 'ts':1990, 'loggedIn':True, 'verified':True})
    (scope / 'qr.png').write_bytes(b'\x89PNG\r\n\x1a\nold-qr')
    assert runtime.login_status('t', 'p')['verified'] is True
    assert runtime.qr_path('t', 'p') is None
    runtime._write(scope / 'status.json', {'state':'success', 'ts':1, 'loggedIn':True, 'verified':True})
    assert runtime.login_status('t', 'p')['verified'] is False


def test_session_lock_serializes_search_and_login(tmp_path):
    runtime = runtime_class()(root=tmp_path)
    other = runtime_class()(root=tmp_path)
    with runtime.session_lock('t', 'p'):
        with pytest.raises(RuntimeError):
            with other.session_lock('t', 'p'):
                pass


def test_disconnect_deletes_only_research_scope(tmp_path):
    runtime = runtime_class()(root=tmp_path)
    scope = runtime.scope('t', 'p', create=True)
    (scope / 'profiles' / 'DouyinResearchProfile').mkdir(parents=True)
    (scope / 'profiles' / 'DouyinResearchProfile' / 'cookie').write_text('private')
    publisher = scope.parent / 'profiles' / 'DouyinProfile'
    publisher.mkdir(parents=True)
    (publisher / 'cookie').write_text('publisher-private')
    runtime.disconnect('t', 'p')
    assert not (scope / 'profiles').exists()
    assert (publisher / 'cookie').read_text() == 'publisher-private'


def test_verify_during_official_login_only_reads_status_never_opens_another_browser(tmp_path, monkeypatch):
    runtime = runtime_class()(root=tmp_path)
    scope = runtime.scope('t', 'p', create=True)
    runtime._write(scope / 'status.json', {'state':'window_login'})
    monkeypatch.setattr(runtime, '_owned_process', lambda scope: 42)
    monkeypatch.setattr(runtime, 'readiness', lambda: {'python':True, 'browser':True})
    def forbidden(*args, **kwargs):
        raise AssertionError('must not launch another browser during human login')
    import app.douyin_research_runtime as module
    monkeypatch.setattr(module.subprocess, 'Popen', forbidden)
    assert runtime.verify('t', 'p')['state'] == 'window_login'


def test_login_child_retains_profile_lock_until_ended_and_first_profile_is_private(tmp_path, monkeypatch):
    runtime = runtime_class()(root=tmp_path, python=sys.executable)
    scope = runtime.scope('t', 'p')
    other = runtime_class()(root=tmp_path, python=sys.executable)
    monkeypatch.setattr(runtime, 'readiness', lambda: {'python':True, 'browser':True})
    monkeypatch.setattr(runtime._paths, '_process_stamp', lambda pid:'test-boundary')
    children = []
    real_popen = subprocess.Popen
    def external_boundary(command, **kwargs):
        profile = scope / 'profiles' / 'DouyinResearchProfile'
        assert profile.is_dir() and profile.stat().st_mode & 0o777 == 0o700
        assert command == runtime.command(scope, 'login')
        # Replace the browser with a real finite child, retaining the inherited
        # descriptor so cross-process advisory-lock lifetime is actually tested.
        child = real_popen([sys.executable, '-c', 'import time; time.sleep(10)'], **kwargs)
        children.append(child)
        return child
    import app.douyin_research_runtime as module
    monkeypatch.setattr(module.subprocess, 'Popen', external_boundary)
    monkeypatch.setattr(runtime, '_owned_process', lambda path:
        children[-1].pid if children and children[-1].poll() is None else None)
    try:
        assert runtime.start_login('t', 'p')['state'] == 'starting'
        with pytest.raises(RuntimeError):
            with other.session_lock('t', 'p'):
                pass
        assert runtime.disconnect('t', 'p')['state'] == 'unknown'
        assert children[0].poll() is not None
        with other.session_lock('t', 'p'):
            pass
    finally:
        for child in children:
            if child.poll() is None:
                runtime._stop_child(child)


@pytest.fixture
def research_client(discovery_client, tmp_path, monkeypatch):
    client, factory, current, ctx = discovery_client
    module = importlib.import_module('app.douyin_research_accounts')
    class Boundary:
        def login_status(self, *args):
            return {'state':'qr_ready', 'message':'扫码登录', 'qrReady':True, 'qrTs':1,
                    'loggedIn':False, 'verified':False, 'ready':True, 'scope':'research',
                    'cookies':'never-return-private', 'path':'/private/profile'}
        start_login = verify = disconnect = login_status
        def qr_path(self, *args):
            path = tmp_path / 'qr.png'
            path.write_bytes(b'\x89PNG\r\n\x1a\nqr')
            return path
    monkeypatch.setattr(module, 'research_runtime', Boundary())
    return SimpleNamespace(client=client, current=current, ctx=ctx)


def test_research_routes_are_real_scoped_and_allowlisted(research_client):
    env = research_client
    for action in ['login', 'verify', 'disconnect']:
        result = env.client.post(BASE + '/' + action)
        assert result.status_code == 200, result.text
        assert result.json()['scope'] == 'research'
        assert 'never-return-private' not in result.text and '/private' not in result.text
    assert env.client.get(BASE + '/status').status_code == 200
    assert env.client.get(BASE).status_code == 200
    result = env.client.get(BASE + '/qr')
    assert result.status_code == 200 and result.content.startswith(b'\x89PNG')
    assert result.headers['cache-control'] == 'no-store'


def test_research_login_qr_verify_disconnect_enforce_content_and_project_scope(research_client):
    env = research_client
    env.current['ctx'] = replace(env.ctx, role='viewer')
    for action in ['login', 'verify', 'disconnect']:
        assert env.client.post(BASE + '/' + action).status_code == 403
    assert env.client.get(BASE + '/qr').status_code == 403
    env.current['ctx'] = replace(env.ctx, project_ids=['p2'])
    assert env.client.get(BASE + '/status').status_code == 404
    assert env.client.post(BASE + '/login').status_code == 404

