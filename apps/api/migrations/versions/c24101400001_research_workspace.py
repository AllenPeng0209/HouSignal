"""Persist saved discovery searches and source research workspaces.

Revision ID: c24101400001
Revises: c24101300001
"""
from alembic import op
import sqlalchemy as sa

revision = 'c24101400001'
down_revision = 'c24101300001'
branch_labels = None
depends_on = None


def upgrade():
    # Server defaults initialize every existing row through DDL, including rows
    # hidden by FORCE RLS. Unknown legacy source identities deliberately stay NULL.
    with op.batch_alter_table('content_opportunities') as batch:
        batch.add_column(sa.Column('source_key', sa.String(200), nullable=True))
        batch.add_column(sa.Column('source_snapshot', sa.JSON(), server_default='{}', nullable=False))
        batch.add_column(sa.Column('curation_state', sa.String(30), server_default='inbox', nullable=False))
        batch.add_column(sa.Column('tags', sa.JSON(), server_default='[]', nullable=False))
        batch.add_column(sa.Column('note', sa.Text(), server_default='', nullable=False))
        batch.add_column(sa.Column('media_asset_id', sa.String(36), nullable=True))
        batch.add_column(sa.Column('research_notes', sa.JSON(), server_default='{}', nullable=False))
        batch.add_column(sa.Column('analysis_snapshot', sa.JSON(), server_default='{}', nullable=False))
        batch.add_column(sa.Column('revision', sa.Integer(), server_default='1', nullable=False))
        batch.create_foreign_key('fk_opportunity_media_asset', 'assets', ['media_asset_id'], ['id'])
        batch.create_unique_constraint('uq_content_opportunity_source', ['tenant_id', 'property_id', 'source_key'])

    with op.batch_alter_table('content_plans') as batch:
        batch.add_column(sa.Column('reference_id', sa.String(36), nullable=True))
        batch.create_foreign_key('fk_content_plan_reference', 'content_opportunities', ['reference_id'], ['id'])
        batch.create_index('ix_content_plans_reference_id', ['reference_id'], unique=False)

    op.create_table('discovery_saved_searches',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('property_id', sa.String(36), sa.ForeignKey('properties.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('filters', sa.JSON(), server_default='{}', nullable=False),
        sa.Column('revision', sa.Integer(), server_default='1', nullable=False))
    for column in ('tenant_id', 'property_id'):
        op.create_index(f'ix_discovery_saved_searches_{column}', 'discovery_saved_searches', [column])

    if op.get_context().dialect.name == 'postgresql':
        op.execute('ALTER TABLE discovery_saved_searches ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE discovery_saved_searches FORCE ROW LEVEL SECURITY')
        expression = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')"
        op.execute(f'CREATE POLICY tenant_boundary ON discovery_saved_searches USING ({expression}) WITH CHECK ({expression})')
        op.execute("""DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'housignal_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON discovery_saved_searches TO housignal_app;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'housignal_worker') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON discovery_saved_searches TO housignal_worker;
            END IF;
            END $$""")


def downgrade():
    op.drop_table('discovery_saved_searches')
    with op.batch_alter_table('content_plans') as batch:
        batch.drop_index('ix_content_plans_reference_id')
        batch.drop_constraint('fk_content_plan_reference', type_='foreignkey')
        batch.drop_column('reference_id')
    with op.batch_alter_table('content_opportunities') as batch:
        batch.drop_constraint('uq_content_opportunity_source', type_='unique')
        batch.drop_constraint('fk_opportunity_media_asset', type_='foreignkey')
        for column in ('revision', 'analysis_snapshot', 'research_notes', 'media_asset_id',
                       'note', 'tags', 'curation_state', 'source_snapshot', 'source_key'):
            batch.drop_column(column)
