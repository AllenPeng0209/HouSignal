"""Content operations persist real project data and enforce scope at the API."""
from dataclasses import replace
import importlib.util

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from pathlib import Path


@pytest.fixture
def operations_client():
    from app.db import Base, get_db
    from app.core_models import Tenant, User, Property
    from app.security import Context, get_context
    import app.business_models
    import app.agent_models
    import app.job_models
    import app.audit_models
    from app import operations
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    db.add_all([Tenant(id='t', name='团队'), Tenant(id='other', name='另一个团队'),
                User(id='u', email='ops@test.cn', password_hash='unused', name='编辑', active=True)])
    db.flush()
    for pid, tenant in [('p', 't'), ('p2', 't'), ('foreign', 'other')]:
        db.add(Property(id=pid, tenant_id=tenant, name=pid, city='苏州', channel='抖音'))
    db.commit()
    context = Context(user_id='u', tenant_id='t', role='content', name='编辑', email='ops@test.cn', project_ids=None)
    current = {'context': context}
    app = FastAPI()
    app.include_router(operations.router)
    def database():
        try:
            yield db
        except Exception:
            db.rollback()
            raise
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_context] = lambda: current['context']
    with TestClient(app) as client:
        yield client, db, current, context
    db.close()
    engine.dispose()


ROOT = '/api/projects/p/operations'


def account(client, **extra):
    result = client.post(ROOT + '/accounts', json={'name': '江岸生活', 'platform': '抖音',
        'homepage': 'https://www.douyin.com/user/example', 'positioning': '实拍豪宅生活',
        'audience': '苏州改善家庭', 'voice': '克制、具体', 'red_lines': '不承诺收益', **extra})
    assert result.status_code == 200, result.text
    return result.json()


def opportunity(client, **extra):
    result = client.post(ROOT + '/opportunities', json={'title': '改善家庭关注采光', 'kind': 'gap',
        'platform': '抖音', 'source_url': 'https://example.org/research', 'summary': '回答客厅采光和动线问题', **extra})
    assert result.status_code == 200, result.text
    return result.json()


def test_account_create_and_update_persist_across_reads(operations_client):
    client, _, _, _ = operations_client
    row = account(client)
    assert row['status'] == 'managed'
    assert row['voice'] == '克制、具体'
    result = client.patch(ROOT + '/accounts/' + row['id'], json={'status': 'paused', 'audience': '本地置换家庭'})
    assert result.status_code == 200
    saved = client.get(ROOT + '/accounts').json()
    assert len(saved) == 1
    assert saved[0]['status'] == 'paused'
    assert saved[0]['audience'] == '本地置换家庭'
    assert saved[0]['positioning'] == '实拍豪宅生活'
    assert not {'password', 'login_state', 'access_token'} & saved[0].keys()


@pytest.mark.parametrize('field,value', [('platform', '未知平台'), ('status', 'logged_in'),
    ('name', ''), ('homepage', 'javascript:alert(1)'), ('homepage', 'https://user:password@example.org'),
    ('homepage', 'https://'), ('password', 'do-not-store')])
def test_invalid_account_input_never_creates_a_record(operations_client, field, value):
    client, _, _, _ = operations_client
    result = client.post(ROOT + '/accounts', json={'name': '账号', 'platform': '抖音', field: value})
    assert result.status_code == 422
    assert client.get(ROOT + '/accounts').json() == []


@pytest.mark.parametrize('role', ['viewer', 'sales', 'reviewer'])
def test_readers_cannot_write_operations(operations_client, role):
    client, _, current, context = operations_client
    account(client)
    current['context'] = replace(context, role=role)
    assert len(client.get(ROOT + '/accounts').json()) == 1
    assert client.post(ROOT + '/accounts', json={'name': '不得写入', 'platform': '抖音'}).status_code == 403
    assert client.post(ROOT + '/opportunities', json={'title': '不得写入', 'kind': 'trend'}).status_code == 403
    assert client.post(ROOT + '/plans', json={'title': '不得写入', 'topic_id': 'tour'}).status_code == 403


