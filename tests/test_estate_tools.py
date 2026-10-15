"""Native catalog edits remain scoped, revisioned, and part of the agent transaction."""
import importlib

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.audit_models import AuditEvent
from app.core_models import Asset, Fact, FactRevision, Property, Tenant, User
from app.db import Base
from app.security import Context


@pytest.fixture
def estate_env():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    db.add_all([Tenant(id='tenant', name='经纪团队'), Tenant(id='other', name='其他团队'),
                User(id='secretary', name='秘书', email='secretary@example.test', password_hash='unused'),
                Property(id='home', tenant_id='tenant', name='滨江公馆', city='上海'),
                Property(id='second', tenant_id='tenant', name='其他楼盘', city='杭州'),
                Property(id='foreign', tenant_id='other', name='外部楼盘', city='广州')])
    db.add_all([Fact(id='area', tenant_id='tenant', property_id='home', semantic_key='area', label='建筑面积',
                     value='328', unit='㎡', source='测绘报告第2页', status='verified', revision=3),
                Fact(id='layout', tenant_id='tenant', property_id='home', semantic_key='layout', label='户型',
                     value='待确认', source='', status='unverified'),
                Fact(id='other-fact', tenant_id='tenant', property_id='second', semantic_key='area', label='面积'),
                Asset(id='photo', tenant_id='tenant', property_id='home', name='江景.jpg', kind='image',
                      storage_key='private/original.jpg', mime_type='image/jpeg', sha256='a' * 64, byte_size=999,
                      rights='pending', origin_type='reference', extracted_text='不能传给模型的文件正文',
                      metadata_json={'model_processing_authorized': False, 'usage_scope': 'research_only',
                                     'description': '傍晚客厅', 'tags': ['江景', '黄昏'],
                                     'user_metadata': {'拍摄位置': '南阳台'}, 'text_pages': [{'text': '私密正文'}]}),
                Asset(id='other-photo', tenant_id='tenant', property_id='second', name='别的江景.jpg', kind='image',
                      storage_key='other.jpg', mime_type='image/jpeg', sha256='b' * 64, byte_size=9)])
    db.commit()
    yield db, Context('secretary', 'tenant', 'content', '秘书', 'secretary@example.test', ['home'])
    db.close()
    engine.dispose()


def run(db, ctx, name, args=None, pid='home'):
    module = importlib.import_module('app.estate_tools')
    return module.execute_estate_tool(db, ctx, pid, name, args or {}, raw_text='经纪人明确要求记录资料')


def test_details_include_pending_facts_and_current_revision(estate_env):
    db, ctx = estate_env
    result = run(db, ctx, 'get_property_details')
    facts = {fact['id']: fact for fact in result['data']['facts']}
    assert result['data']['project']['name'] == '滨江公馆'
    assert facts['layout']['status'] == 'unverified'
    assert facts['layout']['valid'] is False
    assert facts['area']['revision'] == 3
    assert facts['area']['valid'] is True
    assert {source['id'] for source in result['sources'] if source['kind'] == 'fact'} == {'area', 'layout'}


def test_fact_edit_resets_verification_records_both_revisions_and_rolls_back(estate_env):
    db, ctx = estate_env
    result = run(db, ctx, 'update_property_fact', {'fact_id': 'area', 'value': '330',
                 'unit': '㎡', 'source': '业主今天提供的新测绘报告', 'expected_revision': 3})
    assert result['data']['fact']['value'] == '330'
    assert result['data']['fact']['revision'] == 4
    assert result['data']['fact']['status'] == 'unverified'
    assert result['data']['fact']['valid'] is False
    revisions = db.scalars(select(FactRevision).where(FactRevision.fact_id == 'area').order_by(FactRevision.revision)).all()
    assert [(item.revision, item.payload['value'], item.payload['status']) for item in revisions] == [
        (3, '328', 'verified'), (4, '330', 'unverified')]
    audit = db.scalar(select(AuditEvent))
    assert audit.actor_id == 'secretary' and audit.property_id == 'home'
    db.rollback()
    assert db.get(Fact, 'area').value == '328'
    assert db.get(Fact, 'area').status == 'verified'
    assert db.scalar(select(AuditEvent)) is None


def test_stale_fact_revision_does_not_overwrite_or_create_history(estate_env):
    db, ctx = estate_env
    with pytest.raises(HTTPException) as error:
        run(db, ctx, 'update_property_fact', {'fact_id': 'area', 'value': '331', 'expected_revision': 2})
    assert error.value.status_code == 409
    assert db.get(Fact, 'area').value == '328'
    assert db.scalar(select(FactRevision)) is None


def test_create_fact_is_unverified_and_duplicate_key_is_a_conflict(estate_env):
    db, ctx = estate_env
    result = run(db, ctx, 'create_property_fact', {'semantic_key': 'ceiling_height', 'label': '客厅层高',
                 'value': '3.6', 'unit': 'm', 'source': '业主口述，待实测'})
    fact = db.get(Fact, result['data']['fact']['id'])
    assert (fact.status, fact.required, fact.revision, fact.value) == ('unverified', False, 1, '3.6')
    assert db.scalar(select(FactRevision).where(FactRevision.fact_id == fact.id)).payload['source'] == '业主口述，待实测'
    with pytest.raises(HTTPException) as error:
        run(db, ctx, 'create_property_fact', {'semantic_key': 'ceiling_height', 'label': '层高', 'value': '4', 'source': '业主口述'})
    assert error.value.status_code == 409


