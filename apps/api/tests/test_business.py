"""Business invariants: invalid evidence cannot release, CRM withdrawals revoke access."""
from datetime import datetime, timedelta, timezone
import importlib
import hashlib
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


def test_missing_metric_window_is_unknown_not_zero():
    b = importlib.import_module('app.business')
    assert b.normalize_metrics(None) == {'D7': None, 'D30': None}
    assert b.normalize_metrics({'D7': {'views': 0}})['D7']['views'] == 0
    assert b.normalize_metrics({'D7': {'views': 0}})['D30'] is None


@pytest.fixture
def scenario(tmp_path,monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings,"storage_root",tmp_path)
    from app.db import Base
    from app.core_models import User, Tenant, Membership, Property, Fact, Asset
    from app.business_models import ContentVersion, Inquiry
    from app.security import Context
    import app.job_models
    eng = create_engine('sqlite://')
    Base.metadata.create_all(eng)
    db = Session(eng)
    db.add_all([Tenant(id='tenant', name='团队'), Tenant(id='other', name='另一团队'), User(id='admin', email='admin@test.cn', password_hash='unused',name='主管',active=True),User(id='sales',email='sales@test.cn',password_hash='unused',name='销售',active=True)])
    db.flush()
    db.add(Membership(id='m1',user_id='admin',tenant_id='tenant',role='admin',project_ids=None,active=True))
    p = Property(id='p',tenant_id='tenant',name='江岸',city='苏州',property_type='豪宅',owner_name='主管',audience='改善',sales_owner_name='销售',channel='抖音',marketing_confirmed=True,marketing_evidence='业主书面营销授权2026-10-12',is_test=False)
    f = Fact(id='f',tenant_id='tenant',property_id='p',semantic_key='area',label='面积',value='420',unit='㎡',source='测绘文件第2页',status='verified',revision=1,required=True)
    a = Asset(id='a',tenant_id='tenant',property_id='p',name='客厅实拍',kind='image',storage_key='a.jpg',mime_type='image/jpeg',sha256='abc',byte_size=3,rights='authorized',rights_evidence='摄制与用途授权书',origin_type='actual',metadata_json={})
    c = ContentVersion(id='c',tenant_id='tenant',property_id='p',series_id='series',version=1,topic='空间',script='420㎡实拍空间',fact_refs=[{'id':'f','revision':1}],storyboard=[{'asset_id':'a','duration':3,'caption':'420㎡空间'}],channel='抖音',cover_title='江岸',aspect_ratio='9:16',subtitle_style='minimal',status='draft',created_by='admin')
    db.add_all([p,f,a,c]); db.commit()
    ctx = Context(user_id='admin',tenant_id='tenant',role='admin',name='主管',email='admin@test.cn',project_ids=None)
    yield db, ctx, p, f, a, c
    db.close(); eng.dispose()


def rendered(scenario):
    from app.business import content_snapshot, snapshot_sha256
    from app.core_models import Asset
    db,ctx,p,f,a,c=scenario
    snapshot=content_snapshot(db,ctx,c)
    from app.config import settings
    payload=b'opaque rendered artifact; decode covered by media integration'
    (settings.storage_root/'render.mp4').write_bytes(payload)
    r=Asset(id='render',tenant_id='tenant',property_id='p',name='江岸.mp4',kind='video',storage_key='render.mp4',mime_type='video/mp4',sha256=hashlib.sha256(payload).hexdigest(),byte_size=len(payload),rights='authorized',rights_evidence='项目已授权素材渲染',origin_type='actual',metadata_json={'content_snapshot_sha256':snapshot_sha256(snapshot),'content_version_id':'c','renderer':'ffmpeg'})
    db.add(r); c.render_asset_id='render'; c.status='rendered'; db.commit()
    return c


def test_revision_invalidates_existing_release(scenario):
    from app.business import approve_content, export_content
    db,ctx,p,f,a,c=scenario; rendered(scenario)
    approve_content(db,ctx,'p','c',{'confirmed':True,'note':'已人工审核'})
    assert export_content(db,ctx,'p','c')['content']['id']=='c'
    f.revision=2; db.commit()
    with pytest.raises(HTTPException) as e: export_content(db,ctx,'p','c')
    assert e.value.status_code==409


