"""Actual-call receipts survive business rejection without publishing outputs.

Real SQLite jobs, leases and cancellation are exercised. Only model generation
is replaced; no live model, platform or paid request is sent.
"""
from dataclasses import replace
import json

from fastapi import HTTPException
import httpx
import pytest
from sqlalchemy import select

from app import jobs, provider
from app.agent_models import ModelUsage
from app.config import settings
from app.core_models import Membership
from app.job_models import Job
from app.security import context_for_user
from test_operations_skills import execute, execute_queued, run, skill_client


@pytest.fixture
def metered_model(monkeypatch):
    state = {'calls': [], 'during': None, 'content': '请依照核验资料整理项目内容，未知事实保持待补充。',
             'finish': 'stop', 'error': None, 'response_extra': {}}
    def generate(messages, **kwargs):
        ordinal = len(state['calls']) + 1
        state['calls'].append({'messages': messages, **kwargs})
        if state['during']:
            state['during']()
        if state['error']:
            raise state['error']
        return {'id': f'synthetic-metered-call-{ordinal}', 'model': 'ledger-test-model',
                'choices': [{'index': 0, 'finish_reason': state['finish'],
                             'message': {'role': 'assistant', 'content': state['content']}}],
                'usage': {'prompt_tokens': 17, 'completion_tokens': 9, 'total_tokens': 26},
                **state['response_extra']}
    monkeypatch.setattr(provider, 'chat', generate)
    monkeypatch.setattr(settings, 'native_text_backend', 'dashscope')
    return state


def receipts(factory, job_id):
    with factory() as db:
        return db.scalars(select(ModelUsage).where(ModelUsage.job_id == job_id)).all()


def assert_one_receipt(factory, job):
    rows = receipts(factory, job.id)
    assert len(rows) == 1, 'a consumed model call must survive the business transaction rollback'
    row = rows[0]
    assert (row.tenant_id, row.property_id, row.model, row.input_tokens, row.output_tokens) == (
        't', 'p', 'ledger-test-model', 17, 9)


def test_incomplete_billable_response_retains_usage_after_job_rejection(skill_client, metered_model):
    client, factory, *_ = skill_client
    metered_model['finish'] = 'length'
    completed, job = execute_queued(client, factory, input='写一份待核验草稿。')
    assert not completed and job.status == 'failed' and job.result == {}
    assert len(metered_model['calls']) == 1
    assert_one_receipt(factory, job)


@pytest.mark.parametrize('change', ['cancel', 'membership'])
def test_completed_model_call_is_billed_when_late_business_result_is_fenced(skill_client, metered_model, change):
    client, factory, *_ = skill_client
    queued = run(client, input='写一份待核验草稿。')
    assert queued.status_code == 200
    jid = queued.json()['job_id']
    def revoke():
        with factory() as db:
            if change == 'cancel':
                jobs.cancel_job(jid, context_for_user(db, 'u', 't'), db)
            else:
                db.scalar(select(Membership).where(Membership.user_id == 'u')).active = False
            db.commit()
    metered_model['during'] = revoke
    completed, job = execute(factory, jid)
    assert not completed and job.result == {}
    assert job.status == ('cancelled' if change == 'cancel' else 'failed')
    assert_one_receipt(factory, job)


def test_provider_error_retains_only_its_safe_metering_receipt(skill_client, metered_model):
    client, factory, *_ = skill_client
    metered_model['error'] = provider.ProviderError('模型返回格式无效，请重试。', usage_receipt={
        'model': 'ledger-test-model', 'usage': {'prompt_tokens': 17, 'completion_tokens': 9},
        'output': 'PRIVATE_OUTPUT', 'private_path': '/private/PRIVATE_PATH', 'api_key': 'PRIVATE_KEY'})
    completed, job = execute_queued(client, factory, input='写一份待核验草稿。')
    assert not completed and job.result == {}
    assert 'PRIVATE_' not in (job.error or '')
    assert_one_receipt(factory, job)
    storage = skill_client[-1].parents[2]
    for path in (storage / 'usage-ledger').rglob('*.json'):
        assert 'PRIVATE_' not in path.read_text()