@pytest.mark.parametrize('query', ['黄昏', '南阳台', '傍晚', '江景'])
def test_asset_search_finds_catalog_metadata_without_reading_file_content(estate_env, query):
    db, ctx = estate_env
    result = run(db, ctx, 'search_assets', {'query': query})
    assert [item['id'] for item in result['data']['assets']] == ['photo']
    assert '私密正文' not in str(result) and '文件正文' not in str(result)
    assert 'private/original.jpg' not in str(result)
    assert result['sources'][0]['metadata_only'] is True


def test_asset_edit_preserves_evidence_and_can_be_rolled_back(estate_env):
    db, ctx = estate_env
    result = run(db, ctx, 'update_asset', {'asset_id': 'photo', 'name': '滨江南阳台.jpg',
                 'description': '经纪人备注：彭先生喜欢的江景', 'tags': ['彭先生偏好', '江景'],
                 'metadata_patch': {'拍摄位置': '主卧阳台'}})
    asset = db.get(Asset, 'photo')
    assert asset.metadata_json['description'] == '经纪人备注：彭先生喜欢的江景'
    assert asset.metadata_json['tags'] == ['彭先生偏好', '江景']
    assert asset.metadata_json['user_metadata'] == {'拍摄位置': '主卧阳台'}
    assert (asset.storage_key, asset.sha256, asset.rights, asset.origin_type, asset.rights_revision) == (
        'private/original.jpg', 'a' * 64, 'pending', 'reference', 1)
    assert asset.metadata_json['model_processing_authorized'] is False
    assert asset.metadata_json['usage_scope'] == 'research_only'
    assert result['data']['asset']['name'] == '滨江南阳台.jpg'
    assert db.scalar(select(AuditEvent)).action == 'agent.update_asset'
    db.rollback()
    assert db.get(Asset, 'photo').name == '江景.jpg'


@pytest.mark.parametrize('field', ['model_processing_authorized', 'rights', 'usage_scope', 'origin_type', 'text_pages'])
def test_metadata_patch_cannot_change_authorization_or_operational_provenance(estate_env, field):
    db, ctx = estate_env
    with pytest.raises((ValueError, HTTPException)):
        run(db, ctx, 'update_asset', {'asset_id': 'photo', 'metadata_patch': {field: True}})
    assert db.get(Asset, 'photo').metadata_json['model_processing_authorized'] is False
    assert db.scalar(select(AuditEvent)) is None


def test_property_edit_keeps_manual_marketing_authorization_separate(estate_env):
    db, ctx = estate_env
    result = run(db, ctx, 'update_property', {'sales_owner_name': '陈经纪', 'audience': '重视江景与隐私的家庭'})
    assert result['data']['project']['sales_owner_name'] == '陈经纪'
    assert db.get(Property, 'home').marketing_confirmed is False
    with pytest.raises((ValueError, HTTPException)):
        run(db, ctx, 'update_property', {'marketing_confirmed': True, 'marketing_evidence': '模型自行确认'})


@pytest.mark.parametrize(('name', 'args'), [
    ('update_property', {'name': '   '}),
    ('update_property', {'city': None}),
    ('update_property', {}),
    ('update_asset', {'asset_id': 'photo', 'tags': '江景'}),
    ('update_asset', {'asset_id': 'photo', 'tags': [3]}),
    ('update_asset', {'asset_id': 'photo', 'name': 'x' * 256}),
    ('update_asset', {'asset_id': 'photo', 'sha256': 'forged'}),
    ('update_property_fact', {'fact_id': 'area', 'value': 100}),
    ('update_property_fact', {'fact_id': 'area', 'value': '330', 'expected_revision': True}),
    ('update_property_fact', {'fact_id': 'area', 'status': 'verified'}),
    ('create_property_fact', {'semantic_key': 'bad key', 'label': '层高', 'value': '3', 'source': '业主'}),
    ('search_assets', {'limit': True}),
])
def test_invalid_edits_are_rejected_before_mutation(estate_env, name, args):
    db, ctx = estate_env
    with pytest.raises((ValueError, HTTPException)):
        run(db, ctx, name, args)
    assert db.get(Fact, 'area').revision == 3
    assert db.scalar(select(AuditEvent)) is None


@pytest.mark.parametrize(('pid', 'name', 'args', 'status'), [
    ('foreign', 'get_property_details', {}, 404),
    ('second', 'get_property_details', {}, 404),
    ('home', 'update_property_fact', {'fact_id': 'other-fact', 'value': '330'}, 404),
    ('home', 'update_asset', {'asset_id': 'other-photo', 'name': '越权.jpg'}, 404),
])
def test_project_and_entity_scope_are_enforced(estate_env, pid, name, args, status):
    db, ctx = estate_env
    with pytest.raises(HTTPException) as error:
        run(db, ctx, name, args, pid=pid)
    assert error.value.status_code == status


def test_viewer_cannot_mutate_property(estate_env):
    db, ctx = estate_env
    viewer = Context(ctx.user_id, ctx.tenant_id, 'viewer', ctx.name, ctx.email, ctx.project_ids)
    assert run(db, viewer, 'get_property_details')['data']['project']['id'] == 'home'
    with pytest.raises(HTTPException) as error:
        run(db, viewer, 'update_property', {'owner_name': '越权负责人'})
    assert error.value.status_code == 403