def test_rights_revocation_invalidates_existing_release(scenario):
    from app.business import approve_content, export_content
    db,ctx,p,f,a,c=scenario; rendered(scenario)
    approve_content(db,ctx,'p','c',{'confirmed':True,'note':'审核通过'})
    a.rights='revoked'; db.commit()
    with pytest.raises(HTTPException): export_content(db,ctx,'p','c')


def test_expired_fact_blocks_approval_even_if_status_verified(scenario):
    from app.business import approve_content
    db,ctx,p,f,a,c=scenario; rendered(scenario)
    f.valid_until=datetime.now(timezone.utc)-timedelta(seconds=1); db.commit()
    with pytest.raises(HTTPException): approve_content(db,ctx,'p','c',{'confirmed':True})


def test_fake_render_metadata_does_not_bypass_stale_snapshot(scenario):
    from app.business import approve_content
    db,ctx,p,f,a,c=scenario; rendered(scenario)
    c.script='修改过的420㎡脚本'; db.commit()
    with pytest.raises(HTTPException): approve_content(db,ctx,'p','c',{'confirmed':True})


def test_approval_requires_human_confirmation(scenario):
    from app.business import approve_content
    db,ctx,p,f,a,c=scenario; rendered(scenario)
    with pytest.raises(HTTPException): approve_content(db,ctx,'p','c',{'confirmed':False})


def test_revision_creates_new_version_and_preserves_approved_script(scenario):
    from app.business import revise_content
    db,ctx,p,f,a,c=scenario
    result=revise_content(db,ctx,'p','c',{'script':'新的420㎡文案'})
    assert result['version']==2
    assert db.get(type(c),'c').script=='420㎡实拍空间'
    assert result['render_asset_id'] is None


def test_cross_tenant_content_not_readable(scenario):
    from app.business import export_content
    from app.security import Context
    db,ctx,p,f,a,c=scenario
    other=Context(user_id='admin',tenant_id='other',role='admin',name='主管',email='admin@test.cn',project_ids=None)
    with pytest.raises(HTTPException) as e: export_content(db,other,'p','c')
    assert e.value.status_code==404


def test_qualification_requires_all_four_checks_and_deduplicates(scenario):
    from app.business import create_inquiry, qualify_inquiry
    from app.business_models import Lead
    db,ctx,p,f,a,c=scenario
    inquiry=create_inquiry(db,ctx,'p',{'name':'王先生','need':'改善','contact':'13800138000','contactable':True,'consent':True})
    with pytest.raises(HTTPException): qualify_inquiry(db,ctx,'p',inquiry['id'],{'contactable':True,'consent':True,'demand_confirmed':True,'fit_confirmed':False})
    checks={'contactable':True,'consent':True,'demand_confirmed':True,'fit_confirmed':True}
    lead=qualify_inquiry(db,ctx,'p',inquiry['id'],checks)
    second=qualify_inquiry(db,ctx,'p',inquiry['id'],checks)
    assert lead['id']==second['id']
    assert len(db.scalars(select(Lead)).all())==1


def test_contact_is_encrypted_and_request_retry_is_not_duplicated(scenario):
    from app.business import create_inquiry
    from app.business_models import Inquiry
    db,ctx,p,f,a,c=scenario
    payload={'name':'王先生','need':'改善','contact':'13800138000','contactable':True,'consent':True,'request_key':'retry-1'}
    first=create_inquiry(db,ctx,'p',payload); second=create_inquiry(db,ctx,'p',payload)
    assert first['id']==second['id']
    assert '13800138000' not in first.get('contact','')
    record=db.get(Inquiry,first['id'])
    assert record.contact_cipher!='13800138000'


