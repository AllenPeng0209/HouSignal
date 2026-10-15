"""Research persistence must survive an upgrade of existing editorial data."""
from datetime import datetime
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, Table, create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import agent_models, audit_models, business_models, core_models, job_models, operations_models
from app.core_models import Property, Tenant
from app.db import Base


PREVIOUS_REVISION = 'c24101300001'


@pytest.fixture
def upgraded_research_store():
    engine = create_engine('sqlite://')
    config = Config()
    config.set_main_option('script_location', str(Path(__file__).parents[1] / 'migrations'))
    scripts = ScriptDirectory.from_config(config)
    revisions = list(reversed(list(scripts.iterate_revisions('heads', PREVIOUS_REVISION))))
    existing_tables = [table for table in Base.metadata.sorted_tables if table.name not in {
        'content_accounts', 'content_opportunities', 'content_plans', 'discovery_saved_searches',
        # These tables are introduced after PREVIOUS_REVISION and must be
        # created by the upgrade, even when other tests imported their models.
        'customer_profiles', 'viewing_appointments', 'agent_controls',
    }]
    Base.metadata.create_all(engine, tables=existing_tables)
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            scripts.get_revision(PREVIOUS_REVISION).module.upgrade()
    with Session(engine) as db:
        db.add(Tenant(id='tenant', name='已有组织'))
        db.flush()
        db.add_all([
            Property(id='project', tenant_id='tenant', name='保留项目', city='苏州'),
            Property(id='other-project', tenant_id='tenant', name='另一个项目', city='上海'),
        ])
        db.commit()
    old = Table('content_opportunities', MetaData(), autoload_with=engine)
    old_plans = Table('content_plans', MetaData(), autoload_with=engine)
    with engine.begin() as connection:
        connection.execute(old.insert(), [
            dict(id='saved', tenant_id='tenant', property_id='project', created_at=datetime(2026, 10, 13, 8),
                 title='保留选题', kind='video', platform='douyin', source_url='https://example.test/video/1',
                 summary='原始依据', status='saved'),
            dict(id='planned', tenant_id='tenant', property_id='project', created_at=datetime(2026, 10, 13, 8),
                 title='已策划选题', kind='video', platform='douyin', source_url='', summary='', status='planned'),
        ])
        connection.execute(old_plans.insert(), dict(
            id='legacy-plan', tenant_id='tenant', property_id='project', created_at=datetime(2026, 10, 13, 8),
            title='已有策划', angle='', channel='抖音', scheduled_for=None, account_id=None,
            opportunity_id='planned', topic_id='tour', content_version_id=None, status='draft',
            brief='保留内容', source_snapshot={}, request_hash=None,
        ))
        with Operations.context(MigrationContext.configure(connection)):
            for revision in revisions:
                revision.module.upgrade()
    yield engine, revisions
    engine.dispose()


def test_upgrade_preserves_editorial_state_and_initializes_research_fields(upgraded_research_store):
    # Catches omitted defaults/backfill or overwriting the existing planning status.
    engine, _ = upgraded_research_store
    assert 'discovery_saved_searches' in inspect(engine).get_table_names()
    with Session(engine) as db:
        saved = db.get(operations_models.ContentOpportunity, 'saved')
        planned = db.get(operations_models.ContentOpportunity, 'planned')
        assert (saved.title, saved.summary, saved.status) == ('保留选题', '原始依据', 'saved')
        assert planned.status == 'planned'
        assert saved.source_key is None
        assert saved.source_snapshot == {}
        assert saved.curation_state == 'inbox'
        assert saved.tags == []
        assert saved.note == ''
        assert saved.media_asset_id is None
        assert saved.research_notes == {}
        assert saved.analysis_snapshot == {}
        assert saved.revision == 1
        search = operations_models.DiscoverySavedSearch(tenant_id='tenant', property_id='project', name='全国改善住宅')
        db.add(search)
        db.commit()
        db.expire_all()
        assert db.get(operations_models.DiscoverySavedSearch, search.id).filters == {}
        assert db.get(operations_models.DiscoverySavedSearch, search.id).revision == 1


def test_upgrade_enforces_project_scoped_source_identity(upgraded_research_store):
    # Catches a missing unique constraint or an identity constraint that crosses projects.
    engine, _ = upgraded_research_store
    columns = {column['name'] for column in inspect(engine).get_columns('content_opportunities')}
    assert 'source_key' in columns
    with Session(engine) as db:
        saved = db.get(operations_models.ContentOpportunity, 'saved')
        saved.source_key = 'douyin:123'
        db.add(operations_models.ContentOpportunity(tenant_id='tenant', property_id='other-project',
            title='另一项目收藏', kind='video', platform='douyin', source_key='douyin:123'))
        db.commit()
        db.add(operations_models.ContentOpportunity(tenant_id='tenant', property_id='project',
            title='重复收藏', kind='video', platform='douyin', source_key='douyin:123'))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        assert len(db.scalars(select(operations_models.ContentOpportunity)).all()) == 3


def test_upgrade_allows_multiple_plans_per_reference_and_preserves_legacy_link(upgraded_research_store):
    # Catches accidentally making a research reference unique or dropping legacy linkage.
    engine, _ = upgraded_research_store
    assert 'reference_id' in {column['name'] for column in inspect(engine).get_columns('content_plans')}
    with Session(engine) as db:
        legacy = db.get(operations_models.ContentPlan, 'legacy-plan')
        assert (legacy.reference_id, legacy.opportunity_id, legacy.brief) == (None, 'planned', '保留内容')
        db.add_all([
            operations_models.ContentPlan(tenant_id='tenant', property_id='project',
                title=title, channel='抖音', topic_id='tour', reference_id='saved')
            for title in ('第一版脚本', '第二版脚本')
        ])
        db.commit()
        plans = db.scalars(select(operations_models.ContentPlan).where(
            operations_models.ContentPlan.reference_id == 'saved')).all()
        assert {plan.title for plan in plans} == {'第一版脚本', '第二版脚本'}
        assert all(plan.opportunity_id is None for plan in plans)
        db.add(operations_models.ContentPlan(tenant_id='tenant', property_id='project',
            title='重复旧选题关系', channel='抖音', topic_id='tour', opportunity_id='planned'))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_downgrade_keeps_existing_projects_and_opportunities(upgraded_research_store):
    # Catches destructive rollback or batch-table replacement losing existing rows.
    engine, revisions = upgraded_research_store
    assert revisions, 'research upgrade must exist before downgrade is meaningful'
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            for revision in reversed(revisions):
                revision.module.downgrade()
        old = Table('content_opportunities', MetaData(), autoload_with=connection)
        assert connection.execute(select(old.c.id, old.c.status).order_by(old.c.id)).all() == [
            ('planned', 'planned'), ('saved', 'saved'),
        ]
        old_plans = Table('content_plans', MetaData(), autoload_with=connection)
        assert connection.execute(select(old_plans.c.id, old_plans.c.opportunity_id)).all() == [('legacy-plan', 'planned')]
        assert 'reference_id' not in {column['name'] for column in inspect(connection).get_columns('content_plans')}
        assert 'discovery_saved_searches' not in inspect(connection).get_table_names()
        assert 'source_key' not in {column['name'] for column in inspect(connection).get_columns('content_opportunities')}
    with Session(engine) as db:
        assert db.get(Property, 'project').name == '保留项目'
