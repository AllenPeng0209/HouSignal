"""Project account briefs, sourced opportunities, and persisted editorial plans.

Revision ID: c24101300001
Revises: 73b9a8f0d125
"""
from alembic import op
import sqlalchemy as sa

revision = 'c24101300001'
down_revision = '73b9a8f0d125'
branch_labels = None
depends_on = None
TABLES = ('content_accounts', 'content_opportunities', 'content_plans')


def _project_columns():
    return [sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
            sa.Column('property_id', sa.String(36), sa.ForeignKey('properties.id'), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False)]


def upgrade():
    op.create_table('content_accounts', *_project_columns(),
        sa.Column('platform', sa.String(40), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('homepage', sa.Text(), nullable=False),
        sa.Column('positioning', sa.Text(), nullable=False),
        sa.Column('audience', sa.Text(), nullable=False),
        sa.Column('voice', sa.Text(), nullable=False),
        sa.Column('red_lines', sa.Text(), nullable=False),
        sa.Column('status', sa.String(30), nullable=False))
    op.create_table('content_opportunities', *_project_columns(),
        sa.Column('title', sa.String(300), nullable=False),
        sa.Column('kind', sa.String(30), nullable=False),
        sa.Column('platform', sa.String(40), nullable=False),
        sa.Column('source_url', sa.Text(), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('status', sa.String(30), nullable=False))
    op.create_table('content_plans', *_project_columns(),
        sa.Column('title', sa.String(300), nullable=False),
        sa.Column('angle', sa.Text(), nullable=False),
        sa.Column('channel', sa.String(40), nullable=False),
        sa.Column('scheduled_for', sa.Date(), nullable=True),
        sa.Column('account_id', sa.String(36), sa.ForeignKey('content_accounts.id'), nullable=True),
        sa.Column('opportunity_id', sa.String(36), sa.ForeignKey('content_opportunities.id'), nullable=True),
        sa.Column('topic_id', sa.String(30), nullable=False),
        sa.Column('content_version_id', sa.String(36), sa.ForeignKey('content_versions.id'), nullable=True),
        sa.Column('status', sa.String(30), nullable=False),
        sa.Column('brief', sa.Text(), nullable=False),
        sa.Column('source_snapshot', sa.JSON(), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=True),
        sa.UniqueConstraint('tenant_id', 'property_id', 'opportunity_id', name='uq_content_plan_opportunity'))
    for table in TABLES:
        for column in ('tenant_id', 'property_id'):
            op.create_index(f'ix_{table}_{column}', table, [column])
    for column in ('account_id', 'opportunity_id', 'content_version_id'):
        op.create_index(f'ix_content_plans_{column}', 'content_plans', [column])
    if op.get_context().dialect.name == 'postgresql':
        for table in TABLES:
            op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY')
            expression = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')"
            op.execute(f'CREATE POLICY tenant_boundary ON {table} USING ({expression}) WITH CHECK ({expression})')
            # Standalone migration runs may not have the app roles installed yet.
            # Grant each established runtime role without granting cross-tenant access.
            op.execute(f"""DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'housignal_app') THEN
                    GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO housignal_app;
                END IF;
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'housignal_worker') THEN
                    GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO housignal_worker;
                END IF;
                END $$""")


def downgrade():
    for table in reversed(TABLES):
        op.drop_table(table)
