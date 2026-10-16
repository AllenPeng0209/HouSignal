import json
from datetime import datetime, timezone
from app.job_models import Job
from app.jobs import job_dict


def test_publication_job_exposes_safe_exact_release_binding_without_private_inputs():
    stamp = datetime.now(timezone.utc)
    for kind in ('platform_prepare', 'platform_publish'):
        job = Job(id='job', tenant_id='tenant', property_id='p', user_id='admin', kind=kind,
                  payload={'platform':'douyin','content_version_id':'content-v2','release_id':'release-v2',
                           'session_id':'private-session','credential':'private-credential'},
                  result={},status='running',progress=0,attempt=1,created_at=stamp,updated_at=stamp)
        public = job_dict(job)
        assert public.get('publication_request') == {'platform':'douyin','content_version_id':'content-v2','release_id':'release-v2'}
        assert 'private-session' not in json.dumps(public)
        assert 'private-credential' not in json.dumps(public)
        job.payload['release_id'] = 'invalid/id'
        assert job_dict(job).get('publication_request') is None