def test_rejection_before_any_model_call_has_no_fabricated_usage(skill_client, metered_model):
    client, factory, *_ = skill_client
    queued = run(client, input='写一份待核验草稿。')
    assert queued.status_code == 200
    jid = queued.json()['job_id']
    with factory() as db:
        db.scalar(select(Membership).where(Membership.user_id == 'u')).active = False
        db.commit()
    completed, job = execute(factory, jid)
    assert not completed and job.result == {}
    assert metered_model['calls'] == []
    assert receipts(factory, jid) == []


def test_supplier_scope_fields_cannot_bill_another_tenant_or_job(skill_client, metered_model):
    client, factory, *_ = skill_client
    metered_model['response_extra'] = {'tenant_id': 'other', 'property_id': 'p2', 'job_id': 'wrong-job'}
    completed, job = execute_queued(client, factory, input='写一份待核验草稿。')
    assert completed and job.status == 'succeeded'
    assert_one_receipt(factory, job)
    with factory() as db:
        assert db.scalars(select(ModelUsage).where(ModelUsage.tenant_id == 'other')).all() == []


def test_local_quality_retries_keep_each_consumed_call_even_after_final_rejection(skill_client, metered_model, monkeypatch):
    from app.content_skills.tools import local_text
    client, factory, *_ = skill_client
    monkeypatch.setattr(settings, 'native_text_backend', 'mlx')
    monkeypatch.setattr(local_text, 'readiness', lambda: {'status': 'ready', 'reason': 'controlled local model transport'})
    metered_model['content'] = '短。'
    completed, job = execute_queued(client, factory, 'native:text-condenser',
        input='摘要限定80至100个非空白字符，只输出摘要正文。')
    assert not completed and job.status == 'failed' and job.result == {}
    assert len(metered_model['calls']) == 3, 'initial generation plus at most two local retries'
    rows = receipts(factory, job.id)
    assert len(rows) == 3
    assert sum(row.input_tokens for row in rows) == 51
    assert sum(row.output_tokens for row in rows) == 27


def test_lease_loss_after_provider_response_cannot_erase_actual_usage(skill_client, metered_model):
    client, factory, *_ = skill_client
    queued = run(client, input='写一份待核验草稿。')
    assert queued.status_code == 200
    jid = queued.json()['job_id']
    def replace_lease():
        with factory() as db:
            row = db.get(Job, jid)
            row.lease_token = 'new-worker-lease'
            row.attempt = 2
            db.commit()
    metered_model['during'] = replace_lease
    completed, job = execute(factory, jid)
    assert not completed and job.result == {}
    assert_one_receipt(factory, job)


def test_same_call_ticket_is_idempotent_and_distinct_calls_are_not_deduplicated(skill_client, metered_model):
    from app import model_usage_ledger as ledger
    from app.worker import claim_job
    client, factory, *_ = skill_client
    jid = run(client, input='写一份待核验草稿。').json()['job_id']
    with factory() as db:
        job = claim_job(db, kinds=('content_skill',))
        assert job.id == jid
        db.info['model_usage_recorder'] = ledger.WorkerUsageRecorder(factory, job)
        first = ledger.begin_call(db, job)
        value = {'model': 'ledger-test-model', 'input_tokens': 17, 'output_tokens': 9,
                 'units': {'characters': 5}}
        ledger.record_receipt(db, job, first, value)
        ledger.record_receipt(db, job, first, value)
        second = ledger.begin_call(db, job)
        ledger.record_receipt(db, job, second, value)
    rows = receipts(factory, jid)
    assert len(rows) == 2
    assert len({row.id for row in rows}) == 2
    assert sum(row.input_tokens for row in rows) == 34
    assert sum(row.output_tokens for row in rows) == 18


