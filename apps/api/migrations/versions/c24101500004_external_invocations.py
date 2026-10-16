"""Durable project-scoped external tool receipts and idempotency."""
from alembic import op
import sqlalchemy as sa

revision = 'c24101500004'
down_revision = 'c24101500003'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('external_agent_invocations',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('property_id', sa.String(36), sa.ForeignKey('properties.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('grant_id', sa.String(36), sa.ForeignKey('external_agent_grants.id'), nullable=False),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('client_id', sa.String(200), nullable=False),
        sa.Column('tool_name', sa.String(80), nullable=False),
        sa.Column('idempotency_key', sa.String(128), nullable=False),
        sa.Column('payload_sha', sa.String(64), nullable=False),
        sa.Column('result_json', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('grant_id', 'tool_name', 'property_id', 'idempotency_key',
                            name='uq_external_agent_invocation_key'))
    for column in ('tenant_id', 'property_id', 'grant_id', 'user_id'):
        op.create_index(f'ix_external_agent_invocations_{column}', 'external_agent_invocations', [column])
    if op.get_context().dialect.name == 'postgresql':
        expression = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')"
        op.execute('ALTER TABLE external_agent_invocations ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE external_agent_invocations FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_boundary ON external_agent_invocations USING ({expression}) WITH CHECK ({expression})')
        for role in ('housignal_app', 'housignal_worker'):
            op.execute(f"""DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON external_agent_invocations TO {role};
                END IF; END $$""")


def downgrade():
    op.drop_table('external_agent_invocations')
