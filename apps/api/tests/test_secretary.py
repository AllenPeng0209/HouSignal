"""Native CRM capture: no invented consent, no plaintext contacts, durable viewings."""
from datetime import datetime, timezone
import importlib
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


@pytest.fixture
def workspace():
    """Build real scoped records after the service registers its models."""
    sessions=[]
    def create():
        from app.db import Base
        from app.core_models import Tenant, User, Membership, Property
        from app.security import Context
        engine=create_engine('sqlite://')
        Base.metadata.create_all(engine)
        db=Session(engine)
        sessions.append((db,engine))
        db.add_all([Tenant(id='t',name='团队'),Tenant(id='other',name='其他团队'),User(id='admin',name='主管',email='admin@test.cn',password_hash='unused',active=True),User(id='sales',name='销售',email='sales@test.cn',password_hash='unused',active=True)])
        db.flush()
        db.add_all([Membership(id='ma',tenant_id='t',user_id='admin',role='admin',active=True),Membership(id='ms',tenant_id='t',user_id='sales',role='sales',active=True),Property(id='p',tenant_id='t',name='江岸',city='上海'),Property(id='p2',tenant_id='t',name='别墅',city='苏州')])
        db.commit()
        return db,Context(user_id='admin',tenant_id='t',role='admin',name='主管',email='admin@test.cn')
    yield create
    for db,engine in sessions:
        db.close();engine.dispose()


def secretary():
    return importlib.import_module('app.secretary')


def capture(s,db,ctx,name='彭先生'):
    return s.execute_secretary_tool(db,ctx,'p','create_customer',{'name':name,'need':'看江景住宅'})['data']


def test_minimal_customer_is_visible_draft_without_fabricated_qualification(workspace):
    s=secretary();db,ctx=workspace()
    from app.business_models import Inquiry,Lead
    from app.business import list_leads_data,lead_detail_data
    data=capture(s,db,ctx)
    lead=db.get(Lead,data['lead']['id']);inquiry=db.get(Inquiry,lead.inquiry_id)
    assert lead.stage=='captured'
    assert inquiry.consent is False
    assert inquiry.contactable is False
    assert inquiry.qualified is False
    assert inquiry.qualification_checks=={}
    assert inquiry.contact_cipher is None
    assert list_leads_data(db,ctx,'p')[0]['id']==lead.id
    assert lead_detail_data(db,ctx,'p',lead.id)['profile']['background']==''


def test_profile_patch_preserves_other_fields_and_records_source(workspace):
    s=secretary();db,ctx=workspace();lid=capture(s,db,ctx)['lead']['id']
    s.execute_secretary_tool(db,ctx,'p','update_customer',{'lead_id':lid,'background':'科技企业创业者','budget':'5000万至8000万元','preferences':'江景、安静','source_note':'经纪人本次面谈记录'})
    changed=s.execute_secretary_tool(db,ctx,'p','update_customer',{'lead_id':lid,'preferences':'江景、安静、带书房'})['data']
    assert changed['profile']['budget']=='5000万至8000万元'
    assert changed['profile']['background']=='科技企业创业者'
    assert changed['profile']['preferences']=='江景、安静、带书房'
    assert changed['profile']['financial_situation']==''
    assert changed['profile']['provenance']['budget']['source_note']=='经纪人本次面谈记录'
    assert changed['profile']['provenance']['budget']['recorded_by']=='admin'


