"""Add UX fields to an existing project without deleting its business history."""
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, create_engine, inspect, select

from app import business, core_models, business_models
from app.db import Base


def test_ux_upgrade_preserves_existing_inquiries_followups_and_unknown_publish_times():
    # Catches recreating CRM tables, inventing old deadlines or omitting the durable review store.
    engine=create_engine('sqlite://')
    legacy=MetaData()
    additions={'publications':('published_at',),'inquiries':('publication_id',),'leads':('next_action_at',),'followups':('next_action_at',)}
    for table in Base.metadata.sorted_tables:
        if table.name=='content_review_feedback': continue
        cloned=table.to_metadata(legacy)
        for name in additions.get(table.name,()):
            column=cloned.c[name]
            for index in list(cloned.indexes):
                if column in index.columns.values(): cloned.indexes.remove(index)
            for constraint in list(cloned.constraints):
                if column in constraint.columns.values(): cloned.constraints.remove(constraint)
            cloned._columns.remove(column)
    legacy.create_all(engine)
    with engine.begin() as connection:
        connection.execute(legacy.tables['tenants'].insert(),dict(id='t',name='已有组织'))
        connection.execute(legacy.tables['users'].insert(),dict(id='u',email='u@test.cn',password_hash='unused',name='已有销售'))
        connection.execute(legacy.tables['properties'].insert(),dict(id='p',tenant_id='t',name='保留项目',city='苏州'))
        connection.execute(legacy.tables['publications'].insert(),dict(id='pub',tenant_id='t',property_id='p',release_id='r',url='https://example.test/1'))
        connection.execute(legacy.tables['inquiries'].insert(),dict(id='i',tenant_id='t',property_id='p',name='保留咨询',source_confidence='self_reported',owner_id='u'))
        connection.execute(legacy.tables['leads'].insert(),dict(id='l',tenant_id='t',property_id='p',inquiry_id='i',name='保留客户',owner_id='u',next_action='保留动作'))
        connection.execute(legacy.tables['followups'].insert(),dict(id='f',tenant_id='t',property_id='p',lead_id='l',note='保留历史',created_by='u'))
        config=Config(); config.set_main_option('script_location',str(Path(__file__).parents[1]/'migrations'))
        scripts=ScriptDirectory.from_config(config)
        revision=scripts.get_revision('c24101700001')
        with Operations.context(MigrationContext.configure(connection)):
            revision.module.upgrade()
        metadata=MetaData(); metadata.reflect(connection)
        saved=connection.execute(select(metadata.tables['inquiries'])).mappings().one()
        assert (saved['name'],saved['source_confidence'],saved['publication_id'])==('保留咨询','self_reported',None)
        assert connection.execute(select(metadata.tables['leads'].c.next_action_at)).scalar() is None
        assert connection.execute(select(metadata.tables['followups'].c.note)).scalar()=='保留历史'
        assert connection.execute(select(metadata.tables['publications'].c.published_at)).scalar() is None
        assert 'content_review_feedback' in inspect(connection).get_table_names()
        with Operations.context(MigrationContext.configure(connection)):
            revision.module.downgrade()
        assert connection.execute(select(legacy.tables['inquiries'].c.name)).scalar()=='保留咨询'
        assert 'content_review_feedback' not in inspect(connection).get_table_names()
    engine.dispose()