def test_cumulative_long_agent_usage_preserves_totals_without_duplicate_billing(skill_client, metered_model):
    # Pi reuses one metering ticket for its cumulative multi-turn task totals.
    # Treating a long total as an invalid per-call count would lose the bill.
    from app import model_usage_ledger as ledger
    from app.worker import claim_job
    client, factory, *_ = skill_client
    jid = run(client, input='写一份待核验草稿。').json()['job_id']
    with factory() as db:
        job = claim_job(db, kinds=('content_skill',))
        db.info['model_usage_recorder'] = ledger.WorkerUsageRecorder(factory, job)
        ticket = ledger.begin_call(db, job)
        for input_tokens, output_tokens in ((1_500_000, 1_000_000),
                                            (12_000_000, 3_000_000),
                                            (12_000_000, 3_000_000),
                                            (9_000_000, 2_000_000)):
            ledger.record_receipt(db, job, ticket, {
                'model': 'ledger-test-model', 'input_tokens': input_tokens,
                'output_tokens': output_tokens, 'prompt': 'PRIVATE_PROMPT',
                'api_key': 'PRIVATE_KEY'})
        recorded = ledger.recorded_receipts(db, job)

    rows = receipts(factory, jid)
    assert len(rows) == 1
    assert (rows[0].input_tokens, rows[0].output_tokens) == (12_000_000, 3_000_000)
    assert len(recorded) == 1
    assert (recorded[0]['input_tokens'], recorded[0]['output_tokens']) == (12_000_000, 3_000_000)
    files = list((skill_client[-1].parents[2] / 'usage-ledger').rglob('*.json'))
    assert len(files) == 1
    stored = json.loads(files[0].read_text())
    assert (stored['input_tokens'], stored['output_tokens']) == (12_000_000, 3_000_000)
    assert 'PRIVATE_' not in files[0].read_text()


@pytest.mark.parametrize('count', [12_000_000, 2_147_483_647])
def test_metering_accepts_large_counts_within_actual_sql_integer_storage_range(skill_client, metered_model, count):
    from app import model_usage_ledger as ledger
    client, factory, *_ = skill_client
    jid = run(client, input='写一份待核验草稿。').json()['job_id']
    with factory() as db:
        job = db.get(Job, jid)
        ticket = ledger.begin_call(db, job)
        ledger.record_receipt(db, job, ticket, {
            'model': 'ledger-test-model', 'usage': {'prompt_tokens': count, 'completion_tokens': 3_000_000}})
        db.commit()
    rows = receipts(factory, jid)
    assert len(rows) == 1
    assert (rows[0].input_tokens, rows[0].output_tokens) == (count, 3_000_000)


@pytest.mark.parametrize('count', [-1, True, False, 12_000_000.0, '12000000',
                                  2_147_483_648, 10 ** 100, float('inf'), float('nan')])
def test_invalid_or_unstorable_counts_cannot_inflate_metered_usage(skill_client, metered_model, count):
    from app import model_usage_ledger as ledger
    client, factory, *_ = skill_client
    jid = run(client, input='写一份待核验草稿。').json()['job_id']
    with factory() as db:
        job = db.get(Job, jid)
        ticket = ledger.begin_call(db, job)
        ledger.record_receipt(db, job, ticket, {
            'model': 'ledger-test-model', 'input_tokens': count, 'output_tokens': 9})
        db.commit()
    rows = receipts(factory, jid)
    assert len(rows) == 1
    assert (rows[0].input_tokens, rows[0].output_tokens) == (0, 9)


def test_worker_recorder_rejects_forged_scope_without_creating_cost(skill_client, metered_model):
    from app import model_usage_ledger as ledger
    from app.worker import claim_job
    client, factory, *_ = skill_client
    jid = run(client, input='写一份待核验草稿。').json()['job_id']
    with factory() as db:
        job = claim_job(db, kinds=('content_skill',))
        db.info['model_usage_recorder'] = ledger.WorkerUsageRecorder(factory, job)
        from types import SimpleNamespace
        forged = SimpleNamespace(id=job.id, tenant_id='other', property_id='p', user_id='u', attempt=job.attempt)
        with pytest.raises((HTTPException, ValueError)):
            ledger.begin_call(db, forged)
    assert receipts(factory, jid) == []