def test_contact_extraction_requires_explicit_consent_and_stays_encrypted(workspace):
    s=secretary();db,ctx=workspace();lid=capture(s,db,ctx)['lead']['id']
    from app.business_models import Inquiry,Lead
    from app.security import decrypt_pii
    with pytest.raises(HTTPException) as e:
        s.execute_secretary_tool(db,ctx,'p','update_customer',{'lead_id':lid,'contact_from_message':True},raw_text='彭先生电话13800138000，请记录')
    assert e.value.status_code==422
    result=s.execute_secretary_tool(db,ctx,'p','update_customer',{'lead_id':lid,'contact_from_message':True,'consent_contact':True,'notes':'联系电话13800138000'},raw_text='彭先生电话13800138000，客户已同意后续联系')
    inquiry=db.get(Inquiry,db.get(Lead,lid).inquiry_id)
    assert decrypt_pii(inquiry.contact_cipher)=='13800138000'
    assert inquiry.contactable is True
    assert inquiry.consent is True
    assert inquiry.qualified is False
    assert '13800138000' not in json.dumps(result,ensure_ascii=False)
    assert '13800138000' not in json.dumps(s.profile_data(db,ctx,'p',lid),ensure_ascii=False)


def test_multiple_contacts_require_clarification_instead_of_guess(workspace):
    s=secretary();db,ctx=workspace();lid=capture(s,db,ctx)['lead']['id']
    with pytest.raises(HTTPException) as e:
        s.execute_secretary_tool(db,ctx,'p','update_customer',{'lead_id':lid,'contact_from_message':True,'consent_contact':True},raw_text='彭先生13800138000，助理13900139000，请记录')
    assert e.value.status_code==422


def test_search_and_profile_enforce_sales_owner_project_and_tenant_scope(workspace):
    s=secretary();db,ctx=workspace();lid=capture(s,db,ctx)['lead']['id']
    from app.security import Context
    sales=Context(user_id='sales',tenant_id='t',role='sales',name='销售',email='sales@test.cn')
    assert s.execute_secretary_tool(db,sales,'p','search_customers',{'query':'彭'})['data']==[]
    for actor,pid in [(sales,'p'),(ctx,'p2'),(Context(user_id='admin',tenant_id='other',role='admin',name='主管',email='admin@test.cn'),'p')]:
        with pytest.raises(HTTPException) as e:s.profile_data(db,actor,pid,lid)
        assert e.value.status_code==404


def test_withdrawn_customer_cannot_be_searched_or_changed(workspace):
    s=secretary();db,ctx=workspace();lid=capture(s,db,ctx)['lead']['id']
    from app.business import withdraw_inquiry
    from app.business_models import Lead
    withdraw_inquiry(db,ctx,'p',db.get(Lead,lid).inquiry_id)
    assert s.execute_secretary_tool(db,ctx,'p','search_customers',{'query':'彭'})['data']==[]
    with pytest.raises(HTTPException) as e:s.execute_secretary_tool(db,ctx,'p','update_customer',{'lead_id':lid,'background':'继续跟进'})
    assert e.value.status_code==409


def test_next_wednesday_afternoon_is_a_date_window_without_invented_time(workspace,monkeypatch):
    s=secretary();db,ctx=workspace();lid=capture(s,db,ctx)['lead']['id']
    monkeypatch.setattr(s,'now',lambda:datetime(2026,10,15,3,0,tzinfo=timezone.utc))
    result=s.execute_secretary_tool(db,ctx,'p','schedule_viewing',{'lead_id':lid,'date_text':'下周三下午'})['data']
    assert result['date']=='2026-10-21'
    assert result['time_window']=='下午'
    assert result['starts_at'] is None
    assert result['location']==''
    assert result['status']=='pending_details'
    assert result['missing_details']==['time','location']
    assert '具体' in result['clarification']
    assert result['timezone']=='Asia/Shanghai'
    db.commit()
    assert s.list_viewings_data(db,ctx,'p',lid)[0]['id']==result['id']


def test_calendar_relative_date_uses_shanghai_day_not_utc(workspace,monkeypatch):
    s=secretary();db,ctx=workspace();lid=capture(s,db,ctx)['lead']['id']
    monkeypatch.setattr(s,'now',lambda:datetime(2026,10,18,17,0,tzinfo=timezone.utc))
    result=s.execute_secretary_tool(db,ctx,'p','schedule_viewing',{'lead_id':lid,'date_text':'下周三下午'})['data']
    assert result['date']=='2026-10-28'


