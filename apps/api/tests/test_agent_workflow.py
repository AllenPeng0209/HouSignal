"""Dependent agent work waits durably without blocking unrelated chats."""
import pytest
from app.job_models import Job
from test_worker_performance import queue_factory, add_job


@pytest.mark.parametrize('terminal', ['succeeded', 'failed', 'cancelled'])
def test_agent_continuation_waits_for_real_production_terminal(queue_factory, terminal):
    from app.worker import claim_job
    upstream = add_job(queue_factory, 'generate_content')
    child = add_job(queue_factory, payload={'depends_on_job_id': upstream})
    other = add_job(queue_factory)
    with queue_factory() as db:
        assert claim_job(db, kinds=('chat',)).id == other
        assert claim_job(db, kinds=('chat',)) is None
        db.get(Job, upstream).status = terminal
        db.commit()
        assert claim_job(db, kinds=('chat',)).id == child


def test_agent_continuation_cannot_depend_on_another_projects_job(queue_factory):
    from app.worker import claim_job
    upstream = add_job(queue_factory, 'generate_content', property_id='other-project', status='succeeded')
    add_job(queue_factory, payload={'depends_on_job_id': upstream})
    with queue_factory() as db:
        assert claim_job(db, kinds=('chat',)) is None


def test_failed_pi_chat_retains_metering_for_authorized_sales(queue_factory, monkeypatch, tmp_path):
    from sqlalchemy import select
    from app.agent_models import ModelUsage
    from app.core_models import Membership
    from app.config import settings
    from app.worker import claim_job, execute_job
    monkeypatch.setattr(settings, 'storage_root', tmp_path / 'usage-storage')
    jid = add_job(queue_factory)
    with queue_factory() as db:
        db.scalar(select(Membership).where(Membership.user_id == 'u')).role = 'sales'
        db.commit()
        job = claim_job(db, kinds=('chat',))
        ticket = (jid, job.lease_token, job.attempt)
    def billed_then_rejected(db, job):
        recorder = db.info['model_usage_recorder']
        token = recorder.begin(job)
        recorder.record(job, token, {'model': 'qwen-plus', 'input_tokens': 30, 'output_tokens': 9})
        raise ValueError('模型没有实际执行业务工具')
    assert not execute_job(*ticket, session_factory=queue_factory, handlers={'chat': billed_then_rejected})
    with queue_factory() as db:
        receipt = db.scalar(select(ModelUsage).where(ModelUsage.job_id == jid))
        assert receipt is not None
        assert (receipt.input_tokens, receipt.output_tokens) == (30, 9)
        assert db.get(Job, jid).status == 'failed'
