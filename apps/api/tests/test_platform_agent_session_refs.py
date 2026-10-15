"""Platform-derived assistant answers must expire when the real account changes."""
from types import SimpleNamespace

import pytest

from test_agent import agent_data


@pytest.fixture
def platform_sources(agent_data, monkeypatch):
    from app.platform_runtime import runtime
    db, ctx = agent_data
    states = {platform: {'session_id': 'a' * 64, 'loggedIn': True,
        'verified': True, 'state': 'success', 'name': '住宅运营', 'avatar': ''}
        for platform in ('douyin', 'xiaohongshu')}
    def status(tenant, pid, platform):
        assert (tenant, pid) == ('t1', 'p1')
        return dict(states.get(platform, {'session_id': '', 'loggedIn': False,
            'verified': False, 'state': 'unknown', 'name': '', 'avatar': ''}))
    monkeypatch.setattr(runtime, 'login_status', status)
    monkeypatch.setattr(runtime, 'list_accounts', lambda tenant, pid: [
        {'platform': platform, 'supported': True, 'ready': True,
         'identity_name': value['name'], **value} for platform, value in states.items()])
    yield SimpleNamespace(db=db, ctx=ctx, states=states)


def analytics_job(env):
    from app.jobs import enqueue_job
    job = enqueue_job(env.db, env.ctx, 'p1', 'platform_analytics',
        {'platform': 'douyin', 'session_id': 'a' * 64})
    job.status = 'succeeded'
    job.result = {'platform_analytics': {'platform': 'douyin', 'session_id': 'a' * 64,
        'account': {'session_id': 'a' * 64, 'name': '住宅运营'},
        'status': 'success', 'metrics': {'followers': 12}, 'items': [],
        'period_metrics': [], 'observed_at': '2026-10-14T00:00:00+00:00'}}
    env.db.flush()
    return job


@pytest.mark.parametrize('changed', [{'session_id': 'b' * 64}, {'loggedIn': False}])
def test_account_analytics_answer_cannot_finish_after_account_switch_or_logout(platform_sources, changed):
    from app import agent, provider
    env = platform_sources
    analytics_job(env)
    result = agent.execute_tool(env.db, env.ctx, 'p1', 'get_platform_analytics', {'platform': 'douyin'})
    assert result['data']['accounts'][0]['metrics']['followers'] == 12
    refs = {}
    agent._register_sources(result, refs)
    job = SimpleNamespace(user_id='u1', tenant_id='t1', property_id='p1')
    agent._check_live_sources(env.db, job, refs)
    env.states['douyin'].update(changed)
    with pytest.raises(provider.ProviderError):
        agent._check_live_sources(env.db, job, refs)


def test_sources_for_two_platforms_do_not_overwrite_the_first_session_fence(platform_sources, monkeypatch):
    from app import agent, provider
    from app.platform_accounts import run_agent_tool
    from app.platform_runtime import runtime
    env = platform_sources
    analytics_job(env)
    refs = {}
    agent._register_sources(agent.execute_tool(env.db, env.ctx, 'p1',
        'get_platform_analytics', {'platform': 'douyin'}), refs)
    monkeypatch.setattr(runtime, 'whoami', lambda tenant, pid, platform: dict(env.states[platform]))
    second = run_agent_tool(env.db, env.ctx, 'p1', 'check_platform_account', {'platform': 'xiaohongshu'})
    agent._register_sources(second, refs)
    env.states['douyin']['session_id'] = 'b' * 64
    with pytest.raises(provider.ProviderError):
        agent._check_live_sources(env.db, SimpleNamespace(user_id='u1', tenant_id='t1', property_id='p1'), refs)


def test_task_result_data_binds_the_original_analytics_account(platform_sources):
    from app import agent
    env = platform_sources
    job = analytics_job(env)
    result = agent.execute_tool(env.db, env.ctx, 'p1', 'get_operation_task', {'job_id': job.id})
    assert result['data']['result']['platform_analytics']['metrics']['followers'] == 12
    refs = {}
    agent._register_sources(result, refs)
    env.states['douyin']['session_id'] = 'b' * 64
    assert all(agent._visible_source(env.db, env.ctx, 'p1', source) for source in refs.values())
    assert not all(agent._visible_source(env.db, env.ctx, 'p1', source,
        check_revision=True) for source in refs.values())


def test_missing_snapshot_changes_when_a_previously_logged_out_account_connects(platform_sources):
    from app import agent, provider
    env = platform_sources
    env.states['douyin'].update(loggedIn=False)
    result = agent.execute_tool(env.db, env.ctx, 'p1', 'get_platform_analytics', {'platform': 'douyin'})
    assert result['data']['accounts'][0]['metrics']['followers'] is None
    refs = {}
    agent._register_sources(result, refs)
    env.states['douyin'].update(loggedIn=True)
    with pytest.raises(provider.ProviderError):
        agent._check_live_sources(env.db, SimpleNamespace(user_id='u1', tenant_id='t1', property_id='p1'), refs)