def test_viewing_update_keeps_location_and_rejects_foreign_customer(workspace):
    s=secretary();db,ctx=workspace();lid=capture(s,db,ctx)['lead']['id']
    data=s.execute_secretary_tool(db,ctx,'p','schedule_viewing',{'lead_id':lid,'date':'2026-10-21','time_window':'下午','location':'江岸售楼处'})['data']
    updated=s.execute_secretary_tool(db,ctx,'p','update_viewing',{'viewing_id':data['id'],'starts_at':'2026-10-21T14:30:00+08:00'})['data']
    assert updated['location']=='江岸售楼处'
    assert updated['status']=='scheduled'
    assert updated['missing_details']==[]
    assert updated['starts_at']=='2026-10-21T14:30:00+08:00'
    with pytest.raises(HTTPException) as e:s.execute_secretary_tool(db,ctx,'p2','schedule_viewing',{'lead_id':lid,'date':'2026-10-22'})
    assert e.value.status_code==404


def test_followup_capture_preserves_next_action_when_not_provided(workspace):
    s=secretary();db,ctx=workspace();lid=capture(s,db,ctx)['lead']['id']
    s.execute_secretary_tool(db,ctx,'p','record_followup',{'lead_id':lid,'note':'彭先生希望先看江景','next_action':'约周三下午'})
    s.execute_secretary_tool(db,ctx,'p','record_followup',{'lead_id':lid,'note':'配偶偏好大书房'})
    from app.business import lead_detail_data
    detail=lead_detail_data(db,ctx,'p',lid)
    assert detail['lead']['next_action']=='约周三下午'
    assert {x['note'] for x in detail['followups']}=={'彭先生希望先看江景','配偶偏好大书房'}


def test_tools_leave_transaction_to_caller_and_audit_fields_without_contact(workspace):
    s=secretary();db,ctx=workspace()
    from app.business_models import Lead
    from app.audit_models import AuditEvent
    lid=capture(s,db,ctx)['lead']['id']
    s.execute_secretary_tool(db,ctx,'p','update_customer',{'lead_id':lid,'financial_situation':'客户自述现金充裕'})
    audits=db.scalars(select(AuditEvent)).all()
    assert any(x.action=='secretary.update_customer' and 'financial_situation' in x.details['fields'] for x in audits)
    db.rollback()
    assert db.get(Lead,lid) is None


def test_captured_customer_does_not_count_as_qualified_business_result(workspace):
    s=secretary();db,ctx=workspace();capture(s,db,ctx)
    from app.business import overview_data
    assert overview_data(db,ctx,'p')['metrics']['qualified']==0


def test_unknown_profile_field_fails_instead_of_silently_discarding_input(workspace):
    s=secretary();db,ctx=workspace();lid=capture(s,db,ctx)['lead']['id']
    with pytest.raises(HTTPException) as e:s.update_customer_data(db,ctx,'p',lid,{'finances':'不应该被忽略'})
    assert e.value.status_code==422


def test_mutation_sources_link_to_authorized_customer_and_not_generic_crm(workspace):
    s=secretary();db,ctx=workspace()
    result=s.execute_secretary_tool(db,ctx,'p','create_customer',{'name':'彭先生'})
    assert result['sources'][0]['kind']=='lead'
    assert result['sources'][0]['id']==result['data']['lead']['id']
    assert result['sources'][0]['label']=='彭先生'


def test_model_boolean_cannot_invent_contact_consent_without_user_statement(workspace):
    s=secretary();db,ctx=workspace();lid=capture(s,db,ctx)['lead']['id']
    from app.business_models import Lead,Inquiry
    with pytest.raises(HTTPException) as e:
        s.execute_secretary_tool(db,ctx,'p','update_customer',{'lead_id':lid,'contact_from_message':True,'consent_contact':True},raw_text='彭先生电话13800138000，存到CRM里')
    assert e.value.status_code==422
    inquiry=db.get(Inquiry,db.get(Lead,lid).inquiry_id)
    assert inquiry.contact_cipher is None
    assert inquiry.consent is False