def test_withdrawal_removes_contact_and_revokes_lead_followup(scenario):
    from app.business import create_inquiry, qualify_inquiry, withdraw_inquiry, add_followup, list_leads_data
    from app.business_models import Inquiry, Lead
    db,ctx,p,f,a,c=scenario
    i=create_inquiry(db,ctx,'p',{'name':'王先生','contact':'13800138000','contactable':True,'consent':True})
    l=qualify_inquiry(db,ctx,'p',i['id'],{'contactable':True,'consent':True,'demand_confirmed':True,'fit_confirmed':True})
    withdraw_inquiry(db,ctx,'p',i['id'])
    assert db.get(Inquiry,i['id']).contact_cipher is None
    assert db.get(Lead,l['id']).stage=='withdrawn'
    assert list_leads_data(db,ctx,'p')==[]
    with pytest.raises(HTTPException): add_followup(db,ctx,'p',l['id'],{'note':'再次联系','next_action':'致电'})


def test_sales_cannot_read_or_assign_another_sales_lead(scenario):
    from app.business import create_inquiry, qualify_inquiry, list_leads_data, lead_detail_data
    from app.security import Context
    db,ctx,p,f,a,c=scenario
    i=create_inquiry(db,ctx,'p',{'name':'王先生','contact':'13800138000','contactable':True,'consent':True})
    l=qualify_inquiry(db,ctx,'p',i['id'],{'contactable':True,'consent':True,'demand_confirmed':True,'fit_confirmed':True})
    sales=Context(user_id='sales',tenant_id='tenant',role='sales',name='销售',email='sales@test.cn',project_ids=None)
    assert list_leads_data(db,sales,'p')==[]
    with pytest.raises(HTTPException) as e: lead_detail_data(db,sales,'p',l['id'])
    assert e.value.status_code==404


def test_stage_skipping_rejected_but_appointment_then_visit_allowed(scenario):
    from app.business import create_inquiry, qualify_inquiry, change_stage
    db,ctx,p,f,a,c=scenario
    i=create_inquiry(db,ctx,'p',{'name':'王先生','contact':'13800138000','contactable':True,'consent':True})
    l=qualify_inquiry(db,ctx,'p',i['id'],{'contactable':True,'consent':True,'demand_confirmed':True,'fit_confirmed':True})
    with pytest.raises(HTTPException): change_stage(db,ctx,'p',l['id'],{'stage':'visit_completed'})
    assert change_stage(db,ctx,'p',l['id'],{'stage':'appointment'})['stage']=='appointment'
    assert change_stage(db,ctx,'p',l['id'],{'stage':'visit_completed'})['stage']=='visit_completed'


def test_only_verified_live_publications_count_and_missing_d30_stays_unknown(scenario):
    from app.business import overview_data, reports_data
    from app.business_models import Publication
    db,ctx,p,f,a,c=scenario
    db.add_all([Publication(id='pub1',tenant_id='tenant',property_id='p',release_id='r',title='可见',channel='抖音',url='https://www.douyin.com/video/1',platform_state='live',receipt_status='verified',metrics={'D7':{'views':100}}),Publication(id='pub2',tenant_id='tenant',property_id='p',release_id='r',title='已下架',channel='抖音',url='https://www.douyin.com/video/2',platform_state='removed',receipt_status='verified',metrics={'D7':{'views':900}})])
    db.commit()
    assert overview_data(db,ctx,'p')['metrics']['published']==1
    report=reports_data(db,ctx,'p','D30')
    assert report['metrics']['views'] is None
    assert report['metrics']['window_complete'] is False


def test_generated_fact_refs_reject_unverified_or_stale_fact(scenario):
    from app.business import validate_generated_content
    db,ctx,p,f,a,c=scenario
    f.status='unverified'; db.commit()
    with pytest.raises(ValueError): validate_generated_content(db,ctx,'p',{'script':'420㎡文案','fact_refs':[{'id':'f','revision':1}],'storyboard':[]})


def test_empty_metric_object_is_not_an_observed_window():
    from app.business import normalize_metrics
    assert normalize_metrics({'D7':{}})['D7'] is None


