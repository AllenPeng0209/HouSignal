"""Project-scoped external Agent OAuth identity records (no business mutations)."""
from alembic import op
import sqlalchemy as sa

revision = 'c24101500003'
down_revision = 'c24101500002'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('external_agent_clients',
        sa.Column('id', sa.String(200), primary_key=True),
        sa.Column('name', sa.String(160), nullable=False),
        sa.Column('redirect_uris', sa.JSON(), nullable=False),
        sa.Column('source', sa.String(20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('external_agent_authorizations',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('binding_hash', sa.String(64), nullable=False),
        sa.Column('client_id', sa.String(200), sa.ForeignKey('external_agent_clients.id'), nullable=False),
        sa.Column('redirect_uri', sa.Text(), nullable=False),
        sa.Column('resource', sa.String(512), nullable=False),
        sa.Column('scope', sa.String(200), nullable=False),
        sa.Column('state', sa.Text(), nullable=False),
        sa.Column('challenge', sa.String(43), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_external_agent_authorizations_request_hash', 'external_agent_authorizations', ['request_hash'], unique=True)
    op.create_table('external_agent_grants',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('organization_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('client_id', sa.String(200), sa.ForeignKey('external_agent_clients.id'), nullable=False),
        sa.Column('resource', sa.String(512), nullable=False),
        sa.Column('scope', sa.String(200), nullable=False),
        sa.Column('project_ids', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True))
    for column in ('organization_id', 'user_id', 'client_id'):
        op.create_index(f'ix_external_agent_grants_{column}', 'external_agent_grants', [column])
    op.create_table('external_agent_codes',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('code_hash', sa.String(64), nullable=False),
        sa.Column('grant_id', sa.String(36), sa.ForeignKey('external_agent_grants.id'), nullable=False),
        sa.Column('client_id', sa.String(200), sa.ForeignKey('external_agent_clients.id'), nullable=False),
        sa.Column('redirect_uri', sa.Text(), nullable=False),
        sa.Column('resource', sa.String(512), nullable=False),
        sa.Column('challenge', sa.String(43), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_external_agent_codes_code_hash', 'external_agent_codes', ['code_hash'], unique=True)
    op.create_index('ix_external_agent_codes_grant_id', 'external_agent_codes', ['grant_id'])
    op.create_table('external_agent_credentials',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('token_hash', sa.String(64), nullable=False),
        sa.Column('grant_id', sa.String(36), sa.ForeignKey('external_agent_grants.id'), nullable=False),
        sa.Column('kind', sa.String(10), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_external_agent_credentials_token_hash', 'external_agent_credentials', ['token_hash'], unique=True)
    op.create_index('ix_external_agent_credentials_grant_id', 'external_agent_credentials', ['grant_id'])
    # These are login identity tables, like login_sessions, not tenant business
    # records. A token hash must be looked up before RLS tenancy can be restored.
    # All grant-management routes enforce both user_id and organization_id.
    if op.get_context().dialect.name == 'postgresql':
        for table in ('external_agent_clients', 'external_agent_authorizations', 'external_agent_grants',
                      'external_agent_codes', 'external_agent_credentials'):
            for role in ('housignal_app', 'housignal_worker'):
                op.execute(f"""DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN
                    GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {role};
                    END IF; END $$""")


def downgrade():
    for table in ('external_agent_credentials', 'external_agent_codes', 'external_agent_grants',
                  'external_agent_authorizations', 'external_agent_clients'):
        op.drop_table(table)