@pytest.mark.parametrize('patch',[{'time_window':'下午'},{'date_text':'2026-10-21下午'}])
def test_changing_same_day_period_clears_old_morning_exact_time(workspace,patch):
    s=secretary();db,ctx=workspace();lid=capture(s,db,ctx)['lead']['id']
    first=s.schedule_viewing_data(db,ctx,'p',{'lead_id':lid,'date':'2026-10-21','time_window':'上午','starts_at':'2026-10-21T10:30:00+08:00','location':'江岸售楼处'})
    changed=s.update_viewing_data(db,ctx,'p',first['id'],patch)
    assert changed['date']=='2026-10-21'
    assert changed['time_window']=='下午'
    assert changed['starts_at'] is None
    assert changed['status']=='pending_details'
    assert changed['missing_details']==['time']


def test_withdrawing_unqualified_capture_does_not_inflate_qualified_metric(workspace):
    s=secretary();db,ctx=workspace();lid=capture(s,db,ctx)['lead']['id']
    from app.business import overview_data,withdraw_inquiry
    from app.business_models import Lead
    withdraw_inquiry(db,ctx,'p',db.get(Lead,lid).inquiry_id)
    assert overview_data(db,ctx,'p')['metrics']['qualified']==0


@pytest.fixture
def postgres_workspace():
    """Opt-in real PostgreSQL isolation; never writes application-schema records."""
    import os
    from uuid import uuid4
    from sqlalchemy import text
    if os.environ.get('LOUXIANG_TEST_POSTGRES')!='1':pytest.skip('set LOUXIANG_TEST_POSTGRES=1 for database concurrency coverage')
    s=secretary()
    from app.config import settings
    from app.db import Base
    from app.core_models import Tenant,User,Membership,Property
    from app.security import Context
    url=settings.migration_database_url
    if not url.startswith('postgresql'):pytest.skip('PostgreSQL migration connection required')
    schema='secretary_test_'+uuid4().hex[:16]
    engine=create_engine(url,hide_parameters=True,execution_options={'schema_translate_map':{None:schema}})
    with engine.begin() as connection:connection.execute(text(f'CREATE SCHEMA {schema}'))
    try:
        Base.metadata.create_all(engine)
        ctx=Context(user_id='admin',tenant_id='t',role='admin',name='主管',email='admin@test.cn')
        with Session(engine) as db:
            db.add_all([Tenant(id='t',name='团队'),User(id='admin',name='主管',email='admin@test.cn',password_hash='unused',active=True)])
            db.flush()
            db.add_all([Membership(id='ma',tenant_id='t',user_id='admin',role='admin',active=True),Property(id='p',tenant_id='t',name='江岸',city='上海')])
            db.flush();lid=capture(s,db,ctx)['lead']['id'];db.commit()
        yield s,engine,ctx,lid
    finally:
        with engine.begin() as connection:connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        engine.dispose()


def test_postgres_stale_profile_session_preserves_other_committed_fields(postgres_workspace):
    s,engine,ctx,lid=postgres_workspace
    from app.secretary_models import CustomerProfile
    with Session(engine) as first,Session(engine) as second:
        cached=second.scalar(select(CustomerProfile).where(CustomerProfile.lead_id==lid))
        assert cached is not None
        s.update_customer_data(first,ctx,'p',lid,{'budget':'8000万元'});first.commit()
        result=s.update_customer_data(second,ctx,'p',lid,{'preferences':'江景'});second.commit()
        assert result['profile']['budget']=='8000万元'
    with Session(engine) as db:
        profile=s.profile_data(db,ctx,'p',lid)
        assert profile['budget']=='8000万元'
        assert profile['preferences']=='江景'


