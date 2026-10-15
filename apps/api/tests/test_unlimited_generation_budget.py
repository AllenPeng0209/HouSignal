"""An explicit zero quota admits real generation paths and keeps their receipts."""
import json

import pytest
from sqlalchemy import select

from app import discovery, provider
from app.agent_models import ModelUsage
from app.business import handle_generate
from app.business_models import ContentVersion
from app.config import settings
from app.content_skills import text
from app.job_models import Job
from test_business import scenario
from test_discovery import discovery_client, discovery_http
from test_generation_duration import draft, generation_job


def _unlimited(monkeypatch, backend='dashscope'):
    monkeypatch.setattr(settings, 'app_env', 'development')
    monkeypatch.setattr(settings, 'development_token_budget', 0)
    monkeypatch.setattr(settings, 'native_local_text_daily_token_budget', 0)
    monkeypatch.setattr(settings, 'native_text_backend', backend)


@pytest.mark.parametrize(('backend', 'model'), [
    ('dashscope', 'qwen-plus'), ('mlx', 'local:mlx:fixture'),
    ('resident_ppu', 'ppu:resident-fixture')])
def test_zero_budget_generates_after_prior_usage_and_still_records_it(scenario, monkeypatch, backend, model):
    # Treating zero as an exhausted budget would reject before saving the draft.
    db, *_ = scenario
    _unlimited(monkeypatch, backend)
    db.add(ModelUsage(id='earlier', tenant_id='tenant', property_id='p', job_id='earlier',
                      model=model, input_tokens=2_000_000, output_tokens=1_000_000))
    db.commit()
    monkeypatch.setattr(provider, 'chat', lambda *args, **kwargs: {
        'model': model, 'choices': [{'message': {'content': json.dumps(draft(30), ensure_ascii=False)},
                                   'finish_reason': 'stop'}],
        'usage': {'prompt_tokens': 40, 'completion_tokens': 60}})

    result = handle_generate(db, generation_job())

    assert db.get(ContentVersion, result['content_version_id']).script == '从420平方米的实拍空间观察日常居住体验。'
    receipt = db.scalar(select(ModelUsage).where(ModelUsage.job_id == 'duration-generation'))
    assert (receipt.model, receipt.input_tokens, receipt.output_tokens) == (model, 40, 60)
    assert db.get(ModelUsage, 'earlier').input_tokens == 2_000_000


@pytest.mark.parametrize('backend', ['dashscope', 'mlx', 'resident_ppu'])
def test_native_text_zero_budget_admits_large_current_and_requested_usage(scenario, monkeypatch, backend):
    # Native skills and their agent bridge share this actual admission function.
    db, ctx, *_ = scenario
    _unlimited(monkeypatch, backend)
    db.add_all([ModelUsage(tenant_id='tenant', property_id='p', job_id='old-cloud', model='qwen-plus',
                          input_tokens=2_000_000, output_tokens=0),
                ModelUsage(tenant_id='tenant', property_id='p', job_id='old-local', model='ppu:fixture',
                          input_tokens=2_000_000, output_tokens=0)])
    db.commit()

    text.check_budget(db, ctx, additional_tokens=2_000_000, local_generation=True)


def test_zero_cloud_budget_searches_after_prior_usage_and_still_records_it(discovery_client, discovery_http, monkeypatch):
    # A real public-search result and persistent receipt prove the quota did not stop the path.
    _, factory, _, _ = discovery_client
    _unlimited(monkeypatch)
    with factory() as db:
        db.add(ModelUsage(id='earlier', tenant_id='t', property_id='p', job_id='earlier',
                          model='qwen-plus', input_tokens=2_000_000, output_tokens=0))
        job = Job(id='search', tenant_id='t', property_id='p', user_id='u', kind='discovery_search', payload={}, result={})
        db.add(job)
        db.commit()

        sources = discovery._search_sources(db, job, discovery.DiscoverySearch(
            query='住宅', platforms=['xiaohongshu'], days=30))

        assert any('xiaohongshu.com/explore/' in source['url'] for source in sources)
        receipt = db.scalar(select(ModelUsage).where(ModelUsage.job_id == 'search'))
        assert (receipt.input_tokens, receipt.output_tokens) == (170, 50)
        assert db.get(ModelUsage, 'earlier').input_tokens == 2_000_000
