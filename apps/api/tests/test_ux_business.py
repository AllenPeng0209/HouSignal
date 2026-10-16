"""User tasks, explicit CRM attribution and review feedback persist real evidence."""
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from app import audit_models  # Register the existing audit table before the shared fixture creates metadata.

from test_business import scenario, rendered


def publication(db, *, pid='p', ident='pub', published_at=None, metrics=None):
    from app.business_models import Publication
    value = Publication(id=ident, tenant_id='tenant', property_id=pid, release_id='r',
        title='客厅采光实拍', channel='抖音', url='https://example.test/'+ident,
        receipt_status='verified', platform_state='live', metrics=metrics or {'D7': None, 'D30': None})
    if published_at is not None:
        value.published_at = published_at
    db.add(value); db.flush()
    return value


def qualified(db, ctx):
    from app.business import create_inquiry, qualify_inquiry
    inquiry = create_inquiry(db, ctx, 'p', {'name':'客户甲', 'contact':'13800138000', 'contactable':True, 'consent':True})
    lead = qualify_inquiry(db, ctx, 'p', inquiry['id'], dict.fromkeys(('contactable','consent','demand_confirmed','fit_confirmed'), True))
    return inquiry, lead


def test_inquiry_publication_is_scoped_and_self_report_does_not_become_verified(scenario):
    # Catches ignored/cross-project publication IDs and false upgrades of client attribution.
    from app.business import create_inquiry, inquiries
    from app.core_models import Property
    db, ctx, *_ = scenario
    publication(db)
    db.add(Property(id='p2', tenant_id='tenant', name='另一个项目', city='上海')); db.flush()
    publication(db, pid='p2', ident='foreign')
    with pytest.raises(HTTPException) as error:
        create_inquiry(db, ctx, 'p', {'publication_id':'foreign'})
    assert error.value.status_code == 404
    result = create_inquiry(db, ctx, 'p', {'publication_id':'pub','source_confidence':'self_reported'})
    db.commit(); db.expire_all()
    saved = next(item for item in inquiries('p', db, ctx) if item['id'] == result['id'])
    assert (saved['publication_id'], saved['publication_title'], saved['source_confidence']) == ('pub','客厅采光实拍','self_reported')


def test_followup_due_time_persists_and_rejects_naive_datetime(scenario):
    # Catches silently discarded deadlines and treating local times as UTC.
    from app.business import add_followup, lead_detail_data
    db, ctx, *_ = scenario
    _, lead = qualified(db, ctx)
    with pytest.raises(HTTPException) as error:
        add_followup(db, ctx, 'p', lead['id'], {'note':'回访', 'next_action_at':'2026-10-18T10:00:00'})
    assert error.value.status_code == 422
    add_followup(db, ctx, 'p', lead['id'], {'note':'回访', 'next_action':'致电', 'next_action_at':'2026-10-18T10:00:00+08:00'})
    db.commit(); db.expire_all()
    result = lead_detail_data(db, ctx, 'p', lead['id'])
    assert datetime.fromisoformat(result['lead']['next_action_at']) == datetime(2026,10,18,2,tzinfo=timezone.utc)
    assert datetime.fromisoformat(result['followups'][0]['next_action_at']) == datetime(2026,10,18,2,tzinfo=timezone.utc)


def test_qualification_preserves_explicit_next_action_deadline(scenario):
    from app.business import create_inquiry, qualify_inquiry, list_leads_data
    db, ctx, *_ = scenario
    i = create_inquiry(db, ctx, 'p', {'contact':'13800138000','contactable':True,'consent':True})
    checks = dict.fromkeys(('contactable','consent','demand_confirmed','fit_confirmed'), True)
    qualify_inquiry(db, ctx, 'p', i['id'], checks | {'next_action_at':'2026-10-18T10:00:00+08:00'})
    db.commit(); db.expire_all()
    assert datetime.fromisoformat(list_leads_data(db, ctx, 'p')[0]['next_action_at']) == datetime(2026,10,18,2,tzinfo=timezone.utc)


