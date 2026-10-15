"""CRM matches use saved scoped records, valid facts and eligible source assets."""
from datetime import datetime, timedelta, timezone
import importlib
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core_models import Asset, Fact, Membership, Property, Tenant, User
from app.business_models import Inquiry, Lead
from app.db import Base
from app.security import Context
from app.secretary import create_customer_data, update_customer_data


@pytest.fixture
def matching_env():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    db.add_all([Tenant(id='t', name='合成团队'), Tenant(id='other', name='其他团队'),
                User(id='admin', name='合成主管', email='admin@example.test', password_hash='unused'),
                User(id='sales', name='合成销售', email='sales@example.test', password_hash='unused')])
    db.flush()
    db.add_all([Membership(id='member', tenant_id='t', user_id='admin', role='admin', active=True),
                Property(id='p', tenant_id='t', name='合成江岸', city='上海', is_test=True),
                Property(id='second', tenant_id='t', name='其他楼盘', city='上海', is_test=True)])
    db.flush()
    ctx = Context('admin', 't', 'admin', '合成主管', 'admin@example.test', ['p'])
    customer = create_customer_data(db, ctx, 'p', {'name': '合成彭先生', 'budget': '5000万至8000万元',
        'preferences': '江景、四房', 'requirements': '350至500平方米', 'need': '想要江景四房住宅'})
    for key, value, unit in [('total_price', '6500', '万元'), ('area', '420', '㎡'),
                              ('view', '开阔江景', ''), ('layout', '四房两厅', '')]:
        db.add(Fact(id=key, tenant_id='t', property_id='p', semantic_key=key, label=key,
                    value=value, unit=unit, source='合成核验资料', status='verified', revision=2))
    def asset(identity, **changes):
        values = dict(id=identity, tenant_id='t', property_id='p', name=identity + '.png', kind='image',
            storage_key='private/' + identity + '.png', mime_type='image/png', sha256='a' * 64, byte_size=1,
            rights='authorized', rights_evidence='合成测试图', origin_type='test',
            metadata_json={}, extracted_text='私密正文不应进入匹配')
        values.update(changes)
        db.add(Asset(**values))
    asset('irrelevant', metadata_json={'description': '建筑入口'})
    asset('preferred', metadata_json={'description': '客厅江景', 'tags': ['四房', '江景'],
                                      'model_processing_authorized': True})
    asset('pending', rights='pending', metadata_json={'tags': ['江景']})
    asset('research', metadata_json={'usage_scope': 'research_only', 'tags': ['江景']})
    asset('old-output', metadata_json={'renderer': 'ffmpeg', 'tags': ['江景']})
    asset('other-project', property_id='second', metadata_json={'tags': ['江景', '四房']})
    asset('missing-evidence', rights_evidence='')
    db.commit()
    yield db, ctx, customer['lead']['id']
    db.close()
    engine.dispose()


def run(env, args=None, ctx=None, pid='p'):
    db, actor, lead_id = env
    module = importlib.import_module('app.customer_matching')
    return module.execute_matching_tool(db, ctx or actor, pid, 'match_customer_resources',
                                       args if args is not None else {'lead_id': lead_id})


def test_saved_budget_area_and_preferences_match_verified_property(matching_env):
    result = run(matching_env)
    data = result['data']
    assert data['scope'] == 'current_project'
    assert data['constraints']['budget_max_wan'] == 8000
    assert (data['constraints']['area_min_sqm'], data['constraints']['area_max_sqm']) == (350, 500)
    assert data['property_match']['qualification'] == 'matches'
    assert data['property_match']['project_id'] == 'p'
    assert data['property_match']['is_test'] is True
    assert data['property_match']['unconfirmed'] == []


def test_preferred_catalog_assets_rank_above_irrelevant_and_exclude_ineligible(matching_env):
    data = run(matching_env)['data']
    assert [row['id'] for row in data['assets']] == ['preferred', 'irrelevant']
    assert data['candidate_asset_count'] == 2
    assert data['assets'][0]['score'] > data['assets'][1]['score']
    assert data['assets'][0]['matched_preferences'] == ['江景', '四房']
    assert data['assets'][0]['model_processing_authorized'] is True


@pytest.mark.parametrize(('value', 'unit', 'want'), [('9000', '万元', 'not_match'),
    ('0.65', '亿元', 'matches'), ('65000000', '元', 'matches')])
