"""The real bounded worker must pick up reference analysis and enforce its role."""
from threading import Event, Timer
import pytest
from sqlalchemy import select

from app.core_models import Membership
from app.jobs import enqueue_job, job_dict
from app.job_models import Job
from app.worker import claim_job, execute_job, run_worker
from test_discovery import discovery_client


def test_reference_analysis_runs_in_background_lane_and_exposes_its_reference(discovery_client):
    _, factory, _, ctx = discovery_client
    with factory() as db:
        queued = enqueue_job(db, ctx, 'p', 'reference_analyze', {'reference_id':'reference-a'})
        jid = queued.id
        db.commit()
    stop, called = Event(), []
    def handle(db, job):
        called.append(job.id); stop.set()
        return {'reference_id':'reference-a', 'analyzed':True}
    timeout = Timer(3, stop.set); timeout.start()
    try:
        run_worker(stop, session_factory=factory, handlers={'reference_analyze':handle})
    finally:
        timeout.cancel()
    assert called == [jid]
    with factory() as db:
        result = db.get(Job, jid)
        assert result.status == 'succeeded'
        assert job_dict(result)['reference_id'] == 'reference-a'


def test_viewer_cannot_execute_queued_reference_analysis(discovery_client):
    _, factory, _, ctx = discovery_client
    with factory() as db:
        queued = enqueue_job(db, ctx, 'p', 'reference_analyze', {'reference_id':'reference-a'})
        jid = queued.id; db.commit()
        claimed = claim_job(db, kinds=('reference_analyze',))
        ticket = (claimed.id, claimed.lease_token, claimed.attempt)
        member = db.query(Membership).filter_by(user_id='u',tenant_id='t').one()
        member.role = 'viewer'; db.commit()
    called = []
    def handle(db, job):
        called.append(job.id); return {'analyzed':True}
    assert execute_job(*ticket, session_factory=factory, handlers={'reference_analyze':handle}) is False
    assert called == []


@pytest.mark.parametrize('after_response', ['invalid_analysis', 'cancelled'])
def test_paid_reference_usage_survives_result_rejection(discovery_client, tmp_path, monkeypatch, after_response):
    from app.config import settings
    from app.agent_models import ModelUsage
    from app.model_usage_ledger import begin_call, record_response
    monkeypatch.setattr(settings, 'storage_root', tmp_path)
    _, factory, _, ctx = discovery_client
    with factory() as db:
        queued=enqueue_job(db,ctx,'p','reference_analyze',{'reference_id':'reference-a'})
        jid=queued.id;db.commit()
        claimed=claim_job(db,kinds=('reference_analyze',))
        ticket=(claimed.id,claimed.lease_token,claimed.attempt)
    def handle(db,job):
        token=begin_call(db,job)
        if after_response=='cancelled':
            with factory() as other:
                current=other.get(Job,job.id);current.status='cancelled';current.lease_token=None;other.commit()
        record_response(db,job,token,{'model':'reference-vision-fixture','usage':{'prompt_tokens':120,'completion_tokens':80}})
        raise ValueError('识别结果未通过证据校验')
    assert execute_job(*ticket,session_factory=factory,handlers={'reference_analyze':handle}) is False
    with factory() as db:
        rows=db.scalars(select(ModelUsage).where(ModelUsage.job_id==jid)).all()
        assert [(row.input_tokens,row.output_tokens) for row in rows]==[(120,80)]