def test_source_merge_keeps_conflicting_session_constraints_instead_of_weakening_them(platform_sources):
    from app import agent
    env = platform_sources
    refs = {}
    for sid in ('a' * 64, 'b' * 64):
        agent._register_sources({'sources': [{'kind': 'page', 'id': 'p1', 'page': 'accounts',
            'label': '运营数据', 'platform_sessions': [{'platform': 'douyin',
                'session_id': sid, 'loggedIn': True, 'scope': 'operations'}]}]}, refs)
    env.states['douyin']['session_id'] = 'b' * 64
    assert not all(agent._visible_source(env.db, env.ctx, 'p1', source,
        check_revision=True) for source in refs.values())


def test_an_ordinary_account_page_reference_cannot_remove_a_platform_fence(platform_sources):
    from app import agent
    env = platform_sources
    analytics_job(env)
    refs = {}
    agent._register_sources(agent.execute_tool(env.db, env.ctx, 'p1',
        'get_platform_analytics', {'platform': 'douyin'}), refs)
    agent._register_sources({'sources': [{'kind': 'page', 'id': 'p1',
        'page': 'accounts', 'label': '普通账号入口'}]}, refs)
    env.states['douyin']['session_id'] = 'b' * 64
    assert not all(agent._visible_source(env.db, env.ctx, 'p1', source,
        check_revision=True) for source in refs.values())


def test_old_account_answer_is_excluded_from_the_next_model_history(platform_sources):
    from app import agent
    from app.agent_models import ChatMessage, Conversation, ToolCall
    from test_agent import _queued_chat
    env = platform_sources
    analytics_job(env)
    previous = _queued_chat(env.db, '抖音粉丝数据')
    result = agent.execute_tool(env.db, env.ctx, 'p1', 'get_platform_analytics', {'platform': 'douyin'})
    refs = {}
    sources = agent._register_sources(result, refs)
    env.db.add(ToolCall(tenant_id='t1', property_id='p1',
        conversation_id=previous.payload['conversation_id'], job_id=previous.id,
        name='get_platform_analytics', arguments={'platform': 'douyin'}, result_refs=sources))
    env.db.add(ChatMessage(tenant_id='t1', property_id='p1', conversation_id=previous.payload['conversation_id'],
        job_id=previous.id, role='assistant', content='旧账号粉丝十二人', status='completed', citations=sources))
    env.db.commit()
    current = _queued_chat(env.db, '现在的住宅内容怎么策划？', previous.payload['conversation_id'])
    conversation = env.db.get(Conversation, current.payload['conversation_id'])
    message = env.db.get(ChatMessage, current.payload['user_message_id'])
    assert '旧账号粉丝十二人' in str(agent._history(env.db, env.ctx, conversation, message))
    env.states['douyin']['session_id'] = 'b' * 64
    assert '旧账号粉丝十二人' not in str(agent._history(env.db, env.ctx, conversation, message))


@pytest.mark.parametrize('tool,logged_in', [('start_platform_login', False), ('check_platform_account', True)])
def test_first_account_connection_can_follow_an_unbound_metadata_read(platform_sources, monkeypatch, tool, logged_in):
    from app import agent, platform_accounts
    from app.platform_runtime import runtime
    env = platform_sources
    legacy = tool == 'check_platform_account'
    env.states['douyin'].update(session_id=None, loggedIn=legacy, verified=False,
        state='success' if legacy else 'unknown')
    monkeypatch.setattr(platform_accounts, 'credentials_metadata', lambda tenant, pid: {'configured': False})
    refs = {}
    agent._register_sources(agent.execute_tool(env.db, env.ctx, 'p1', 'get_platform_accounts', {}), refs)
    def connect(tenant, pid, platform):
        assert (tenant, pid, platform) == ('t1', 'p1', 'douyin')
        env.states['douyin'].update(session_id='b' * 64, loggedIn=logged_in,
            verified=logged_in, state='success' if logged_in else 'starting')
        return dict(env.states['douyin'])
    monkeypatch.setattr(runtime, 'start_login' if tool == 'start_platform_login' else 'whoami', connect)
    agent._register_sources(agent.execute_tool(env.db, env.ctx, 'p1', tool, {'platform': 'douyin'}), refs)
    job = SimpleNamespace(user_id='u1', tenant_id='t1', property_id='p1')
    agent._check_live_sources(env.db, job, refs)
    # Permitting this explicit first connection must not discard the other
    # account read in the same call, whose generation still remains bound.
    env.states['xiaohongshu']['session_id'] = 'c' * 64
    with pytest.raises(agent.provider.ProviderError):
        agent._check_live_sources(env.db, job, refs)


def test_external_first_connection_without_a_new_tool_read_still_invalidates_the_answer(platform_sources, monkeypatch):
    from app import agent, platform_accounts, provider
    env = platform_sources
    env.states['douyin'].update(session_id=None, loggedIn=False, verified=False, state='unknown')
    monkeypatch.setattr(platform_accounts, 'credentials_metadata', lambda tenant, pid: {'configured': False})
    refs = {}
    agent._register_sources(agent.execute_tool(env.db, env.ctx, 'p1', 'get_platform_accounts', {}), refs)
    env.states['douyin'].update(session_id='b' * 64)
    with pytest.raises(provider.ProviderError):
        agent._check_live_sources(env.db, SimpleNamespace(user_id='u1', tenant_id='t1', property_id='p1'), refs)
