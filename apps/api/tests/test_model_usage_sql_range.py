"""Token usage aggregation must not overflow PostgreSQL's INTEGER addition."""
from sqlalchemy import event
from sqlalchemy.dialects import postgresql

from app.agent_models import ModelUsage
from app.model_budget import used_daily_tokens
from test_business import scenario


def test_usage_aggregation_widens_columns_before_postgresql_integer_addition(scenario):
    db, *_ = scenario
    db.add(ModelUsage(tenant_id='tenant', property_id='p', job_id='long-task',
                      model='qwen-plus', input_tokens=2_147_483_647, output_tokens=2_147_483_647))
    db.commit()
    statements = []
    def capture(connection, statement, multiparams, params, execution_options):
        statements.append(str(statement.compile(dialect=postgresql.dialect())))
    event.listen(db.get_bind(), 'before_execute', capture)
    try:
        assert used_daily_tokens(db, 'tenant') == 4_294_967_294
    finally:
        event.remove(db.get_bind(), 'before_execute', capture)

    # SQLite represents the sum correctly even without a cast; checking the
    # emitted PostgreSQL query catches the INTEGER intermediate overflow too.
    assert len(statements) == 1
    assert 'CAST(model_usage.input_tokens AS BIGINT)' in statements[0]
    assert 'CAST(model_usage.output_tokens AS BIGINT)' in statements[0]
