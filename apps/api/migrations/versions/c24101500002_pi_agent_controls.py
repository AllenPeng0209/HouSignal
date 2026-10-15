"""Durable steering and follow-up commands for the Pi agent runtime."""
from alembic import op
import sqlalchemy as sa

revision = 'c24101500002'
down_revision = 'c24101500001'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('agent_controls',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('property_id', sa.String(36), sa.ForeignKey('properties.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('job_id', sa.String(36), sa.ForeignKey('jobs.id'), nullable=False),
        sa.Column('conversation_id', sa.String(36), sa.ForeignKey('conversations.id'), nullable=False),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('private_input_cipher', sa.Text(), nullable=True),
        sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True))
    for column in ('tenant_id', 'property_id', 'job_id', 'conversation_id', 'user_id'):
        op.create_index(f'ix_agent_controls_{column}', 'agent_controls', [column])
    if op.get_context().dialect.name == 'postgresql':
        expression = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')"
        op.execute('ALTER TABLE agent_controls ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE agent_controls FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_boundary ON agent_controls USING ({expression}) WITH CHECK ({expression})')
        for role in ('housignal_app', 'housignal_worker'):
            op.execute(f"""DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON agent_controls TO {role};
                END IF; END $$""")


def downgrade():
    op.drop_table('agent_controls')
