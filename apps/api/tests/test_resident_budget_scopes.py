"""Resident PPU and MLX share compute allowance with distinct usage identities.

Only external provider requests are replaced. Real SQL accounting, generation
validation and project writes exercise the same paths used by native workers.
"""
from datetime import datetime, timedelta, timezone
import json

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app import provider
from app.agent_models import ChatMessage, ModelUsage
from app.business import handle_generate
from app.business_models import ContentVersion
from app.config import Settings, settings
from app.content_skills import research, text
from app.job_models import Job
from app.native_skills import _model_budget
from test_business import scenario
from test_discovery import discovery_client, discovery_http
from test_generation_duration import draft, generation_job
from test_native_skills import native_client
from test_native_text_contracts import CAROUSEL_INPUT, carousel
from test_operations_skills import execute


def _receipt(db, identifier, model, tokens, *, tenant_id='tenant', property_id='p', created_at=None):
    values = {'id': identifier, 'tenant_id': tenant_id, 'property_id': property_id,
              'job_id': 'earlier-' + identifier, 'model': model,
              'input_tokens': tokens, 'output_tokens': 0}
    if created_at is not None:
        values['created_at'] = created_at
    db.add(ModelUsage(**values))
    db.commit()


def _budgets(monkeypatch, backend, local=1_000_000):
    monkeypatch.setattr(settings, 'native_text_backend', backend)
    monkeypatch.setattr(settings, 'app_env', 'development')
    monkeypatch.setattr(settings, 'development_token_budget', 200_000)
    monkeypatch.setattr(settings, 'native_local_text_daily_token_budget', local)


