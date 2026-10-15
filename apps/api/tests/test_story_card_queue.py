from app.jobs import job_dict
from app.worker import claim_job, execute_job
from app.core_models import Membership
from app.job_models import Job
from test_worker_performance import queue_factory, add_job


def test_illustration_job_recovers_only_the_source_version_not_private_inputs(queue_factory):
    jid=add_job(queue_factory,'illustrate_content',payload={'content_version_id':'draft-v1','private_prompt':'do not expose'})
    with queue_factory() as db:
        assert job_dict(db.get(Job,jid))['production_request']=={'kind':'illustrate_content','content_version_id':'draft-v1'}


def test_illustration_worker_checks_edit_permission_before_source_writes(queue_factory):
    jid=add_job(queue_factory,'illustrate_content')
    with queue_factory() as db:
        job=claim_job(db);ticket=(jid,job.lease_token,job.attempt)
        db.query(Membership).one().role='reviewer';db.commit()
    calls=[]
    execute_job(*ticket,session_factory=queue_factory,handlers={'illustrate_content':lambda *_:calls.append('write') or {}})
    assert not calls
    with queue_factory() as db:assert db.get(Job,jid).status=='failed'