def test_tasks_respect_shanghai_day_and_only_mature_known_publications(scenario, monkeypatch):
    # Catches early D7/D30 reminders and fabricated deadlines from ingestion timestamps.
    import app.ux_tasks as tasks
    db, ctx, _, fact, *_ = scenario
    monkeypatch.setattr(tasks, 'now', lambda: datetime(2026,10,17,16,30,tzinfo=timezone.utc))
    fact.status = 'unverified'
    publication(db, ident='mature', published_at=datetime(2026,10,10,16,30,tzinfo=timezone.utc))
    publication(db, ident='fresh', published_at=datetime(2026,10,17,15,tzinfo=timezone.utc))
    publication(db, ident='unknown')
    db.commit()
    result = tasks.project_tasks_data(db, ctx, 'p')
    assert result['timezone'] == 'Asia/Shanghai'
    metrics = [item for item in result['items'] if item['kind'] == 'metrics']
    assert [(item['target']['publicationId'], item['target']['metricsWindow']) for item in metrics] == [('mature','D7')]
    assert any(item['kind'] == 'fact_gap' for item in result['items'])


def test_tasks_sales_only_include_own_due_leads_and_viewings(scenario, monkeypatch):
    # Catches task lists bypassing the CRM owner boundary and user role action permissions.
    import app.ux_tasks as tasks
    from app.business import add_followup
    from app.business_models import Inquiry, Lead
    from app.secretary_models import ViewingAppointment
    from app.security import Context
    db, ctx, *_ = scenario
    monkeypatch.setattr(tasks, 'now', lambda: datetime(2026,10,17,16,30,tzinfo=timezone.utc))
    _, lead = qualified(db, ctx)
    add_followup(db, ctx, 'p', lead['id'], {'note':'跟进', 'next_action_at':'2026-10-18T10:00:00+08:00'})
    db.add(Inquiry(id='own-i', tenant_id='tenant', property_id='p', name='自己的咨询', owner_id='sales', consent=True, contactable=True, qualified=True))
    db.add(Lead(id='own-l', tenant_id='tenant', property_id='p', inquiry_id='own-i', name='自己的客户', owner_id='sales', next_action='回访', next_action_at=datetime(2026,10,18,2,tzinfo=timezone.utc)))
    db.add(ViewingAppointment(id='v', tenant_id='tenant', property_id='p', lead_id='own-l', date='2026-10-18', status='pending_details', time_window='下午', created_by='sales'))
    db.commit()
    sales = Context(user_id='sales',tenant_id='tenant',role='sales',name='销售',email='sales@test.cn',project_ids=None)
    items = tasks.project_tasks_data(db, sales, 'p')['items']
    assert {item['kind'] for item in items} == {'followup','viewing'}
    assert all(item['target']['leadId'] == 'own-l' for item in items)
    viewing = next(item for item in items if item['kind'] == 'viewing')
    assert viewing['due_at'] is None  # Day/afternoon does not mean an invented exact time.
    viewer = Context(user_id='sales',tenant_id='tenant',role='viewer',name='只读',email='sales@test.cn',project_ids=None)
    assert tasks.project_tasks_data(db, viewer, 'p')['items'] == []


def test_failed_tasks_link_exact_job_without_leaking_another_owners_jobs(scenario):
    # Catches a task button losing the job it should open or bypassing job owner scope.
    from app.ux_tasks import project_tasks_data
    from app.job_models import Job
    from app.security import Context
    db,ctx,*_=scenario
    db.add_all([
        Job(id='own-job',tenant_id='tenant',property_id='p',user_id='sales',kind='render_content',status='failed',payload={'content_version_id':'c'}),
        Job(id='other-job',tenant_id='tenant',property_id='p',user_id='admin',kind='render_content',status='failed',payload={'content_version_id':'c'}),
    ]);db.commit()
    content_ctx=Context(user_id='sales',tenant_id='tenant',role='content',name='内容',email='sales@test.cn',project_ids=None)
    jobs=[item for item in project_tasks_data(db,content_ctx,'p')['items'] if item['kind']=='failed_job']
    assert len(jobs)==1
    assert jobs[0]['target']['jobId']=='own-job'
    assert jobs[0]['target']['contentId']=='c'