def _generation_response(messages, **kwargs):
    return {'model': 'ppu:Qwen3-VL-32B@resident-test-revision',
            'choices': [{'message': {'role': 'assistant', 'content': json.dumps(draft(30), ensure_ascii=False)},
                         'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 40, 'completion_tokens': 60}}


@pytest.mark.parametrize('backend', ['resident_ppu', 'mlx'])
def test_both_compute_backends_share_team_daily_local_and_ppu_receipts(scenario, monkeypatch, backend):
    # A resident/MLX-only or project-only SQL scope would incorrectly admit this request.
    db, ctx, *_ = scenario
    _budgets(monkeypatch, backend)
    _receipt(db, 'mlx-today', 'local:mlx:Qwen3-4B', 600_000)
    _receipt(db, 'resident-today', 'ppu:Qwen3-VL-32B@resident-test-revision', 399_900, property_id='another-project')
    _receipt(db, 'cloud-today', 'qwen-plus', 2_000_000)
    _receipt(db, 'other-team', 'ppu:Qwen3-VL-32B@resident-test-revision', 2_000_000, tenant_id='other')
    _receipt(db, 'yesterday', 'local:mlx:Qwen3-4B', 2_000_000,
             created_at=datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(seconds=1))

    text.check_budget(db, ctx, additional_tokens=99, local_generation=True)
    with pytest.raises(HTTPException) as error:
        text.check_budget(db, ctx, additional_tokens=100, local_generation=True)
    assert error.value.status_code == 429


@pytest.mark.parametrize('backend', ['resident_ppu', 'mlx'])
def test_default_local_allowance_permits_work_without_a_daily_quota(scenario, monkeypatch, backend):
    # A finite default would prevent longer local work after historical usage.
    monkeypatch.delenv('NATIVE_LOCAL_TEXT_DAILY_TOKEN_BUDGET', raising=False)
    defaults = Settings(_env_file=None)
    db, ctx, *_ = scenario
    _budgets(monkeypatch, backend, local=defaults.native_local_text_daily_token_budget)
    _receipt(db, 'already-local', 'ppu:Qwen3-VL-32B@resident-test-revision', 250_000)
    _receipt(db, 'already-cloud', 'qwen-plus', 200_000)

    _model_budget(db, ctx, local_generation=True)
    text.check_budget(db, ctx, additional_tokens=20_000_000, local_generation=True)


def test_resident_generation_persists_ppu_receipt_when_cloud_allowance_is_exhausted(scenario, monkeypatch):
    # Treating resident generation as cloud would block before its valid project write.
    db, *_ = scenario
    _budgets(monkeypatch, 'resident_ppu')
    _receipt(db, 'cloud-exhausted', 'qwen-plus', 200_000)
    _receipt(db, 'mlx-used', 'local:mlx:Qwen3-4B', 600_000)
    monkeypatch.setattr(provider, 'chat', _generation_response)
    job = generation_job()

    result = handle_generate(db, job)
    content = db.get(ContentVersion, result['content_version_id'])
    assert content.script == '从420平方米的实拍空间观察日常居住体验。'
    usage = db.scalar(select(ModelUsage).where(ModelUsage.job_id == job.id))
    assert (usage.model, usage.input_tokens, usage.output_tokens) == ('ppu:Qwen3-VL-32B@resident-test-revision', 40, 60)
    assert db.get(ModelUsage, 'cloud-exhausted').input_tokens == 200_000


def test_resident_generation_is_rejected_when_shared_local_allowance_is_exhausted(scenario, monkeypatch):
    # Missing resident detection would incorrectly use the still-empty cloud budget.
    db, *_ = scenario
    _budgets(monkeypatch, 'resident_ppu')
    _receipt(db, 'mlx-used', 'local:mlx:Qwen3-4B', 600_000)
    _receipt(db, 'resident-used', 'ppu:Qwen3-VL-32B@resident-test-revision', 400_000)
    monkeypatch.setattr(provider, 'chat', lambda *args, **kwargs: pytest.fail('exhausted local work cannot invoke a provider'))

    with pytest.raises(HTTPException) as error:
        handle_generate(db, generation_job())
    assert error.value.status_code == 429
    assert db.scalar(select(ContentVersion).where(ContentVersion.series_id == 'duration-generation')) is None


def test_native_text_worker_uses_compute_budget_and_persists_distinct_ppu_usage(native_client, monkeypatch):
    # Native worker admission must not accidentally switch the text task to cloud.
    _, factory, _, _, _ = native_client
    _budgets(monkeypatch, 'resident_ppu')
    # Local runtime availability is an external dependency; contracts and quality
    # gates remain real and use the existing validated per-page acceptance case.
    monkeypatch.setattr(text, 'readiness', lambda skill_name: {
        'status': 'ready', 'reason': '测试 runtime 可用', 'execution_engine': 'housignal_text'})
    calls = []
    def model_response(*args, **kwargs):
        calls.append(kwargs)
        return {'model': 'ppu:Qwen3-VL-32B@resident-test-revision',
                'choices': [{'message': {'role': 'assistant', 'content': json.dumps({'issues': []})
                            if kwargs.get('response_format') == {'type': 'json_object'} else carousel()}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 170, 'completion_tokens': 250}}
    monkeypatch.setattr(provider, 'chat', model_response)
    job = Job(id='resident-native-task', user_id='u', tenant_id='t', property_id='p',
              kind='content_skill', payload={'skill_id': 'native:skill-carousel-planner',
              'skill_name': 'skill-carousel-planner', 'input': CAROUSEL_INPUT, 'parameters': {}})
    with factory() as db:
        _receipt(db, 'cloud-exhausted', 'qwen-plus', 200_000, tenant_id='t')
        _receipt(db, 'mlx-used', 'local:mlx:Qwen3-4B', 600_000, tenant_id='t')
        db.add(job)
        db.commit()
    completed, job = execute(factory, 'resident-native-task')
    assert completed and job.status == 'succeeded', job.error
    result = job.result
    assert result['response'] == carousel()
    assert result['execution_engine'] == 'housignal_native'
    assert result['native_data']['text_metrics']['characters_without_whitespace'] > 60
    # A separate connection observes the worker's durable actual-call accounting.
    with factory() as accounting:
        usage = accounting.scalars(select(ModelUsage).where(ModelUsage.job_id == 'resident-native-task')).all()
        assert len(usage) == 2 and len({row.id for row in usage}) == 2
        assert all((row.model, row.input_tokens, row.output_tokens) == ('ppu:Qwen3-VL-32B@resident-test-revision', 170, 250) for row in usage)
        assert accounting.get(ModelUsage, 'cloud-exhausted').input_tokens == 200_000
        assert accounting.get(ModelUsage, 'mlx-used').input_tokens == 600_000
    assert len(calls) == 2 and calls[1]['response_format'] == {'type': 'json_object'}


@pytest.mark.parametrize('backend', ['resident_ppu', 'mlx'])
def test_explicit_cloud_budget_does_not_borrow_available_local_allowance(scenario, monkeypatch, backend):
    # An explicit cloud/multimodal call must still stop at the cloud allowance.
    db, ctx, *_ = scenario
    _budgets(monkeypatch, backend)
    _receipt(db, 'local-used', 'ppu:Qwen3-VL-32B@resident-test-revision', 100)
    _receipt(db, 'cloud-used', 'qwen-plus', 200_000)

    _model_budget(db, ctx, local_generation=True)
    with pytest.raises(HTTPException) as error:
        _model_budget(db, ctx, local_generation=False)
    assert error.value.status_code == 429


@pytest.mark.parametrize('cloud_used', [50, 200_000])
def test_native_search_keeps_cloud_budget_when_resident_backend_is_selected(scenario, monkeypatch, cloud_used):
    # Search stays cloud-only even if all local work has exhausted its own allowance.
    db, *_ = scenario
    _budgets(monkeypatch, 'resident_ppu')
    _receipt(db, 'local-used', 'ppu:Qwen3-VL-32B@resident-test-revision', 1_000_000)
    _receipt(db, 'cloud-used', 'qwen-plus', cloud_used)
    monkeypatch.setattr(settings, 'dashscope_api_key', 'synthetic-test-key')
    monkeypatch.setattr(settings, 'dashscope_model', 'qwen-plus')
    monkeypatch.setattr(settings, 'dashscope_base_url', 'https://dashscope.aliyuncs.com/compatible-mode/v1')
    job = generation_job()
    db.add(job)
    db.commit()
    def boundary(request):
        return httpx.Response(200, json={'output': {'search_info': {'search_results': [
            {'title': '公开研究来源', 'url': 'https://example.org/research'}]}},
            'usage': {'input_tokens': 170, 'output_tokens': 50, 'plugins': {'search': {'count': 1}}}})
    monkeypatch.setattr(research.discovery, '_http_client',
                        lambda: httpx.Client(transport=httpx.MockTransport(boundary)))

    if cloud_used == 200_000:
        with pytest.raises(HTTPException) as error:
            research._search(db, job, '住宅公开研究')
        assert error.value.status_code == 429
        assert db.scalar(select(ModelUsage).where(ModelUsage.job_id == job.id)) is None
    else:
        sources, _ = research._search(db, job, '住宅公开研究')
        assert sources[0]['url'] == 'https://example.org/research'
        usage = db.scalar(select(ModelUsage).where(ModelUsage.job_id == job.id))
        assert (usage.model, usage.input_tokens, usage.output_tokens) == ('qwen-plus', 170, 50)
    assert db.get(ModelUsage, 'local-used').input_tokens == 1_000_000


@pytest.mark.parametrize('cloud_used', [50, 200_000])
def test_discovery_search_excludes_distinct_ppu_receipts_from_cloud_budget(discovery_client, discovery_http, monkeypatch, cloud_used):
    # PPU is remote existing compute, but it must not spend Model Studio's allowance.
    from app import discovery
    _, factory, _, _ = discovery_client
    _budgets(monkeypatch, 'resident_ppu')
    with factory() as db:
        _receipt(db, 'ppu-used', 'ppu:Qwen3-VL-32B@resident-test-revision', 1_000_000, tenant_id='t')
        _receipt(db, 'cloud-used', 'qwen-plus', cloud_used, tenant_id='t')
        job = Job(id='ppu-scoped-search', tenant_id='t', property_id='p', user_id='u',
                  kind='discovery_search', payload={}, result={})
        db.add(job)
        db.commit()
        request = discovery.DiscoverySearch(query='住宅', platforms=['xiaohongshu'], days=30)
        if cloud_used == 200_000:
            with pytest.raises(HTTPException) as error:
                discovery._search_sources(db, job, request)
            assert error.value.status_code == 429
            assert db.scalar(select(ModelUsage).where(ModelUsage.job_id == job.id)) is None
        else:
            sources = discovery._search_sources(db, job, request)
            assert any('xiaohongshu.com/explore/' in source['url'] for source in sources)
            usage = db.scalar(select(ModelUsage).where(ModelUsage.job_id == job.id))
            assert (usage.model, usage.input_tokens, usage.output_tokens) == ('qwen-plus', 170, 50)
        assert db.get(ModelUsage, 'ppu-used').input_tokens == 1_000_000


@pytest.mark.parametrize('cloud_used', [50, 200_000])
def test_agent_message_admission_excludes_ppu_usage_and_still_enforces_cloud_limit(discovery_client, monkeypatch, cloud_used):
    # Misclassifying PPU as cloud prevents a legitimate user message from queueing.
    from app import agent
    client, factory, _, _ = discovery_client
    client.app.include_router(agent.router)
    _budgets(monkeypatch, 'resident_ppu')
    monkeypatch.setattr(agent.provider, 'stream_chat', lambda *args, **kwargs:
                        pytest.fail('message admission must not synchronously invoke a provider'))
    with factory() as db:
        _receipt(db, 'ppu-used', 'ppu:Qwen3-VL-32B@resident-test-revision', 1_000_000, tenant_id='t')
        _receipt(db, 'cloud-used', 'qwen-plus', cloud_used, tenant_id='t')
    conversation = client.post('/api/projects/p/conversations', json={'title': '住宅研究'})
    assert conversation.status_code == 201, conversation.text
    response = client.post('/api/conversations/' + conversation.json()['id'] + '/messages',
                           json={'text': '查看当前项目资料'})
    with factory() as db:
        if cloud_used == 200_000:
            assert response.status_code == 429, response.text
            assert db.scalar(select(Job).where(Job.kind == 'chat')) is None
            assert db.scalar(select(ChatMessage)) is None
        else:
            assert response.status_code == 202, response.text
            job = db.get(Job, response.json()['job_id'])
            assert job.kind == 'chat' and job.status == 'queued'
            assistant = db.scalar(select(ChatMessage).where(ChatMessage.job_id == job.id, ChatMessage.role == 'assistant'))
            assert assistant.status == 'queued'
        assert db.get(ModelUsage, 'ppu-used').input_tokens == 1_000_000