def test_tenant_and_project_scope_hide_records_and_references(operations_client):
    client, _, current, context = operations_client
    row = account(client)
    found = opportunity(client)
    plan = client.post(ROOT + '/plans', json={'title': '采光实拍', 'topic_id': 'light', 'account_id': row['id'], 'opportunity_id': found['id']})
    assert plan.status_code == 200, plan.text
    for resource in ('accounts', 'opportunities', 'plans'):
        assert client.get('/api/projects/p2/operations/' + resource).json() == []
    assert client.patch('/api/projects/p2/operations/accounts/' + row['id'], json={'name': '越权'}).status_code == 404
    assert client.post('/api/projects/p2/operations/plans', json={'title': '越权', 'topic_id': 'tour', 'account_id': row['id']}).status_code == 404
    assert client.post('/api/projects/p2/operations/plans', json={'title': '越权', 'topic_id': 'tour', 'opportunity_id': found['id']}).status_code == 404
    current['context'] = replace(context, tenant_id='other')
    for resource in ('accounts', 'opportunities', 'plans'):
        assert client.get(ROOT + '/' + resource).status_code == 404
    current['context'] = replace(context, project_ids=['p2'])
    assert client.get(ROOT + '/accounts').status_code == 404
    assert client.post(ROOT + '/plans', json={'title': '越权', 'topic_id': 'tour'}).status_code == 404


def test_discovery_source_is_saved_and_conversion_is_idempotent(operations_client):
    client, _, _, _ = operations_client
    found = opportunity(client)
    assert found['status'] == 'saved'
    assert found['observed_at']
    payload = {'topic_id': 'light', 'channel': '抖音', 'scheduled_for': '2026-10-18', 'status': 'scheduled'}
    first = client.post(ROOT + '/opportunities/' + found['id'] + '/plan', json=payload)
    assert first.status_code == 200, first.text
    again = client.post(ROOT + '/opportunities/' + found['id'] + '/plan', json=payload)
    assert again.status_code == 200
    assert again.json()['id'] == first.json()['id']
    assert first.json()['opportunity_id'] == found['id']
    assert first.json()['source']['source_url'] == 'https://example.org/research'
    assert first.json()['title'] == '改善家庭关注采光'
    assert first.json()['scheduled_for'] == '2026-10-18'
    assert len(client.get(ROOT + '/plans').json()) == 1
    assert client.get(ROOT + '/opportunities').json()[0]['status'] == 'planned'


def test_conflicting_conversion_does_not_change_existing_plan(operations_client):
    client, _, _, _ = operations_client
    found = opportunity(client)
    url = ROOT + '/opportunities/' + found['id'] + '/plan'
    first = client.post(url, json={'topic_id': 'light', 'title': '最初的方向'})
    assert first.status_code == 200
    assert client.post(url, json={'topic_id': 'layout', 'title': '不同方向'}).status_code == 409
    assert client.get(ROOT + '/plans').json()[0]['title'] == '最初的方向'
    assert client.get(ROOT + '/plans').json()[0]['topic_id'] == 'light'


def test_plan_edit_validates_before_mutating_and_can_clear_date(operations_client):
    client, _, _, _ = operations_client
    row = account(client)
    result = client.post(ROOT + '/plans', json={'title': '客厅实拍', 'topic_id': 'light', 'account_id': row['id'],
        'channel': '抖音', 'scheduled_for': '2026-10-19', 'status': 'scheduled', 'brief': '只使用已核验资料'})
    assert result.status_code == 200, result.text
    pid = result.json()['id']
    bad = client.patch(ROOT + '/plans/' + pid, json={'title': '不应保存', 'scheduled_for': '2026-02-30'})
    assert bad.status_code == 422
    assert client.get(ROOT + '/plans').json()[0]['title'] == '客厅实拍'
    assert client.patch(ROOT + '/plans/' + pid, json={'scheduled_for': ''}).status_code == 422
    cleared = client.patch(ROOT + '/plans/' + pid, json={'scheduled_for': '', 'status': 'draft'})
    assert cleared.status_code == 200
    assert cleared.json()['scheduled_for'] is None
    assert cleared.json()['account_id'] == row['id']
    assert client.patch(ROOT + '/plans/' + pid, json={'status': 'completed'}).json()['status'] == 'completed'