@pytest.mark.parametrize('retry_status',['queued','running','succeeded'])
def test_resolved_failure_is_removed_only_for_a_later_job_with_the_same_input(scenario,retry_status):
    # Catches repeatedly asking for a failure already being retried or successfully recovered.
    from app.ux_tasks import project_tasks_data
    from app.job_models import Job
    db,ctx,*_=scenario
    old=datetime(2026,10,17,1,tzinfo=timezone.utc);new=datetime(2026,10,17,2,tzinfo=timezone.utc)
    db.add_all([
        Job(id='resolved',tenant_id='tenant',property_id='p',user_id='admin',kind='generate_content',status='failed',payload={'topic_id':'space','plan_id':'plan-1','channel':'抖音'},created_at=old),
        Job(id='retry',tenant_id='tenant',property_id='p',user_id='admin',kind='generate_content',status=retry_status,payload={'topic_id':'space','plan_id':'plan-1','channel':'抖音','resume_from_job_id':'resolved'},created_at=new),
        Job(id='other-plan',tenant_id='tenant',property_id='p',user_id='admin',kind='generate_content',status='failed',payload={'topic_id':'space','plan_id':'plan-2','channel':'抖音'},created_at=old),
        Job(id='other-topic',tenant_id='tenant',property_id='p',user_id='admin',kind='generate_content',status='failed',payload={'topic_id':'light','plan_id':'plan-1','channel':'抖音'},created_at=old),
        Job(id='other-channel',tenant_id='tenant',property_id='p',user_id='admin',kind='generate_content',status='failed',payload={'topic_id':'space','plan_id':'plan-1','channel':'小红书'},created_at=old),
    ]);db.commit()
    jobs={item['target']['jobId'] for item in project_tasks_data(db,ctx,'p')['items'] if item['kind']=='failed_job'}
    assert jobs=={'other-plan','other-topic','other-channel'}


def test_different_content_assets_and_unidentified_inputs_do_not_resolve_failures(scenario):
    # Catches broad kind-only matching, older successes and canceled/failed retry suppression.
    from app.ux_tasks import project_tasks_data
    from app.job_models import Job
    db,ctx,*_=scenario
    old=datetime(2026,10,17,1,tzinfo=timezone.utc);new=datetime(2026,10,17,2,tzinfo=timezone.utc)
    fixtures=[
        ('content-failed','render_content','failed',{'content_version_id':'c'},old),
        ('other-content','render_content','succeeded',{'content_version_id':'other-c'},new),
        ('older-success','render_content','succeeded',{'content_version_id':'c'},datetime(2026,10,16,1,tzinfo=timezone.utc)),
        ('asset-failed','extract_asset','failed',{'asset_id':'a'},old),
        ('asset-success','extract_asset','succeeded',{'asset_id':'different-a'},new),
        ('unidentified','render_content','failed',{},old),
        ('unidentified-success','render_content','succeeded',{},new),
        ('canceled','render_content','canceled',{'content_version_id':'c'},new),
    ]
    for ident,kind,status,payload,created in fixtures:
        db.add(Job(id=ident,tenant_id='tenant',property_id='p',user_id='admin',kind=kind,status=status,payload=payload,created_at=created))
    db.commit()
    jobs={item['target']['jobId'] for item in project_tasks_data(db,ctx,'p')['items'] if item['kind']=='failed_job'}
    assert jobs=={'content-failed','asset-failed','unidentified'}