def test_verified_total_price_units_are_compared_with_customer_budget(matching_env, value, unit, want):
    db, _, _ = matching_env
    fact = db.get(Fact, 'total_price'); fact.value = value; fact.unit = unit; db.commit()
    assert run(matching_env)['data']['property_match']['qualification'] == want


@pytest.mark.parametrize('case', ['missing', 'unverified', 'expired', 'unit_price'])
def test_unknown_total_price_never_becomes_confirmed_budget_fit(matching_env, case):
    db, _, _ = matching_env
    fact = db.get(Fact, 'total_price')
    if case == 'missing': db.delete(fact)
    elif case == 'unverified': fact.status = 'unverified'
    elif case == 'expired': fact.valid_until = datetime.now(timezone.utc) - timedelta(days=1)
    else: fact.semantic_key = 'unit_price'; fact.unit = '元/㎡'; fact.value = '150000'
    db.commit()
    match = run(matching_env)['data']['property_match']
    assert match['qualification'] == 'partial'
    assert any('总价' in item for item in match['unconfirmed'])


def test_sources_keep_lead_and_fact_versions_but_no_file_content(matching_env):
    result = run(matching_env)
    assert any(s['kind'] == 'lead' and s['id'] == matching_env[2] for s in result['sources'])
    assert any(s['kind'] == 'fact' and s['id'] == 'total_price' and s['revision'] == 2 for s in result['sources'])
    assert all(s.get('metadata_only') is True for s in result['sources'] if s['kind'] == 'asset')
    assert 'private/' not in json.dumps(result, ensure_ascii=False)
    assert '私密正文' not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize(('actor', 'pid', 'status'), [
    (Context('sales', 't', 'sales', '合成销售', 'sales@example.test', ['p']), 'p', 404),
    (None, 'second', 404),
    (Context('admin', 'other', 'admin', '合成主管', 'admin@example.test'), 'p', 404)])
def test_matching_obeys_owner_project_and_tenant_scope(matching_env, actor, pid, status):
    with pytest.raises(HTTPException) as error: run(matching_env, ctx=actor, pid=pid)
    assert error.value.status_code == status


def test_withdrawn_customer_is_not_matched(matching_env):
    db, _, lead_id = matching_env
    lead = db.get(Lead, lead_id); db.get(Inquiry, lead.inquiry_id).withdrawn = True; db.commit()
    with pytest.raises(HTTPException) as error: run(matching_env)
    assert error.value.status_code == 409


def test_unknown_crm_budget_and_unverified_preferences_are_explicit(matching_env):
    db, ctx, lead_id = matching_env
    update_customer_data(db, ctx, 'p', lead_id, {'budget': '预算尚未确定', 'preferences': '江景、安静、低楼层'})
    data = run(matching_env)['data']
    assert data['constraints']['budget_max_wan'] is None
    assert data['property_match']['qualification'] == 'partial'
    assert any('预算' in item for item in data['property_match']['unconfirmed'])
    assert any('安静' in item for item in data['property_match']['unconfirmed'])
    assert any('低楼层' in item for item in data['property_match']['unconfirmed'])


@pytest.mark.parametrize('bad', [{'lead_id': ''}, {'lead_id': 'x', 'limit': True},
    {'lead_id': 'x', 'limit': 13}, {'lead_id': 'x', 'scope': 'all'}])
def test_invalid_or_unbounded_arguments_are_rejected(matching_env, bad):
    with pytest.raises((ValueError, HTTPException)): run(matching_env, bad)


def test_new_explicit_preferences_override_older_free_text_need(matching_env):
    db, ctx, lead_id = matching_env
    update_customer_data(db, ctx, 'p', lead_id, {'preferences': '不喜欢江景，只要四房'})
    data = run(matching_env)['data']
    assert data['constraints']['preferences'] == ['四房']
    assert data['assets'][0]['matched_preferences'] == ['四房']
    assert data['property_match']['qualification'] == 'partial'
    assert any('排除' in item for item in data['property_match']['unconfirmed'])


def test_area_outside_saved_bounds_is_not_a_match(matching_env):
    db, _, _ = matching_env
    db.get(Fact, 'area').value = '520'; db.commit()
    assert run(matching_env)['data']['property_match']['qualification'] == 'not_match'


def test_read_only_matching_does_not_add_or_change_records(matching_env):
    db, _, _ = matching_env
    before = [(f.id, f.value, f.revision) for f in db.scalars(select(Fact)).all()]
    run(matching_env)
    assert not db.new and not db.dirty and not db.deleted
    assert [(f.id, f.value, f.revision) for f in db.scalars(select(Fact)).all()] == before