def test_generated_storyboard_rejects_unprovided_duration_and_template_asset(scenario):
    from app.business import validate_generated_content
    db,ctx,p,f,a,c=scenario
    payload={'script':'420㎡文案','fact_refs':[{'id':'f','revision':1}],'storyboard':[{'asset_id':'a','duration':-1,'caption':'空间'}]}
    with pytest.raises(ValueError): validate_generated_content(db,ctx,'p',payload)
    a.origin_type='template'; db.commit()
    payload['storyboard'][0]['duration']=15
    with pytest.raises(ValueError): validate_generated_content(db,ctx,'p',payload)


def test_generated_content_rejects_unsupported_unit_even_if_numeric_value_exists(scenario):
    from app.business import validate_generated_content
    db,ctx,p,f,a,c=scenario
    with pytest.raises(ValueError): validate_generated_content(db,ctx,'p',{'script':'报价420万元','fact_refs':[{'id':'f','revision':1}],'storyboard':[]})


def test_rights_revision_change_blocks_export_even_if_still_authorized(scenario):
    from app.business import approve_content, export_content
    db,ctx,p,f,a,c=scenario; rendered(scenario)
    approve_content(db,ctx,'p','c',{'confirmed':True})
    a.rights_revision=2; a.rights_evidence='新的授权范围'; db.commit()
    with pytest.raises(HTTPException): export_content(db,ctx,'p','c')


def test_followup_updates_next_action_and_keeps_history(scenario):
    from app.business import create_inquiry, qualify_inquiry, add_followup, lead_detail_data
    db,ctx,p,f,a,c=scenario
    i=create_inquiry(db,ctx,'p',{'name':'王先生','contact':'13800138000','contactable':True,'consent':True})
    l=qualify_inquiry(db,ctx,'p',i['id'],{'contactable':True,'consent':True,'demand_confirmed':True,'fit_confirmed':True})
    add_followup(db,ctx,'p',l['id'],{'note':'客户希望看庭院','next_action':'周六预约'})
    add_followup(db,ctx,'p',l['id'],{'note':'预约已确认','next_action':'发送到访位置'})
    detail=lead_detail_data(db,ctx,'p',l['id'])
    assert detail['lead']['next_action']=='发送到访位置'
    assert {x['note'] for x in detail['followups']}=={'客户希望看庭院','预约已确认'}


def test_unrecognized_window_raises_instead_of_silently_using_d7(scenario):
    from app.business import reports_data
    db,ctx,p,f,a,c=scenario
    with pytest.raises(HTTPException) as e: reports_data(db,ctx,'p','D90')
    assert e.value.status_code==422


def test_content_listing_exposes_only_current_valid_release_id(scenario):
    from app.business import approve_content, contents
    db,ctx,p,f,a,c=scenario; rendered(scenario)
    released=approve_content(db,ctx,'p','c',{'confirmed':True})
    row=contents('p',db,ctx)[0]
    assert row['release_id']==released['id']
    assert row['approval_valid'] is True
    a.rights_revision+=1; db.flush()
    row=contents('p',db,ctx)[0]
    assert row['release_id'] is None
    assert row['approval_valid'] is False


def test_database_uniqueness_prevents_concurrent_request_retry(scenario):
    from app.business import create_inquiry
    from app.business_models import Inquiry
    from sqlalchemy.exc import IntegrityError
    db,ctx,p,f,a,c=scenario
    first=create_inquiry(db,ctx,'p',{'name':'王先生','contact':'13800138000','contactable':True,'consent':True,'request_key':'retry-1'})
    original=db.get(Inquiry,first['id'])
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            duplicate=Inquiry(tenant_id='tenant',property_id='p',name='第二个请求',request_key=original.request_key,owner_id='admin')
            db.add(duplicate); db.flush()