def test_postgres_concurrent_first_profile_creation_merges_both_patches(postgres_workspace):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from sqlalchemy import delete
    from app.secretary_models import CustomerProfile
    s,engine,ctx,lid=postgres_workspace
    with Session(engine) as setup:
        setup.execute(delete(CustomerProfile).where(CustomerProfile.lead_id==lid));setup.commit()
    started=Event();finished=Event()
    def second_update():
        with Session(engine) as db:
            started.set()
            try:
                result=s.update_customer_data(db,ctx,'p',lid,{'preferences':'带书房'})
                db.commit();return result
            finally:finished.set()
    with ThreadPoolExecutor(max_workers=1) as pool,Session(engine) as first:
        s.update_customer_data(first,ctx,'p',lid,{'budget':'6000万元'})
        future=pool.submit(second_update)
        try:
            assert started.wait(2)
            assert not finished.wait(.15)
        finally:first.commit()
        future.result(timeout=5)
    with Session(engine) as db:
        profile=s.profile_data(db,ctx,'p',lid)
        assert profile['budget']=='6000万元'
        assert profile['preferences']=='带书房'


def test_postgres_viewing_patch_reloads_current_location_before_receipt(postgres_workspace):
    s,engine,ctx,lid=postgres_workspace
    from app.secretary_models import ViewingAppointment
    with Session(engine) as setup:
        vid=s.schedule_viewing_data(setup,ctx,'p',{'lead_id':lid,'date':'2026-10-21','location':'原地点'})['id'];setup.commit()
    with Session(engine) as first,Session(engine) as second:
        cached=second.get(ViewingAppointment,vid)
        assert cached.location=='原地点'
        s.update_viewing_data(first,ctx,'p',vid,{'location':'江岸售楼处'});first.commit()
        result=s.update_viewing_data(second,ctx,'p',vid,{'notes':'带上户型资料'});second.commit()
        assert result['location']=='江岸售楼处'


@pytest.mark.parametrize('revocation,expected_status',[('owner',404),('consent',409)])
def test_postgres_waiting_write_rechecks_changed_owner_and_contact_consent(postgres_workspace,revocation,expected_status):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from app.business_models import Inquiry,Lead
    from app.core_models import User,Membership
    from app.security import Context
    s,engine,ctx,lid=postgres_workspace
    with Session(engine) as setup:
        setup.add(User(id='sales',name='销售',email='sales@test.cn',password_hash='unused',active=True));setup.flush()
        setup.add(Membership(id='ms',tenant_id='t',user_id='sales',role='sales',active=True))
        lead=setup.get(Lead,lid);lead.owner_id='sales';lead.stage='qualified'
        inquiry=setup.get(Inquiry,lead.inquiry_id);inquiry.owner_id='sales';inquiry.consent=True;inquiry.contactable=True
        setup.commit()
    sales=Context(user_id='sales',tenant_id='t',role='sales',name='销售',email='sales@test.cn')
    started=Event();finished=Event()
    def waiting_patch():
        with Session(engine) as db:
            started.set()
            try:
                s.update_customer_data(db,sales,'p',lid,{'notes':'不得写入的过期权限更新'})
                db.commit();return 200
            except HTTPException as error:
                db.rollback();return error.status_code
            finally:finished.set()
    with ThreadPoolExecutor(max_workers=1) as pool,Session(engine) as first:
        lead=first.get(Lead,lid)
        if revocation=='owner':lead.owner_id='admin'
        else:lead.next_action='联系授权变更中'
        first.flush()
        if revocation=='consent':
            inquiry=first.get(Inquiry,lead.inquiry_id);inquiry.consent=False;inquiry.contactable=False;first.flush()
        future=pool.submit(waiting_patch)
        try:
            assert started.wait(2)
            assert not finished.wait(.15)
        finally:first.commit()
        assert future.result(timeout=5)==expected_status
