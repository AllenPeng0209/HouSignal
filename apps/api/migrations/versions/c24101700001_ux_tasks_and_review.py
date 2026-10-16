"""Explicit CRM attribution, follow-up deadlines and immutable review feedback."""
from alembic import op
import sqlalchemy as sa

revision='c24101700001'
down_revision='c24101500004'
branch_labels=None
depends_on=None


def _has_column(table,name):
    return name in {column['name'] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    # All additions are nullable: existing records retain an honest unknown time/link.
    for table,column in (('publications','published_at'),('leads','next_action_at'),('followups','next_action_at')):
        if not _has_column(table,column):
            op.add_column(table,sa.Column(column,sa.DateTime(timezone=True),nullable=True))
    if not _has_column('inquiries','publication_id'):
        with op.batch_alter_table('inquiries') as batch:
            batch.add_column(sa.Column('publication_id',sa.String(36),nullable=True))
            batch.create_foreign_key('fk_inquiry_publication','publications',['publication_id'],['id'])
            batch.create_index('ix_inquiries_publication_id',['publication_id'])
    if not sa.inspect(op.get_bind()).has_table('content_review_feedback'):
        op.create_table('content_review_feedback',
            sa.Column('id',sa.String(36),primary_key=True),
            sa.Column('tenant_id',sa.String(36),sa.ForeignKey('tenants.id'),nullable=False),
            sa.Column('property_id',sa.String(36),sa.ForeignKey('properties.id'),nullable=False),
            sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
            sa.Column('content_version_id',sa.String(36),sa.ForeignKey('content_versions.id'),nullable=False),
            sa.Column('version',sa.Integer(),nullable=False),
            sa.Column('render_asset_id',sa.String(36),nullable=True),
            sa.Column('reviewer_id',sa.String(36),sa.ForeignKey('users.id'),nullable=False),
            sa.Column('reviewer_name',sa.String(100),nullable=False),
            sa.Column('note',sa.Text(),nullable=False),
            sa.Column('shot_id',sa.String(100),nullable=True),
            sa.Column('sentence',sa.Text(),nullable=True))
        for column in ('tenant_id','property_id','content_version_id'):
            op.create_index(f'ix_content_review_feedback_{column}','content_review_feedback',[column])
    if op.get_context().dialect.name=='postgresql':
        expression="tenant_id = NULLIF(current_setting('app.tenant_id', true), '')"
        op.execute('ALTER TABLE content_review_feedback ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE content_review_feedback FORCE ROW LEVEL SECURITY')
        op.execute('DROP POLICY IF EXISTS tenant_boundary ON content_review_feedback')
        op.execute(f'CREATE POLICY tenant_boundary ON content_review_feedback USING ({expression}) WITH CHECK ({expression})')
        for role in ('housignal_app','housignal_worker'):
            op.execute(f"""DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN
                GRANT SELECT, INSERT ON content_review_feedback TO {role};
                END IF; END $$""")


def downgrade():
    op.drop_table('content_review_feedback')
    with op.batch_alter_table('inquiries') as batch:
        batch.drop_index('ix_inquiries_publication_id')
        batch.drop_constraint('fk_inquiry_publication',type_='foreignkey')
        batch.drop_column('publication_id')
    for table,column in (('followups','next_action_at'),('leads','next_action_at'),('publications','published_at')):
        op.drop_column(table,column)