@pytest.mark.parametrize('incoming,want',[('confirmed','confirmed'),('self_reported','self_reported'),('inferred','inferred'),('unknown','unknown'),('verified','confirmed'),('unverified','inferred')])
def test_source_confidence_preserves_prd_enum_and_normalizes_legacy_inputs(scenario,incoming,want):
    from app.business import create_inquiry
    db,ctx,p,f,a,c=scenario
    dto=create_inquiry(db,ctx,'p',{'name':'来源测试','source_confidence':incoming,'source_text':'平台咨询原始记录'})
    assert dto['source_confidence']==want


def test_owner_has_admin_approval_and_crm_capabilities(scenario):
    from app.business import create_inquiry, approve_content
    from app.core_models import Membership
    from app.security import Context
    db,ctx,p,f,a,c=scenario; rendered(scenario)
    db.get(Membership,'m1').role='owner'; db.flush()
    owner=Context(user_id='admin',tenant_id='tenant',role='owner',name='主管',email='admin@test.cn',project_ids=None)
    assert create_inquiry(db,owner,'p',{'name':'业主权限测试'})['owner_id']=='admin'
    assert approve_content(db,owner,'p','c',{'confirmed':True})['status']=='approved'


def test_contact_reveal_requires_explicit_purpose_and_authorized_actor(scenario):
    from app.business import create_inquiry, reveal_contact
    from app.security import Context
    db,ctx,p,f,a,c=scenario
    i=create_inquiry(db,ctx,'p',{'name':'王先生','contact':'13800138000','contactable':True,'consent':True})
    with pytest.raises(HTTPException): reveal_contact(db,ctx,'p',i['id'],{'purpose':''})
    assert reveal_contact(db,ctx,'p',i['id'],{'purpose':'本次咨询服务'})=={'contact':'13800138000'}
    viewer=Context(user_id='admin',tenant_id='tenant',role='content',name='内容负责人',email='admin@test.cn',project_ids=None)
    with pytest.raises(HTTPException) as e: reveal_contact(db,viewer,'p',i['id'],{'purpose':'本次咨询服务'})
    assert e.value.status_code==403
    sales=Context(user_id='sales',tenant_id='tenant',role='sales',name='其他销售',email='sales@test.cn',project_ids=None)
    with pytest.raises(HTTPException) as e: reveal_contact(db,sales,'p',i['id'],{'purpose':'本次咨询服务'})
    assert e.value.status_code==404


def test_contact_reveal_denies_withdrawn_consent(scenario):
    from app.business import create_inquiry, reveal_contact, withdraw_inquiry
    db,ctx,p,f,a,c=scenario
    i=create_inquiry(db,ctx,'p',{'name':'王先生','contact':'13800138000','contactable':True,'consent':True})
    withdraw_inquiry(db,ctx,'p',i['id'])
    with pytest.raises(HTTPException) as e: reveal_contact(db,ctx,'p',i['id'],{'purpose':'本次咨询服务'})
    assert e.value.status_code==409


def test_missing_render_file_blocks_approval(scenario):
    from app.business import approve_content
    from app.config import settings
    db,ctx,p,f,a,c=scenario; rendered(scenario)
    (settings.storage_root/'render.mp4').unlink()
    with pytest.raises(HTTPException) as e: approve_content(db,ctx,'p','c',{'confirmed':True})
    assert e.value.status_code==409


def test_corrupted_render_file_blocks_export(scenario):
    from app.business import approve_content, export_content
    from app.config import settings
    db,ctx,p,f,a,c=scenario; rendered(scenario)
    approve_content(db,ctx,'p','c',{'confirmed':True})
    path=settings.storage_root/'render.mp4'
    original=path.read_bytes()
    path.write_bytes(b'x'*len(original))
    with pytest.raises(HTTPException) as e: export_content(db,ctx,'p','c')
    assert e.value.status_code==409


@pytest.mark.parametrize('visible_field',['cover_title','caption'])
def test_generated_final_visible_text_rejects_uncited_quantity(scenario,visible_field):
    from app.business import validate_generated_content
    db,ctx,p,f,a,c=scenario
    payload={'script':'420㎡实拍空间','cover_title':'江岸空间','fact_refs':[{'id':'f','revision':1}],'storyboard':[{'asset_id':'a','duration':15,'caption':'420㎡空间','start':0}]}
    if visible_field=='cover_title': payload['cover_title']='总价1万元'
    else: payload['storyboard'][0]['caption']='999㎡超大空间'
    with pytest.raises(ValueError): validate_generated_content(db,ctx,'p',payload)


