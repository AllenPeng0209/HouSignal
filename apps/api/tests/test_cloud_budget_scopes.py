"""Local-model accounting cannot consume the separate cloud search/chat budget."""
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.agent_models import ChatMessage, ModelUsage
from app.config import settings
from app.job_models import Job
from test_discovery import discovery_client, discovery_http


def _usage(factory, cloud):
    with factory() as db:
        db.add_all([ModelUsage(id='local-usage', tenant_id='t', property_id='p', job_id='earlier-local',
            model='local:fixture', input_tokens=1500, output_tokens=0),
            ModelUsage(id='cloud-usage', tenant_id='t', property_id='p', job_id='earlier-cloud',
            model='qwen-plus', input_tokens=cloud, output_tokens=0)])
        db.commit()


@pytest.mark.parametrize('cloud_used', [50, 1000])
def test_web_search_only_enforces_cloud_usage_and_preserves_local_accounting(discovery_client, discovery_http, monkeypatch, cloud_used):
    from app import discovery
    _, factory, _, _ = discovery_client
    monkeypatch.setattr(settings, 'app_env', 'development')
    monkeypatch.setattr(settings, 'development_token_budget', 1000)
    _usage(factory, cloud_used)
    with factory() as db:
        job = Job(id='search', tenant_id='t', property_id='p', user_id='u', kind='discovery_search', payload={}, result={})
        db.add(job)
        db.commit()
        request = discovery.DiscoverySearch(query='住宅', platforms=['xiaohongshu'], days=30)
        if cloud_used == 1000:
            with pytest.raises(HTTPException) as error:
                discovery._search_sources(db, job, request)
            assert error.value.status_code == 429
            assert discovery_http['posted'] == []
        else:
            sources = discovery._search_sources(db, job, request)
            assert any('xiaohongshu.com/explore/' in source['url'] for source in sources)
            assert len(discovery_http['posted']) == 1
            usage = db.scalar(select(ModelUsage).where(ModelUsage.job_id == 'search'))
            assert (usage.input_tokens, usage.output_tokens) == (170, 50)
        assert db.get(ModelUsage, 'local-usage').input_tokens == 1500


@pytest.mark.parametrize('cloud_used', [50, 1000])
def test_chat_enqueue_only_enforces_cloud_usage_without_synchronous_provider_calls(discovery_client, monkeypatch, cloud_used):
    from app import agent
    client, factory, _, _ = discovery_client
    client.app.include_router(agent.router)
    monkeypatch.setattr(settings, 'app_env', 'development')
    monkeypatch.setattr(settings, 'development_token_budget', 1000)
    monkeypatch.setattr(agent.provider, 'stream_chat', lambda *args, **kwargs: pytest.fail('message admission cannot invoke provider'))
    _usage(factory, cloud_used)
    conversation = client.post('/api/projects/p/conversations', json={'title': '住宅研究'})
    assert conversation.status_code == 201, conversation.text
    response = client.post('/api/conversations/' + conversation.json()['id'] + '/messages', json={'text': '查看当前项目资料'})
    with factory() as db:
        if cloud_used == 1000:
            assert response.status_code == 429, response.text
            assert db.scalar(select(Job).where(Job.kind == 'chat')) is None
            assert db.scalar(select(ChatMessage)) is None
        else:
            assert response.status_code == 202, response.text
            job = db.get(Job, response.json()['job_id'])
            assert job.kind == 'chat' and job.status == 'queued'
            assistant = db.scalar(select(ChatMessage).where(ChatMessage.job_id == job.id, ChatMessage.role == 'assistant'))
            assert assistant.status == 'queued'
        assert db.get(ModelUsage, 'local-usage').input_tokens == 1500
