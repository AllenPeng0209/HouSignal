"""Native CRM profiles and durable viewing appointments.

Revision ID: c24101500001
Revises: c24101400001
"""
from alembic import op
import sqlalchemy as sa

revision='c24101500001'
down_revision='c24101400001'
branch_labels=None
depends_on=None
TABLES=('customer_profiles','viewing_appointments')


def _project_columns():
    return [sa.Column('id',sa.String(36),primary_key=True),
            sa.Column('tenant_id',sa.String(36),sa.ForeignKey('tenants.id'),nullable=False),
            sa.Column('property_id',sa.String(36),sa.ForeignKey('properties.id'),nullable=False),
            sa.Column('created_at',sa.DateTime(timezone=True),nullable=False)]


def upgrade():
    op.create_table('customer_profiles',*_project_columns(),
        sa.Column('lead_id',sa.String(36),sa.ForeignKey('leads.id'),nullable=False),
        sa.Column('details',sa.JSON(),server_default='{}',nullable=False),
        sa.Column('provenance',sa.JSON(),server_default='{}',nullable=False),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('lead_id',name='uq_customer_profile_lead'))
    op.create_table('viewing_appointments',*_project_columns(),
        sa.Column('lead_id',sa.String(36),sa.ForeignKey('leads.id'),nullable=False),
        sa.Column('date',sa.String(10),server_default='',nullable=False),
        sa.Column('time_window',sa.String(80),server_default='',nullable=False),
        sa.Column('starts_at',sa.DateTime(timezone=True),nullable=True),
        sa.Column('ends_at',sa.DateTime(timezone=True),nullable=True),
        sa.Column('timezone',sa.String(60),server_default='Asia/Shanghai',nullable=False),
        sa.Column('location',sa.Text(),server_default='',nullable=False),
        sa.Column('notes',sa.Text(),server_default='',nullable=False),
        sa.Column('status',sa.String(30),server_default='pending_details',nullable=False),
        sa.Column('source_note',sa.Text(),server_default='',nullable=False),
        sa.Column('created_by',sa.String(36),sa.ForeignKey('users.id'),nullable=False),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
    for table in TABLES:
        for column in ('tenant_id','property_id','lead_id'):
            op.create_index(f'ix_{table}_{column}',table,[column])
    if op.get_context().dialect.name=='postgresql':
        expression="tenant_id = NULLIF(current_setting('app.tenant_id', true), '')"
        for table in TABLES:
            op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY')
            op.execute(f'CREATE POLICY tenant_boundary ON {table} USING ({expression}) WITH CHECK ({expression})')
            for role in ('housignal_app','housignal_worker'):
                op.execute(f"""DO $$ BEGIN
                    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN
                        GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {role};
                    END IF;
                END $$""")


def downgrade():
    for table in reversed(TABLES):op.drop_table(table)
