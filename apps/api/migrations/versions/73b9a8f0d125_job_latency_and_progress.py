"""durable job latency measurements and partial progress

Revision ID: 73b9a8f0d125
Revises: 19cd6f457d4b
"""
from alembic import op
import sqlalchemy as sa

revision = "73b9a8f0d125"
down_revision = "19cd6f457d4b"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("jobs", sa.Column("phase", sa.String(80), server_default="等待执行", nullable=False))
    for name in ("started_at", "attempt_started_at", "completed_at", "first_token_at"):
        op.add_column("jobs", sa.Column(name, sa.DateTime(timezone=True), nullable=True))
    # Terminal rows already record the final state transition in updated_at.
    # Historical start/first-token timestamps cannot be reconstructed safely.
    backfill = """UPDATE jobs SET phase = CASE status
        WHEN 'succeeded' THEN '已完成'
        WHEN 'failed' THEN '执行失败'
        WHEN 'cancelled' THEN '已取消'
        WHEN 'running' THEN '正在执行'
        ELSE '等待执行' END,
        completed_at = CASE WHEN status IN ('succeeded','failed','cancelled')
                            THEN updated_at ELSE NULL END"""
    if op.get_context().dialect.name == "postgresql":
        # FORCE RLS applies even to the migration owner. Use each established
        # tenant context rather than disabling tenant policies for the backfill.
        op.execute("""DO $$ DECLARE tenant_record RECORD;
            previous_tenant TEXT := current_setting('app.tenant_id', true);
            BEGIN
            FOR tenant_record IN SELECT id FROM tenants LOOP
                PERFORM set_config('app.tenant_id', tenant_record.id, true);
                """+backfill+""" WHERE tenant_id = tenant_record.id;
            END LOOP;
            PERFORM set_config('app.tenant_id', COALESCE(previous_tenant, ''), true);
            END $$""")
    else:
        op.execute(backfill)


def downgrade():
    for name in ("first_token_at", "completed_at", "attempt_started_at", "started_at", "phase"):
        op.drop_column("jobs", name)