@pytest.mark.parametrize('payload', [
    {'title': '无效', 'topic_id': 'freeform'}, {'title': '无效', 'topic_id': 'tour', 'status': 'published'},
    {'title': '无效', 'topic_id': 'tour', 'status': 'scheduled'},
    {'title': '无效', 'topic_id': 'tour', 'scheduled_for': '2026-1-1'},
    {'title': '无效', 'topic_id': 'tour', 'scheduled_for': '2026-10-01T10:00:00'},
    {'title': '无效', 'topic_id': 'tour', 'channel': '未知平台'},
])
def test_invalid_plan_does_not_create_data(operations_client, payload):
    client, _, _, _ = operations_client
    assert client.post(ROOT + '/plans', json=payload).status_code == 422
    assert client.get(ROOT + '/plans').json() == []


@pytest.mark.parametrize('field,value', [('kind', 'fake'), ('platform', '未知平台'),
    ('source_url', 'file:///secret'), ('source_url', 'https://user:secret@example.org'), ('title', '')])
def test_invalid_discovery_input_is_rejected(operations_client, field, value):
    client, _, _, _ = operations_client
    result = client.post(ROOT + '/opportunities', json={'title': '事件', 'kind': 'event', field: value})
    assert result.status_code == 422
    assert client.get(ROOT + '/opportunities').json() == []


def test_plan_account_must_match_channel_and_be_managed(operations_client):
    client, _, _, _ = operations_client
    row = account(client)
    assert client.post(ROOT + '/plans', json={'title': '不匹配', 'topic_id': 'tour', 'account_id': row['id'], 'channel': '小红书'}).status_code == 422
    client.patch(ROOT + '/accounts/' + row['id'], json={'status': 'paused'})
    assert client.post(ROOT + '/plans', json={'title': '已暂停', 'topic_id': 'tour', 'account_id': row['id'], 'channel': '抖音'}).status_code == 409
    assert client.get(ROOT + '/plans').json() == []


def test_plan_exposes_generated_content_link_without_marking_complete(operations_client):
    from app.business_models import ContentVersion
    from app.operations_models import ContentPlan
    client, db, _, _ = operations_client
    result = client.post(ROOT + '/plans', json={'title': '项目导览', 'topic_id': 'tour'})
    assert result.status_code == 200
    assert result.json().get('content_version_id', 'missing') is None
    record = db.get(ContentPlan, result.json()['id'])
    db.add(ContentVersion(id='generated', tenant_id='t', property_id='p', created_by='u'))
    db.flush()
    record.content_version_id = 'generated'
    db.commit()
    listed = client.get(ROOT + '/plans').json()[0]
    assert listed['content_version_id'] == 'generated'
    assert listed['status'] == 'draft'


def plan_with_generated_content(operations_client):
    from app.business_models import ContentVersion
    from app.operations_models import ContentPlan
    client, db, _, _ = operations_client
    result = client.post(ROOT + '/plans', json={'title': '项目导览', 'topic_id': 'tour', 'channel': '抖音',
                                              'brief': '原始策划', 'angle': '原始角度'})
    assert result.status_code == 200
    db.add(ContentVersion(id='previous_content', tenant_id='t', property_id='p', created_by='u', script='已有草稿'))
    db.flush()
    record = db.get(ContentPlan, result.json()['id'])
    record.content_version_id = 'previous_content'
    db.commit()
    return result.json()['id']