def test_direct_nonworker_ledger_uses_only_its_current_transaction(skill_client, metered_model):
    from app import model_usage_ledger as ledger
    client, factory, *_ = skill_client
    jid = run(client, input='写一份待核验草稿。').json()['job_id']
    with factory() as db:
        job = db.get(Job, jid)
        token = ledger.begin_call(db, job)
        ledger.record_receipt(db, job, token, {'model': 'ledger-test-model', 'input_tokens': 17, 'output_tokens': 9})
        db.flush()
        assert len(db.scalars(select(ModelUsage).where(ModelUsage.job_id == jid)).all()) == 1
        db.rollback()
    assert receipts(factory, jid) == []
    assert not (skill_client[-1].parents[2] / 'usage-ledger').exists()


def test_sidecar_write_failure_does_not_erase_db_cost_or_claim_units_persisted(skill_client, metered_model):
    from app import model_usage_ledger as ledger
    from app.worker import claim_job
    client, factory, *_ = skill_client
    jid = run(client, input='写一份待核验草稿。').json()['job_id']
    occupied = skill_client[-1].parents[2] / 'usage-ledger'
    occupied.write_bytes(b'controlled occupied destination')
    with factory() as db:
        job = claim_job(db, kinds=('content_skill',))
        db.info['model_usage_recorder'] = ledger.WorkerUsageRecorder(factory, job)
        token = ledger.begin_call(db, job)
        recorded = ledger.record_receipt(db, job, token, {'model': 'ledger-test-model',
            'input_tokens': 17, 'output_tokens': 9, 'units': {'characters': 5}})
    assert recorded['units_persisted'] is False
    assert len(receipts(factory, jid)) == 1
    assert occupied.read_bytes() == b'controlled occupied destination'


def test_worker_metering_sidecar_contains_only_bounded_safe_metadata(skill_client, metered_model):
    from app import model_usage_ledger as ledger
    from app.worker import claim_job
    client, factory, *_ = skill_client
    jid = run(client, input='写一份待核验草稿。').json()['job_id']
    with factory() as db:
        job = claim_job(db, kinds=('content_skill',))
        db.info['model_usage_recorder'] = ledger.WorkerUsageRecorder(factory, job)
        token = ledger.begin_call(db, job)
        ledger.record_receipt(db, job, token, {'model': 'ledger-test-model', 'input_tokens': 17,
            'output_tokens': 9, 'units': {'characters': 5, 'duration': float('inf'),
                'api_key': 123, 'request_id': 456}, 'prompt': 'PRIVATE_PROMPT',
            'output': 'PRIVATE_OUTPUT', 'url': 'https://private.invalid/?token=PRIVATE_TOKEN'})
    files = list((skill_client[-1].parents[2] / 'usage-ledger').rglob('*.json'))
    assert len(files) == 1
    assert files[0].stat().st_mode & 0o777 == 0o600
    document = json.loads(files[0].read_text())
    assert document['units'] == {'characters': 5}
    assert 'PRIVATE_' not in files[0].read_text()
    assert document['tenant_id'] == 't' and document['property_id'] == 'p'


def test_actual_local_model_revision_is_preserved_in_the_cost_ledger(skill_client, metered_model):
    from app import model_usage_ledger as ledger
    from app.worker import claim_job
    client, factory, *_ = skill_client
    jid = run(client, input='写一份待核验草稿。').json()['job_id']
    model = 'local:Qwen3-4B-Instruct-2507-4bit@50d42775'
    with factory() as db:
        job = claim_job(db, kinds=('content_skill',))
        db.info['model_usage_recorder'] = ledger.WorkerUsageRecorder(factory, job)
        token = ledger.begin_call(db, job)
        ledger.record_receipt(db, job, token, {'model': model, 'input_tokens': 17, 'output_tokens': 9})
    assert receipts(factory, jid)[0].model == model