@pytest.mark.parametrize('visible_field',['cover_title','caption'])
def test_manual_revision_cannot_render_unverified_visible_quantities(scenario,visible_field):
    from app.business import revise_content, validate_content_ready
    from app.business_models import ContentVersion
    db,ctx,p,f,a,c=scenario
    changes={'script':'420㎡实拍空间','cover_title':'江岸空间','storyboard':[{'asset_id':'a','duration':15,'caption':'420㎡空间','start':0}]}
    if visible_field=='cover_title': changes['cover_title']='总价1万元'
    else: changes['storyboard'][0]['caption']='999㎡超大空间'
    dto=revise_content(db,ctx,'p','c',changes)
    revised=db.get(ContentVersion,dto['id'])
    with pytest.raises(HTTPException) as e: validate_content_ready(db,ctx,revised)
    assert e.value.status_code==409


def test_all_supported_final_visible_quantities_remain_valid(scenario):
    from app.business import validate_generated_content, validate_content_ready
    db,ctx,p,f,a,c=scenario
    payload={'script':'420㎡实拍空间','cover_title':'420㎡空间','fact_refs':[{'id':'f','revision':1}],'storyboard':[{'asset_id':'a','duration':15,'caption':'420平方米空间','start':0}]}
    assert validate_generated_content(db,ctx,'p',payload)['cover_title']=='420㎡空间'
    c.cover_title=payload['cover_title']; c.storyboard=payload['storyboard']; db.flush()
    assert validate_content_ready(db,ctx,c)['cover_title']=='420㎡空间'


def test_same_contact_different_people_are_separate_inquiries(scenario):
    from app.business import create_inquiry
    from app.business_models import Inquiry
    db,ctx,p,f,a,c=scenario
    first=create_inquiry(db,ctx,'p',{'name':'王先生','contact':'13800138000','contactable':True,'consent':True})
    second=create_inquiry(db,ctx,'p',{'name':'李女士','contact':'13800138000','contactable':True,'consent':True})
    assert first['id']!=second['id']
    assert second['name']=='李女士'
    assert len(db.scalars(select(Inquiry)).all())==2


def test_request_key_changed_payload_conflicts_without_merging(scenario):
    from app.business import create_inquiry
    from app.business_models import Inquiry
    db,ctx,p,f,a,c=scenario
    body={'name':'王先生','need':'庭院','contact':'13800138000','contactable':True,'consent':True,'request_key':'create-01'}
    first=create_inquiry(db,ctx,'p',body)
    with pytest.raises(HTTPException) as e: create_inquiry(db,ctx,'p',body|{'name':'李女士'})
    assert e.value.status_code==409
    assert db.get(Inquiry,first['id']).name=='王先生'
    assert len(db.scalars(select(Inquiry)).all())==1


def test_manual_same_person_link_reuses_lead_and_records_qualified_inquiry(scenario):
    from app.business import create_inquiry, qualify_inquiry
    from app.business_models import Inquiry, Lead
    db,ctx,p,f,a,c=scenario
    checks={'contactable':True,'consent':True,'demand_confirmed':True,'fit_confirmed':True}
    first=create_inquiry(db,ctx,'p',{'name':'王先生','contact':'13800138000','contactable':True,'consent':True})
    lead=qualify_inquiry(db,ctx,'p',first['id'],checks)
    second=create_inquiry(db,ctx,'p',{'name':'王先生再次咨询','contact':'13800138000','contactable':True,'consent':True})
    with pytest.raises(HTTPException): qualify_inquiry(db,ctx,'p',second['id'],checks|{'existing_lead_id':lead['id']})
    linked=qualify_inquiry(db,ctx,'p',second['id'],checks|{'existing_lead_id':lead['id'],'same_contact_confirmed':True})
    assert linked['id']==lead['id']
    stored=db.get(Inquiry,second['id'])
    assert stored.qualified is True
    assert stored.linked_lead_id==lead['id']
    assert stored.qualification_checks['same_contact_confirmed'] is True
    assert len(db.scalars(select(Lead)).all())==1