def test_reconnected_agent_success_resolves_same_business_input_within_owner_scope(scenario):
    # Catches revoked/renewed OAuth grant metadata leaving a recovered production failure in the queue.
    from app.ux_tasks import project_tasks_data
    from app.job_models import Job
    from app.security import Context
    db,ctx,*_=scenario
    old=datetime(2026,10,17,1,tzinfo=timezone.utc);new=datetime(2026,10,17,2,tzinfo=timezone.utc)
    association={'client_id':'client','project_id':'p','user_id':'sales','scopes':['louxiang:read','louxiang:production']}
    db.add_all([
        Job(id='old-connection',tenant_id='tenant',property_id='p',user_id='sales',kind='render_content',status='failed',created_at=old,
            payload={'content_version_id':'c','external_agent':association|{'grant_id':'old'}}),
        Job(id='new-connection',tenant_id='tenant',property_id='p',user_id='sales',kind='render_content',status='succeeded',created_at=new,
            payload={'content_version_id':'c','external_agent':association|{'grant_id':'new'}}),
        Job(id='different-business-input',tenant_id='tenant',property_id='p',user_id='sales',kind='render_content',status='failed',created_at=old,
            payload={'content_version_id':'c','render_profile':'different','external_agent':association|{'grant_id':'old'}}),
        Job(id='own-unresolved',tenant_id='tenant',property_id='p',user_id='sales',kind='render_content',status='failed',created_at=old,
            payload={'content_version_id':'own-c'}),
        Job(id='other-owner-success',tenant_id='tenant',property_id='p',user_id='admin',kind='render_content',status='succeeded',created_at=new,
            payload={'content_version_id':'own-c'}),
    ]);db.commit()
    content_ctx=Context(user_id='sales',tenant_id='tenant',role='content',name='内容',email='sales@test.cn',project_ids=None)
    jobs={item['target']['jobId'] for item in project_tasks_data(db,content_ctx,'p')['items'] if item['kind']=='failed_job'}
    assert jobs=={'different-business-input','own-unresolved'}


def test_same_public_url_cannot_be_rebound_to_another_release(scenario):
    # Catches a copied v1 link falsely completing the publication flow for a v2 release.
    from app.business import approve_content,create_publication
    from app.business_models import Release
    db,ctx,*_=scenario;rendered(scenario)
    current=approve_content(db,ctx,'p','c',{'confirmed':True});db.commit()
    db.add(Release(id='old-release',tenant_id='tenant',property_id='p',content_version_id='c',status='approved',snapshot={},snapshot_sha256='old',reviewer_id='admin'))
    p=publication(db);p.release_id='old-release';db.commit()
    with pytest.raises(HTTPException) as error:
        create_publication('p',{'release_id':current['id'],'url':p.url},db,ctx)
    assert error.value.status_code==409
    assert p.release_id=='old-release'
    p.release_id=current['id'];db.commit()
    retried=create_publication('p',{'release_id':current['id'],'url':p.url},db,ctx)
    assert (retried['id'],retried['release_id'])==(p.id,current['id'])


def test_platform_receipt_for_another_release_requires_reconciliation_without_false_binding(scenario):
    # Catches the worker inventing a second publication for an URL already tied to another approved work.
    from app.platform_publish import _final_result
    from app.job_models import Job
    from app.business_models import Publication
    db,ctx,*_=scenario
    old=publication(db);db.commit()
    job=Job(id='publish',tenant_id='tenant',property_id='p',user_id='admin',kind='platform_publish')
    old.url='https://www.douyin.com/video/1234567890123456789';db.commit()
    result=_final_result(db,job,{'content':{'id':'c','cover_title':'新作品','channel':'抖音'},'release':{'id':'new-release'}},
        {'platform':'douyin','result':{'status':'submitted','url':old.url}})
    assert result['status']=='reconciliation_required'
    assert 'publication_id' not in result
    assert len(db.scalars(select(Publication)).all())==1


def test_crm_attribution_counts_explicit_links_separately_from_platform_metrics(scenario):
    # Catches merging platform-entered conversion counts with actual linked CRM records.
    from app.business import create_inquiry, qualify_inquiry, reports_data
    db, ctx, *_ = scenario
    publication(db, metrics={'D7':{'views':200,'inquiries':99,'qualified':88},'D30':None})
    i = create_inquiry(db, ctx, 'p', {'publication_id':'pub','source_confidence':'self_reported','contact':'13800138000','contactable':True,'consent':True})
    qualify_inquiry(db, ctx, 'p', i['id'], dict.fromkeys(('contactable','consent','demand_confirmed','fit_confirmed'),True))
    create_inquiry(db, ctx, 'p', {'name':'未关联'})
    result = reports_data(db, ctx, 'p', 'D7')
    row = result['crm_attribution'][0]
    assert (row['publication_id'],row['linked_inquiries'],row['qualified_customers']) == ('pub',1,1)
    assert row['source_confidence_counts'] == {'confirmed':0,'self_reported':1,'inferred':0,'unknown':0}
    assert result['metrics']['inquiries'] == 99
    assert result['metrics']['qualified'] == 88


