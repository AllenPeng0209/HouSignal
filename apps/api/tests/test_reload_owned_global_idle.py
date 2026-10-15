"""Maintenance reloads cannot mistake an RLS-filtered count for global idle."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


class ProcessChecksReached(RuntimeError):
    pass


class Result:
    def __init__(self, value):
        self.value = value

    def mappings(self):
        return self

    def first(self):
        return self.value

    def scalar(self):
        return self.value


class Connection:
    def __init__(self, role, pending=0, dialect='postgresql', fail_role=False, read_only='on'):
        self.role, self.pending, self.fail_role = role, pending, fail_role
        self.dialect = SimpleNamespace(name=dialect)
        self.statements, self.read_only = [], read_only

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, statement):
        self.statements.append(str(statement))
        if str(statement) == 'SHOW transaction_read_only':
            return Result(self.read_only)
        if 'pg_roles' in str(statement):
            if self.fail_role:
                raise RuntimeError('raw connection diagnostic must not reach user')
            return Result(self.role)
        return Result(self.pending)


@pytest.fixture
def reload_script(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[3] / 'scripts/skill_acceptance/reload_owned.py'
    spec = importlib.util.spec_from_file_location('reload_owned_under_test', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    runtime = tmp_path / '.runtime'
    runtime.mkdir()
    (runtime / 'worker.pid').write_text('101')
    (runtime / 'api.pid').write_text('102')
    env = tmp_path / '.env'
    env.write_text('NATIVE_TEXT_BACKEND=dashscope\n')
    config = tmp_path / 'native.json'
    config.write_text('{"NATIVE_TEXT_BACKEND":"mlx"}')
    monkeypatch.setattr('sys.argv', ['reload_owned.py', '--config', str(config)])
    monkeypatch.setattr(module.subprocess, 'check_output', lambda *args, **kwargs: (_ for _ in ()).throw(ProcessChecksReached('process checks reached')))
    monkeypatch.setattr(module.os, 'kill', lambda *args, **kwargs: pytest.fail('tests must never signal a service'))
    return module, env


def connection(module, monkeypatch, role, pending=0, **kwargs):
    if role is not None:
        role = {'base_jobs_table': True, **role}
    db = Connection(role, pending, **kwargs)
    engine = SimpleNamespace(connect=lambda: db, dispose=lambda: None)
    monkeypatch.setattr(module, 'create_engine', lambda *args, **kwargs: engine)


GLOBAL_WORKER = {'rolsuper': False, 'rolbypassrls': False, 'can_select_jobs': True,
                 'base_jobs_table': True, 'globally_permissive_select': True,
                 'no_restrictive_select': True}


def fallback_connections(module, monkeypatch, pending=0, worker_url=None):
    primary = 'postgresql+psycopg://housignal_owner:fixture@127.0.0.1:55432/housignal'
    worker = worker_url or 'postgresql+psycopg://housignal_worker:fixture@127.0.0.1:55432/housignal'
    monkeypatch.setattr(module.settings, 'migration_database_url', primary)
    monkeypatch.setattr(module.settings, 'worker_database_url', worker)
    restricted = Connection({'rolsuper': False, 'rolbypassrls': False, 'can_select_jobs': True,
                             'base_jobs_table': True}, pending=0)
    global_worker = Connection(GLOBAL_WORKER, pending=pending)
    requested = []

    def engine(url, **kwargs):
        requested.append(str(url))
        db = restricted if str(url) == primary else global_worker
        return SimpleNamespace(connect=lambda: db, dispose=lambda: None)

    monkeypatch.setattr(module, 'create_engine', engine)
    return requested, restricted, global_worker


@pytest.mark.parametrize('role', [
    {'rolsuper': False, 'rolbypassrls': False, 'can_select_jobs': True},
    {'rolsuper': False, 'rolbypassrls': True, 'can_select_jobs': False},
    {'rolsuper': True, 'rolbypassrls': False, 'can_select_jobs': None},
    None,
])
def test_rls_filtered_or_unreadable_zero_cannot_authorize_reload(reload_script, monkeypatch, role):
    module, env = reload_script
    connection(module, monkeypatch, role, pending=0)
    with pytest.raises(RuntimeError, match='当前连接无法全局检查运行任务'):
        module.main()
    assert env.read_text() == 'NATIVE_TEXT_BACKEND=dashscope\n'


def test_unknown_role_visibility_fails_closed_without_raw_diagnostics(reload_script, monkeypatch):
    module, env = reload_script
    connection(module, monkeypatch, None, fail_role=True)
    with pytest.raises(RuntimeError, match='当前连接无法全局检查运行任务') as error:
        module.main()
    assert 'raw connection diagnostic' not in str(error.value)
    assert env.read_text() == 'NATIVE_TEXT_BACKEND=dashscope\n'


@pytest.mark.parametrize('role', [
    {'rolsuper': True, 'rolbypassrls': False, 'can_select_jobs': True},
    {'rolsuper': False, 'rolbypassrls': True, 'can_select_jobs': True},
])
def test_verified_global_idle_reaches_owned_process_validation(reload_script, monkeypatch, role):
    module, _ = reload_script
    connection(module, monkeypatch, role)
    with pytest.raises(ProcessChecksReached):
        module.main()


def test_verified_global_active_count_blocks_before_process_or_config_changes(reload_script, monkeypatch):
    module, env = reload_script
    connection(module, monkeypatch, {'rolsuper': False, 'rolbypassrls': True, 'can_select_jobs': True}, pending=1)
    with pytest.raises((RuntimeError, AssertionError), match='Active jobs must finish'):
        module.main()
    assert env.read_text() == 'NATIVE_TEXT_BACKEND=dashscope\n'


@pytest.mark.parametrize('pending', [None, -1, False])
def test_invalid_count_is_not_treated_as_global_zero(reload_script, monkeypatch, pending):
    module, _ = reload_script
    connection(module, monkeypatch, {'rolsuper': True, 'rolbypassrls': False, 'can_select_jobs': True}, pending=pending)
    with pytest.raises(RuntimeError, match='当前连接无法全局检查运行任务'):
        module.main()


def test_literal_global_select_policy_proves_idle_in_read_only_transaction(reload_script):
    module, _ = reload_script
    db = Connection(GLOBAL_WORKER)
    assert module.global_pending_jobs(db) == 0
    assert db.statements[0] == 'SET TRANSACTION READ ONLY'
    assert db.statements[1] == 'SHOW transaction_read_only'


@pytest.mark.parametrize('overrides', [
    {'base_jobs_table': False}, {'base_jobs_table': None},
    {'globally_permissive_select': False}, {'globally_permissive_select': None},
    {'no_restrictive_select': False}, {'no_restrictive_select': None},
    {'can_select_jobs': False},
])
def test_filtered_policy_or_view_cannot_prove_global_zero(reload_script, overrides):
    module, _ = reload_script
    db = Connection({**GLOBAL_WORKER, **overrides})
    with pytest.raises(RuntimeError, match='当前连接无法全局检查运行任务'):
        module.global_pending_jobs(db)


def test_unconfirmed_read_only_transaction_is_rejected(reload_script):
    module, _ = reload_script
    db = Connection({'rolsuper': True, 'rolbypassrls': False,
                     'can_select_jobs': True, 'base_jobs_table': True}, read_only='off')
    with pytest.raises(RuntimeError, match='当前连接无法全局检查运行任务'):
        module.global_pending_jobs(db)


def test_restricted_zero_uses_only_same_owned_database_global_worker_proof(reload_script, monkeypatch):
    module, _ = reload_script
    requested, restricted, worker = fallback_connections(module, monkeypatch)
    with pytest.raises(ProcessChecksReached):
        module.main()
    assert len(requested) == 2
    assert not any('count(*)' in statement for statement in restricted.statements)
    assert any('count(*)' in statement for statement in worker.statements)


@pytest.mark.parametrize('worker_url', [
    'postgresql+psycopg://housignal_worker:fixture@outside.invalid:55432/housignal',
    'postgresql+psycopg://housignal_worker:fixture@127.0.0.1:5432/housignal',
    'postgresql+psycopg://housignal_worker:fixture@127.0.0.1:55432/another',
    'postgresql+psycopg://postgres:fixture@127.0.0.1:55432/housignal',
    'postgresql+psycopg://housignal_worker:fixture@127.0.0.1:55432/housignal?host=outside.invalid',
    'postgresql+psycopg://housignal_worker:fixture@127.0.0.1:55432/housignal?host=/wrong/socket',
])
def test_external_or_overridden_worker_target_is_not_a_fallback(reload_script, monkeypatch, worker_url):
    module, env = reload_script
    requested, _, _ = fallback_connections(module, monkeypatch, worker_url=worker_url)
    with pytest.raises(RuntimeError, match='当前连接无法全局检查运行任务'):
        module.main()
    assert len(requested) == 1
    assert env.read_text() == 'NATIVE_TEXT_BACKEND=dashscope\n'


@pytest.mark.parametrize('primary_url', [
    'postgresql+psycopg://housignal_owner:fixture@outside.invalid:55432/housignal',
    'postgresql+psycopg://housignal_owner:fixture@127.0.0.1:5432/housignal',
    'postgresql+psycopg://housignal_owner:fixture@127.0.0.1:55432/another',
    'postgresql+psycopg://postgres:fixture@127.0.0.1:55432/housignal',
    'postgresql+psycopg://housignal_owner:fixture@127.0.0.1:55432/housignal?host=/wrong/socket',
])
def test_external_primary_connection_cannot_switch_to_project_worker(reload_script, monkeypatch, primary_url):
    module, env = reload_script
    fallback_connections(module, monkeypatch)
    monkeypatch.setattr(module.settings, 'migration_database_url', primary_url)
    requested = []
    restricted = Connection({'rolsuper': False, 'rolbypassrls': False, 'can_select_jobs': True,
                             'base_jobs_table': True})

    def engine(url, **kwargs):
        requested.append(str(url))
        return SimpleNamespace(connect=lambda: restricted, dispose=lambda: None)

    monkeypatch.setattr(module, 'create_engine', engine)
    with pytest.raises(RuntimeError, match='当前连接无法全局检查运行任务'):
        module.main()
    assert requested == [primary_url]
    assert env.read_text() == 'NATIVE_TEXT_BACKEND=dashscope\n'


def test_global_worker_active_jobs_block_config_changes_and_signals(reload_script, monkeypatch):
    module, env = reload_script
    fallback_connections(module, monkeypatch, pending=1)
    with pytest.raises(RuntimeError, match='Active jobs must finish'):
        module.main()
    assert env.read_text() == 'NATIVE_TEXT_BACKEND=dashscope\n'


def test_read_only_worker_check_adds_no_database_override_to_spawned_environment(reload_script, monkeypatch):
    module, _ = reload_script
    fallback_connections(module, monkeypatch)
    python = str(module.ROOT / '.venv/bin/python')
    monkeypatch.setattr(module.subprocess, 'check_output', lambda args, **kwargs:
                        python + (' -m app.worker' if args[2] == '101' else ' -m uvicorn app.main:app'))
    stopped, spawned = set(), []

    def signal_owned(pid, signal):
        if signal == module.signal.SIGTERM:
            stopped.add(pid)
        elif signal == 0 and pid in stopped:
            raise ProcessLookupError

    def spawn(command, **kwargs):
        spawned.append(kwargs['env'])
        return SimpleNamespace(pid=200 + len(spawned))

    monkeypatch.setattr(module.os, 'kill', signal_owned)
    monkeypatch.setattr(module.subprocess, 'Popen', spawn)
    response = SimpleNamespace(raise_for_status=lambda: None,
        json=lambda: {'paths': {'/api/projects/{pid}/operations/skills/{skill_id}/preflight': {}}})
    monkeypatch.setattr(module.httpx, 'get', lambda *args, **kwargs: response)
    before = dict(module.os.environ)
    module.main()
    assert len(spawned) == 2
    assert all(child == {**before, 'PYTHONPATH': str(module.ROOT / 'apps/api')} for child in spawned)
    assert dict(module.os.environ) == before