def test_unlinked_same_contact_creates_distinct_leads(scenario):
    from app.business import create_inquiry, qualify_inquiry
    db,ctx,p,f,a,c=scenario
    checks={'contactable':True,'consent':True,'demand_confirmed':True,'fit_confirmed':True}
    first=create_inquiry(db,ctx,'p',{'name':'王先生','contact':'13800138000','contactable':True,'consent':True})
    second=create_inquiry(db,ctx,'p',{'name':'李女士','contact':'13800138000','contactable':True,'consent':True})
    l1=qualify_inquiry(db,ctx,'p',first['id'],checks)
    l2=qualify_inquiry(db,ctx,'p',second['id'],checks)
    assert l1['id']!=l2['id']


def test_manual_link_cannot_target_other_sales_or_other_project(scenario):
    from app.business import create_inquiry, qualify_inquiry
    from app.core_models import Membership, Property
    from app.security import Context
    db,ctx,p,f,a,c=scenario
    checks={'contactable':True,'consent':True,'demand_confirmed':True,'fit_confirmed':True}
    first=create_inquiry(db,ctx,'p',{'name':'王先生','contact':'13800138000','contactable':True,'consent':True})
    lead=qualify_inquiry(db,ctx,'p',first['id'],checks)
    db.add(Membership(id='ms',user_id='sales',tenant_id='tenant',role='sales',project_ids=None,active=True)); db.flush()
    sales=Context(user_id='sales',tenant_id='tenant',role='sales',name='销售',email='sales@test.cn',project_ids=None)
    mine=create_inquiry(db,sales,'p',{'name':'我的咨询','contact':'13900139000','contactable':True,'consent':True})
    with pytest.raises(HTTPException) as e: qualify_inquiry(db,sales,'p',mine['id'],checks|{'existing_lead_id':lead['id'],'same_contact_confirmed':True})
    assert e.value.status_code==404
    p2=Property(id='p2',tenant_id='tenant',name='另一项目',city='上海'); db.add(p2); db.flush()
    inquiry2=create_inquiry(db,ctx,'p2',{'name':'跨项目咨询','contact':'13800138000','contactable':True,'consent':True})
    with pytest.raises(HTTPException) as e: qualify_inquiry(db,ctx,'p2',inquiry2['id'],checks|{'existing_lead_id':lead['id'],'same_contact_confirmed':True})
    assert e.value.status_code==404


def test_withdrawal_revokes_primary_and_all_manually_linked_inquiries(scenario):
    from app.business import create_inquiry, qualify_inquiry, withdraw_inquiry, reveal_contact, list_leads_data
    from app.business_models import Inquiry, Lead
    db,ctx,p,f,a,c=scenario
    checks={'contactable':True,'consent':True,'demand_confirmed':True,'fit_confirmed':True}
    first=create_inquiry(db,ctx,'p',{'name':'王先生','contact':'13800138000','contactable':True,'consent':True})
    lead=qualify_inquiry(db,ctx,'p',first['id'],checks)
    second=create_inquiry(db,ctx,'p',{'name':'王先生再次咨询','contact':'13800138000','contactable':True,'consent':True})
    qualify_inquiry(db,ctx,'p',second['id'],checks|{'existing_lead_id':lead['id'],'same_contact_confirmed':True})
    withdraw_inquiry(db,ctx,'p',second['id'])
    assert db.get(Lead,lead['id']).stage=='withdrawn'
    for iid in (first['id'],second['id']):
        stored=db.get(Inquiry,iid)
        assert stored.withdrawn is True
        assert stored.contact_cipher is None
        with pytest.raises(HTTPException): reveal_contact(db,ctx,'p',iid,{'purpose':'本次咨询服务'})
    assert list_leads_data(db,ctx,'p')==[]