def test_content_role_reads_project_crm_aggregate_without_customer_identity(scenario):
    # Catches showing a permission-denied aggregate as zero or leaking contacts to content work.
    from app.business import create_inquiry, reports_data
    from app.security import Context
    db,ctx,*_=scenario
    publication(db)
    create_inquiry(db,ctx,'p',{'name':'客户私密姓名','publication_id':'pub','source_confidence':'self_reported','source_text':'客户口述'})
    content_ctx=Context(user_id='sales',tenant_id='tenant',role='content',name='内容',email='sales@test.cn',project_ids=None)
    report=reports_data(db,content_ctx,'p')
    assert report['crm_attribution_scope']=='cumulative_explicit_links'
    assert report['crm_attribution'][0]['linked_inquiries']==1
    assert '客户私密姓名' not in str(report['crm_attribution'])
    assert '客户口述' not in str(report['crm_attribution'])


def test_review_exposes_invalid_evidence_and_hides_cross_project_records(scenario):
    # Catches reviewing only the happy path or exposing arbitrary facts/assets by ID.
    from app.ux_review import review_content_data
    from app.core_models import Fact, Property
    db, ctx, _, f, a, c = scenario
    f.revision=2; a.rights='revoked'
    db.add(Property(id='p2',tenant_id='tenant',name='别处',city='上海')); db.flush()
    db.add(Fact(id='foreign-f',tenant_id='tenant',property_id='p2',semantic_key='price',label='不应泄漏',value='999',source='保密资料'))
    c.fact_refs=[{'id':'f','revision':1},{'id':'foreign-f','revision':1}]; db.commit()
    result=review_content_data(db,ctx,'p','c')
    assert result['facts'][0]['valid'] is False
    assert result['facts'][1] == {'id':'foreign-f','expected_revision':1,'missing':True,'valid':False}
    assert result['assets'][0]['valid'] is False
    assert result['shots'][0]['id'] == 'shot-1'


def test_review_feedback_persists_identity_version_and_requires_reviewer(scenario):
    # Catches ephemeral feedback, unauthorized writes and feedback detached from its evidence.
    from app.ux_review import add_review_feedback, review_content_data
    from app.security import Context
    db, ctx, _, _, _, c = scenario
    content_ctx=Context(user_id='sales',tenant_id='tenant',role='content',name='内容',email='sales@test.cn',project_ids=None)
    with pytest.raises(HTTPException) as error:
        add_review_feedback(db, content_ctx, 'p','c', {'note':'改一改'})
    assert error.value.status_code == 403
    with pytest.raises(HTTPException): add_review_feedback(db,ctx,'p','c',{'note':'改','shot_id':'missing'})
    with pytest.raises(HTTPException): add_review_feedback(db,ctx,'p','c',{'note':'改','sentence':'不在脚本中'})
    add_review_feedback(db,ctx,'p','c',{'note':'请核对面积表述','shot_id':'shot-1','sentence':'420㎡'})
    db.commit(); db.expire_all()
    result=review_content_data(db,ctx,'p','c')
    record=result['feedback'][0]
    assert (record['reviewer_id'],record['version'],record['note'],record['shot_id']) == ('admin',1,'请核对面积表述','shot-1')
    assert result['content']['status'] == 'needs_revision'


def test_review_feedback_keeps_approved_release_and_rejects_changed_render(scenario):
    # Catches changing a frozen release after feedback and races against regenerated previews.
    from app.business import approve_content
    from app.business_models import Release
    from app.ux_review import add_review_feedback
    db, ctx, _, _, _, c = scenario; rendered(scenario)
    release=approve_content(db,ctx,'p','c',{'confirmed':True}); db.commit()
    before=db.get(Release,release['id']).snapshot
    with pytest.raises(HTTPException) as error:
        add_review_feedback(db,ctx,'p','c',{'note':'请修改','expected_render_asset_id':'old-render'})
    assert error.value.status_code == 409
    result=add_review_feedback(db,ctx,'p','c',{'note':'请修改','expected_render_asset_id':'render'})
    db.commit(); db.expire_all()
    assert result['release_frozen'] is True
    assert db.get(Release,release['id']).snapshot == before
    assert db.get(type(c),'c').status == 'approved'


