"""Tenant guards, with a queue-only worker exception."""
from sqlalchemy import create_engine, inspect, text
from app.config import settings


def main():
    engine = create_engine(settings.migration_database_url, hide_parameters=True)
    with engine.begin() as conn:
        conn.execute(text('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO housignal_app'))
        conn.execute(text('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO housignal_app'))
        conn.execute(text('GRANT SELECT ON users, tenants, memberships, login_sessions TO housignal_worker'))
        for name in inspect(conn).get_table_names():
            columns = {column['name'] for column in inspect(conn).get_columns(name)}
            if 'tenant_id' not in columns or name in {'memberships', 'login_sessions'}:
                continue
            quoted = '"' + name.replace('"', '""') + '"'
            conn.execute(text(f'ALTER TABLE {quoted} ENABLE ROW LEVEL SECURITY'))
            conn.execute(text(f'ALTER TABLE {quoted} FORCE ROW LEVEL SECURITY'))
            conn.execute(text(f'DROP POLICY IF EXISTS tenant_boundary ON {quoted}'))
            expression = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')"
            conn.execute(text(f'CREATE POLICY tenant_boundary ON {quoted} USING ({expression}) WITH CHECK ({expression})'))
            conn.execute(text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON {quoted} TO housignal_worker'))
            if name == 'jobs':
                conn.execute(text(f'DROP POLICY IF EXISTS queue_worker ON {quoted}'))
                conn.execute(text(f'CREATE POLICY queue_worker ON {quoted} TO housignal_worker USING (true) WITH CHECK (true)'))
        conn.execute(text('REVOKE ALL ON alembic_version FROM housignal_app'))
    print('Tenant RLS installed; worker cross-tenant access limited to jobs')


if __name__ == '__main__':
    main()