@pytest.mark.parametrize('field,value', [('title', '新标题'), ('angle', '新的切入角度'),
    ('brief', '修改事实叙述范围'), ('channel', '小红书'), ('account_id', 'new_account'), ('topic_id', 'light')])
def test_semantic_plan_edit_detaches_stale_generated_content(operations_client, field, value):
    from app.business_models import ContentVersion
    client, db, _, _ = operations_client
    plan_id = plan_with_generated_content(operations_client)
    if field == 'account_id':
        value = account(client)['id']
    result = client.patch(ROOT + '/plans/' + plan_id, json={field: value})
    assert result.status_code == 200, result.text
    assert result.json()['content_version_id'] is None
    assert client.get(ROOT + '/plans').json()[0]['content_version_id'] is None
    assert db.get(ContentVersion, 'previous_content').script == '已有草稿'


@pytest.mark.parametrize('body', [{'scheduled_for': '2026-10-25', 'status': 'scheduled'},
    {'status': 'completed'}, {'title': ' 项目导览 '}])
def test_schedule_status_and_unchanged_semantic_edits_preserve_content_link(operations_client, body):
    client, _, _, _ = operations_client
    plan_id = plan_with_generated_content(operations_client)
    result = client.patch(ROOT + '/plans/' + plan_id, json=body)
    assert result.status_code == 200
    assert result.json()['content_version_id'] == 'previous_content'


@pytest.mark.parametrize('legacy,canonical', [('视频号', '微信视频号'), ('B 站', 'B站')])
def test_legacy_project_channel_normalizes_for_new_discovery_and_plan(operations_client, legacy, canonical):
    from app.core_models import Property
    client, db, _, _ = operations_client
    db.get(Property, 'p').channel = legacy
    db.commit()
    discovered = client.post(ROOT + '/opportunities', json={'title': '平台洞察', 'kind': 'industry'})
    assert discovered.status_code == 200, discovered.text
    assert discovered.json()['platform'] == canonical
    planned = client.post(ROOT + '/plans', json={'title': '项目导览', 'topic_id': 'tour'})
    assert planned.status_code == 200, planned.text
    assert planned.json()['channel'] == canonical


def test_migration_installs_persistent_plans_and_preserves_existing_projects():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect, select, MetaData, Table
    from datetime import datetime, timezone
    from app.db import Base
    from app.core_models import Tenant, Property
    import app.business_models
    engine = create_engine('sqlite://')
    existing = [table for table in Base.metadata.sorted_tables if not table.name.startswith('content_accounts')
                and not table.name.startswith('content_opportunities') and not table.name.startswith('content_plans')]
    Base.metadata.create_all(engine, tables=existing)
    migration_path = Path(__file__).parents[1] / 'migrations/versions/c24101300001_content_operations.py'
    with engine.begin() as connection:
        spec = importlib.util.spec_from_file_location('content_operations_migration', migration_path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        assert {'content_accounts', 'content_opportunities', 'content_plans'} <= set(inspect(connection).get_table_names())
    with Session(engine) as db:
        db.add(Tenant(id='migration_t', name='已有组织'))
        db.flush()
        db.add(Property(id='migration_p', tenant_id='migration_t', name='保留项目', city='苏州'))
        db.flush()
        # This test targets this historical schema revision. Current ORM models
        # legitimately contain columns added by later migrations.
        plans = Table('content_plans', MetaData(), autoload_with=db.connection())
        db.execute(plans.insert().values(id='migration_plan', tenant_id='migration_t',
            property_id='migration_p', title='迁移后计划', angle='', channel='抖音', topic_id='tour',
            status='draft', brief='', source_snapshot={}, created_at=datetime.now(timezone.utc)))
        db.commit()
        assert db.scalar(select(plans.c.title)) == '迁移后计划'
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
    with Session(engine) as db:
        assert db.get(Property, 'migration_p').name == '保留项目'
    engine.dispose()