def test_review_feedback_cannot_attach_an_unrelated_script_sentence_to_a_shot(scenario):
    # Catches misleading scene-specific audit evidence when a sentence is in another shot.
    from app.ux_review import add_review_feedback
    db,ctx,_,_,_,c=scenario
    c.script='420㎡实拍空间。这里是卧室。';db.commit()
    with pytest.raises(HTTPException) as error:
        add_review_feedback(db,ctx,'p','c',{'note':'修改这句','shot_id':'shot-1','sentence':'这里是卧室'})
    assert error.value.status_code==422


def test_fact_confirmation_rejects_revision_changed_since_preview(scenario):
    # Catches bulk verification approving facts which changed after their checkbox was selected.
    from app.core import Confirmation, verify_fact
    db, ctx, _, f, *_ = scenario
    f.revision=2; f.status='unverified'; db.commit()
    with pytest.raises(HTTPException) as error:
        verify_fact('p','f',Confirmation(confirmed=True,expected_revision=1),ctx,db)
    assert error.value.status_code == 409
    assert f.status == 'unverified'


def test_fact_edit_rejects_revision_changed_since_candidate_selection(scenario):
    # Catches a batch candidate overwriting a colleague's newer verified source.
    from app.core import FactInput, update_fact
    db, ctx, _, f, *_ = scenario
    with pytest.raises(HTTPException) as error:
        update_fact('p','f',FactInput(label='面积',value='999',unit='㎡',source='旧候选',expected_revision=0),ctx,db)
    assert error.value.status_code == 409
    assert (f.value,f.source,f.status) == ('420','测绘文件第2页','verified')


def test_tasks_and_review_reject_project_access_outside_membership(scenario):
    from app.ux_tasks import project_tasks_data
    from app.ux_review import review_content_data, add_review_feedback
    from app.security import Context
    db, *_ = scenario
    restricted=Context(user_id='admin',tenant_id='tenant',role='admin',name='主管',email='admin@test.cn',project_ids=['elsewhere'])
    for call in (lambda:project_tasks_data(db,restricted,'p'),lambda:review_content_data(db,restricted,'p','c'),lambda:add_review_feedback(db,restricted,'p','c',{'note':'不能写'})):
        with pytest.raises(HTTPException) as error: call()
        assert error.value.status_code==404


def test_review_accepts_test_assets_only_for_explicit_test_projects(scenario):
    # Catches a review gate diverging from the existing project test-material policy.
    from app.ux_review import review_content_data
    db,ctx,project,_,asset,_=scenario
    asset.origin_type='template'; db.commit()
    assert review_content_data(db,ctx,'p','c')['assets'][0]['valid'] is False
    project.is_test=True; db.commit()
    assert review_content_data(db,ctx,'p','c')['assets'][0]['valid'] is True


def test_stale_batch_cannot_restore_revoked_asset_rights_or_enable_ai(scenario):
    # Catches authorization based on an old batch preview resurrecting a newer revocation.
    from app.core import RightsInput, asset_rights
    from app.core_models import Asset
    from app.job_models import Job
    from sqlalchemy.orm import Session
    db,ctx,_,_,asset,_=scenario
    loaded_revision=asset.rights_revision
    with Session(db.get_bind()) as other:
        changed=other.get(Asset,'a')
        changed.rights='revoked'; changed.rights_evidence='授权方已撤销'
        changed.rights_revision=2; changed.metadata_json={'model_processing_authorized':False}
        other.commit()
    with pytest.raises(HTTPException) as error:
        asset_rights('p','a',RightsInput(rights='authorized',evidence='旧窗口看到的授权',
            model_processing_authorized=True,expected_rights_revision=loaded_revision),ctx,db)
    assert error.value.status_code==409
    db.rollback(); db.expire_all()
    saved=db.get(Asset,'a')
    assert (saved.rights,saved.rights_revision,saved.metadata_json['model_processing_authorized'])==('revoked',2,False)
    assert db.scalars(select(Job)).all()==[]