def test_configured_secret_is_never_retained_as_a_supplier_model_name(skill_client, metered_model):
    from app import model_usage_ledger as ledger
    from app.worker import claim_job
    client, factory, *_ = skill_client
    jid = run(client, input='写一份待核验草稿。').json()['job_id']
    with factory() as db:
        job = claim_job(db, kinds=('content_skill',))
        db.info['model_usage_recorder'] = ledger.WorkerUsageRecorder(factory, job)
        token = ledger.begin_call(db, job)
        ledger.record_receipt(db, job, token, {'model': settings.dashscope_api_key, 'input_tokens': 17, 'output_tokens': 9})
    assert receipts(factory, jid)[0].model == 'redacted-model'


@pytest.mark.parametrize('model', ['local:/Users/private-person/models/checkpoint',
                                  'file:/private/models/checkpoint',
                                  'https://private.invalid/models/checkpoint',
                                  'private-person@example.org'])
def test_supplier_model_metadata_cannot_persist_private_paths_urls_or_contacts(model):
    from app.model_usage_ledger import _safe_receipt
    safe = _safe_receipt({'model': model, 'input_tokens': 17, 'output_tokens': 9})
    assert safe['model'] == 'redacted-model'
    assert (safe['input_tokens'], safe['output_tokens']) == (17, 9)


@pytest.mark.parametrize('scenario', ['http_failure', 'task_failure', 'cancel_after_response'])
def test_actual_media_worker_retains_reported_units_before_failure_or_cancel(skill_client, metered_model, monkeypatch, scenario):
    from app.content_skills.tools import models
    client, factory, *_ = skill_client
    monkeypatch.setattr(settings, 'dashscope_image_model', 'wan2.6-t2i')
    monkeypatch.setattr(settings, 'dashscope_base_url', 'https://dashscope.aliyuncs.com/compatible-mode/v1')
    real_client = httpx.Client
    holder = {}
    def boundary(request):
        if request.method == 'POST':
            if scenario == 'http_failure':
                return httpx.Response(500, json={'code': 'reported_failure', 'usage': {'image_count': 1}})
            if scenario == 'cancel_after_response':
                with factory() as db:
                    jobs.cancel_job(holder['job_id'], context_for_user(db, 'u', 't'), db)
                    db.commit()
                return httpx.Response(200, json={'output': {'results': [{'url': 'data:image/png;base64,aGVsbG8='}]},
                                                'usage': {'image_count': 1}})
            return httpx.Response(200, json={'output': {'task_id': 'controlled-generation-task'},
                                            'usage': {'image_count': 0}})
        assert request.method == 'GET' and request.url.path.endswith('/tasks/controlled-generation-task')
        return httpx.Response(200, json={'output': {'task_id': 'controlled-generation-task', 'task_status': 'FAILED'},
                                        'usage': {'image_count': 1}})
    transport = httpx.MockTransport(boundary)
    monkeypatch.setattr(models.httpx, 'Client', lambda *args, **kwargs: real_client(*args, **kwargs, transport=transport))
    queued = run(client, 'ai-image', input='只生成控制测试图片。', parameters={'size': '1024x1024'})
    assert queued.status_code == 200, queued.text
    holder['job_id'] = queued.json()['job_id']
    completed, job = execute(factory, holder['job_id'])
    assert not completed and job.result == {}
    assert job.status == ('cancelled' if scenario == 'cancel_after_response' else 'failed')
    rows = receipts(factory, job.id)
    assert len(rows) == 1, 'submission and failed final poll belong to one consumed generation ticket'
    assert rows[0].model == 'wan2.6-t2i' and rows[0].input_tokens == rows[0].output_tokens == 0
    documents = [json.loads(path.read_text()) for path in (skill_client[-1].parents[2] / 'usage-ledger').rglob('*.json')]
    assert len(documents) == 1 and documents[0]['units'] == {'image_count': 1}
